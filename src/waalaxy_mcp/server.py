#!/usr/bin/env python3
"""Serveur MCP Waalaxy (stdio).

Habillage mince du client `waalaxy_api` (API interne, passerelle stargate) :
stats de campagnes, brouillons, listes de prospects, cycle de vie et lancement.

GARDE-FOU. Toute ecriture (pause, reprise, archivage, lancement) passe par
`confirm=True`. Sans lui, l'outil rend un apercu nomme de ce qui partirait et
n'emet aucune requete d'ecriture. `WAALAXY_DRY_RUN=1` bloque en plus au niveau
du client, quoi que dise `confirm` (tests, evals).

Authentification : jeton du mode cloud dans ~/.config/waalaxy/api.env, sans
expiration. Cette API est interne et non documentee : elle peut changer sans preavis.
"""
from __future__ import annotations

import json
import time
from typing import Any

try:  # mcp >= 2.0
    from mcp.server import MCPServer as _Server
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server

from waalaxy_api.client import STATES, WaalaxyClient, WaalaxyError

from . import views

mcp = _Server("waalaxy")

_CLIENT: WaalaxyClient | None = None

# Codes d'erreur de POST /campaigns releves dans le code de l'app.
_CODES = {
    "R005400-004": "des prospects sont deja dans une campagne en cours",
    "R005409-002": "ce nom de campagne est deja pris",
    "R002400-011": "ce nom de campagne est deja pris",
    "R002401-001": "pas la permission de creer cette sequence",
    "R002401-002": "plan Business requis pour cette sequence",
    "R002400-002": "compte email introuvable (etape email dans la sequence)",
}


def _client() -> WaalaxyClient:
    """Indirection testable : les tests la remplacent par un client factice."""
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = WaalaxyClient()
    return _CLIENT


def _out(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=1)


def _erreur(e: WaalaxyError) -> str:
    msg = str(e)
    for code, sens in _CODES.items():
        if code in (e.body or ""):
            msg = f"{sens} ({code})"
            break
    return _out({"erreur": msg, "status": e.status})


def _resultat_ecriture(r: Any, action: str) -> dict:
    if isinstance(r, dict) and r.get("dry_run"):
        return {"execute": False, "dry_run_force": True, "action": action,
                "note": "WAALAXY_DRY_RUN est actif : rien n'est parti."}
    return {"execute": True, "action": action, "resultat": r}


# --- Lecture ----------------------------------------------------------------------

@mcp.tool()
def waalaxy_stats(states: list[str] | None = None, search: str | None = None) -> str:
    """Stats par campagne : une ligne par campagne (etat, total, en cours, termines, erreurs,
    invitations acceptees/non, reponses/sans reponse, derniere maj).

    `states` parmi draft, paused, running, stopped ; defaut : running + paused.
    `search` filtre sur le nom. Les compteurs viennent de Waalaxy, pas d'appel supplementaire.
    Pour le detail d'une campagne (sequence, listes d'origine) : waalaxy_campaign.
    """
    st = tuple(states) if states else ("running", "paused")
    bad = [s for s in st if s not in STATES]
    if bad:
        return _out({"erreur": f"etats inconnus : {bad} ; attendus : {list(STATES)}"})
    try:
        out = _client().campaigns(st, count=99999, search=search)
    except WaalaxyError as e:
        return _erreur(e)
    rows = [views.campaign_row(c) for c in out.get("campaigns", [])]
    return _out({"total": out.get("total", len(rows)), "campagnes": rows})


@mcp.tool()
def waalaxy_campaign(campaign_id: str, full: bool = False) -> str:
    """Fiche d'une campagne : etat, dates, listes de prospects d'origine, compteurs de prospects,
    sequence en etapes lisibles. `full=True` rend en plus la campagne brute de l'API (`brut`).
    """
    cl = _client()
    try:
        c = cl.campaign(campaign_id)
        counts = views.summary_counts(cl.travelers_summary(campaign_id)) if c.get("state") != "draft" else {}
    except WaalaxyError as e:
        return _erreur(e)
    out = views.campaign_detail(c, counts)
    if full:
        out["brut"] = c
    return _out(out)


@mcp.tool()
def waalaxy_drafts() -> str:
    """Brouillons de campagnes (id, nom, date). Un brouillon est une sequence prete, sans prospects
    enroles : pour le lancer, waalaxy_launch_draft avec une liste de prospects.
    """
    try:
        ds = _client().drafts()
    except WaalaxyError as e:
        return _erreur(e)
    return _out({"brouillons": [views.draft_row(d) for d in ds]})


