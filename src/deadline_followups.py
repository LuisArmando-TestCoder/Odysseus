"""
deadline_followups.py — "snooze" and "mark done" for Deadline Watch.

Deadline Watch (``src/deadline_watch.py``) follows up on notes and email asks
with a due date until they are finished. Sometimes the user already knows:
the invoice is paid outside Odysseus, the reply went from the phone, the
report will be late and that is fine. Without a way to say so, the same item
keeps coming back in every daily brief and alert.

This module keeps a small per-owner state of those answers:

* **done**    — stop following up on this deadline. Keyed by item id *and*
                due date, so giving the note a new due date brings it back.
                Nothing in the note or the mailbox is changed.
* **snoozed** — hide it until a time (tomorrow 08:00, in 3 days, in a week).
                When the snooze runs out the item is reported again.

``apply_followups`` splits a list of Deadline Watch items into what should
still be shown and what is being held. ``prune_followups`` drops answers for
items that left play (archived, re-dated, email opened) and expired snoozes.

Stdlib only; clock and state path are injectable.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote

DAY = 86400.0
MAX_ENTRIES = 500
MORNING_HOUR = 8  # "tomorrow" = next local 08:00 (Deadline Watch runs 08:41)
SNOOZE_PRESETS = ("tomorrow", "3d", "1w")
_ID_RE = re.compile(r"^[A-Za-z0-9:@._+\-]{1,200}$")
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")


def valid_item_id(item_id: Any) -> bool:
    return isinstance(item_id, str) and bool(_ID_RE.match(item_id))


def followup_key(item_id: str, due_date: Any) -> str:
    return f"{item_id}|{str(due_date or '').strip()}"


def _clean_title(text: Any, limit: int = 140) -> str:
    s = " ".join(_CTRL_RE.sub(" ", str(text or "")).split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


def snooze_until(preset: str, now: Optional[float] = None) -> float:
    """Epoch seconds a snooze preset runs until. ValueError on unknown preset."""
    now = time.time() if now is None else now
    if preset == "tomorrow":
        local = datetime.fromtimestamp(now)
        nxt = (local + timedelta(days=1)).replace(hour=MORNING_HOUR, minute=0,
                                                  second=0, microsecond=0)
        return nxt.timestamp()
    if preset == "3d":
        return now + 3 * DAY
    if preset == "1w":
        return now + 7 * DAY
    raise ValueError(f"unknown snooze '{preset}' — use one of: {', '.join(SNOOZE_PRESETS)}")


# ── state ────────────────────────────────────────────────────────────────────

def state_path(owner: str) -> str:
    from src.constants import DATA_DIR
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", str(owner or "default"))
    return os.path.join(DATA_DIR, "deadline_followups", f"{safe}.json")


def load_followups(owner: str, path: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """The owner's follow-up answers; {} when none or unreadable. A file
    written for another owner (slug collision) is ignored."""
    p = path or state_path(owner)
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or str(data.get("owner") or "") != str(owner or ""):
        return {}
    entries = data.get("entries")
    if not isinstance(entries, dict):
        return {}
    return {k: v for k, v in entries.items() if isinstance(k, str) and isinstance(v, dict)}


def save_followups(owner: str, entries: Dict[str, Dict[str, Any]],
                   path: Optional[str] = None) -> None:
    p = path or state_path(owner)
    if len(entries) > MAX_ENTRIES:  # keep the newest answers
        keep = sorted(entries.items(), key=lambda kv: kv[1].get("at") or 0)[-MAX_ENTRIES:]
        entries = dict(keep)
    d = os.path.dirname(p)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".deadline-followups-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"owner": str(owner or ""), "entries": entries}, f)
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _status(entry: Dict[str, Any], now: float) -> Optional[str]:
    if entry.get("done"):
        return "done"
    until = entry.get("until")
    if isinstance(until, (int, float)) and until > now:
        return "snoozed"
    return None


def apply_followups(items: Iterable[Dict[str, Any]], entries: Dict[str, Dict[str, Any]],
                    now: Optional[float] = None) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Split Deadline Watch items into (visible, held). Held items get a
    ``followup`` dict: ``{"status": "done"|"snoozed", "until": epoch|None}``."""
    now = time.time() if now is None else now
    visible: List[Dict[str, Any]] = []
    held: List[Dict[str, Any]] = []
    for it in items or []:
        entry = entries.get(followup_key(it.get("note_id", ""), it.get("due_date"))) if entries else None
        st = _status(entry, now) if entry else None
        if st:
            held.append({**it, "followup": {"status": st,
                                            "until": entry.get("until") if st == "snoozed" else None}})
        else:
            visible.append(it)
    return visible, held


