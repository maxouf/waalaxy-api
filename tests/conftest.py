import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402

FIX = Path(__file__).parent / "fixtures"


def load(name: str):
    return json.loads((FIX / f"{name}.json").read_text(encoding="utf-8"))


class FakeClient:
    """Meme surface que WaalaxyClient, adossee aux fixtures. Enregistre les ecritures."""

    def __init__(self):
        self.getall = load("getAll")
        self._campaign = load("campaign")
        self._draft = load("draft")
        self._summary = load("travelerssummary")
        self._stats = load("allStats")
        self._lists = load("prospectLists")
        self._prospects = load("getProspects")
        self._full = load("getProspectsFull")
        self._one = load("getProspect")
        self._tags = load("getTags")
        self.writes: list[tuple] = []
        self.force_dry_run = False

    def campaigns(self, states, *, start=0, count=20, search=None):
        rows = [c for c in self.getall["campaigns"] if c["state"] in states]
        if search:
            rows = [c for c in rows if search.lower() in c["name"].lower()]
        return {"total": len(rows), "campaigns": rows[:count]}

    def campaign(self, campaign_id):
        from waalaxy_api.client import WaalaxyError
        if campaign_id != self._campaign["_id"]:
            raise WaalaxyError(f"404 GET /profesor/campaigns/{campaign_id}", 404, "not found")
        return self._campaign

    def count_per_status(self):
        return {"count": [{"status": "total", "value": 3}]}

    def drafts(self):
        d = self._draft
        return [{"_id": d["_id"], "name": d["name"], "state": "draft", "updatedAt": d["updatedAt"]}]

    def draft(self, draft_id):
        return self._draft

    def travelers_summary(self, campaign_id, status=None):
        return self._summary

    def all_stats(self, start, end=None, **q):
        return self._stats

    def prospect_lists(self):
        return self._lists

    def eligible_prospects(self, list_id):
        return [p["_id"] for p in self._prospects["prospects"]], self._prospects["prospectsCount"]

    def search_prospects(self, *, search=None, list_id=None, size=20, in_campaign=None, ids=None):
        ps = self._full["prospects"]
        if ids:
            ps = [p for p in ps if p["_id"] in ids]
        if in_campaign is not None:
            ps = [p for p in ps if bool(p.get("isActiveInCampaign")) == in_campaign]
        self.last_search = {"search": search, "list_id": list_id, "size": size, "ids": ids}
        return {"prospects": ps[:size], "prospectsCount": len(ps)}

    def prospect(self, *, public_identifier=None, member_id=None):
        from waalaxy_api.client import WaalaxyError
        pr = self._one["profile"]
        if public_identifier == pr["publicIdentifier"] or member_id == pr["memberId"]:
            return self._one
        raise WaalaxyError("404 prospect_not_found", 404, "prospect_not_found")

    def tags(self):
        return self._tags["tags"]

    def prospect_list(self, list_id):
        from waalaxy_api.client import WaalaxyError
        for l in self._lists:
            if l["_id"] == list_id:
                return l
        raise WaalaxyError("404", 404, "not found")

    def count_prospects(self, list_id=None, *, in_campaign=None):
        return 3

    def _w(self, action, payload, dry_run):
        if dry_run or self.force_dry_run:
            return {"dry_run": True, "method": "POST", "path": action, "cible": "", "payload": payload}
        self.writes.append((action, payload))
        return {"status": 200, "_id": "bbbbbbbbbbbbbbbbbbbbbbbb", "name": payload.get("name"), "tag": {"_id": "cc", **payload}}

    def create_list(self, name, *, icon_color="blue", icon_label=None, dry_run=True):
        return self._w("create_list", {"name": name}, dry_run)

    def rename_list(self, list_id, name, *, dry_run=True):
        return self._w("rename_list", {"list_id": list_id, "name": name}, dry_run)

    def create_tag(self, name, color="blue", *, dry_run=True):
        return self._w("create_tag", {"name": name, "color": color}, dry_run)

    def add_note(self, prospect_id, text, *, dry_run=True):
        return self._w("add_note", {"prospect": prospect_id, "text": text}, dry_run)

    def add_to_campaign(self, campaign_id, list_id, prospect_ids, *, dry_run=True):
        from waalaxy_api.client import WaalaxyError
        if not prospect_ids:
            raise WaalaxyError("vide")
        return self._w("add_to_campaign", {"campaign_id": campaign_id, "list_id": list_id, "prospect_ids": list(prospect_ids)}, dry_run)

    def move_prospects(self, prospect_ids, from_list_id, to_list_id, *, dry_run=True):
        from waalaxy_api.client import WaalaxyClient
        sel = WaalaxyClient._selection(prospect_ids, "move")
        return self._w("move_prospects", {"from": from_list_id, "to": to_list_id, "sel": sel}, dry_run)

    def tag_prospects(self, prospect_ids, list_id, tag_id, *, remove=False, dry_run=True):
        from waalaxy_api.client import WaalaxyClient
        sel = WaalaxyClient._selection(prospect_ids, "tag")
        return self._w("untag" if remove else "tag", {"list": list_id, "tag": tag_id, "sel": sel}, dry_run)

    def set_prospection_state(self, prospect_ids, list_id, state, *, dry_run=True):
        from waalaxy_api.client import WaalaxyClient
        sel = WaalaxyClient._selection(prospect_ids, "state")
        return self._w("state", {"list": list_id, "state": state, "sel": sel}, dry_run)

    def _write(self, action, cid, dry_run):
        if dry_run or self.force_dry_run:
            return {"dry_run": True, "method": "PUT", "path": f"/x/{action}", "cible": cid, "payload": None}
        self.writes.append((action, cid))
        return {"status": 200}

    def play(self, cid, *, dry_run=True):
        return self._write("play", cid, dry_run)

    def pause(self, cid, *, dry_run=True):
        return self._write("pause", cid, dry_run)

    def stop(self, cid, *, dry_run=True):
        return self._write("stop", cid, dry_run)

    def launch_from_draft(self, draft_id, *, dry_run=True, **kw):
        if dry_run or self.force_dry_run:
            return {"dry_run": True, "method": "POST", "path": "/profesor/campaigns", "cible": draft_id, "payload": kw}
        self.writes.append(("launch", draft_id, kw))
        return {"campaign": {"_id": "aaaaaaaaaaaaaaaaaaaaaaaa", "name": kw.get("name") or self._draft["name"], "state": "running"}}


@pytest.fixture
def fake(monkeypatch):
    from waalaxy_mcp import server
    c = FakeClient()
    monkeypatch.setattr(server, "_client", lambda: c)
    monkeypatch.delenv("WAALAXY_DRY_RUN", raising=False)
    return c