@mcp.tool()
def waalaxy_draft(draft_id: str, full: bool = False) -> str:
    """Detail d'un brouillon : nom, sources de prospects prevues dans l'interface, sequence en etapes.
    `full=True` ajoute le brouillon brut (`brut`).
    """
    try:
        d = _client().draft(draft_id)
    except WaalaxyError as e:
        return _erreur(e)
    out = views.draft_detail(d)
    if full:
        out["brut"] = d
    return _out(out)


@mcp.tool()
def waalaxy_travelers_summary(campaign_id: str, full: bool = False) -> str:
    """Prospects d'une campagne groupes par etat (traveling, finished, error, hasReplied, ...),
    en nombre. `full=True` rend aussi les ids de prospects par etat (volumineux).
    """
    try:
        r = _client().travelers_summary(campaign_id)
    except WaalaxyError as e:
        return _erreur(e)
    return _out({"campaign_id": campaign_id, "par_etat": views.travelers_summary(r, full)})


@mcp.tool()
def waalaxy_global_stats(start_date: str = "2020-01-01", end_date: str | None = None) -> str:
    """Stats globales du compte sur une periode : invitations envoyees/acceptees et taux, messages
    envoyes/reponses et taux, emails, email finder. Dates `YYYY-MM-DD` (UTC) ; fin = aujourd'hui.
    Pour une campagne precise, utiliser waalaxy_stats (compteurs par campagne).
    """
    start = _iso(start_date, "00:00:00.000Z")
    end = _iso(end_date, "23:59:59.000Z") if end_date else time.strftime("%Y-%m-%dT23:59:59.000Z", time.gmtime())
    try:
        r = _client().all_stats(start, end)
    except WaalaxyError as e:
        return _erreur(e)
    return _out(views.global_stats(r, start, end))


def _iso(d: str, suffix: str) -> str:
    d = (d or "").strip()
    return d if "T" in d else f"{d}T{suffix}"


@mcp.tool()
def waalaxy_prospect_lists() -> str:
    """Listes de prospects du compte (id, nom, nombre de prospects, liste « ne pas contacter »).
    L'id sert a waalaxy_launch_draft. Le nombre inclut les prospects deja en campagne.
    """
    try:
        ls = _client().prospect_lists()
    except WaalaxyError as e:
        return _erreur(e)
    rows = [views.prospect_list_row(l) for l in ls]
    rows.sort(key=lambda r: -r["prospects"])
    return _out({"listes": rows})


# --- Lecture : prospects, tags, listes ---------------------------------------------------

def _tags_by_id(c) -> dict:
    try:
        return {t["_id"]: t.get("name") for t in c.tags()}
    except WaalaxyError:
        return {}


@mcp.tool()
def waalaxy_search_prospects(query: str | None = None, list_id: str | None = None, limit: int = 20,
                             in_campaign: bool | None = None) -> str:
    """Cherche des prospects (nom, poste, entreprise) dans tout le compte ou dans une liste.
    Rend id, nom, poste, entreprise, lien LinkedIn, relation, liste, tags, en campagne ou non.
    `in_campaign` filtre ceux qui sont (True) ou ne sont pas (False) dans une campagne en cours.
    `total` = nombre de resultats cote Waalaxy, `limit` borne la page (max 200).
    """
    c = _client()
    try:
        r = c.search_prospects(search=query, list_id=list_id, size=max(1, min(int(limit), 200)), in_campaign=in_campaign)
    except WaalaxyError as e:
        return _erreur(e)
    tags = _tags_by_id(c)
    return _out({"total": r.get("prospectsCount", 0), "prospects": [views.prospect_row(p, tags) for p in r.get("prospects", [])]})


@mcp.tool()
def waalaxy_prospect(linkedin: str, full: bool = False) -> str:
    """Fiche d'un prospect a partir de son URL LinkedIn, de son identifiant public (ce qui suit /in/)
    ou de son memberId (commence par ACoAA). Notes, tags, relation, liste, derniers evenements.
    `full=True` ajoute le prospect brut. 404 si le prospect n'est pas dans le compte.
    """
    ident = linkedin.strip().rstrip("/")
    if "/in/" in ident:
        ident = ident.split("/in/", 1)[1].split("/")[0].split("?")[0]
    c = _client()
    try:
        p = c.prospect(member_id=ident) if ident.startswith("ACoAA") else c.prospect(public_identifier=ident)
    except WaalaxyError as e:
        return _erreur(e)
    out = views.prospect_detail(p, _tags_by_id(c))
    if full:
        out["brut"] = p
    return _out(out)


