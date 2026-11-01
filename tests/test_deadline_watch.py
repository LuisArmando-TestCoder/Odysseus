"""Deadline Watch: overdue / due-soon notes, brief lines, once-per-state alerts."""
import asyncio
import json
from datetime import datetime, timezone

import pytest

from src.deadline_watch import (DAY, HOUR, assess_notes, deadline_brief_lines,
                                due_deadline_alerts, format_deadline_alerts,
                                parse_due)

NOW = datetime(2026, 11, 1, 12, 0, 0).timestamp()  # local noon


def iso(offset_seconds):
    return datetime.fromtimestamp(NOW + offset_seconds).strftime("%Y-%m-%dT%H:%M")


def note(nid="n1", **kw):
    n = {"id": nid, "title": "Pay rent", "content": "", "items": None,
         "due_date": iso(-2 * HOUR), "archived": False, "repeat": "none"}
    n.update(kw)
    return n


def test_parse_due_formats():
    assert parse_due("2026-11-01T12:00") == pytest.approx(NOW)
    utc = parse_due("2026-11-01T12:00Z")
    assert utc == datetime(2026, 11, 1, 12, tzinfo=timezone.utc).timestamp()
    assert parse_due("2026-11-01T12:00:00+00:00") == utc
    # date-only → end of that day, local
    assert parse_due("2026-11-01") == datetime(2026, 11, 1, 23, 59, 59).timestamp()
    for bad in (None, "", "tomorrow", "2026-13-40", 12):
        assert parse_due(bad) is None


def test_overdue_and_due_soon_classification():
    res = assess_notes([
        note("over", due_date=iso(-3 * DAY)),
        note("soon", title="Submit report", due_date=iso(5 * HOUR)),
        note("far", due_date=iso(5 * DAY)),
        note("arch", archived=True),
        note("nodate", due_date=""),
    ], now=NOW)
    kinds = [(i["note_id"], i["kind"]) for i in res["items"]]
    assert kinds == [("over", "overdue"), ("soon", "due_soon")]
    assert res["items"][0]["message"] == 'Overdue by 3 days: "Pay rent"'
    assert res["items"][1]["message"] == 'Due in 5 hours: "Submit report"'
    assert res["long_overdue"] == 0


def test_checklists_done_vs_pending():
    done = json.dumps([{"text": "a", "done": True}, {"text": "b", "checked": True}])
    open_ = json.dumps([{"text": "a", "done": True}, {"text": "b"}, {"text": "c"}])
    res = assess_notes([note("done", items=done), note("open", items=open_),
                        note("broken", items="{nope")], now=NOW)
    ids = {i["note_id"]: i for i in res["items"]}
    assert "done" not in ids
    assert ids["open"]["pending"] == ["b", "c"]
    assert "(2 open items)" in ids["open"]["message"]
    assert "broken" in ids  # unparseable items → treated as a plain note


def test_recurring_past_due_skipped_but_upcoming_kept():
    res = assess_notes([note("r1", repeat="weekly"),
                        note("r2", repeat="daily", due_date=iso(HOUR))], now=NOW)
    assert [i["note_id"] for i in res["items"]] == ["r2"]


def test_long_overdue_counted_not_listed():
    res = assess_notes([note("old", due_date=iso(-40 * DAY))], now=NOW)
    assert res["items"] == [] and res["long_overdue"] == 1
    lines = deadline_brief_lines([note("old", due_date=iso(-40 * DAY))], now=NOW)
    assert lines[0].startswith("Deadlines: 0 overdue")
    assert "more than 30 days" in lines[-1]


def test_titles_are_cleaned_and_fallbacks():
    res = assess_notes([note("x", title="Bad\x00title\nwith\x1b junk" + "z" * 300),
                        note("y", title="", content="Call the bank about the card")],
                       now=NOW)
    t = {i["note_id"]: i["title"] for i in res["items"]}
    assert "\x00" not in t["x"] and "\n" not in t["x"] and len(t["x"]) <= 100
    assert t["y"] == "Call the bank about the card"