def prune_followups(entries: Dict[str, Dict[str, Any]], live_keys: Iterable[str],
                    now: Optional[float] = None) -> Dict[str, Dict[str, Any]]:
    """Keep answers for items still in play; drop expired snoozes."""
    now = time.time() if now is None else now
    live = set(live_keys)
    return {k: v for k, v in entries.items() if k in live and _status(v, now)}


# ── mutations (owner's file only; callers check the item is the owner's) ────

def _set(owner: str, item_id: str, due_date: Any, entry: Optional[Dict[str, Any]],
         path: Optional[str]) -> Dict[str, Dict[str, Any]]:
    if not valid_item_id(item_id):
        raise ValueError("invalid deadline id")
    if not str(due_date or "").strip():
        raise ValueError("due_date is required")
    entries = load_followups(owner, path)
    key = followup_key(item_id, due_date)
    if entry is None:
        entries.pop(key, None)
    else:
        entries[key] = entry
    save_followups(owner, entries, path)
    return entries


def snooze_deadline(owner: str, item_id: str, due_date: Any, preset: str, *,
                    title: str = "", now: Optional[float] = None,
                    path: Optional[str] = None) -> Dict[str, Any]:
    now = time.time() if now is None else now
    until = snooze_until(preset, now)
    entry = {"until": until, "done": False, "at": now, "title": _clean_title(title)}
    _set(owner, item_id, due_date, entry, path)
    return {"id": item_id, "due_date": str(due_date), "status": "snoozed", "until": until}


def mark_deadline_done(owner: str, item_id: str, due_date: Any, *, title: str = "",
                       now: Optional[float] = None, path: Optional[str] = None) -> Dict[str, Any]:
    now = time.time() if now is None else now
    entry = {"until": None, "done": True, "at": now, "title": _clean_title(title)}
    _set(owner, item_id, due_date, entry, path)
    return {"id": item_id, "due_date": str(due_date), "status": "done"}


def restore_deadline(owner: str, item_id: str, due_date: Any, *,
                     path: Optional[str] = None) -> Dict[str, Any]:
    """Undo a snooze / done: Deadline Watch follows up on it again."""
    _set(owner, item_id, due_date, None, path)
    return {"id": item_id, "due_date": str(due_date), "status": "active"}


def deadlines_link(base_url: str = "", item_id: Optional[str] = None) -> str:
    """Link to the Deadlines page, optionally highlighting one item. Opening
    it never changes anything — snooze / done are buttons on the page."""
    q = "/deadlines"
    if item_id and valid_item_id(item_id):
        q += "?item=" + quote(item_id, safe="")
    return (base_url or "").rstrip("/") + q


# ── page / API helpers (notes are injected; routes load them from the DB) ────

PAGE_HORIZON_HOURS = 7 * 24.0


def _public(item: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: item.get(k) for k in ("note_id", "kind", "title", "message", "due_date",
                                    "due_at", "source", "pending")}
    out["id"] = out.pop("note_id")
    if item.get("followup"):
        out["followup"] = item["followup"]
    return out


def deadline_overview(notes: Iterable[Dict[str, Any]], entries: Dict[str, Dict[str, Any]], *,
                      now: Optional[float] = None,
                      horizon_hours: float = PAGE_HORIZON_HOURS) -> Dict[str, Any]:
    """What the Deadlines page shows: open items, held (snoozed / done) ones."""
    from src.deadline_watch import assess_notes
    now = time.time() if now is None else now
    res = assess_notes(notes, now=now, horizon_hours=horizon_hours)
    visible, held = apply_followups(res["items"], entries, now)
    return {
        "items": [_public(i) for i in visible],
        "held": [_public(i) for i in held],
        "long_overdue": res["long_overdue"],
        "horizon_hours": horizon_hours,
        "presets": list(SNOOZE_PRESETS),
        "now": now,
    }


