"""
deadline_watch.py — follow up on deadlines before and after they pass.

Notes can carry a ``due_date``. ``action_ping_notes`` fires one reminder in
the minute the note is due — and if that ping is missed (phone asleep, app
offline, notification swiped away) nothing ever mentions the note again.
This module looks at every open note with a due date and sorts it into:

* ``overdue``  — the due time passed and the note is still open (not
                 archived; a checklist still has unchecked items). Only
                 the last ``overdue_days`` count; older ones are summarized
                 as "long overdue" so a forgotten backlog does not flood
                 the brief.
* ``due_soon`` — due within ``horizon_hours`` from now.

Recurring notes that are past due are skipped: the Notes UI rolls their
due date forward on its own, so they are not "missed".

``deadline_brief_lines`` feeds the daily brief. ``due_deadline_alerts``
feeds the ``check_deadlines`` scheduled action and remembers what it already
said (per owner) so each note is reported once per state and due date:
moving a note's due date, or a due-soon note becoming overdue, is new news.

Note titles and bodies are user text: cleaned and capped before they reach
a notification. Stdlib only; notes, clock and state path are injectable.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

HOUR = 3600.0
DAY = 86400.0
DEFAULT_HORIZON_HOURS = 48.0
DEFAULT_ALERT_HORIZON_HOURS = 24.0
DEFAULT_OVERDUE_DAYS = 30.0
BRIEF_LIMIT = 8
ALERT_LIMIT = 10
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")
_DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _clean(text: Any, limit: int = 100) -> str:
    s = " ".join(_CTRL_RE.sub(" ", str(text or "")).split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


def parse_due(value: Any) -> Optional[float]:
    """Due date → epoch seconds, or None if unparseable.

    Accepts what the Notes UI and agent tools store: ``2026-05-29T16:31``
    (server-local), ``...Z`` / ``+02:00`` (aware) and ``2026-05-29``
    (date only → end of that day, local).
    """
    s = str(value or "").strip()
    if not s:
        return None
    try:
        if _DATE_ONLY_RE.match(s):
            d = datetime.fromisoformat(s).replace(hour=23, minute=59, second=59)
            return d.timestamp()
        if s.endswith("Z") or s.endswith("z"):
            return datetime.fromisoformat(s[:-1]).replace(tzinfo=timezone.utc).timestamp()
        return datetime.fromisoformat(s).timestamp()
    except (ValueError, OverflowError, OSError):
        return None


def _pending_items(note: Dict[str, Any]) -> Optional[List[str]]:
    """Unchecked checklist entries, or None when the note has no items."""
    raw = note.get("items")
    if not raw:
        return None
    try:
        items = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return None
    if not isinstance(items, list) or not items:
        return None
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        if it.get("done") or it.get("checked"):
            continue
        out.append(_clean(it.get("text"), 80))
    return out


def _span(seconds: float) -> str:
    seconds = abs(seconds)
    if seconds < HOUR:
        m = max(1, int(seconds / 60))
        return "1 minute" if m == 1 else f"{m} minutes"
    if seconds < 2 * DAY:
        h = int(seconds / HOUR)
        return "1 hour" if h == 1 else f"{h} hours"
    d = int(seconds / DAY)
    return f"{d} days"


def assess_notes(notes: Iterable[Dict[str, Any]], *, now: Optional[float] = None,
                 horizon_hours: float = DEFAULT_HORIZON_HOURS,
                 overdue_days: float = DEFAULT_OVERDUE_DAYS) -> Dict[str, Any]:
    """Classify open notes with a due date.

    Returns ``{"items": [...], "long_overdue": n}``; items are sorted by due
    time (overdue first) and look like ``{kind, note_id, title, due_date,
    due_at, pending, message}``.
    """
    now = time.time() if now is None else now
    items: List[Dict[str, Any]] = []
    long_overdue = 0
    for note in notes or []:
        if not isinstance(note, dict) or not note.get("id"):
            continue
        if note.get("archived"):
            continue
        due_at = parse_due(note.get("due_date"))
        if due_at is None:
            continue
        pending = _pending_items(note)
        if pending is not None and not pending:
            continue  # checklist fully checked off: done
        delta = due_at - now
        recurring = (note.get("repeat") or "none") != "none"
        if delta < 0:
            if recurring:
                continue
            if -delta > overdue_days * DAY:
                long_overdue += 1
                continue
            kind = "overdue"
        elif delta <= horizon_hours * HOUR:
            kind = "due_soon"
        else:
            continue
        title = _clean(note.get("title"), 100) or _clean(note.get("content"), 60) or "(untitled note)"
        extra = f" ({len(pending)} open item{'s' if len(pending) != 1 else ''})" if pending else ""
        if kind == "overdue":
            message = f'Overdue by {_span(delta)}: "{title}"{extra}'
        else:
            message = f'Due in {_span(delta)}: "{title}"{extra}'
        items.append({
            "kind": kind, "note_id": str(note["id"]), "title": title,
            "due_date": str(note.get("due_date")), "due_at": due_at,
            "pending": pending or [], "message": message,
        })
    items.sort(key=lambda i: (0 if i["kind"] == "overdue" else 1, i["due_at"]))
    return {"items": items, "long_overdue": long_overdue}


def deadline_brief_lines(notes: Iterable[Dict[str, Any]], *, now: Optional[float] = None,
                         horizon_hours: float = DEFAULT_HORIZON_HOURS,
                         limit: int = BRIEF_LIMIT) -> List[str]:
    """Lines for the daily brief, or [] when nothing is overdue or due soon."""
    res = assess_notes(notes, now=now, horizon_hours=horizon_hours)
    items, old = res["items"], res["long_overdue"]
    if not items and not old:
        return []
    n_over = sum(1 for i in items if i["kind"] == "overdue")
    n_soon = len(items) - n_over
    head = f"Deadlines: {n_over} overdue, {n_soon} due in the next {int(horizon_hours)}h"
    lines = [head]
    for i in items[:limit]:
        lines.append(f"  · {i['message']}")
    if len(items) > limit:
        lines.append(f"  … and {len(items) - limit} more")
    if old:
        lines.append(f"  ({old} note(s) overdue for more than {int(DEFAULT_OVERDUE_DAYS)} days — "
                     f"archive them or set a new date)")
    return lines


# ── dedup state for the scheduled action ────────────────────────────────────

def _state_path(owner: str) -> str:
    from src.constants import DATA_DIR
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", str(owner or "default"))
    return os.path.join(DATA_DIR, "deadline_alerts", f"{safe}.json")


def _load_state(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(path: str, state: Dict[str, Any]) -> None:
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".deadline-alerts-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _signature(item: Dict[str, Any]) -> str:
    return f'{item["note_id"]}|{item["kind"]}|{item["due_date"]}'


def due_deadline_alerts(owner: str, notes: Iterable[Dict[str, Any]], *,
                        now: Optional[float] = None,
                        horizon_hours: float = DEFAULT_ALERT_HORIZON_HOURS,
                        state_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Deadline items not yet reported for this note/state/due date; records them.

    State only keeps signatures of notes still in play, so it stays small and
    a note that leaves and re-enters a state (new due date) is reported again.
    """
    now = time.time() if now is None else now
    items = assess_notes(notes, now=now, horizon_hours=horizon_hours)["items"]
    path = state_path or _state_path(owner)
    state = _load_state(path)
    live = {_signature(i) for i in items}
    fresh = [i for i in items if _signature(i) not in state]
    new_state = {k: v for k, v in state.items() if k in live}
    for i in fresh:
        new_state[_signature(i)] = now
    if new_state != state:
        _save_state(path, new_state)
    return fresh