def test_brief_lines_empty_and_capped():
    assert deadline_brief_lines([note(due_date=iso(9 * DAY))], now=NOW) == []
    many = [note(f"n{i}", due_date=iso(-(i + 1) * HOUR)) for i in range(12)]
    lines = deadline_brief_lines(many, now=NOW, limit=5)
    assert lines[0] == "Deadlines: 12 overdue, 0 due in the next 48h"
    assert len(lines) == 1 + 5 + 1 and lines[-1].endswith("7 more")


def test_alerts_once_per_state_and_due_date(tmp_path):
    path = str(tmp_path / "s.json")
    notes = [note("a", due_date=iso(3 * HOUR)), note("b", due_date=iso(-HOUR))]
    first = due_deadline_alerts("u", notes, now=NOW, state_path=path)
    assert sorted(i["note_id"] for i in first) == ["a", "b"]
    assert due_deadline_alerts("u", notes, now=NOW + 60, state_path=path) == []
    # "a" goes past due → reported again as overdue
    later = due_deadline_alerts("u", notes, now=NOW + 4 * HOUR, state_path=path)
    assert [(i["note_id"], i["kind"]) for i in later] == [("a", "overdue")]
    # rescheduling "b" into the window is new news
    notes[1]["due_date"] = iso(4 * HOUR + 2 * HOUR)
    again = due_deadline_alerts("u", notes, now=NOW + 4 * HOUR, state_path=path)
    assert [(i["note_id"], i["kind"]) for i in again] == [("b", "due_soon")]
    # state prunes notes that left play (archived)
    notes[0]["archived"] = True
    due_deadline_alerts("u", notes, now=NOW + 4 * HOUR, state_path=path)
    state = json.loads(open(path).read())
    assert all(k.startswith("b|") for k in state)


def test_alert_horizon_is_24h(tmp_path):
    path = str(tmp_path / "s.json")
    assert due_deadline_alerts("u", [note(due_date=iso(30 * HOUR))], now=NOW, state_path=path) == []


def test_alerts_tolerate_corrupt_state(tmp_path):
    path = tmp_path / "s.json"
    path.write_text("[not a dict")
    assert len(due_deadline_alerts("u", [note()], now=NOW, state_path=str(path))) == 1


def test_format_alerts():
    items = assess_notes([note("a"), note("b", title="Renew passport", due_date=iso(HOUR))],
                         now=NOW)["items"]
    body = format_deadline_alerts(items)
    assert body.splitlines()[0] == "Deadlines: 1 overdue, 1 due soon."
    assert '- Overdue by 2 hours: "Pay rent"' in body
    assert '- Due in 1 hour: "Renew passport"' in body
    assert format_deadline_alerts(items * 6, limit=10).count("\n- ") == 10
    assert "…and 2 more" in format_deadline_alerts(items * 6, limit=10)


def test_check_deadlines_action_and_registration(monkeypatch, tmp_path):
    ba = pytest.importorskip("src.builtin_actions")
    ts = pytest.importorskip("src.task_scheduler")
    import src.deadline_watch as dw
    monkeypatch.setattr(dw, "load_owner_notes", lambda owner: [note(due_date=iso(-HOUR))] if owner == "u" else [])
    monkeypatch.setattr(dw, "_state_path", lambda owner: str(tmp_path / f"{owner}.json"))
    body, ok = asyncio.run(ba.action_check_deadlines("u"))
    assert ok and "Overdue by" in body and "Pay rent" in body
    with pytest.raises(ba.TaskNoop):
        asyncio.run(ba.action_check_deadlines("u"))
    with pytest.raises(ba.TaskNoop):
        asyncio.run(ba.action_check_deadlines("other"))
    assert "check_deadlines" in ba.BUILTIN_ACTIONS and "check_deadlines" in ba.BUILTIN_ACTION_INFO
    assert ts.HOUSEKEEPING_DEFAULTS["check_deadlines"]["name"] == "Deadline Watch"
