"""Client de l'API interne Waalaxy — produit par /api-reverse le 2026-09-23.

Config : ~/.config/waalaxy/api.env (chmod 600), ou les memes cles en variables d'environnement
(l'environnement prime ; WAALAXY_CONFIG change le chemin du fichier).
    WAALAXY_TOKEN=...        # JWT du mode cloud (localStorage.cloudAuthStore.apiToken), sans expiration
    WAALAXY_USER_ID=...      # optionnel : deduit du jeton (payload._id) s'il manque
    WAALAXY_API_URL=https://stargate.prod.aws.waalaxy.com/api   # optionnel, valeur par defaut

Usage :
    python client.py campaigns                    # campagnes running/paused (page de 20)
    python client.py campaigns --all --state draft running paused stopped
    python client.py stats                        # tableau compact : une ligne par campagne
    python client.py campaign <id>
    python client.py drafts
    python client.py draft <draftId>
    python client.py summary <campaignId>
    python client.py allstats [--start 2026-01-01T00:00:00.000Z --end ...]
    python client.py play|pause|stop <campaignId> [--go]
    python client.py lists                        # listes de prospects
    python client.py eligible <listId>            # prospects d'une liste hors campagne en cours
    python client.py launch-draft <draftId> --list <listId> [--name N] [--go]

Toute ecriture est en dry-run par defaut : le payload s'affiche, rien ne part sans --go.
WAALAXY_DRY_RUN=1 dans l'environnement bloque toute ecriture, --go ou pas (tests, evals).
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path

import httpx

CONFIG = Path(os.environ.get("WAALAXY_CONFIG") or (Path.home() / ".config" / "waalaxy" / "api.env"))
ENV_KEYS = ("WAALAXY_TOKEN", "WAALAXY_USER_ID", "WAALAXY_API_URL")
DEFAULT_API_URL = "https://stargate.prod.aws.waalaxy.com/api"
RATE_LIMIT_S = 0.5  # 2 req/s max
STATES = ("draft", "paused", "running", "stopped")


class WaalaxyError(Exception):
    """Erreur API ou de configuration. `status` vaut 0 hors HTTP."""

    def __init__(self, message: str, status: int = 0, body: str = ""):
        super().__init__(message)
        self.status = status
        self.body = body


def dry_run_force() -> bool:
    """WAALAXY_DRY_RUN=1 : aucune ecriture ne part, quoi qu'on demande."""
    return os.environ.get("WAALAXY_DRY_RUN", "").strip() not in ("", "0", "false")


def load_env(path: Path = CONFIG) -> dict[str, str]:
    """Fichier api.env puis variables d'environnement (qui priment). Le userId manquant
    est lu dans le jeton : le JWT Waalaxy porte {_id, linkedinId} en clair."""
    env: dict[str, str] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip("'\"")
    for k in ENV_KEYS:
        if os.environ.get(k):
            env[k] = os.environ[k].strip()
    if not env.get("WAALAXY_TOKEN"):
        raise WaalaxyError(
            f"WAALAXY_TOKEN manquant : ni dans {path} ni dans l'environnement. "
            "Voir README, section Installation, pour recuperer le jeton.")
    if not env.get("WAALAXY_USER_ID"):
        env["WAALAXY_USER_ID"] = user_id_from_token(env["WAALAXY_TOKEN"]) or ""
    return env


def user_id_from_token(token: str) -> str | None:
    """payload._id du JWT (non verifie : on ne fait que lire)."""
    try:
        part = token.split(".")[1]
        payload = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        return payload.get("_id")
    except (IndexError, ValueError, AttributeError):
        return None