def format_deadline_alerts(items: List[Dict[str, Any]], limit: int = ALERT_LIMIT) -> str:
    n_over = sum(1 for i in items if i["kind"] == "overdue")
    n_soon = len(items) - n_over
    parts = []
    if n_over:
        parts.append(f"{n_over} overdue")
    if n_soon:
        parts.append(f"{n_soon} due soon")
    lines = [f"Deadlines: {', '.join(parts)}."]
    lines += [f"- {i['message']}" for i in items[:limit]]
    if len(items) > limit:
        lines.append(f"…and {len(items) - limit} more")
    lines.append("Open Notes to finish, reschedule or archive them.")
    return "\n".join(lines)


# ── DB loader (lazy; keeps the module importable without SQLAlchemy) ────────

def load_owner_notes(owner: str) -> List[Dict[str, Any]]:
    """Open notes with a due date for ``owner``, as plain dicts.

    Null-owner (legacy) notes are only included on single-user installs,
    matching the daily brief's rule.
    """
    from core.database import SessionLocal, Note
    from src.auth_helpers import owner_filter
    try:
        from core.auth import AuthManager
        allow_null = not AuthManager().is_configured
    except Exception:
        allow_null = False
    db = SessionLocal()
    try:
        q = db.query(Note).filter(Note.archived == False)  # noqa: E712
        q = q.filter(Note.due_date.isnot(None), Note.due_date != "")
        if owner:
            q = owner_filter(q, Note, owner, include_shared=allow_null)
        return [{
            "id": n.id, "title": n.title, "content": n.content, "items": n.items,
            "due_date": n.due_date, "archived": bool(n.archived),
            "repeat": n.repeat or "none", "note_type": n.note_type,
        } for n in q.all()]
    finally:
        db.close()
