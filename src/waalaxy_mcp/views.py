"""Vues resumees pour le modele : jamais le brut de l'API par defaut.

Chaque fonction prend un dict tel que l'API le rend et produit ce qu'un agent
a besoin de lire. Le brut reste accessible derriere `full=True` cote outil.
"""
from __future__ import annotations

from waalaxy_api.client import campaign_row  # noqa: F401  (re-export : une ligne de stats)

ETAPES = {
    "entry": "Entrée",
    "connectLinkedin": "Invitation LinkedIn",
    "messageLinkedin": "Message LinkedIn",
    "visitLinkedin": "Visite de profil",
    "followLinkedin": "Suivre le profil",
    "emailFinder": "Recherche d'email",
    "sendEmail": "Email",
    "sleep": "Attente",
    "goal": "Objectif atteint",
    "failed": "Échec",
    "end": "Fin",
}

CONDITIONS = {
    "isConnected": "relation acceptée",
    "isNotConnected": "pas en relation",
    "isUnknownStatus": "statut inconnu",
    "isPending": "invitation en attente",
    "hasReplied": "a répondu",
    "hasNotReplied": "sans réponse",
    "wasReplied": "a répondu",
    "wasNotReplied": "sans réponse",
    "hasEmail": "email trouvé",
    "hasNoEmail": "pas d'email",
    "isSeen": "a vu",
    "isNotSeen": "n'a pas vu",
}


def _condition(node) -> str:
    """Arbre de condition {isAtomic, entity} | {leftOperand, comparator, rightOperand} -> texte."""
    if not node:
        return ""
    if node.get("isAtomic"):
        ent = node.get("entity") or {}
        t = ent.get("type", "?")
        if t == "sleep":
            delai = (ent.get("params") or {}).get("time")
            return f"délai de {delai} jours écoulé" if delai is not None else "délai écoulé"
        return CONDITIONS.get(t, t)
    left, right = _condition(node.get("leftOperand")), _condition(node.get("rightOperand"))
    op = " ou " if (node.get("comparator") or "").upper() == "OR" else " et "
    return op.join(x for x in (left, right) if x)


def _params(w: dict) -> str:
    p = w.get("params") or {}
    bits = []
    if p.get("contentReference"):
        bits.append(f"modèle {p['contentReference']}")
    if p.get("note"):
        bits.append("avec note")
    for k in ("delay", "duration", "days", "hours"):
        if p.get(k) is not None:
            bits.append(f"{k}={p[k]}")
    return ", ".join(bits)


def sequence_steps(world: dict | None) -> list[str]:
    """La sequence (world ou draft.sequence) en lignes lisibles : etapes puis branchements."""
    if not world:
        return []
    by_id = {str(w.get("id")): w for w in world.get("waypoints", [])}
    lines = []
    for w in world.get("waypoints", []):
        label = ETAPES.get(w.get("type"), w.get("type", "?"))
        extra = _params(w)
        lines.append(f"[{w.get('id')}] {label}" + (f" ({extra})" if extra else ""))
    for p in world.get("paths", []):
        cond = _condition(p.get("condition"))
        dst = ETAPES.get((by_id.get(str(p.get("to"))) or {}).get("type"), "?")
        lines.append(f"{p.get('from')} → {p.get('to')} {dst}" + (f" si {cond}" if cond else ""))
    return lines


def travelers(tc: dict | None) -> dict:
    """travelersCount reduit aux compteurs utiles, cles francaises stables."""
    tc = tc or {}
    return {
        "total": tc.get("total", 0),
        "en_cours": tc.get("traveling", 0),
        "termines": tc.get("finished", 0),
        "en_pause": tc.get("paused", 0),
        "erreur": tc.get("error", 0),
        "invitation_acceptee": tc.get("hasAcceptedInvitation", 0),
        "invitation_refusee_ou_sans_suite": tc.get("hasNotAcceptedInvitation", 0),
        "a_repondu": tc.get("hasReplied", 0),
        "sans_reponse": tc.get("hasNotReplied", 0),
        "interesse": tc.get("hasRepliedInterested", 0),
        "interesse_plus_tard": tc.get("hasRepliedLaterInterested", 0),
        "pas_interesse": tc.get("hasRepliedNotInterested", 0),
    }


def summary_counts(r: dict) -> dict:
    """POST /travelerssummary -> {status: count}, meme cles que travelersCount."""
    return {status: sum(int(e.get("travelerCount", len(e.get("travelers", [])))) for e in entries or [])
            for status, entries in (r.get("travelersSummaries") or {}).items()}


def campaign_detail(c: dict, travelers_count: dict | None = None) -> dict:
    """GET /campaigns/:id -> fiche : etat, dates, listes d'origine, compteurs, sequence.

    La reponse de GET /campaigns/:id n'a pas `travelersCount` (seulement `activeTravelersCount`) :
    les compteurs viennent de `travelers_count`, fourni par l'outil depuis travelerssummary.
    """
    return {
        "id": c.get("_id"),
        "nom": c.get("name"),
        "etat": c.get("state"),
        "priorite": c.get("priority"),
        "creee_le": (c.get("createdAt") or "")[:10],
        "modifiee_le": (c.get("updatedAt") or "")[:10],
        "origines": [
            {"type": o.get("origin"), "liste_id": o.get("prospectList"), "prospects": o.get("travelerCount", 0)}
            for o in c.get("origins", [])
        ],
        "prospects_actifs": c.get("activeTravelersCount", 0),
        "prospects": travelers(travelers_count if travelers_count is not None else c.get("travelersCount")),
        "sequence": sequence_steps(c.get("world")),
    }


