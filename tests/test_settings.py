"""Reglages du compte (horaires, quotas) et modification de campagne : vues, apercus, gates, corps."""
import json

from waalaxy_mcp import server


def j(s):
    return json.loads(s)


# --- plages horaires ---------------------------------------------------------

def test_working_hours_vue(fake):
    out = j(server.waalaxy_working_hours())
    assert out["fuseau"] == "Europe/Paris" and out["actif_maintenant"] is True
    assert [d["jour"] for d in out["jours"]][:2] == ["lundi", "mardi"]
    lundi, dimanche = out["jours"][0], out["jours"][6]
    assert lundi["actif"] and lundi["plages"] == ["07:00-23:00"]
    assert not dimanche["actif"] and dimanche["plages"] == []


def test_set_working_hours_apercu_sans_ecriture(fake):
    out = j(server.waalaxy_set_working_hours([{"day": 1, "start": "08:00", "end": "19:00"}]))
    assert out["apercu"] and fake.writes == []
    assert out["avant"]["jours"][0]["plages"] == ["07:00-23:00"]
    assert out["apres"]["jours"][0]["plages"] == ["08:00-19:00"]
    assert out["apres"]["jours"][1]["actif"] is False  # mardi absent -> inactif
    assert out["apres"]["fuseau"] == "Europe/Paris"  # fuseau conserve


def test_set_working_hours_refus(fake):
    assert "erreur" in j(server.waalaxy_set_working_hours([]))
    assert "erreur" in j(server.waalaxy_set_working_hours([{"day": 7, "start": "08:00", "end": "19:00"}]))
    assert "erreur" in j(server.waalaxy_set_working_hours([{"day": 1, "start": "25:00", "end": "19:00"}]))
    assert "erreur" in j(server.waalaxy_set_working_hours([{"day": 1, "start": "20:00", "end": "19:00"}]))
    assert "erreur" in j(server.waalaxy_set_working_hours([{"day": 1, "start": "08:00", "end": "19:00"}, {"day": 1, "start": "08:00", "end": "19:00"}]))
    assert "erreur" in j(server.waalaxy_set_working_hours([{"day": 1, "start": "08:00", "end": "19:00"}], timezone="Mars/Olympus"))
    assert fake.writes == []


def test_set_working_hours_confirm_corps(fake):
    out = j(server.waalaxy_set_working_hours([{"day": 5, "start": "9:30", "end": "18:00"}, {"day": 1, "start": "08:00", "end": "19:00"}],
                                             timezone="Europe/Lisbon", confirm=True))
    assert out["execute"]
    action, body = fake.writes[-1]
    assert action == "set_working_settings" and body["timezone"] == "Europe/Lisbon"
    assert body["workingDays"] == [
        {"day": 1, "ranges": [{"start": {"hour": 8, "minute": 0}, "end": {"hour": 19, "minute": 0}}]},
        {"day": 5, "ranges": [{"start": {"hour": 9, "minute": 30}, "end": {"hour": 18, "minute": 0}}]},
    ]


def test_set_working_hours_dry_run_force(fake, monkeypatch):
    monkeypatch.setenv("WAALAXY_DRY_RUN", "1")
    fake.force_dry_run = True
    out = j(server.waalaxy_set_working_hours([{"day": 1, "start": "08:00", "end": "19:00"}], confirm=True))
    assert out["execute"] is False and fake.writes == []


# --- quotas --------------------------------------------------------------------

def test_quotas_vue(fake):
    out = j(server.waalaxy_quotas())
    rows = {r["type"]: r for r in out["quotas"]}
    assert len(rows) == 5 and out["actions_en_file"] == 2 and out["remise_a_zero"].startswith("2026-11-06")
    c = rows["connectLinkedin"]
    assert c["libelle"] == "invitations" and c["utilises_aujourdhui"] == 6 and c["restants_aujourdhui"] == 44
    assert c["plafond_actif"] == 50 and c["plafond_personnalise"] == 50 and c["plafond_du_plan"] == 100
    assert rows["messageLinkedin"]["plafond_personnalise"] is None and rows["messageLinkedin"]["plafond_du_plan"] == 150


def test_set_quota_refus(fake):
    assert "erreur" in j(server.waalaxy_set_quota("sendMail", 10))
    assert "erreur" in j(server.waalaxy_set_quota("connectLinkedin", 101))
    assert "erreur" in j(server.waalaxy_set_quota("connectLinkedin", -1))
    assert fake.writes == []


def test_set_quota_apercu_puis_confirm(fake):
    out = j(server.waalaxy_set_quota("connectLinkedin", 30))
    assert out["apercu"] and out["plafond_avant"] == 50 and out["plafond_apres"] == 30 and fake.writes == []
    out = j(server.waalaxy_set_quota("connectLinkedin", 30, confirm=True))
    assert out["execute"] and fake.writes[-1] == ("set_quota", {"customMaxValue": 30, "type": "connectLinkedin"})