@mcp.tool()
def waalaxy_tags() -> str:
    """Tags du compte (id, nom, couleur). L'id sert a waalaxy_create_tag pour eviter un doublon."""
    try:
        ts = _client().tags()
    except WaalaxyError as e:
        return _erreur(e)
    return _out({"tags": [views.tag_row(t) for t in ts]})


@mcp.tool()
def waalaxy_prospect_list(list_id: str) -> str:
    """Fiche d'une liste de prospects : nom, taille, dates, nombre de prospects actuellement en campagne."""
    c = _client()
    try:
        l = c.prospect_list(list_id)
        en_camp = c.count_prospects(list_id, in_campaign=True)
    except WaalaxyError as e:
        return _erreur(e)
    return _out(views.prospect_list_detail(l, en_camp))


# --- Ecriture (gate confirm) ----------------------------------------------------------

def _cycle(action: str, campaign_id: str, confirm: bool, avertissement: str | None = None) -> str:
    c = _client()
    try:
        camp = c.campaign(campaign_id)
    except WaalaxyError as e:
        return _erreur(e)
    cible = {"id": camp.get("_id"), "nom": camp.get("name"), "etat": camp.get("state"),
             "prospects_actifs": camp.get("activeTravelersCount", 0)}
    if not confirm:
        ap = {"apercu": True, "action": action, "campagne": cible}
        if avertissement:
            ap["avertissement"] = avertissement
        return _out(ap)
    try:
        r = getattr(c, action)(campaign_id, dry_run=False)
    except WaalaxyError as e:
        return _erreur(e)
    out = _resultat_ecriture(r, action)
    out["campagne"] = cible
    return _out(out)


@mcp.tool()
def waalaxy_pause(campaign_id: str, confirm: bool = False) -> str:
    """Met une campagne en pause (les actions LinkedIn en attente s'arretent). Reversible avec
    waalaxy_play. Sans `confirm=True`, rend un apercu nomme et ne fait rien.
    """
    return _cycle("pause", campaign_id, confirm)


@mcp.tool()
def waalaxy_play(campaign_id: str, confirm: bool = False) -> str:
    """Reprend une campagne en pause. Ne lance PAS un brouillon (pour ca : waalaxy_launch_draft).
    Sans `confirm=True`, rend un apercu nomme et ne fait rien.
    """
    return _cycle("play", campaign_id, confirm)


@mcp.tool()
def waalaxy_stop(campaign_id: str, confirm: bool = False) -> str:
    """ARCHIVE une campagne : irreversible dans Waalaxy (etat stopped, plus de reprise possible).
    Sans `confirm=True`, rend un apercu nomme et ne fait rien.
    """
    return _cycle("stop", campaign_id, confirm,
                  avertissement="Archivage irreversible : la campagne ne pourra plus etre reprise.")