def draft_row(d: dict) -> dict:
    return {"id": d.get("_id"), "nom": d.get("name"), "modifie_le": (d.get("updatedAt") or "")[:10]}


def draft_detail(d: dict) -> dict:
    """GET /campaigns/draft/:id -> nom, sources de prospects prevues, sequence."""
    sources = []
    for s in d.get("prospectSources", []) or []:
        pl = s.get("prospectList") or {}
        sources.append({
            "liste_id": pl.get("_id"),
            "liste": pl.get("name"),
            "total_liste": pl.get("totalProspects", 0),
            "prevus": s.get("count", 0),
        })
    return {
        "id": d.get("_id"),
        "nom": d.get("name"),
        "modifie_le": (d.get("updatedAt") or "")[:10],
        "sources_prevues": sources,
        "sequence": sequence_steps(d.get("sequence")),
        "note": "Un brouillon garde la sequence mais pas l'enrolement : au lancement, on choisit la liste.",
    }


def travelers_summary(r: dict, full: bool = False) -> dict:
    """POST /travelerssummary -> nombre de prospects par etat ; les ids seulement en `full`."""
    out = {}
    counts = summary_counts(r)
    for status, entries in (r.get("travelersSummaries") or {}).items():
        n = counts[status]
        if n or full:
            out[status] = {"count": n, "ids": [t for e in entries for t in e.get("travelers", [])]} if full else n
    return out


def global_stats(r: dict, start: str, end: str) -> dict:
    """GET /hawking/stats/allStats -> taux d'acceptation et de reponse sur la periode."""
    acc = r.get("acceptance") or {}
    msg = r.get("responsesMessage") or {}
    mail = r.get("responsesEmail") or {}
    finder = r.get("emailFinder") or {}
    return {
        "periode": {"debut": start[:10], "fin": end[:10]},
        "invitations": {"envoyees": acc.get("totalSent", 0), "acceptees": acc.get("totalAccepted", 0),
                        "taux_pct": acc.get("acceptanceRate", 0)},
        "messages_linkedin": {"envoyes": msg.get("totalSent", 0), "reponses": msg.get("totalReplies", 0),
                              "taux_pct": msg.get("answerRate", 0)},
        "emails": {"envoyes": mail.get("totalSent", 0), "reponses": mail.get("totalReplies", 0),
                   "taux_pct": mail.get("answerRate", 0)},
        "email_finder": {"recherches": finder.get("total", 0), "trouves": finder.get("totalSuccess", 0),
                         "taux_pct": finder.get("successRate", 0)},
    }


def prospect_list_row(l: dict) -> dict:
    return {
        "id": l.get("_id"),
        "nom": l.get("name"),
        "prospects": l.get("totalProspects", 0),
        "ne_pas_contacter": bool(l.get("doNotContact")),
    }


STATUTS = {"connected": "en relation", "pending": "invitation en attente", "not_connected": "pas en relation",
           "unknown": "inconnu"}


def _linkedin(pr: dict) -> str | None:
    pid = pr.get("publicIdentifier")
    return f"https://www.linkedin.com/in/{pid}/" if pid else pr.get("profileUrl")


def prospect_row(p: dict, tags_by_id: dict | None = None) -> dict:
    """Un prospect de getProspects -> ligne : identite, poste, statut, liste, tags, campagne."""
    pr = p.get("profile") or {}
    comp = pr.get("company")
    pl = p.get("prospectList") or {}
    tags = [tags_by_id.get(t, t) if tags_by_id else t for t in (p.get("tags") or []) if isinstance(t, str)]
    return {
        "id": p.get("_id"),
        "nom": " ".join(x for x in (pr.get("firstName"), pr.get("lastName")) if x) or None,
        "poste": pr.get("occupation"),
        "entreprise": comp.get("name") if isinstance(comp, dict) else comp,
        "region": pr.get("region"),
        "linkedin": _linkedin(pr),
        "relation": STATUTS.get(p.get("status"), p.get("status")),
        "liste": pl.get("name") if isinstance(pl, dict) else pl,
        "liste_id": pl.get("_id") if isinstance(pl, dict) else pl,
        "en_campagne": bool(p.get("isActiveInCampaign")),
        "tags": tags,
        "email": p.get("contactEmail") or p.get("enrichedEmail") or None,
    }


def prospect_detail(p: dict, tags_by_id: dict | None = None, history: int = 8) -> dict:
    """getProspect -> fiche : ligne + notes + derniers evenements (import, invitation, message, reponse...)."""
    out = prospect_row(p, tags_by_id)
    out["notes"] = [n.get("text") if isinstance(n, dict) else n for n in (p.get("notes") or [])]
    out["en_relation_depuis"] = (p.get("connectedAt") or "")[:10] or None
    out["ajoute_le"] = (p.get("createdAt") or "")[:10] or None
    evts = sorted((p.get("history") or []), key=lambda e: e.get("executionDate") or "", reverse=True)[:history]
    out["historique"] = [f"{(e.get('executionDate') or '')[:10]} {e.get('name')}" for e in evts]
    return out


def tag_row(t: dict) -> dict:
    return {"id": t.get("_id"), "nom": t.get("name"), "couleur": t.get("color")}


def prospect_list_detail(l: dict, en_campagne: int | None = None) -> dict:
    out = prospect_list_row(l)
    out["creee_le"] = (l.get("createdAt") or "")[:10] or None
    out["modifiee_le"] = (l.get("updatedAt") or "")[:10] or None
    if en_campagne is not None:
        out["dont_en_campagne"] = en_campagne
    return out
