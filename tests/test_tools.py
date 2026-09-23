import json

from waalaxy_mcp import server


def j(s):
    return json.loads(s)


# --- lecture ---------------------------------------------------------------

def test_stats_defaut_running_paused(fake):
    out = j(server.waalaxy_stats())
    assert set(c["state"] for c in out["campagnes"]) <= {"running", "paused"}
    assert out["total"] == len(out["campagnes"])


def test_stats_etat_inconnu_refuse(fake):
    assert "erreur" in j(server.waalaxy_stats(states=["archived"]))


def test_campaign_full_ajoute_le_brut(fake):
    cid = fake._campaign["_id"]
    assert "brut" not in j(server.waalaxy_campaign(cid))
    assert j(server.waalaxy_campaign(cid, full=True))["brut"]["_id"] == cid


def test_campaign_inconnue_rend_erreur_pas_exception(fake):
    out = j(server.waalaxy_campaign("000000000000000000000000"))
    assert out["status"] == 404 and "erreur" in out


def test_drafts_et_draft(fake):
    ds = j(server.waalaxy_drafts())["brouillons"]
    assert ds[0]["id"] == fake._draft["_id"]
    d = j(server.waalaxy_draft(ds[0]["id"]))
    assert d["sequence"] and d["sources_prevues"]


def test_global_stats_dates_courtes(fake):
    out = j(server.waalaxy_global_stats("2026-01-01", "2026-06-30"))
    assert out["periode"] == {"debut": "2026-01-01", "fin": "2026-06-30"}


def test_prospect_lists_triees_par_taille(fake):
    rows = j(server.waalaxy_prospect_lists())["listes"]
    assert rows == sorted(rows, key=lambda r: -r["prospects"])


# --- ecriture : gates ------------------------------------------------------

def test_pause_sans_confirm_ne_fait_rien(fake):
    cid = fake._campaign["_id"]
    out = j(server.waalaxy_pause(cid))
    assert out["apercu"] is True and out["campagne"]["nom"] == fake._campaign["name"]
    assert fake.writes == []


def test_pause_avec_confirm_ecrit(fake):
    cid = fake._campaign["_id"]
    out = j(server.waalaxy_pause(cid, confirm=True))
    assert out["execute"] is True and fake.writes == [("pause", cid)]


def test_stop_avertit_irreversible(fake):
    out = j(server.waalaxy_stop(fake._campaign["_id"]))
    assert "irreversible" in out["avertissement"]
    assert fake.writes == []


def test_dry_run_force_bloque_meme_avec_confirm(fake):
    fake.force_dry_run = True
    out = j(server.waalaxy_stop(fake._campaign["_id"], confirm=True))
    assert out["execute"] is False and out["dry_run_force"] is True
    assert fake.writes == []


def test_launch_sans_confirm_compte_les_eligibles(fake):
    did = fake._draft["_id"]
    lid = fake._lists[0]["_id"]
    out = j(server.waalaxy_launch_draft(did, lid))
    assert out["apercu"] is True
    assert out["prospects_qui_partiront"] == 1
    assert out["liste"]["id"] == lid and out["sequence"]
    assert fake.writes == []


def test_launch_liste_inconnue_refuse(fake):
    out = j(server.waalaxy_launch_draft(fake._draft["_id"], "ffffffffffffffffffffffff"))
    assert "erreur" in out and fake.writes == []


def test_launch_avec_confirm_envoie_les_ids(fake):
    did = fake._draft["_id"]
    lid = fake._lists[0]["_id"]
    out = j(server.waalaxy_launch_draft(did, lid, name="Test MCP", confirm=True))
    assert out["execute"] is True and out["campagne"]["etat"] == "running"
    action, draft_id, kw = fake.writes[0]
    assert action == "launch" and draft_id == did
    assert kw["prospects"] == [{"listId": lid, "prospectIds": ["6e5c3e6d8e1b6fd0b4c02a48"]}] or kw["prospects"][0]["listId"] == lid
    assert kw["name"] == "Test MCP"