@mcp.tool()
def waalaxy_launch_draft(draft_id: str, prospect_list_id: str, name: str | None = None,
                         confirm: bool = False) -> str:
    """Lance une campagne depuis un brouillon : la sequence du brouillon + les prospects ELIGIBLES
    de la liste (ceux qui ne sont pas deja dans une campagne en cours). La campagne est creee
    directement en `running` : les actions LinkedIn partent des la file d'attente.

    Sans `confirm=True`, rend un apercu : nom, liste, nombre de prospects qui partiront, sequence.
    Le brouillon n'est pas supprime par le lancement. `name` remplace le nom du brouillon
    (un nom deja pris est refuse par Waalaxy).
    """
    c = _client()
    try:
        d = c.draft(draft_id)
        liste = next((l for l in c.prospect_lists() if l.get("_id") == prospect_list_id), None)
        if liste is None:
            return _out({"erreur": f"liste de prospects inconnue : {prospect_list_id}",
                         "aide": "waalaxy_prospect_lists donne les ids"})
        ids, total = c.eligible_prospects(prospect_list_id)
    except WaalaxyError as e:
        return _erreur(e)
    nom = name or d.get("name")
    apercu = {
        "apercu": not confirm,
        "action": "launch_draft",
        "nom_campagne": nom,
        "brouillon": {"id": d.get("_id"), "nom": d.get("name")},
        "liste": {"id": prospect_list_id, "nom": liste.get("name"), "total": liste.get("totalProspects", 0)},
        "prospects_qui_partiront": len(ids),
        "prospects_deja_en_campagne": max(0, int(liste.get("totalProspects", 0)) - len(ids)),
        "sequence": views.sequence_steps(d.get("sequence")),
    }
    if not ids:
        apercu["avertissement"] = "Aucun prospect eligible : la campagne serait creee vide."
    if not confirm:
        return _out(apercu)
    try:
        r = c.launch_from_draft(
            draft_id, name=name, dry_run=False,
            prospects=[{"listId": prospect_list_id, "prospectIds": ids}],
        )
    except WaalaxyError as e:
        return _erreur(e)
    out = _resultat_ecriture(r, "launch_draft")
    if out["execute"]:
        camp = (r or {}).get("campaign") or {}
        out["campagne"] = {"id": camp.get("_id"), "nom": camp.get("name"), "etat": camp.get("state")}
        out["note"] = "Le brouillon existe toujours ; le supprimer dans Waalaxy si besoin."
        out.pop("resultat", None)
    out["apercu_lance"] = {k: v for k, v in apercu.items() if k not in ("apercu", "sequence")}
    return _out(out)


@mcp.tool()
def waalaxy_create_list(name: str, confirm: bool = False) -> str:
    """Cree une liste de prospects vide. Refuse un nom deja pris (verifie avant d'ecrire).
    Sans `confirm=True`, rend un apercu et ne cree rien.
    """
    c = _client()
    nom = name.strip()
    try:
        doublon = next((l for l in c.prospect_lists() if (l.get("name") or "").strip().lower() == nom.lower()), None)
    except WaalaxyError as e:
        return _erreur(e)
    if doublon:
        return _out({"erreur": f"une liste « {doublon['name']} » existe deja ({doublon['_id']})"})
    if not confirm:
        return _out({"apercu": True, "action": "create_list", "nom": nom})
    try:
        r = c.create_list(nom, dry_run=False)
    except WaalaxyError as e:
        return _erreur(e)
    out = _resultat_ecriture(r, "create_list")
    if out["execute"]:
        out["liste"] = views.prospect_list_row(r if isinstance(r, dict) and r.get("_id") else (r or {}).get("prospectList", r))
        out.pop("resultat", None)
    return _out(out)


@mcp.tool()
def waalaxy_rename_list(list_id: str, name: str, confirm: bool = False) -> str:
    """Renomme une liste de prospects. Sans `confirm=True`, rend un apercu (ancien nom -> nouveau)."""
    c = _client()
    nom = name.strip()
    try:
        l = c.prospect_list(list_id)
    except WaalaxyError as e:
        return _erreur(e)
    apercu = {"apercu": not confirm, "action": "rename_list", "liste_id": list_id, "ancien_nom": l.get("name"), "nouveau_nom": nom,
              "prospects": l.get("totalProspects", 0)}
    if not confirm:
        return _out(apercu)
    try:
        r = c.rename_list(list_id, nom, dry_run=False)
    except WaalaxyError as e:
        return _erreur(e)
    out = _resultat_ecriture(r, "rename_list"); out.update({k: v for k, v in apercu.items() if k != "apercu"}); out.pop("resultat", None)
    return _out(out)


@mcp.tool()
def waalaxy_create_tag(name: str, color: str = "blue", confirm: bool = False) -> str:
    """Cree un tag (nom, couleur : blue, purple, yellow, orange, green, red, pink, grey).
    Refuse un nom deja pris. Sans `confirm=True`, rend un apercu.
    """
    c = _client()
    nom = name.strip()
    try:
        exist = next((t for t in c.tags() if (t.get("name") or "").strip().lower() == nom.lower()), None)
    except WaalaxyError as e:
        return _erreur(e)
    if exist:
        return _out({"erreur": f"le tag « {exist['name']} » existe deja ({exist['_id']})"})
    if not confirm:
        return _out({"apercu": True, "action": "create_tag", "nom": nom, "couleur": color})
    try:
        r = c.create_tag(nom, color, dry_run=False)
    except WaalaxyError as e:
        return _erreur(e)
    out = _resultat_ecriture(r, "create_tag")
    if out["execute"]:
        t = (r or {}).get("tag", r) if isinstance(r, dict) else {}
        out["tag"] = views.tag_row(t if isinstance(t, dict) else {}); out.pop("resultat", None)
    return _out(out)