ACTIONS = ("snooze", "done", "restore")


def apply_action(owner: str, action: str, body: Dict[str, Any],
                 notes: Iterable[Dict[str, Any]], *, now: Optional[float] = None,
                 path: Optional[str] = None) -> Dict[str, Any]:
    """Snooze / done / restore one of the owner's current deadlines.

    The (id, due_date) pair must be one of the owner's deadline items right
    now (``notes`` are already owner-scoped) — KeyError otherwise — so the
    state only ever holds answers about the owner's own deadlines.
    """
    from src.deadline_watch import assess_notes
    now = time.time() if now is None else now
    if action not in ACTIONS:
        raise ValueError(f"unknown action '{action}'")
    item_id = body.get("id")
    due_date = body.get("due_date")
    if not valid_item_id(item_id) or not isinstance(due_date, str) or not due_date.strip():
        raise ValueError("id and due_date are required")
    if action == "restore":
        if followup_key(item_id, due_date) not in load_followups(owner, path):
            raise KeyError("that deadline is not snoozed or done")
        return restore_deadline(owner, item_id, due_date, path=path)
    items = assess_notes(notes, now=now, horizon_hours=PAGE_HORIZON_HOURS)["items"]
    match = next((i for i in items if i["note_id"] == item_id and i["due_date"] == due_date), None)
    if match is None:
        raise KeyError("deadline not found — it may be finished or re-dated; reload")
    if action == "done":
        return mark_deadline_done(owner, item_id, due_date, title=match["title"], now=now, path=path)
    return snooze_deadline(owner, item_id, due_date, str(body.get("preset") or ""),
                           title=match["title"], now=now, path=path)


# ── sidebar badge (main SPA) ─────────────────────────────────────────────────

BADGE_SOON_HOURS = 24.0


def deadline_status(notes: Iterable[Dict[str, Any]], entries: Dict[str, Dict[str, Any]], *,
                    now: Optional[float] = None) -> Dict[str, Any]:
    """Compact counts for the "Deadlines" entry in the main app's sidebar.

    ``attention`` (the badge) is the number of overdue items still being
    followed up — snoozed / done ones are held and not counted. ``label`` is
    a one-line tooltip such as "Deadlines — 2 overdue · 1 due in 24h". The
    next item (overdue first, then soonest) is included so the tooltip can
    name it.
    """
    from src.deadline_watch import assess_notes
    now = time.time() if now is None else now
    res = assess_notes(notes, now=now, horizon_hours=BADGE_SOON_HOURS)
    visible, held = apply_followups(res["items"], entries, now)
    overdue = sum(1 for i in visible if i["kind"] == "overdue")
    due_soon = sum(1 for i in visible if i["kind"] == "due_soon")
    snoozed = sum(1 for i in held if (i.get("followup") or {}).get("status") == "snoozed")
    parts = []
    if overdue:
        parts.append(f"{overdue} overdue")
    if due_soon:
        parts.append(f"{due_soon} due in 24h")
    if snoozed:
        parts.append(f"{snoozed} snoozed")
    label = "Deadlines — " + (" · ".join(parts) if parts else "nothing due in the next 24h")
    nxt = None
    if visible:
        first = visible[0]
        nxt = {"id": first["note_id"], "kind": first["kind"], "title": first["title"],
               "message": _clean_title(first.get("message"), 160),
               "link": deadlines_link("", first["note_id"])}
        label += f'\nNext: {nxt["message"]}'
    return {
        "attention": overdue,
        "overdue": overdue,
        "due_soon": due_soon,
        "snoozed": snoozed,
        "long_overdue": res["long_overdue"],
        "next": nxt,
        "label": label,
    }
