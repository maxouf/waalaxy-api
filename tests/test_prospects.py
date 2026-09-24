import json

from conftest import load

from waalaxy_mcp import server, views


def j(s):
    return json.loads(s)


# --- vues ---------------------------------------------------------------------

def test_prospect_row_champ_par_champ():
    p = load("getProspectsFull")["prospects"][0]
    pr = p["profile"]
    r = views.prospect_row(p, {})
    assert r["id"] == p["_id"]
    assert r["nom"] == f"{pr['firstName']} {pr['lastName']}"
    assert r["poste"] == pr["occupation"]
    assert r["entreprise"] == pr["company"]["name"]
    assert r["linkedin"] == f"https://www.linkedin.com/in/{pr['publicIdentifier']}/"
    assert r["liste_id"] == p["prospectList"]["_id"] and r["liste"] == p["prospectList"]["name"]
    assert r["en_campagne"] == bool(p["isActiveInCampaign"])
    assert r["relation"] == views.STATUTS.get(p["status"], p["status"])


def test_prospect_detail_notes_et_historique():
    p = load("getProspect")
    d = views.prospect_detail(p, {})
    assert d["ajoute_le"] == p["createdAt"][:10]
    assert len(d["historique"]) == min(8, len(p["history"]))
    assert d["historique"][0].endswith(sorted(p["history"], key=lambda e: e["executionDate"], reverse=True)[0]["name"])
    assert "brut" not in d


def test_tag_row():
    t = load("getTags")["tags"][0]
    assert views.tag_row(t) == {"id": t["_id"], "nom": t["name"], "couleur": t["color"]}


# --- lecture ------------------------------------------------------------------

def test_search_prospects_borne_la_page(fake):
    out = j(server.waalaxy_search_prospects(query="coach", limit=500))
    assert fake.last_search["size"] == 200
    assert out["total"] == 2 and len(out["prospects"]) == 2


def test_prospect_depuis_url_linkedin(fake):
    pid = fake._one["profile"]["publicIdentifier"]
    out = j(server.waalaxy_prospect(f"https://www.linkedin.com/in/{pid}/?utm=x"))
    assert out["id"] == fake._one["_id"]
    out2 = j(server.waalaxy_prospect(fake._one["profile"]["memberId"]))
    assert out2["id"] == fake._one["_id"]
    assert j(server.waalaxy_prospect("inconnu-xyz"))["status"] == 404


def test_prospect_list_fiche(fake):
    l = fake._lists[0]
    out = j(server.waalaxy_prospect_list(l["_id"]))
    assert out["nom"] == l["name"] and out["dont_en_campagne"] == 3


# --- ecriture : gates ----------------------------------------------------------

def test_create_list_refuse_doublon_et_gate(fake):
    existant = fake._lists[0]["name"]
    assert "erreur" in j(server.waalaxy_create_list(existant.upper()))
    out = j(server.waalaxy_create_list("Liste test MCP"))
    assert out["apercu"] is True and fake.writes == []
    out = j(server.waalaxy_create_list("Liste test MCP", confirm=True))
    assert out["execute"] is True and fake.writes == [("create_list", {"name": "Liste test MCP"})]


def test_rename_list_gate(fake):
    l = fake._lists[0]
    out = j(server.waalaxy_rename_list(l["_id"], "Nouveau nom"))
    assert out["ancien_nom"] == l["name"] and fake.writes == []
    j(server.waalaxy_rename_list(l["_id"], "Nouveau nom", confirm=True))
    assert fake.writes == [("rename_list", {"list_id": l["_id"], "name": "Nouveau nom"})]


def test_create_tag_refuse_doublon(fake):
    assert "erreur" in j(server.waalaxy_create_tag(fake._tags["tags"][0]["name"]))
    j(server.waalaxy_create_tag("Tag test", "green", confirm=True))
    assert fake.writes == [("create_tag", {"name": "Tag test", "color": "green"})]


def test_note_montre_le_prospect_puis_ecrit(fake):
    p = fake._full["prospects"][0]
    out = j(server.waalaxy_prospect_note(p["_id"], "RDV pris"))
    assert out["apercu"] is True and out["prospect_id"] == p["_id"] and fake.writes == []
    assert "erreur" in j(server.waalaxy_prospect_note("000000000000000000000000", "x"))
    j(server.waalaxy_prospect_note(p["_id"], "RDV pris", confirm=True))
    assert fake.writes == [("add_note", {"prospect": p["_id"], "text": "RDV pris"})]


def test_add_to_campaign_refuse_vide_et_ecarte_les_actifs(fake):
    assert "erreur" in j(server.waalaxy_add_to_campaign("c", "l", []))
    cid = fake._campaign["_id"]; lid = fake._lists[0]["_id"]
    ps = fake._full["prospects"]
    ids = [p["_id"] for p in ps] + ["000000000000000000000000"]
    out = j(server.waalaxy_add_to_campaign(cid, lid, ids))
    actifs = sum(1 for p in ps if p.get("isActiveInCampaign"))
    assert out["ecartes_deja_en_campagne"] == actifs
    assert out["inconnus_dans_cette_liste"] == ["000000000000000000000000"]
    assert out["nb_retenus"] == len(ps) - actifs and fake.writes == []
    if out["nb_retenus"]:
        j(server.waalaxy_add_to_campaign(cid, lid, ids, confirm=True))
        assert fake.writes[0][0] == "add_to_campaign" and len(fake.writes[0][1]["prospect_ids"]) == out["nb_retenus"]


def test_dry_run_force_bloque_les_nouvelles_ecritures(fake):
    fake.force_dry_run = True
    out = j(server.waalaxy_create_list("Liste test MCP", confirm=True))
    assert out["execute"] is False and fake.writes == []
