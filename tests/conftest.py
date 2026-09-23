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