class WaalaxyClient:
    """Une seule passerelle (stargate), un service par prefixe : /profesor, /hawking, ..."""

    def __init__(self, env: dict[str, str] | None = None, rate: float = RATE_LIMIT_S):
        self.env = env or load_env()
        self.rate = rate
        self._last_call = 0.0
        self.user_id = self.env.get("WAALAXY_USER_ID")
        self.http = httpx.Client(
            base_url=self.env.get("WAALAXY_API_URL", DEFAULT_API_URL).rstrip("/"),
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self.env['WAALAXY_TOKEN']}",
            },
            timeout=30.0,
        )

    def _throttle(self) -> None:
        delta = time.monotonic() - self._last_call
        if delta < self.rate:
            time.sleep(self.rate - delta)
        self._last_call = time.monotonic()

    def _call(self, method: str, path: str, *, json_body=None, params=None):
        self._throttle()
        try:
            r = self.http.request(method, path, json=json_body, params=params)
        except httpx.HTTPError as e:
            raise WaalaxyError(f"reseau : {e}") from e
        if r.status_code == 401:
            raise WaalaxyError(
                "401 — jeton refuse. Se reconnecter sur app.waalaxy.com et recopier le jeton dans "
                f"{CONFIG} (voir README, section Installation).", 401, r.text[:800])
        if r.status_code >= 400:
            # Les erreurs de validation (zod) listent les champs manquants : precieux, on les garde.
            raise WaalaxyError(f"{r.status_code} {method} {path} : {r.text[:800]}", r.status_code, r.text[:800])
        return r.json() if r.content else {"status": r.status_code}

    def _write(self, method: str, path: str, payload: dict | None, *, dry_run: bool, target: str):
        """Ecriture. En dry-run (demande ou force par WAALAXY_DRY_RUN) rien ne part :
        on rend la requete qui serait emise, pour la montrer."""
        if dry_run or dry_run_force():
            return {"dry_run": True, "method": method, "path": path, "cible": target, "payload": payload}
        return self._call(method, path, json_body=payload)

    # --- Lecture : campagnes (service profesor) ---------------------------

    def campaigns(self, states=("running", "paused"), *, start: int = 0, count: int = 20, search: str | None = None):
        """POST /profesor/campaigns/getAll. `total` = total filtre, la page fait `count` elements."""
        body = {"state": list(states), "start": start, "count": count}
        if search:
            body["search"] = {"value": search, "fields": ["name"]}
        return self._call("POST", "/profesor/campaigns/getAll", json_body=body)

    def all_campaigns(self, states=STATES):
        """L'interface passe count=99999 pour tout avoir d'un coup ; on fait pareil."""
        return self.campaigns(states, count=99999)["campaigns"]

    def campaign(self, campaign_id: str) -> dict:
        """GET /profesor/campaigns/:id -> {campaign: {...}} ; on rend la campagne elle-meme (avec `world`)."""
        out = self._call("GET", f"/profesor/campaigns/{campaign_id}")
        return out.get("campaign", out) if isinstance(out, dict) else out

    def count_per_status(self):
        return self._call("POST", "/profesor/campaigns/countPerStatus", json_body={})

    def drafts(self):
        """Un brouillon est une campagne en state=draft dans getAll (sans `world`)."""
        return self.campaigns(("draft",), count=99999)["campaigns"]

    def draft(self, draft_id: str):
        """GET /profesor/campaigns/draft/:draftId -> {draftCampaign: {sequence, prospectSources, ...}}"""
        return self._call("GET", f"/profesor/campaigns/draft/{draft_id}")["draftCampaign"]

    def travelers_summary(self, campaign_id: str, status: dict | None = None):
        """POST /profesor/travelerssummary — ids de voyageurs regroupes par etat."""
        return self._call("POST", "/profesor/travelerssummary", json_body={"campaignId": campaign_id, "status": status or {}})

    # --- Lecture : statistiques (service hawking) --------------------------

    def all_stats(self, start_date: str = "2020-01-01T00:00:00.000Z", end_date: str | None = None, **query):
        """GET /hawking/stats/allStats?users=<userId>&startDate=…&endDate=… (ISO UTC).

        `users` obligatoire ; sans `startDate`/`endDate` tout est a zero (verifie le 2026-09-23,
        les autres noms — from/to, start/end… — sont ignores silencieusement).
        """
        query.setdefault("users", self.user_id)
        query.setdefault("startDate", start_date)
        query.setdefault("endDate", end_date or time.strftime("%Y-%m-%dT23:59:59.000Z", time.gmtime()))
        return self._call("GET", "/hawking/stats/allStats", params=query)

    def stats_table(self, states=STATES) -> list[dict]:
        """Stats par campagne, sans appel supplementaire : tout est dans getAll.travelersCount."""
        return [campaign_row(c) for c in self.all_campaigns(states)]

    # --- Lecture : prospects (service profesor) ----------------------------

    def prospect_lists(self) -> list[dict]:
        """POST /profesor/prospectLists/getProspectLists -> tableau brut de listes."""
        out = self._call("POST", "/profesor/prospectLists/getProspectLists", json_body={})
        return out if isinstance(out, list) else out.get("prospectLists", [])

    def eligible_prospects(self, list_id: str) -> tuple[list[str], int]:
        """Ids des prospects d'une liste qui ne sont pas deja en campagne (traveling/paused/frozen/postponed),
        comme le fait l'app avant createAndStartCampaign. Rend (ids, prospectsCount)."""
        out = self._call("POST", "/profesor/prospects/getProspects", json_body={
            "prospectList": list_id,
            "prospectSelection": {"excluded": []},
            "excTravelerStatus": ["traveling", "paused", "frozen", "postponed"],
            "size": 9999999,
            "projection": {"_idOnly": True},
        })
        ids = [p["_id"] for p in out.get("prospects", [])]
        return ids, int(out.get("prospectsCount", len(ids)))

    # --- Ecriture : cycle de vie (dry-run par defaut) ----------------------

    def play(self, campaign_id: str, *, dry_run: bool = True):
        """Reprend une campagne en pause. Ne lance PAS un brouillon (voir launch_from_draft)."""
        return self._write("PUT", f"/profesor/campaigns/{campaign_id}/play", None, dry_run=dry_run, target=self._name(campaign_id))

    def pause(self, campaign_id: str, *, dry_run: bool = True):
        return self._write("PUT", f"/profesor/campaigns/{campaign_id}/pause", None, dry_run=dry_run, target=self._name(campaign_id))

    def stop(self, campaign_id: str, *, dry_run: bool = True):
        """Irreversible dans l'interface (la campagne passe en Archivee)."""
        return self._write("PUT", f"/profesor/campaigns/{campaign_id}/stop", None, dry_run=dry_run, target=self._name(campaign_id))

    def build_start_payload(self, draft_id: str, *, name: str | None = None, prospects=None, triggers=None,
                            icon_color: str | None = None, prospect_list_id: str | None = None) -> dict:
        """Reproduit createAndStartCampaign de l'interface : la sequence du brouillon devient worldToCreate.

        prospects : [{"listId": ..., "prospectIds": [...]}] — ou `prospect_list_id`, et les ids eligibles
        sont recuperes ici. Sans prospects ni triggers, la campagne est creee vide (running, 0 voyageur).
        Le brouillon n'est pas supprime par l'API (l'app appelle deleteDraft a part).
        """
        d = self.draft(draft_id)
        if prospect_list_id and not prospects:
            ids, _ = self.eligible_prospects(prospect_list_id)
            prospects = [{"listId": prospect_list_id, "prospectIds": ids}]
        seq = {k: v for k, v in d["sequence"].items() if k not in ("_id", "createdAt", "updatedAt")}
        campaign_name = name or d["name"]
        return {
            "name": campaign_name,
            "worldToCreate": {**seq, "name": campaign_name},
            "iconColor": icon_color or d.get("iconColor") or "pink",
            "triggers": triggers or [],
            "prospects": prospects or [],
            "imports": [],
        }

    def launch_from_draft(self, draft_id: str, *, dry_run: bool = True, **kw):
        """POST /profesor/campaigns — cree la campagne directement en etat running."""
        payload = self.build_start_payload(draft_id, **kw)
        return self._write("POST", "/profesor/campaigns", payload, dry_run=dry_run, target=f"nouvelle campagne « {payload['name']} » depuis le brouillon {draft_id}")

    def _name(self, campaign_id: str) -> str:
        try:
            c = self.campaign(campaign_id)
            return f"campagne « {c.get('name')} » ({c.get('state')})"
        except WaalaxyError:
            return campaign_id


