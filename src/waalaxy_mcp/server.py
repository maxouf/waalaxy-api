#!/usr/bin/env python3
"""Serveur MCP Waalaxy (stdio, ou HTTP local avec `--http [port]`).

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
import sys
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


@mcp.tool()
def waalaxy_inbox(search: str | None = None, unread_only: bool = False, last_from: str | None = None,
                  archived: bool = False, limit: int = 20, start: int = 0, full: bool = False) -> str:
    """Inbox LinkedIn vue par Waalaxy (service pictochat), en lecture seule, du plus recent au plus ancien.
    Rend par conversation : contact (nom, titre, lien LinkedIn, memberId), DERNIER message (date, de qui,
    texte tronque a 300 caracteres sauf `full=True`), lu/non lu, tags, relances programmees.
    Le fil complet n'est pas disponible : pictochat ne garde que le dernier message, le reste est lu
    sur LinkedIn par l'extension Chrome.
    Filtres : `search` (nom du contact), `unread_only`, `last_from` ("contact" = il attend ma reponse,
    "moi" = j'attends la sienne), `archived=True` (seulement les archivees). `total` = total filtre.
    `fraicheur` donne la date du message le plus recent du cache : pictochat n'est alimente que quand
    l'inbox Waalaxy est ouverte, donc une date ancienne signale un cache perime, pas une inbox calme.
    """
    if last_from not in (None, "contact", "moi"):
        return _out({"erreur": "last_from doit valoir 'contact', 'moi' ou rester vide"})
    c = _client()
    try:
        r = c.conversations(start=max(0, int(start)), count=max(1, min(int(limit), 100)), search=search,
                            unread=True if unread_only else None,
                            from_contact=None if last_from is None else last_from == "contact",
                            archived=archived or None)
        head = c.conversations(start=0, count=1)
    except WaalaxyError as e:
        return _erreur(e)
    latest = ((head.get("conversations") or [{}])[0].get("lastMessage") or {}).get("sentAt")
    fraicheur = {"message_le_plus_recent": latest}
    if latest:
        try:
            from datetime import datetime, timezone
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(latest.replace("Z", "+00:00"))).days
            fraicheur["age_jours"] = age
            if age > 7:
                fraicheur["avertissement"] = ("cache pictochat probablement perime : ouvrir l'inbox sur "
                                              "app.waalaxy.com pour le resynchroniser")
        except ValueError:
            pass
    return _out({"total": r.get("totalCount"), "fraicheur": fraicheur,
                 "conversations": [views.conversation_row(x, full=full) for x in r.get("conversations", [])]})


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
    out["note"] = "l'etat se propage en quelques secondes (asynchrone) : relire avec waalaxy_campaign"
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


ETATS_PROSPECTION = {"interested": "Intéressé", "later_interested": "À relancer"}


def _cibles(c, prospect_ids: list[str], list_id: str):
    """Resout des ids dans une liste : (prospects trouves par id, ids inconnus dans cette liste, liste)."""
    l = c.prospect_list(list_id)
    r = c.search_prospects(ids=list(prospect_ids), list_id=list_id, size=max(1, len(prospect_ids)))
    trouves = {p["_id"]: p for p in r.get("prospects", []) if p.get("_id") in set(prospect_ids)}
    inconnus = [i for i in prospect_ids if i not in trouves]
    return trouves, inconnus, l


@mcp.tool()
def waalaxy_move_prospects(prospect_ids: list[str], from_list_id: str, to_list_id: str, confirm: bool = False) -> str:
    """Deplace des prospects PRECIS (ids Waalaxy) d'une liste vers une autre. Refuse une liste d'ids vide
    et ignore les ids qui ne sont pas dans la liste d'origine. Sans `confirm=True`, rend un apercu
    (noms, liste d'origine, liste cible).
    """
    if not prospect_ids:
        return _out({"erreur": "prospect_ids vide : aucun deplacement implicite"})
    c = _client()
    try:
        trouves, inconnus, src = _cibles(c, prospect_ids, from_list_id)
        dst = c.prospect_list(to_list_id)
    except WaalaxyError as e:
        return _erreur(e)
    retenus = [i for i in prospect_ids if i in trouves]
    apercu = {"apercu": not confirm, "action": "move_prospects",
              "de": {"id": from_list_id, "nom": src.get("name")}, "vers": {"id": to_list_id, "nom": dst.get("name")},
              "retenus": [views.prospect_row(trouves[i])["nom"] for i in retenus], "nb_retenus": len(retenus),
              "inconnus_dans_la_liste_d_origine": inconnus}
    if not retenus:
        apercu["avertissement"] = "aucun prospect retenu : rien ne sera envoye"
        return _out(apercu)
    if not confirm:
        return _out(apercu)
    try:
        r = c.move_prospects(retenus, from_list_id, to_list_id, dry_run=False)
    except WaalaxyError as e:
        return _erreur(e)
    out = _resultat_ecriture(r, "move_prospects"); out.update({k: v for k, v in apercu.items() if k != "apercu"})
    return _out(out)


@mcp.tool()
def waalaxy_tag_prospects(prospect_ids: list[str], list_id: str, tag_id: str, remove: bool = False, confirm: bool = False) -> str:
    """Pose (ou retire avec `remove=True`) un tag sur des prospects PRECIS (ids Waalaxy) d'une liste.
    `tag_id` vient de waalaxy_tags. Refuse une liste d'ids vide. Sans `confirm=True`, rend un apercu.
    """
    if not prospect_ids:
        return _out({"erreur": "prospect_ids vide : aucun tag implicite"})
    c = _client()
    try:
        tag = next((t for t in c.tags() if t.get("_id") == tag_id), None)
        if tag is None:
            return _out({"erreur": f"tag inconnu : {tag_id}", "aide": "waalaxy_tags donne les ids"})
        trouves, inconnus, l = _cibles(c, prospect_ids, list_id)
    except WaalaxyError as e:
        return _erreur(e)
    retenus = [i for i in prospect_ids if i in trouves]
    apercu = {"apercu": not confirm, "action": "untag_prospects" if remove else "tag_prospects",
              "tag": {"id": tag_id, "nom": tag.get("name")}, "liste": {"id": list_id, "nom": l.get("name")},
              "retenus": [views.prospect_row(trouves[i])["nom"] for i in retenus], "nb_retenus": len(retenus),
              "inconnus_dans_cette_liste": inconnus}
    if not retenus:
        apercu["avertissement"] = "aucun prospect retenu : rien ne sera envoye"
        return _out(apercu)
    if not confirm:
        return _out(apercu)
    try:
        r = c.tag_prospects(retenus, list_id, tag_id, remove=remove, dry_run=False)
    except WaalaxyError as e:
        return _erreur(e)
    out = _resultat_ecriture(r, apercu["action"]); out.update({k: v for k, v in apercu.items() if k != "apercu"})
    return _out(out)


@mcp.tool()
def waalaxy_set_prospect_state(prospect_ids: list[str], list_id: str, state: str, confirm: bool = False) -> str:
    """Change l'etat de prospection de prospects PRECIS (ids) d'une liste. Etats connus :
    `interested` (Interesse), `later_interested` (A relancer). Une autre valeur est soumise telle quelle
    et refusee par Waalaxy si inconnue. Sans `confirm=True`, rend un apercu.
    """
    if not prospect_ids:
        return _out({"erreur": "prospect_ids vide"})
    c = _client()
    try:
        trouves, inconnus, l = _cibles(c, prospect_ids, list_id)
    except WaalaxyError as e:
        return _erreur(e)
    retenus = [i for i in prospect_ids if i in trouves]
    apercu = {"apercu": not confirm, "action": "set_prospect_state", "etat": state, "etat_libelle": ETATS_PROSPECTION.get(state),
              "liste": {"id": list_id, "nom": l.get("name")},
              "retenus": [views.prospect_row(trouves[i])["nom"] for i in retenus], "nb_retenus": len(retenus),
              "inconnus_dans_cette_liste": inconnus}
    if state not in ETATS_PROSPECTION:
        apercu["avertissement"] = f"etat non observe dans l'app ({list(ETATS_PROSPECTION)}) : Waalaxy peut le refuser"
    if not retenus:
        apercu["avertissement"] = "aucun prospect retenu : rien ne sera envoye"
        return _out(apercu)
    if not confirm:
        return _out(apercu)
    try:
        r = c.set_prospection_state(retenus, list_id, state, dry_run=False)
    except WaalaxyError as e:
        return _erreur(e)
    out = _resultat_ecriture(r, "set_prospect_state"); out.update({k: v for k, v in apercu.items() if k != "apercu"})
    return _out(out)


# --- Modification d'une campagne (gate confirm) ---------------------------------------
# Corps lus dans le code de l'app (useCampaignManager.updateCampaign) le 06/11/2026.

_ETAPES_A_MODELE = ("messageLinkedin", "connectLinkedin", "messageRequestLinkedin")


def _campagne_et_monde(c, campaign_id: str):
    camp = c.campaign(campaign_id)
    world = camp.get("world") or {}
    if not world.get("_id"):
        raise WaalaxyError(f"la campagne {campaign_id} n'a pas de sequence (world) : brouillon ?", 400, "")
    return camp, world


def _cible_campagne(camp: dict) -> dict:
    return {"id": camp.get("_id"), "nom": camp.get("name"), "etat": camp.get("state")}


@mcp.tool()
def waalaxy_rename_campaign(campaign_id: str, name: str, confirm: bool = False) -> str:
    """Renomme une campagne (quel que soit son etat). Refuse un nom vide ou deja pris.
    Sans `confirm=True`, rend un apercu (ancien nom -> nouveau) et ne fait rien.
    """
    nom = (name or "").strip()
    if not nom:
        return _out({"erreur": "nom vide"})
    c = _client()
    try:
        camp = c.campaign(campaign_id)
        pris = any(x.get("name", "").strip().lower() == nom.lower() and x.get("_id") != campaign_id
                   for x in c.all_campaigns())
    except WaalaxyError as e:
        return _erreur(e)
    apercu = {"apercu": not confirm, "action": "rename_campaign", "campagne": _cible_campagne(camp),
              "ancien_nom": camp.get("name"), "nouveau_nom": nom}
    if pris:
        apercu["erreur"] = "ce nom de campagne est deja pris"
        return _out(apercu)
    if not confirm:
        return _out(apercu)
    try:
        r = c.rename_campaign(campaign_id, nom, dry_run=False)
    except WaalaxyError as e:
        return _erreur(e)
    out = _resultat_ecriture(r, "rename_campaign"); out.update({k: v for k, v in apercu.items() if k != "apercu"})
    return _out(out)


def _sleep_nodes(node, acc: list) -> list:
    """Tous les noeuds `sleep` d'un arbre de condition, dans l'ordre."""
    if not isinstance(node, dict):
        return acc
    if node.get("isAtomic"):
        if (node.get("entity") or {}).get("type") == "sleep":
            acc.append(node["entity"])
        return acc
    _sleep_nodes(node.get("leftOperand"), acc)
    _sleep_nodes(node.get("rightOperand"), acc)
    return acc


@mcp.tool()
def waalaxy_set_delay(campaign_id: str, path_id: str, days: int, confirm: bool = False) -> str:
    """Change le delai d'attente (en JOURS) d'un branchement d'une campagne existante. `path_id` est
    l'identifiant entre parentheses des lignes `(id) from -> to` de waalaxy_campaign. Seuls les
    branchements qui portent un delai (« delai de N jours ecoule ») sont modifiables ; le reste de la
    condition est conserve tel quel. Effet sur les prospects deja en attente non verifie.
    Sans `confirm=True`, rend un apercu (condition avant / apres) et ne fait rien.
    """
    import copy
    if not isinstance(days, int) or days < 0 or days > 365:
        return _out({"erreur": "days doit etre un entier entre 0 et 365"})
    c = _client()
    try:
        camp, world = _campagne_et_monde(c, campaign_id)
    except WaalaxyError as e:
        return _erreur(e)
    path = next((p for p in world.get("paths") or [] if str(p.get("id")) == str(path_id)), None)
    if not path:
        return _out({"erreur": f"branchement {path_id} introuvable", "branchements": [str(p.get("id")) for p in world.get("paths") or []]})
    cond = copy.deepcopy(path.get("condition") or {})
    nodes = _sleep_nodes(cond, [])
    if not nodes:
        return _out({"erreur": f"le branchement {path_id} ne porte pas de delai", "condition": views._condition(cond)})
    avant = [int((n.get("params") or {}).get("time") or 0) for n in nodes]
    for n in nodes:
        n["params"] = {**(n.get("params") or {}), "time": days}
    apercu = {"apercu": not confirm, "action": "set_delay", "campagne": _cible_campagne(camp),
              "branchement": f"{path.get('from')} -> {path.get('to')}", "path_id": str(path_id),
              "delai_avant_jours": avant[0] if len(avant) == 1 else avant, "delai_apres_jours": days,
              "condition_avant": views._condition(path.get("condition")), "condition_apres": views._condition(cond)}
    if not confirm:
        return _out(apercu)
    try:
        r = c.update_path_condition(campaign_id, world["_id"], str(path_id), cond, dry_run=False)
    except WaalaxyError as e:
        return _erreur(e)
    out = _resultat_ecriture(r, "set_delay"); out.update({k: v for k, v in apercu.items() if k != "apercu"})
    return _out(out)


@mcp.tool()
def waalaxy_set_step_template(campaign_id: str, waypoint_id: str, content_id: str, confirm: bool = False) -> str:
    """Remplace le modele de message d'une etape (message, invitation avec note, demande de message)
    d'une campagne existante par un autre modele deja enregistre (`content_id`, voir waalaxy_message).
    `waypoint_id` est l'identifiant entre crochets des etapes de waalaxy_campaign.
    Sans `confirm=True`, rend un apercu (modele avant / apres, texte du nouveau) et ne fait rien.
    """
    c = _client()
    try:
        camp, world = _campagne_et_monde(c, campaign_id)
    except WaalaxyError as e:
        return _erreur(e)
    wp = next((w for w in world.get("waypoints") or [] if str(w.get("id")) == str(waypoint_id)), None)
    if not wp:
        return _out({"erreur": f"etape {waypoint_id} introuvable", "etapes": views.sequence_steps(world)})
    if wp.get("type") not in _ETAPES_A_MODELE:
        return _out({"erreur": f"l'etape {waypoint_id} est de type {wp.get('type')} : pas de modele a changer",
                     "types_acceptes": list(_ETAPES_A_MODELE)})
    try:
        nouveau = c.content(content_id)
    except WaalaxyError as e:
        return _erreur(e)
    actuel = (wp.get("params") or {}).get("contentReference")
    apercu = {"apercu": not confirm, "action": "set_step_template", "campagne": _cible_campagne(camp),
              "etape": f"[{wp.get('id')}] {views.ETAPES.get(wp.get('type'), wp.get('type'))}",
              "modele_avant": actuel, "modele_apres": views.content_detail(nouveau)}
    if actuel == content_id:
        apercu["avertissement"] = "l'etape utilise deja ce modele"
    if not confirm:
        return _out(apercu)
    try:
        r = c.update_waypoint_params(campaign_id, world["_id"], str(waypoint_id), {"contentReference": content_id}, dry_run=False)
    except WaalaxyError as e:
        return _erreur(e)
    out = _resultat_ecriture(r, "set_step_template"); out.update({k: v for k, v in apercu.items() if k != "apercu"})
    return _out(out)


@mcp.tool()
def waalaxy_message(content_id: str, full: bool = False) -> str:
    """Un modele de message (texte, nom, canal) depuis son id (`contentReference` des etapes)."""
    try:
        m = _client().content(content_id)
    except WaalaxyError as e:
        return _erreur(e)
    out = views.content_detail(m)
    if full:
        out["brut"] = m
    return _out(out)


@mcp.tool()
def waalaxy_update_message(content_id: str, text: str, name: str | None = None, confirm: bool = False) -> str:
    """Modifie le texte (et au besoin le nom) d'un modele de message. ATTENTION : un modele est partage
    par toutes les campagnes qui le referencent ; la modification vaut pour toutes. Les variables
    ({{firstName}}…) sont a ecrire telles quelles. Sans `confirm=True`, rend un apercu (avant / apres).
    """
    import copy
    if not (text or "").strip():
        return _out({"erreur": "texte vide"})
    c = _client()
    try:
        m = c.content(content_id)
    except WaalaxyError as e:
        return _erreur(e)
    nouveau = copy.deepcopy(m)
    p = nouveau.setdefault("params", {})
    msgs = p.get("messages") or []
    if msgs:
        msgs[0]["value"] = text
    else:
        p["messages"] = [{"value": text, "dispatchOrder": 0}]
    if name and name.strip():
        nouveau["name"] = name.strip()
    apercu = {"apercu": not confirm, "action": "update_message",
              "avertissement": "modele partage : toutes les campagnes qui le referencent sont touchees",
              "avant": views.content_detail(m), "apres": views.content_detail(nouveau)}
    if not confirm:
        return _out(apercu)
    try:
        r = c.update_content(nouveau, dry_run=False)
    except WaalaxyError as e:
        return _erreur(e)
    out = _resultat_ecriture(r, "update_message"); out.update({k: v for k, v in apercu.items() if k != "apercu"})
    return _out(out)


# --- Reglages du compte : plages horaires, quotas (gate confirm) ----------------------
# Routes zilean / shiva lues dans les sourcemaps le 06/11/2026, lectures verifiees en reel.

@mcp.tool()
def waalaxy_working_hours() -> str:
    """Plages horaires d'activite du compte : fuseau, jours actifs et heures, si Waalaxy travaille en ce
    moment. Hors de ces plages aucune action LinkedIn ne part.
    """
    c = _client()
    try:
        ws = c.working_settings()
        now = c.can_work_now()
    except WaalaxyError as e:
        return _erreur(e)
    return _out(views.working_hours(ws, now))


def _parse_hhmm(v: str) -> dict:
    h, m = str(v).strip().split(":")
    h, m = int(h), int(m)
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(v)
    return {"hour": h, "minute": m}


@mcp.tool()
def waalaxy_set_working_hours(days: list[dict], timezone: str | None = None, confirm: bool = False) -> str:
    """Remplace les plages horaires du compte. `days` = la liste COMPLETE des jours actifs, par ex.
    [{"day": 1, "start": "08:00", "end": "19:00"}, …] avec day 0 = dimanche … 6 = samedi ; un jour
    absent devient inactif. `timezone` = fuseau IANA (Europe/Paris), sinon l'actuel est conserve.
    Sans `confirm=True`, rend un apercu (avant / apres) et ne fait rien.
    """
    from zoneinfo import available_timezones
    if not days:
        return _out({"erreur": "days vide : il faut au moins un jour actif"})
    working_days, vus = [], set()
    for d in days:
        try:
            day = int(d["day"])
            if not 0 <= day <= 6 or day in vus:
                raise ValueError
            start, end = _parse_hhmm(d["start"]), _parse_hhmm(d["end"])
        except (KeyError, ValueError, TypeError, AttributeError):
            return _out({"erreur": f"jour invalide : {d} (attendu {{day: 0-6 unique, start: 'HH:MM', end: 'HH:MM'}})"})
        if (start["hour"], start["minute"]) > (end["hour"], end["minute"]):
            return _out({"erreur": f"jour {day} : start apres end"})
        vus.add(day)
        working_days.append({"day": day, "ranges": [{"start": start, "end": end}]})
    working_days.sort(key=lambda x: x["day"])
    c = _client()
    try:
        actuel = c.working_settings()
    except WaalaxyError as e:
        return _erreur(e)
    tz = (timezone or "").strip() or actuel.get("timezone")
    if tz not in available_timezones():
        return _out({"erreur": f"fuseau inconnu : {tz} (attendu un nom IANA, ex. Europe/Paris)"})
    apres = {"timezone": tz, "workingDays": working_days}
    apercu = {"apercu": not confirm, "action": "set_working_hours",
              "avant": views.working_hours(actuel), "apres": views.working_hours(apres)}
    if not confirm:
        return _out(apercu)
    try:
        r = c.set_working_settings(tz, working_days, dry_run=False)
    except WaalaxyError as e:
        return _erreur(e)
    out = _resultat_ecriture(r, "set_working_hours"); out.update({k: v for k, v in apercu.items() if k != "apercu"})
    return _out(out)


@mcp.tool()
def waalaxy_quotas() -> str:
    """Quotas journaliers LinkedIn du compte, par action (invitations, messages, visites, suivis, demandes
    de message) : utilises et restants aujourd'hui, plafond actif, plafond personnalise, plafond du
    plan, actions en file, heure de remise a zero.
    """
    c = _client()
    try:
        q = c.quotas()
        consts = c.quota_constants()
        nxt = c.quota_next_update()
    except WaalaxyError as e:
        return _erreur(e)
    return _out(views.quotas(q, consts, nxt))


@mcp.tool()
def waalaxy_set_quota(type: str, max_value: int, confirm: bool = False) -> str:
    """Fixe le plafond journalier d'une action LinkedIn : `type` parmi connectLinkedin, messageLinkedin,
    visitLinkedin, followLinkedin, messageRequestLinkedin ; `max_value` entre 0 et le plafond du plan
    (voir waalaxy_quotas). Sans `confirm=True`, rend un apercu (plafond avant / apres) et ne fait rien.
    """
    if type not in views.QUOTAS:
        return _out({"erreur": f"type inconnu : {type}", "types": list(views.QUOTAS)})
    c = _client()
    try:
        consts = c.quota_constants()
        q = c.quotas()
    except WaalaxyError as e:
        return _erreur(e)
    plafond = consts.get(type)
    if not isinstance(max_value, int) or max_value < 0 or (plafond is not None and max_value > plafond):
        return _out({"erreur": f"max_value doit etre un entier entre 0 et {plafond} pour {type}"})
    actuel = next((x for x in q.get("quotas") or [] if x.get("type") == type), {})
    apercu = {"apercu": not confirm, "action": "set_quota", "type": type, "libelle": views.QUOTAS[type],
              "plafond_avant": actuel.get("customMaxValue", actuel.get("maxQuota")), "plafond_apres": max_value,
              "plafond_du_plan": plafond}
    if not confirm:
        return _out(apercu)
    try:
        r = c.set_quota(type, max_value, dry_run=False)
    except WaalaxyError as e:
        return _erreur(e)
    out = _resultat_ecriture(r, "set_quota"); out.update({k: v for k, v in apercu.items() if k != "apercu"})
    return _out(out)


@mcp.tool()
def waalaxy_reset_quotas(confirm: bool = False) -> str:
    """Remet tous les plafonds LinkedIn personnalises aux valeurs du plan. Sans `confirm=True`, rend un
    apercu (plafonds actuels et valeurs du plan) et ne fait rien.
    """
    c = _client()
    try:
        consts = c.quota_constants()
        q = c.quotas()
    except WaalaxyError as e:
        return _erreur(e)
    apercu = {"apercu": not confirm, "action": "reset_quotas",
              "avant": {x["type"]: x.get("customMaxValue", x.get("maxQuota")) for x in q.get("quotas") or []},
              "apres": consts}
    if not confirm:
        return _out(apercu)
    try:
        r = c.reset_quotas(dry_run=False)
    except WaalaxyError as e:
        return _erreur(e)
    out = _resultat_ecriture(r, "reset_quotas"); out.update({k: v for k, v in apercu.items() if k != "apercu"})
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
    if len(sys.argv) > 1 and sys.argv[1] == "--http":
        port = int(sys.argv[2]) if len(sys.argv) > 2 else 8000
        _run_http(port)
        return
    mcp.run()


def _run_http(port: int) -> None:
    """Streamable HTTP sur 127.0.0.1 uniquement : aucune authentification, le
    serveur porte le jeton Waalaxy. Pour ChatGPT, passer par le Secure MCP Tunnel."""
    print(f"Waalaxy MCP : http://127.0.0.1:{port}/mcp", file=sys.stderr)
    if hasattr(getattr(mcp, "settings", None), "host"):  # mcp 1.x : host/port dans les reglages
        mcp.settings.host, mcp.settings.port = "127.0.0.1", port
        mcp.run(transport="streamable-http")
    else:  # mcp >= 2.0
        mcp.run(transport="streamable-http", host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
