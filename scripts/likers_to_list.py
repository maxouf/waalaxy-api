#!/usr/bin/env python3
"""Liste Waalaxy des personnes ayant réagi aux N derniers posts d'un profil LinkedIn.

Sans cookie LinkedIn : les posts et leurs réactions viennent d'Apify
(acteur harvestapi/linkedin-profile-posts, ~0,002 $ par post et par réaction).
Les prospects entrent dans Waalaxy par POST /profesor/prospects/addProspectFromIntegration
(route interne identique à l'API publique) : le serveur résout le profil et persiste.

Usage :
  likers_to_list.py --profile https://www.linkedin.com/in/xxx --list "Nom de liste" [--posts 5] [--go]
  likers_to_list.py --dataset <datasetId Apify> --list "Nom de liste" [--go]   # réutilise un run

Sans --go : rien n'est envoyé à Waalaxy (l'appel Apify, lui, est facturé dès qu'il part).
Jeton Apify : APIFY_API_TOKEN dans ~/.config/apify/apify.env.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from waalaxy_api.client import WaalaxyClient, WaalaxyError  # noqa: E402

APIFY_ENV = Path.home() / ".config/apify/apify.env"
ACTOR = "harvestapi~linkedin-profile-posts"
BATCH = 10


def apify_token() -> str:
    tok = os.environ.get("APIFY_API_TOKEN")
    if not tok and APIFY_ENV.exists():
        for line in APIFY_ENV.read_text().splitlines():
            if line.startswith("APIFY_API_TOKEN="):
                tok = line.split("=", 1)[1].strip().strip("'\"")
    if not tok:
        raise SystemExit(f"APIFY_API_TOKEN introuvable ({APIFY_ENV})")
    return tok


def run_apify(profile: str, posts: int, token: str) -> list[dict]:
    """Lance l'acteur et rend les items du dataset (posts + réactions)."""
    r = httpx.post(
        f"https://api.apify.com/v2/acts/{ACTOR}/run-sync-get-dataset-items",
        params={"token": token, "clean": "true", "format": "json"},
        json={"targetUrls": [profile], "maxPosts": posts, "includeReposts": False,
              "includeQuotePosts": True, "scrapeReactions": True, "maxReactions": 5000,
              "postNestedReactions": False, "scrapeComments": False},
        timeout=600.0,
    )
    r.raise_for_status()
    return r.json()


def fetch_dataset(dataset_id: str, token: str) -> list[dict]:
    r = httpx.get(f"https://api.apify.com/v2/datasets/{dataset_id}/items",
                  params={"token": token, "clean": "true", "format": "json"}, timeout=120.0)
    r.raise_for_status()
    return r.json()


def split_name(name: str) -> tuple[str, str]:
    clean = re.sub(r"[^\w\s'\-.]", "", name, flags=re.UNICODE).strip()
    parts = clean.split()
    return (parts[0], " ".join(parts[1:])) if parts else ("", "")


def people_from_items(items: list[dict], exclude_ids: set[str]) -> tuple[list[dict], list[dict]]:
    posts = [it for it in items if it.get("type") == "post"]
    seen, people = set(), []
    for it in items:
        if it.get("type") != "reaction":
            continue
        a = it["actor"]
        if "/company/" in a.get("linkedinUrl", "") or a["id"] in exclude_ids or a["id"] in seen:
            continue
        seen.add(a["id"])
        first, last = split_name(a.get("name", ""))
        people.append({"memberId": a["id"], "name": a.get("name", ""), "firstName": first, "lastName": last})
    return posts, people


def get_or_create_list(c: WaalaxyClient, name: str) -> str:
    lists = c._call("POST", "/profesor/prospectLists/getProspectLists", json_body={})
    existing = next((l for l in lists if l["name"] == name), None)
    if existing:
        return existing["_id"]
    created = c._write("POST", "/profesor/prospectLists/createProspectList",
                       {"prospectList": {"name": name, "iconColor": "#0A66C2", "iconLabel": "LI"}},
                       dry_run=False, target=f"créer la liste « {name} »")
    list_id = created.get("_id") or created.get("prospectList", {}).get("_id")
    if not list_id:
        raise SystemExit(f"createProspectList sans _id : {created}")
    return list_id