def test_reset_quotas(fake):
    out = j(server.waalaxy_reset_quotas())
    assert out["apercu"] and out["avant"]["connectLinkedin"] == 50 and out["apres"]["connectLinkedin"] == 100 and fake.writes == []
    assert j(server.waalaxy_reset_quotas(confirm=True))["execute"] and fake.writes[-1][0] == "reset_quotas"


# --- modification de campagne ----------------------------------------------------

def test_rename_campaign(fake):
    cid = fake._campaign["_id"]
    assert "erreur" in j(server.waalaxy_rename_campaign(cid, "  "))
    pris = next(c["name"] for c in fake.getall["campaigns"] if c["_id"] != cid)
    assert "erreur" in j(server.waalaxy_rename_campaign(cid, pris.upper()))
    out = j(server.waalaxy_rename_campaign(cid, "Nouveau nom"))
    assert out["apercu"] and out["ancien_nom"] == fake._campaign["name"] and fake.writes == []
    out = j(server.waalaxy_rename_campaign(cid, "Nouveau nom", confirm=True))
    assert out["execute"] and fake.writes[-1] == ("rename_campaign", {"campaign_id": cid, "name": "Nouveau nom"})


def test_campaign_affiche_les_ids_de_branchement(fake):
    seq = j(server.waalaxy_campaign(fake._campaign["_id"]))["sequence"]
    assert any(l.startswith("(4) 2 → 4") for l in seq), seq


def test_set_delay(fake):
    cid = fake._campaign["_id"]
    assert "erreur" in j(server.waalaxy_set_delay(cid, "9999", 3))
    assert "erreur" in j(server.waalaxy_set_delay(cid, "1", 3))  # branchement sans delai
    assert "erreur" in j(server.waalaxy_set_delay(cid, "4", -1))
    out = j(server.waalaxy_set_delay(cid, "4", 3))
    assert out["apercu"] and out["delai_avant_jours"] == 10 and out["delai_apres_jours"] == 3 and fake.writes == []
    assert "10 jours" in out["condition_avant"] and "3 jours" in out["condition_apres"]
    out = j(server.waalaxy_set_delay(cid, "4", 3, confirm=True))
    assert out["execute"]
    action, body = fake.writes[-1]
    assert action == "update_path_condition" and body["pathId"] == "4" and body["worldId"] == fake._campaign["world"]["_id"]
    cond = body["condition"]
    assert cond["rightOperand"]["entity"] == {"type": "sleep", "params": {"time": 3}}
    assert cond["leftOperand"]["entity"] == {"type": "wasNotReplied"}  # le reste est intact
    # la fixture n'a pas ete modifiee en place
    assert fake._campaign["world"]["paths"][2]["condition"]["rightOperand"]["entity"]["params"]["time"] == 10


def test_set_step_template(fake):
    cid, mid = fake._campaign["_id"], fake._content["_id"]
    assert "erreur" in j(server.waalaxy_set_step_template(cid, "9999", mid))
    assert "erreur" in j(server.waalaxy_set_step_template(cid, "1", mid))  # entry : pas de modele
    assert j(server.waalaxy_set_step_template(cid, "2", "000000000000000000000000"))["status"] == 404
    out = j(server.waalaxy_set_step_template(cid, "2", mid))
    assert out["apercu"] and out["modele_apres"]["nom"] == "Relance webinaire" and "avertissement" in out and fake.writes == []
    out = j(server.waalaxy_set_step_template(cid, "2", mid, confirm=True))
    assert out["execute"]
    action, body = fake.writes[-1]
    assert action == "update_waypoint_params" and body["waypointId"] == "2" and body["params"] == {"contentReference": mid}


def test_message_et_update_message(fake):
    mid = fake._content["_id"]
    m = j(server.waalaxy_message(mid))
    assert m["nom"] == "Relance webinaire" and m["message"].startswith("Bonjour {{firstName}}")
    assert "erreur" in j(server.waalaxy_update_message(mid, "  "))
    out = j(server.waalaxy_update_message(mid, "Nouveau texte {{firstName}}", name="Relance v2"))
    assert out["apercu"] and out["avant"]["nom"] == "Relance webinaire" and out["apres"]["nom"] == "Relance v2"
    assert out["apres"]["message"] == "Nouveau texte {{firstName}}" and fake.writes == []
    out = j(server.waalaxy_update_message(mid, "Nouveau texte {{firstName}}", name="Relance v2", confirm=True))
    assert out["execute"]
    action, body = fake.writes[-1]
    assert action == "update_content" and body["_id"] == mid and body["name"] == "Relance v2"
    assert body["params"]["messages"][0]["value"] == "Nouveau texte {{firstName}}" and body["params"]["messages"][0]["dispatchOrder"] == 0
    assert fake._content["params"]["messages"][0]["value"].startswith("Bonjour")  # fixture intacte
