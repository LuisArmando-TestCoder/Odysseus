"""
email_deadlines.py — "reply by Friday": deadlines that arrive by email.

People put deadlines in email ("please confirm by Nov 12", "send it over
before Monday 5pm", "Deadline: 2026-11-20") and those never become a note,
so Deadline Watch never follows up on them. This module:

* ``extract_deadline(text, sent_at)`` — finds one deadline phrase in an
  email's subject + body and resolves it against the email's own send time
  (so "tomorrow" means the day after it was sent, not after we read it).
  Deterministic regexes, no LLM. Only phrases tied to an ask count:
  an action verb + by/before/no later than + a date (``reply by Friday``),
  ``deadline: <date>``, or ``due (by|on) <date>``. Ambiguous numeric dates
  (11/12 — US or EU?) are ignored; ISO dates are accepted.
* ``email_deadline_notes(state, now)`` — turns the Email Tags scan state
  (``data/email_urgency_state_<owner>.json``; only *unread* mail) into
  note-shaped dicts that ``deadline_watch.assess_notes`` understands, so
  email deadlines get the same once-per-state alerts and brief lines as
  notes. Spam, marketing and newsletters are skipped ("sale ends Friday" is
  not your deadline). An email drops out once you open it — Odysseus can't
  tell whether you replied, so opening it counts as handled.

Email text is untrusted: everything that reaches a notification is cleaned
and capped by deadline_watch. Stdlib only; clock and paths injectable.
"""

from __future__ import annotations

import calendar
import json
import os
import re
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional

MAX_SCAN_CHARS = 4000
# A deadline more than this far after the email was sent is probably a
# contract term or an event date, not an ask to act on.
MAX_AHEAD_DAYS = 120

_WEEKDAYS = {
    "monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2, "thursday": 3, "thu": 3, "thur": 3, "thurs": 3,
    "friday": 4, "fri": 4, "saturday": 5, "sat": 5, "sunday": 6, "sun": 6,
}
_MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}

_WD = r"(?:mon(?:day)?|tue(?:s(?:day)?)?|wed(?:nesday)?|thu(?:r(?:s(?:day)?)?)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?)"
_MON = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
        r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
_ORD = r"(?:st|nd|rd|th)?"
_TIME = r"(?:(?:at\s+)?(?:\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.)|\d{1,2}:\d{2}|noon|midday|midnight))"

# One date expression; named groups tell the resolver which form matched.
_DATE = (
    r"(?P<date>"
    r"(?P<iso>\d{4}-\d{2}-\d{2})"
    r"|(?P<eod>(?:the\s+)?(?:end\s+of\s+(?:the\s+)?(?:business\s+)?day|eod|cob|close\s+of\s+business)"
    r"(?:\s+(?P<eod_rel>today|tomorrow))?)"
    r"|(?P<eow>(?:the\s+)?end\s+of\s+(?:the|this)\s+week|eow)"
    r"|(?P<eom>(?:the\s+)?end\s+of\s+(?:the|this)\s+month)"
    r"|(?P<rel>today|tonight|tomorrow)"
    r"|(?:(?P<next>next|this)\s+)?(?P<wd>" + _WD + r")\b"
    r"|(?P<md_mon>" + _MON + r")\.?\s+(?P<md_day>\d{1,2})" + _ORD + r"(?:,?\s+(?P<md_year>\d{4}))?"
    r"|(?:the\s+)?(?P<dm_day>\d{1,2})" + _ORD + r"\s+(?:of\s+)?(?P<dm_mon>" + _MON + r")\.?(?:,?\s+(?P<dm_year>\d{4}))?"
    r")"
)
_WHEN = (r"(?:(?P<tpre>" + _TIME + r")\s*(?:on\s+)?)?" + _DATE
         + r"(?:,?\s*(?:(?:at|by)\s+)?(?P<tpost>" + _TIME + r"))?")

_ASK = (r"(?:reply|respond|answer|get\s+back(?:\s+to\s+(?:me|us))?|let\s+(?:me|us)\s+know|confirm|rsvp|"
        r"send(?:\s+(?:it|this|them|me|us|over|back|in))*|submit|sign(?:\s+(?:it|this))?|return|pay|"
        r"complete|finish|review|approve|decide|register|book|deliver|provide|share|upload|"
        r"get\s+(?:it|this|them)\s+(?:to\s+(?:me|us)|done|back))")