def import_people(c: WaalaxyClient, list_id: str, people: list[dict], origin: str,
                  move_duplicates: bool = False) -> tuple[dict, list[str], list]:
    counts: dict[str, int] = {}
    dups, errors = [], []
    for i in range(0, len(people), BATCH):
        chunk = people[i:i + BATCH]
        res = c._write("POST", "/profesor/prospects/addProspectFromIntegration", {
            "prospects": [{"url": f"https://www.linkedin.com/in/{p['memberId']}",
                           "customProfile": {k: v for k, v in
                                             {"firstName": p["firstName"], "lastName": p["lastName"]}.items() if v}}
                          for p in chunk],
            "prospectListId": list_id, "origin": {"name": origin},
            "canCreateDuplicates": False, "moveDuplicatesToOtherList": move_duplicates,
        }, dry_run=False, target=f"liste {list_id} : importer {len(chunk)} prospects")
        if res.get("dry_run"):
            raise SystemExit("WAALAXY_DRY_RUN est actif : rien n'est envoyé.")
        # `result` ne suit pas l'ordre d'entrée : on se fie au memberId renvoyé.
        by_id = {p["memberId"]: p for p in chunk}
        for r in res.get("result", []):
            code = r.get("importCode", "?")
            counts[code] = counts.get(code, 0) + 1
            p = by_id.get(((r.get("prospect") or {}).get("profile") or {}).get("memberId"), {"name": "?"})
            if code == "duplicated_prospect":
                dups.append(p["name"])
            elif code not in ("success", "prospect_successfully_moved_to_another_list"):
                errors.append((p["name"], code, r.get("message")))
    return counts, dups, errors


def missing_from_list(c: WaalaxyClient, list_id: str, people: list[dict]) -> list[dict]:
    r = c._call("POST", "/profesor/prospects/getProspects", json_body={
        "prospectList": list_id, "prospectSelection": {"excluded": []}, "size": 999999})
    ids = {(p.get("profile") or {}).get("memberId") for p in r.get("prospects", [])}
    return [p for p in people if p["memberId"] not in ids]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--profile", help="URL du profil LinkedIn (lance un run Apify, facturé)")
    src.add_argument("--dataset", help="id d'un dataset Apify déjà produit")
    ap.add_argument("--list", required=True, help="nom de la liste Waalaxy (créée si absente)")
    ap.add_argument("--posts", type=int, default=5)
    ap.add_argument("--exclude", action="append", default=[], help="memberId à exclure (répétable)")
    ap.add_argument("--origin", default="claude-code", help="affiché « API-<origin> » dans Waalaxy")
    ap.add_argument("--move-duplicates", action="store_true",
                    help="déplace dans la liste les prospects déjà présents dans une autre liste")
    ap.add_argument("--go", action="store_true", help="écrit réellement dans Waalaxy")
    args = ap.parse_args()

    token = apify_token()
    items = fetch_dataset(args.dataset, token) if args.dataset else run_apify(args.profile, args.posts, token)
    posts, people = people_from_items(items, set(args.exclude))
    print(f"{len(posts)} posts, {sum(1 for i in items if i.get('type') == 'reaction')} réactions, "
          f"{len(people)} personnes uniques (hors pages entreprise)")
    for p in posts:
        print("  -", (p.get("postedAt") or {}).get("date", "")[:10], (p.get("engagement") or {}).get("likes"), "réactions |",
              (p.get("content") or "")[:60].replace("\n", " "))
    if not args.go:
        print("dry-run : rien n'est envoyé à Waalaxy. Relancer avec --go.")
        return

    c = WaalaxyClient()
    list_id = get_or_create_list(c, args.list)
    counts, dups, errors = import_people(c, list_id, people, args.origin, args.move_duplicates)
    missing = missing_from_list(c, list_id, people)
    print("import :", counts, "| liste", args.list, f"({list_id}) : {len(people) - len(missing)}/{len(people)} présents")
    if dups:
        print(f"déjà dans une autre liste ({len(dups)}) :", ", ".join(dups), "-> --move-duplicates pour les déplacer")
    if missing:
        print(f"absents de la liste ({len(missing)}) :", ", ".join(p["name"] for p in missing))
    if errors:
        print("erreurs :", json.dumps(errors, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except WaalaxyError as e:
        print("ERREUR Waalaxy :", e)
        sys.exit(1)
