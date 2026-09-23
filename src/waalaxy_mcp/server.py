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