_PATTERNS = [
    re.compile(r"\b(?P<phrase>" + _ASK + r"(?:s|d|ed|ing)?\b[^.!?\n]{0,60}?\b(?:by|before|no\s+later\s+than|until|not\s+later\s+than)\s+"
               + _WHEN + r")", re.I),
    re.compile(r"\b(?P<phrase>deadline(?:\s+is|\s*:|\s+of)?\s+(?:(?:by|on)\s+)?" + _WHEN + r")", re.I),
    re.compile(r"\b(?P<phrase>(?:is\s+|are\s+)?due\s+(?:by\s+|on\s+|before\s+)?" + _WHEN + r")", re.I),
]

_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]+")


def parse_sent(date_header: Any) -> Optional[float]:
    """Epoch seconds for an RFC 2822 ``Date:`` header, or None."""
    if not date_header or not isinstance(date_header, str):
        return None
    try:
        dt = parsedate_to_datetime(date_header)
    except (TypeError, ValueError, IndexError):
        return None
    if dt is None:
        return None
    try:
        return dt.timestamp()  # naive → local time, aware → honored
    except (OverflowError, OSError, ValueError):
        return None


def _parse_time(text: Optional[str]) -> Optional[tuple]:
    if not text:
        return None
    t = text.lower().replace("at ", "").replace(".", "").strip()
    if t in ("noon", "midday"):
        return (12, 0)
    if t == "midnight":
        return (23, 59)
    m = re.match(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$", t)
    if not m:
        return None
    h, mi, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if ap == "pm" and h < 12:
        h += 12
    elif ap == "am" and h == 12:
        h = 0
    if h > 23 or mi > 59:
        return None
    return (h, mi)


def _safe_date(y: int, mo: int, d: int) -> Optional[datetime]:
    try:
        return datetime(y, mo, d)
    except ValueError:
        return None


def _resolve(m: "re.Match", sent: datetime) -> Optional[datetime]:
    """Datetime (naive, local) for a matched date expression, or None."""
    g = m.groupdict()
    day: Optional[datetime] = None
    default_time = (23, 59)
    base = sent.replace(hour=0, minute=0, second=0, microsecond=0)
    if g.get("iso"):
        y, mo, d = (int(x) for x in g["iso"].split("-"))
        day = _safe_date(y, mo, d)
    elif g.get("eod"):
        day = base + timedelta(days=1 if (g.get("eod_rel") or "").lower() == "tomorrow" else 0)
        default_time = (17, 0)
    elif g.get("eow"):
        ahead = (4 - base.weekday()) % 7  # this week's Friday (today if Friday)
        if base.weekday() > 4:
            ahead = 4 - base.weekday() + 7
        day = base + timedelta(days=ahead)
        default_time = (17, 0)
    elif g.get("eom"):
        last = calendar.monthrange(base.year, base.month)[1]
        day = base.replace(day=last)
        default_time = (17, 0)
    elif g.get("rel"):
        rel = g["rel"].lower()
        day = base + timedelta(days=1 if rel == "tomorrow" else 0)
        if rel == "tonight":
            default_time = (21, 0)
    elif g.get("wd"):
        target = _WEEKDAYS.get(g["wd"].lower().rstrip("."))
        if target is None:
            return None
        ahead = (target - base.weekday()) % 7
        if (g.get("next") or "").lower() == "next" and ahead == 0:
            ahead = 7
        day = base + timedelta(days=ahead)
    elif g.get("md_mon") or g.get("dm_mon"):
        mon = _MONTHS.get((g.get("md_mon") or g.get("dm_mon")).lower().rstrip("."))
        dd = int(g.get("md_day") or g.get("dm_day"))
        yy = g.get("md_year") or g.get("dm_year")
        if mon is None:
            return None
        if yy:
            day = _safe_date(int(yy), mon, dd)
        else:
            day = _safe_date(base.year, mon, dd)
            if day is not None and day < base - timedelta(days=7):
                day = _safe_date(base.year + 1, mon, dd)  # "Jan 5" sent in December
    if day is None:
        return None
    hm = _parse_time(g.get("tpost")) or _parse_time(g.get("tpre")) or default_time
    return day.replace(hour=hm[0], minute=hm[1])


def extract_deadline(text: Any, sent_at: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """First deadline ask in ``text`` resolved against ``sent_at`` (epoch).

    Returns ``{"due_at": epoch, "due_date": "YYYY-MM-DDTHH:MM", "phrase": str}``
    or None. Deadlines before the send time (historical) or more than
    ``MAX_AHEAD_DAYS`` after it are ignored.
    """
    if not text or not isinstance(text, str):
        return None
    text = _CTRL_RE.sub(" ", text[:MAX_SCAN_CHARS])
    sent_ts = float(sent_at) if sent_at else datetime.now().timestamp()
    sent = datetime.fromtimestamp(sent_ts)
    best = None
    for pat in _PATTERNS:
        for m in pat.finditer(text):
            due = _resolve(m, sent)
            if due is None:
                continue
            due_ts = due.timestamp()
            if due_ts < sent_ts - 3600 or due_ts > sent_ts + MAX_AHEAD_DAYS * 86400:
                continue
            if best is None or m.start() < best[0]:
                phrase = re.sub(r"\s+", " ", m.group("phrase")).strip(" ,.;:")
                best = (m.start(), due, phrase[:80])
            break  # earliest valid match per pattern is enough
    if best is None:
        return None
    _, due, phrase = best
    return {"due_at": due.timestamp(), "due_date": due.strftime("%Y-%m-%dT%H:%M"), "phrase": phrase}


# ── Email Tags state → note-shaped items for deadline_watch ─────────────────

_SKIP_TAGS = {"marketing", "newsletter", "promo", "shopping", "social"}


def email_deadline_notes(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Note-shaped dicts for unread, non-bulk emails that carry a deadline.

    ``id`` is ``email:<account>:<uid>`` so dedup signatures never collide
    with note ids; ``due_date`` is the resolved local time string.
    """
    out: List[Dict[str, Any]] = []
    per_uid = state.get("per_uid") if isinstance(state, dict) else None
    if not isinstance(per_uid, dict):
        return out
    for key, v in per_uid.items():
        if not isinstance(v, dict):
            continue
        dl = v.get("deadline")
        if not isinstance(dl, dict) or not dl.get("due_date"):
            continue
        if v.get("spam"):
            continue
        tags = v.get("tags") or []
        if isinstance(tags, list) and _SKIP_TAGS.intersection(str(t).lower() for t in tags):
            continue
        sender = str(v.get("from") or "").strip()
        subject = str(v.get("subject") or "").strip() or "(no subject)"
        phrase = str(dl.get("phrase") or "").strip()
        title = f"Email from {sender}: {subject}" if sender else f"Email: {subject}"
        if phrase:
            title += f' — "{phrase}"'
        out.append({
            "id": f"email:{key}", "title": title, "content": "",
            "due_date": str(dl["due_date"]), "archived": False,
            "repeat": "none", "items": None, "source": "email",
        })
    return out


def _urgency_state_path(owner: str, base_dir: Optional[str] = None) -> str:
    # Same slug rule as action_check_email_urgency / GET /api/email/urgency-state.
    slug = "".join(c if (c.isalnum() or c in "-_.@") else "_" for c in (owner or "default"))
    return os.path.join(base_dir or "data", f"email_urgency_state_{slug}.json")


def load_owner_email_deadlines(owner: str, base_dir: Optional[str] = None) -> List[Dict[str, Any]]:
    """Email deadline items for ``owner`` from the Email Tags scan state.

    A state file written for a different owner (slug collision) is ignored.
    Missing/unreadable state → [] (Email Tags off, or not run yet).
    """
    path = _urgency_state_path(owner, base_dir)
    try:
        with open(path, "r", encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, ValueError):
        return []
    if not isinstance(state, dict):
        return []
    if str(state.get("owner") or "") != str(owner or ""):
        return []
    return email_deadline_notes(state)