def campaign_row(c: dict) -> dict:
    """Une ligne de stats par campagne, a partir d'un element de getAll (travelersCount)."""
    tc = c.get("travelersCount") or {}
    return {
        "id": c["_id"],
        "name": c["name"],
        "state": c["state"],
        "total": tc.get("total", 0),
        "traveling": tc.get("traveling", 0),
        "finished": tc.get("finished", 0),
        "error": tc.get("error", 0),
        "accepted": tc.get("hasAcceptedInvitation", 0),
        "not_accepted": tc.get("hasNotAcceptedInvitation", 0),
        "replied": tc.get("hasReplied", 0),
        "not_replied": tc.get("hasNotReplied", 0),
        "updated": (c.get("updatedAt") or "")[:10],
    }


def _print_table(rows: list[dict]) -> None:
    if not rows:
        print("(aucune campagne)")
        return
    cols = list(rows[0].keys())
    widths = {c: max(len(c), *(len(str(r[c])) for r in rows)) for c in cols}
    print("  ".join(c.ljust(widths[c]) for c in cols))
    for r in rows:
        print("  ".join(str(r[c]).ljust(widths[c]) for c in cols))


def main() -> None:
    ap = argparse.ArgumentParser(description="Client API interne Waalaxy")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("campaigns")
    p.add_argument("--state", nargs="+", default=["running", "paused"], choices=STATES)
    p.add_argument("--all", action="store_true", help="count=99999")
    p.add_argument("--search")
    sub.add_parser("stats")
    sub.add_parser("count")
    sub.add_parser("campaign").add_argument("id")
    sub.add_parser("drafts")
    sub.add_parser("draft").add_argument("id")
    sub.add_parser("summary").add_argument("id")
    p = sub.add_parser("allstats")
    p.add_argument("--start", default="2020-01-01T00:00:00.000Z", help="startDate ISO UTC")
    p.add_argument("--end", help="endDate ISO UTC (defaut : aujourd'hui)")
    p.add_argument("--q", action="append", default=[], help="cle=valeur supplementaire, repetable")
    for cmd in ("play", "pause", "stop"):
        p = sub.add_parser(cmd)
        p.add_argument("id")
        p.add_argument("--go", action="store_true")
    sub.add_parser("lists")
    sub.add_parser("eligible").add_argument("id")
    p = sub.add_parser("launch-draft")
    p.add_argument("id")
    p.add_argument("--list", dest="list_id", help="liste de prospects a enroler (ids eligibles recuperes)")
    p.add_argument("--name")
    p.add_argument("--go", action="store_true")
    args = ap.parse_args()

    try:
        _run(args)
    except WaalaxyError as e:
        sys.exit(str(e))


