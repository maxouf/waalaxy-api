"""Chaque vue comparee a sa source, champ par champ : un chemin mal mappe vide une
valeur en silence, seul ce genre de test le voit."""
from conftest import load

from waalaxy_mcp import views


def test_campaign_row_reprend_travelers_count():
    c = load("campaign")
    tc = c["travelersCount"]
    r = views.campaign_row(c)
    assert r["id"] == c["_id"] and r["name"] == c["name"] and r["state"] == c["state"]
    assert r["total"] == tc["total"] and r["traveling"] == tc["traveling"]
    assert r["accepted"] == tc["hasAcceptedInvitation"] and r["replied"] == tc["hasReplied"]
    assert r["updated"] == c["updatedAt"][:10]


def test_campaign_detail_origines_compteurs_sequence():
    c = load("campaign")
    d = views.campaign_detail(c)
    assert d["id"] == c["_id"] and d["etat"] == c["state"]
    assert d["origines"][0]["liste_id"] == c["origins"][0]["prospectList"]
    assert d["origines"][0]["prospects"] == c["origins"][0]["travelerCount"]
    assert d["prospects"]["total"] == c["travelersCount"]["total"]  # repli quand pas de summary
    counts = views.summary_counts(load("travelerssummary"))
    d2 = views.campaign_detail(c, counts)
    assert d2["prospects"]["en_cours"] == counts["traveling"]
    assert d2["prospects"]["a_repondu"] == counts["hasReplied"]
    assert d2["prospects_actifs"] == c["activeTravelersCount"]
    n_wp, n_paths = len(c["world"]["waypoints"]), len(c["world"]["paths"])
    assert len(d["sequence"]) == n_wp + n_paths
    assert "brut" not in d and "world" not in d  # jamais le brut par defaut


def test_sequence_steps_traduit_types_et_conditions():
    c = load("campaign")
    lines = views.sequence_steps(c["world"])
    assert lines[0].endswith("Entrée")
    assert any("Message LinkedIn (modèle " in l for l in lines)
    assert any(" si relation acceptée ou statut inconnu" in l for l in lines)
    assert views.sequence_steps(None) == []


def test_draft_detail_sources_et_sequence():
    d = load("draft")
    v = views.draft_detail(d)
    assert v["id"] == d["_id"] and v["nom"] == d["name"]
    src = d["prospectSources"][0]
    assert v["sources_prevues"][0]["liste_id"] == src["prospectList"]["_id"]
    assert v["sources_prevues"][0]["total_liste"] == src["prospectList"]["totalProspects"]
    assert v["sources_prevues"][0]["prevus"] == src["count"]
    assert any("Invitation LinkedIn" in l for l in v["sequence"])


def test_travelers_summary_compte_sans_ids_par_defaut():
    r = load("travelerssummary")
    v = views.travelers_summary(r)
    src = r["travelersSummaries"]
    assert v["traveling"] == sum(e["travelerCount"] for e in src["traveling"])
    assert all(isinstance(x, int) for x in v.values())
    full = views.travelers_summary(r, full=True)
    assert len(full["traveling"]["ids"]) == v["traveling"]


def test_global_stats_mappe_les_taux():
    r = load("allStats")
    v = views.global_stats(r, "2024-01-01T00:00:00.000Z", "2026-09-23T23:59:59.000Z")
    assert v["periode"] == {"debut": "2024-01-01", "fin": "2026-09-23"}
    assert v["invitations"]["acceptees"] == r["acceptance"]["totalAccepted"]
    assert v["invitations"]["taux_pct"] == r["acceptance"]["acceptanceRate"]
    assert v["messages_linkedin"]["reponses"] == r["responsesMessage"]["totalReplies"]
    assert v["email_finder"]["trouves"] == r["emailFinder"]["totalSuccess"]


def test_prospect_list_row():
    l = load("prospectLists")[0]
    v = views.prospect_list_row(l)
    assert v == {"id": l["_id"], "nom": l["name"], "prospects": l["totalProspects"],
                 "ne_pas_contacter": bool(l.get("doNotContact"))}


def test_conditions_pending_et_delai():
    d = load("draft")
    lines = views.sequence_steps(d["sequence"])
    assert any("invitation en attente" in l for l in lines)
    assert any("délai de 30 jours écoulé" in l for l in lines)
    assert not any("isPending" in l or " sleep" in l for l in lines)
    assert views._condition({"isAtomic": True, "entity": {"type": "wasNotReplied"}}) == "sans réponse"


def test_client_campaign_deballe_l_enveloppe():
    from waalaxy_api.client import WaalaxyClient
    c = WaalaxyClient.__new__(WaalaxyClient)
    c._call = lambda *a, **k: {"campaign": {"_id": "x", "name": "n", "world": {}}}
    assert c.campaign("x")["_id"] == "x"


def test_user_id_deduit_du_jeton():
    import base64, json
    from waalaxy_api.client import user_id_from_token
    payload = base64.urlsafe_b64encode(json.dumps({"_id": "abc123", "linkedinId": "x"}).encode()).rstrip(b"=").decode()
    assert user_id_from_token(f"hdr.{payload}.sig") == "abc123"
    assert user_id_from_token("pas-un-jwt") is None


def test_call_refuse_toute_ecriture_hors_liste():
    import pytest
    from waalaxy_api.client import WaalaxyClient, WaalaxyError
    c = WaalaxyClient.__new__(WaalaxyClient)
    c._request = lambda *a, **k: {"ok": True}
    assert c._call("POST", "/profesor/campaigns/getAll", json_body={})["ok"]
    assert c._call("GET", "/profesor/campaigns/x")["ok"]
    for m, p in (("POST", "/profesor/prospects/archiveProspects"), ("PUT", "/profesor/campaigns/x/stop"), ("DELETE", "/profesor/tags/removeTag/x"), ("POST", "/profesor/campaigns")):
        with pytest.raises(WaalaxyError):
            c._call(m, p, json_body={})
