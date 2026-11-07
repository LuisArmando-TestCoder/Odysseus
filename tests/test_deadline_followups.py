"""Snooze / mark done for Deadline Watch (src/deadline_followups.py)."""

import json
import os
from datetime import datetime

import pytest

from src import deadline_followups as F
from src.deadline_watch import (deadline_brief_lines, due_deadline_alerts,
                                format_deadline_alerts)

NOW = datetime(2026, 11, 6, 10, 0).timestamp()
HOUR = 3600


def _iso(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%dT%H:%M")


def _notes():
    return [
        {"id": "n1", "title": "Pay rent", "due_date": _iso(NOW - 5 * HOUR)},        # overdue
        {"id": "n2", "title": "Call dentist", "due_date": _iso(NOW + 6 * HOUR)},    # due soon
        {"id": "email:me@x.com:42", "title": "Email from Bob: contract", "due_date": _iso(NOW + 30 * HOUR),
         "source": "email"},
        {"id": "n9", "title": "Far away", "due_date": _iso(NOW + 30 * 24 * HOUR)},  # outside page horizon
    ]


@pytest.fixture
def path(tmp_path):
    return str(tmp_path / "followups" / "alice.json")


def test_snooze_presets():
    assert F.snooze_until("3d", NOW) == NOW + 3 * F.DAY
    assert F.snooze_until("1w", NOW) == NOW + 7 * F.DAY
    t = datetime.fromtimestamp(F.snooze_until("tomorrow", NOW))
    assert (t.day, t.hour, t.minute) == (7, 8, 0)
    with pytest.raises(ValueError):
        F.snooze_until("forever", NOW)


def test_valid_ids():
    assert F.valid_item_id("email:me@x.com:42")
    assert F.valid_item_id("3f2a-uuid_1")
    for bad in ("", "a/b", "x" * 201, "a|b", None, 5, "a b"):
        assert not F.valid_item_id(bad)


def test_done_and_snooze_hide_items_then_snooze_expires(path):
    F.apply_action("alice", "done", {"id": "n1", "due_date": _iso(NOW - 5 * HOUR)}, _notes(), now=NOW, path=path)
    F.apply_action("alice", "snooze", {"id": "n2", "due_date": _iso(NOW + 6 * HOUR), "preset": "3d"},
                   _notes(), now=NOW, path=path)
    entries = F.load_followups("alice", path)
    ov = F.deadline_overview(_notes(), entries, now=NOW)
    assert [i["id"] for i in ov["items"]] == ["email:me@x.com:42"]
    held = {i["id"]: i["followup"] for i in ov["held"]}
    assert held["n1"]["status"] == "done" and held["n1"]["until"] is None
    assert held["n2"] == {"status": "snoozed", "until": NOW + 3 * F.DAY}
    # 4 days later the snooze ran out (the note is overdue by then)
    later = NOW + 4 * F.DAY
    vis, held = F.apply_followups(
        [{"note_id": "n2", "due_date": _iso(NOW + 6 * HOUR)}], entries, later)
    assert len(vis) == 1 and not held


def test_done_is_per_due_date(path):
    F.apply_action("alice", "done", {"id": "n1", "due_date": _iso(NOW - 5 * HOUR)}, _notes(), now=NOW, path=path)
    redated = [{"id": "n1", "title": "Pay rent", "due_date": _iso(NOW + 2 * HOUR)}]
    vis, held = F.apply_followups(
        [{"note_id": "n1", "due_date": redated[0]["due_date"]}], F.load_followups("alice", path), NOW)
    assert len(vis) == 1 and not held


def test_restore_undoes(path):
    key = {"id": "n2", "due_date": _iso(NOW + 6 * HOUR)}
    F.apply_action("alice", "done", key, _notes(), now=NOW, path=path)
    F.apply_action("alice", "restore", key, _notes(), now=NOW, path=path)
    assert F.load_followups("alice", path) == {}
    with pytest.raises(KeyError):
        F.apply_action("alice", "restore", key, _notes(), now=NOW, path=path)


def test_action_refuses_unknown_or_foreign_items(path):
    with pytest.raises(KeyError):  # not one of this owner's deadlines
        F.apply_action("alice", "done", {"id": "someone-elses", "due_date": "2026-11-06"}, _notes(), now=NOW, path=path)
    with pytest.raises(KeyError):  # outside the page horizon
        F.apply_action("alice", "done", {"id": "n9", "due_date": _iso(NOW + 30 * 24 * HOUR)}, _notes(), now=NOW, path=path)
    with pytest.raises(KeyError):  # stale due date
        F.apply_action("alice", "done", {"id": "n1", "due_date": "2020-01-01"}, _notes(), now=NOW, path=path)
    for body in ({}, {"id": "n1"}, {"id": "../x", "due_date": "d"}, {"id": "n1", "due_date": 5}):
        with pytest.raises(ValueError):
            F.apply_action("alice", "done", body, _notes(), now=NOW, path=path)
    with pytest.raises(ValueError):
        F.apply_action("alice", "snooze", {"id": "n1", "due_date": _iso(NOW - 5 * HOUR), "preset": "1y"},
                       _notes(), now=NOW, path=path)
    with pytest.raises(ValueError):
        F.apply_action("alice", "explode", {"id": "n1", "due_date": "x"}, _notes(), now=NOW, path=path)
    assert not os.path.exists(path)


def test_state_is_owner_checked(path):
    F.save_followups("bob", {"n1|x": {"done": True}}, path)
    assert F.load_followups("alice", path) == {}
    assert F.load_followups("bob", path) == {"n1|x": {"done": True}}
    with open(path, "w") as f:
        f.write("not json")
    assert F.load_followups("bob", path) == {}


def test_prune_drops_gone_items_and_expired_snoozes():
    entries = {
        "a|1": {"done": True}, "b|1": {"until": NOW + 10}, "c|1": {"until": NOW - 10},
        "gone|1": {"done": True},
    }
    assert set(F.prune_followups(entries, {"a|1", "b|1", "c|1"}, NOW)) == {"a|1", "b|1"}


def test_state_is_capped(path):
    big = {f"n{i}|d": {"done": True, "at": i} for i in range(F.MAX_ENTRIES + 20)}
    F.save_followups("alice", big, path)
    kept = F.load_followups("alice", path)
    assert len(kept) == F.MAX_ENTRIES and "n0|d" not in kept and f"n{F.MAX_ENTRIES + 19}|d" in kept


def test_brief_hides_held_and_counts_snoozed(path):
    F.apply_action("alice", "snooze", {"id": "n1", "due_date": _iso(NOW - 5 * HOUR), "preset": "1w"},
                   _notes(), now=NOW, path=path)
    lines = deadline_brief_lines(_notes(), now=NOW, followups=F.load_followups("alice", path))
    text = "\n".join(lines)
    assert "Pay rent" not in text and "Call dentist" in text
    assert lines[0].startswith("Deadlines: 0 overdue") and "(1 snoozed)" in text
    # everything held → no section at all
    F.apply_action("alice", "done", {"id": "n2", "due_date": _iso(NOW + 6 * HOUR)}, _notes(), now=NOW, path=path)
    F.apply_action("alice", "done", {"id": "email:me@x.com:42", "due_date": _iso(NOW + 30 * HOUR)},
                   _notes(), now=NOW, path=path)
    assert deadline_brief_lines(_notes(), now=NOW, followups=F.load_followups("alice", path)) == []


def test_alerts_skip_held_and_realert_after_snooze(tmp_path, path):
    alerts = str(tmp_path / "alerts.json")
    first = due_deadline_alerts("alice", _notes(), now=NOW, state_path=alerts)
    assert {i["note_id"] for i in first} == {"n1", "n2"}
    F.apply_action("alice", "snooze", {"id": "n1", "due_date": _iso(NOW - 5 * HOUR), "preset": "tomorrow"},
                   _notes(), now=NOW, path=path)
    fu = F.load_followups("alice", path)
    assert due_deadline_alerts("alice", _notes(), now=NOW + 60, state_path=alerts, followups=fu) == []
    with open(alerts) as f:
        assert not any(k.startswith("n1|") for k in json.load(f))  # signature dropped while held
    # next morning the snooze is over → reported again
    morning = F.snooze_until("tomorrow", NOW) + 60
    again = due_deadline_alerts("alice", _notes(), now=morning, state_path=alerts, followups=fu)
    assert "n1" in {i["note_id"] for i in again}


def test_alert_text_carries_link():
    items = [{"kind": "overdue", "message": 'Overdue by 5 hours: "Pay rent"', "source": "note"}]
    text = format_deadline_alerts(items, link=F.deadlines_link("https://ody.example/"))
    assert text.endswith("Snooze or mark done: https://ody.example/deadlines")
    assert "Snooze" not in format_deadline_alerts(items)
    assert F.deadlines_link("", "email:me@x.com:42") == "/deadlines?item=email%3Ame%40x.com%3A42"
    assert F.deadlines_link("", "bad id") == "/deadlines"


def test_routes_owner_scoped(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import routes.deadline_routes as R

    owners = {"alice": _notes(), "bob": [{"id": "b1", "title": "Bob's", "due_date": _iso(NOW + HOUR)}]}
    who = {"u": "alice"}
    monkeypatch.setattr(R, "get_current_user", lambda request: who["u"])
    monkeypatch.setattr(R, "_owner_items", lambda owner: owners.get(owner, []))
    monkeypatch.setattr(F, "state_path", lambda owner: str(tmp_path / f"{owner}.json"))
    app = FastAPI()
    app.include_router(R.setup_deadline_routes())
    c = TestClient(app)

    ids = {i["id"] for i in c.get("/api/deadlines").json()["items"]}
    assert "n2" in ids and "b1" not in ids
    due = next(n["due_date"] for n in _notes() if n["id"] == "n2")
    r = c.post("/api/deadlines/snooze", json={"id": "n2", "due_date": due, "preset": "1w"})
    assert r.status_code == 200 and r.json()["status"] == "snoozed"
    assert "n2" in {i["id"] for i in c.get("/api/deadlines").json()["held"]}
    # alice cannot act on bob's deadline
    bob_due = owners["bob"][0]["due_date"]
    assert c.post("/api/deadlines/done", json={"id": "b1", "due_date": bob_due}).status_code == 404
    assert c.post("/api/deadlines/snooze", json={"id": "n2", "due_date": due, "preset": "1y"}).status_code == 400
    assert c.post("/api/deadlines/nuke", json={}).status_code == 404
    assert c.post("/api/deadlines/done", content=b"[1]",
                  headers={"content-type": "application/json"}).status_code == 400
    who["u"] = "bob"
    assert c.get("/api/deadlines").json()["held"] == []


# ── sidebar badge (deadline_status) ──────────────────────────────────────────

def test_status_counts_overdue_and_due_soon():
    st = F.deadline_status(_notes(), {}, now=NOW)
    assert st["attention"] == 1 and st["overdue"] == 1
    assert st["due_soon"] == 1          # n2; the email at +30h is outside 24h
    assert st["snoozed"] == 0
    assert st["next"]["id"] == "n1" and st["next"]["kind"] == "overdue"
    assert st["next"]["link"] == "/deadlines?item=n1"
    assert st["label"].startswith("Deadlines — 1 overdue · 1 due in 24h")
    assert "Pay rent" in st["label"]


def test_status_excludes_snoozed_and_done(path):
    notes = _notes()
    due1 = next(n["due_date"] for n in notes if n["id"] == "n1")
    due2 = next(n["due_date"] for n in notes if n["id"] == "n2")
    F.snooze_deadline("alice", "n1", due1, "3d", now=NOW, path=path)
    F.mark_deadline_done("alice", "n2", due2, now=NOW, path=path)
    st = F.deadline_status(notes, F.load_followups("alice", path), now=NOW)
    assert st["attention"] == 0 and st["due_soon"] == 0
    assert st["snoozed"] == 1
    assert st["next"] is None
    assert st["label"] == "Deadlines — 1 snoozed"
    # snooze runs out → overdue again
    later = NOW + 4 * 24 * HOUR
    st = F.deadline_status(notes, F.load_followups("alice", path), now=later)
    assert st["overdue"] >= 1 and st["snoozed"] == 0


def test_status_empty():
    st = F.deadline_status([], {}, now=NOW)
    assert st["attention"] == 0 and st["next"] is None
    assert st["label"] == "Deadlines — nothing due in the next 24h"


def test_status_route_owner_scoped(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import routes.deadline_routes as R

    owners = {"alice": _notes(), "bob": []}
    who = {"u": "bob"}
    monkeypatch.setattr(R, "get_current_user", lambda request: who["u"])
    monkeypatch.setattr(R, "_owner_items", lambda owner: owners.get(owner, []))
    monkeypatch.setattr(F, "state_path", lambda owner: str(tmp_path / f"{owner}.json"))
    app = FastAPI()
    app.include_router(R.setup_deadline_routes())
    c = TestClient(app)
    r = c.get("/api/deadlines/status")
    assert r.status_code == 200 and r.json()["attention"] == 0 and r.json()["next"] is None