def _run(args) -> None:
    c = WaalaxyClient()
    if args.cmd == "campaigns":
        out = c.campaigns(args.state, count=99999 if args.all else 20, search=args.search)
        out = {"total": out["total"], "campaigns": [{k: v for k, v in x.items() if k != "world"} for x in out["campaigns"]]}
    elif args.cmd == "stats":
        _print_table(c.stats_table())
        return
    elif args.cmd == "count":
        out = c.count_per_status()
    elif args.cmd == "campaign":
        out = c.campaign(args.id)
    elif args.cmd == "drafts":
        out = [{"id": d["_id"], "name": d["name"], "updated": d["updatedAt"][:10]} for d in c.drafts()]
    elif args.cmd == "draft":
        out = c.draft(args.id)
    elif args.cmd == "summary":
        out = c.travelers_summary(args.id)
    elif args.cmd == "allstats":
        out = c.all_stats(args.start, args.end, **dict(kv.split("=", 1) for kv in args.q))
    elif args.cmd == "lists":
        out = [{"id": l["_id"], "name": l["name"], "total": l.get("totalProspects", 0)} for l in c.prospect_lists()]
    elif args.cmd == "eligible":
        ids, count = c.eligible_prospects(args.id)
        out = {"eligible": len(ids), "prospectsCount": count}
    elif args.cmd in ("play", "pause", "stop"):
        out = getattr(c, args.cmd)(args.id, dry_run=not args.go)
    elif args.cmd == "launch-draft":
        out = c.launch_from_draft(args.id, name=args.name, prospect_list_id=args.list_id, dry_run=not args.go)
    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