@mcp.tool()
def waalaxy_prospect_note(prospect_id: str, text: str, confirm: bool = False) -> str:
    """Ajoute ou met a jour la note d'un prospect (son id Waalaxy, pas LinkedIn : voir waalaxy_search_prospects).
    Sans `confirm=True`, rend un apercu avec le nom du prospect et la note actuelle.
    """
    c = _client()
    try:
        r = c.search_prospects(ids=[prospect_id], size=1)
    except WaalaxyError as e:
        return _erreur(e)
    ps = r.get("prospects", [])
    if not ps or ps[0].get("_id") != prospect_id:
        return _out({"erreur": f"prospect introuvable : {prospect_id}"})
    p = ps[0]
    apercu = {"apercu": not confirm, "action": "prospect_note", "prospect": views.prospect_row(p)["nom"], "prospect_id": prospect_id,
              "note_actuelle": [n.get("text") if isinstance(n, dict) else n for n in (p.get("notes") or [])], "nouvelle_note": text}
    if not confirm:
        return _out(apercu)
    try:
        r = c.add_note(prospect_id, text, dry_run=False)
    except WaalaxyError as e:
        return _erreur(e)
    out = _resultat_ecriture(r, "prospect_note"); out.update({k: v for k, v in apercu.items() if k != "apercu"}); out.pop("resultat", None)
    return _out(out)


@mcp.tool()
def waalaxy_add_to_campaign(campaign_id: str, list_id: str, prospect_ids: list[str], confirm: bool = False) -> str:
    """Ajoute des prospects PRECIS (ids Waalaxy) d'une liste a une campagne en cours ou en pause.
    Refuse une liste d'ids vide. Les prospects deja dans une campagne en cours sont ecartes de l'apercu
    et de l'envoi. Sans `confirm=True`, rend un apercu : campagne, liste, prospects retenus et ecartes.
    """
    if not prospect_ids:
        return _out({"erreur": "prospect_ids vide : aucun enrolement implicite"})
    c = _client()
    try:
        camp = c.campaign(campaign_id)
        l = c.prospect_list(list_id)
        r = c.search_prospects(ids=list(prospect_ids), list_id=list_id, size=len(prospect_ids))
    except WaalaxyError as e:
        return _erreur(e)
    trouves = {p["_id"]: p for p in r.get("prospects", [])}
    inconnus = [i for i in prospect_ids if i not in trouves]
    deja = [i for i, p in trouves.items() if p.get("isActiveInCampaign")]
    retenus = [i for i in prospect_ids if i in trouves and i not in deja]
    apercu = {
        "apercu": not confirm, "action": "add_to_campaign",
        "campagne": {"id": camp.get("_id"), "nom": camp.get("name"), "etat": camp.get("state")},
        "liste": {"id": list_id, "nom": l.get("name")},
        "retenus": [views.prospect_row(trouves[i])["nom"] for i in retenus], "nb_retenus": len(retenus),
        "ecartes_deja_en_campagne": len(deja), "inconnus_dans_cette_liste": inconnus,
    }
    if camp.get("state") not in ("running", "paused"):
        apercu["avertissement"] = f"la campagne est en etat {camp.get('state')} : l'ajout sera probablement refuse"
    if not retenus:
        apercu["avertissement"] = "aucun prospect retenu : rien ne sera envoye"
        return _out(apercu)
    if not confirm:
        return _out(apercu)
    try:
        r = c.add_to_campaign(campaign_id, list_id, retenus, dry_run=False)
    except WaalaxyError as e:
        return _erreur(e)
    out = _resultat_ecriture(r, "add_to_campaign"); out.update({k: v for k, v in apercu.items() if k != "apercu"})
    return _out(out)


def main() -> None:
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "status":
        try:
            r = _client().count_per_status()
        except WaalaxyError as e:
            print(f"Waalaxy : {e}", file=sys.stderr)
            raise SystemExit(1)
        counts = {x["status"]: x["value"] for x in r.get("count", [])}
        print(f"Waalaxy : jeton valide. Campagnes : {counts}")
        return
    mcp.run()


if __name__ == "__main__":
    main()
