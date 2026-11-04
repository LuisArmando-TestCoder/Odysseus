"""Tests for src/email_deadlines.py and its Deadline Watch integration."""

import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.email_deadlines import (  # noqa: E402
    email_deadline_notes, extract_deadline, load_owner_email_deadlines, parse_sent,
)
from src.deadline_watch import (  # noqa: E402
    assess_notes, due_deadline_alerts, format_deadline_alerts,
)

# Wednesday 2026-11-04 09:30 local time
SENT = datetime(2026, 11, 4, 9, 30).timestamp()


def due(text, sent=SENT):
    r = extract_deadline(text, sent)
    return r and r["due_date"]


def test_weekday_ask():
    assert due("Hi! Could you reply by Friday?") == "2026-11-06T23:59"


def test_weekday_with_time_after_and_before():
    assert due("Please send the signed copy by Friday at 5pm.") == "2026-11-06T17:00"
    assert due("Please confirm before 3:30 pm Monday") == "2026-11-09T15:30"


def test_same_weekday_is_today_and_next_skips_a_week():
    assert due("let me know by Wednesday") == "2026-11-04T23:59"
    assert due("let me know by next Wednesday") == "2026-11-11T23:59"


def test_relative_and_eod():
    assert due("Can you get back to me by tomorrow?") == "2026-11-05T23:59"
    assert due("We need your answer by noon tomorrow") == "2026-11-05T12:00"
    assert due("need it reviewed by EOD") == "2026-11-04T17:00"
    assert due("Please approve by end of day tomorrow") == "2026-11-05T17:00"
    assert due("submit it by end of the week") == "2026-11-06T17:00"
    assert due("pay by the end of the month") == "2026-11-30T17:00"


def test_month_day_forms():
    assert due("Please RSVP by Nov 12th") == "2026-11-12T23:59"
    assert due("Deadline: November 20, 2026") == "2026-11-20T23:59"
    assert due("Kindly respond no later than 15 December") == "2026-12-15T23:59"
    assert due("deadline is 2026-11-18") == "2026-11-18T23:59"


def test_year_rollover():
    sent = datetime(2026, 12, 20, 10, 0).timestamp()
    assert due("please register by Jan 5", sent) == "2027-01-05T23:59"


def test_due_phrase():
    assert due("Your invoice #42 is due on Nov 10.") == "2026-11-10T23:59"


def test_subject_line_counts():
    assert due("Contract — reply by Thursday\nHi, attached the draft.") == "2026-11-05T23:59"


def test_ignores_non_asks_and_ambiguous_dates():
    assert due("We met on Friday and had a great time.") is None
    assert due("The concert is on Nov 12.") is None
    assert due("Please reply by 11/12") is None  # US or EU? skip
    assert due("Thanks for your note!") is None
    assert due("It was approved by the board on Monday.") is None
    assert due("Offer valid until Friday") is None
    assert due("") is None
    assert due(None) is None


def test_ignores_historical_and_far_future():
    assert due("the report was due by Nov 1") is None  # before it was sent
    assert due("please sign before 2027-09-01") is None  # > 120 days ahead


def test_earliest_phrase_wins():
    r = extract_deadline("Deadline: Nov 30. But please reply by Friday so we can plan.", SENT)
    assert r["due_date"] == "2026-11-30T23:59"
    assert r["phrase"].lower().startswith("deadline")


def test_control_chars_stripped_from_phrase():
    r = extract_deadline("reply\x00 by\x1b Friday", SENT)
    assert r and "\x00" not in r["phrase"] and "\x1b" not in r["phrase"]


def test_parse_sent():
    ts = parse_sent("Wed, 04 Nov 2026 09:30:00 +0000")
    assert ts == datetime.fromisoformat("2026-11-04T09:30:00+00:00").timestamp()
    assert parse_sent("garbage") is None
    assert parse_sent(None) is None


def _state(owner="ana@example.com"):
    dl = extract_deadline("reply by Friday", SENT)
    return {
        "owner": owner,
        "per_uid": {
            "1:10": {"score": 2, "tags": ["work"], "spam": False, "subject": "Contract",
                     "from": "Bob", "deadline": dl},
            "1:11": {"score": 0, "tags": ["marketing"], "spam": False, "subject": "Sale ends Friday",
                     "from": "Shop", "deadline": dl},
            "1:12": {"score": 1, "tags": [], "spam": True, "subject": "Act now", "from": "x",
                     "deadline": dl},
            "1:13": {"score": 2, "tags": ["work"], "spam": False, "subject": "No deadline",
                     "from": "Bob"},
        },
    }


def test_email_deadline_notes_filters_bulk_and_spam():
    notes = email_deadline_notes(_state())
    assert [n["id"] for n in notes] == ["email:1:10"]
    n = notes[0]
    assert n["source"] == "email" and n["due_date"] == "2026-11-06T23:59"
    assert "Bob" in n["title"] and "Contract" in n["title"] and "reply by Friday" in n["title"]


def test_email_notes_flow_through_deadline_watch(tmp_path):
    notes = email_deadline_notes(_state())
    now = datetime(2026, 11, 6, 9, 0).timestamp()  # Friday morning: due in ~15h
    items = assess_notes(notes, now=now)["items"]
    assert len(items) == 1 and items[0]["kind"] == "due_soon" and items[0]["source"] == "email"
    sp = str(tmp_path / "st.json")
    fresh = due_deadline_alerts("ana", notes, now=now, state_path=sp)
    assert len(fresh) == 1
    assert due_deadline_alerts("ana", notes, now=now + 60, state_path=sp) == []
    text = format_deadline_alerts(fresh)
    assert "Open Email to reply" in text and "Open Notes" not in text
    # Saturday: still unread → overdue is new news
    later = datetime(2026, 11, 7, 10, 0).timestamp()
    over = due_deadline_alerts("ana", notes, now=later, state_path=sp)
    assert len(over) == 1 and over[0]["kind"] == "overdue"


def test_mixed_footer():
    items = [
        {"kind": "overdue", "message": "a", "source": "note"},
        {"kind": "due_soon", "message": "b", "source": "email"},
    ]
    text = format_deadline_alerts(items)
    assert "Open Notes" in text and "Open Email" in text


def test_load_owner_email_deadlines_owner_scoped(tmp_path):
    owner = "ana@example.com"
    path = tmp_path / f"email_urgency_state_{owner}.json"
    path.write_text(json.dumps(_state(owner)), encoding="utf-8")
    assert len(load_owner_email_deadlines(owner, base_dir=str(tmp_path))) == 1
    # a file whose recorded owner differs is ignored
    path.write_text(json.dumps(_state("mallory@example.com")), encoding="utf-8")
    assert load_owner_email_deadlines(owner, base_dir=str(tmp_path)) == []
    # missing / corrupt → []
    assert load_owner_email_deadlines("nobody", base_dir=str(tmp_path)) == []
    path.write_text("{not json", encoding="utf-8")
    assert load_owner_email_deadlines(owner, base_dir=str(tmp_path)) == []
