"""
case_nudges.py — notice open Case Files that have gone quiet, before the user
has to remember them.

An open case is *stale* when nothing has touched it (no finding, step, link or
status change) for ``stale_days`` and no live goal is driving it. Each stale
case gets one concrete suggestion:

* it has open steps   → "Drive with goal" (a goal works the steps for you)
* it has no open step → the first suggested pivot, or "add a next step"

``stale_cases`` feeds the daily brief; ``due_nudges`` feeds the
``check_stale_cases`` scheduled action and remembers what it already said
(per owner, in a dotfile next to the owner's cases) so a case is re-nudged
only when it went active and quiet again, or after ``renotify_days``.

Case text is user/web data: titles and step text are cleaned and capped
before they reach a notification. Stdlib only; store, goal API, clock and
state path are injectable for tests.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from typing import Any, Dict, List, Optional

DAY = 86400.0
DEFAULT_STALE_DAYS = 3.0
DEFAULT_RENOTIFY_DAYS = 7.0
STATE_FILE = ".nudges.json"
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")


def _clean(text: Any, limit: int = 120) -> str:
    s = " ".join(_CTRL_RE.sub(" ", str(text or "")).split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _store(store=None):
    if store is not None:
        return store
    from src.casefiles import get_case_store
    return get_case_store()


def _driven_case_id(owner: str, goal_api=None) -> Optional[str]:
    """Case id the owner's live goal is bound to, if any. Never raises."""
    try:
        if goal_api is None:
            from src import goal as goal_api  # type: ignore[no-redef]
        goal = goal_api.get_goal(owner)
    except Exception:
        return None
    if goal and goal.get("phase") in ("active", "paused"):
        return goal.get("case_id") or None
    return None


def _ago(days: float) -> str:
    d = int(days)
    return "today" if d < 1 else ("1 day ago" if d == 1 else f"{d} days ago")


def stale_cases(owner: str, *, store=None, goal_api=None, now: Optional[float] = None,
                stale_days: float = DEFAULT_STALE_DAYS, limit: int = 10) -> List[Dict[str, Any]]:
    """Open, un-driven cases idle for ``stale_days`` or more, most idle first.

    Each item: ``id, title, idle_days, updated_at, open_steps, oldest_step,
    action ("drive"|"pivot"|"add_step"), suggestion``.
    """
    if not owner:
        return []
    st = _store(store)
    now = time.time() if now is None else now
    driven = _driven_case_id(owner, goal_api)
    out: List[Dict[str, Any]] = []
    for meta in st.list_cases(owner, status="open"):
        if meta["id"] == driven:
            continue
        updated = float(meta.get("updated_at") or 0)
        idle = (now - updated) / DAY
        if idle < stale_days:
            continue
        try:
            case = st.get_case(owner, meta["id"])
        except Exception:
            continue
        open_steps = [s for s in case.get("next_steps") or [] if not s.get("done")]
        title = _clean(case.get("title"), 80)
        item: Dict[str, Any] = {
            "id": case["id"], "title": title, "idle_days": round(idle, 1),
            "updated_at": updated, "open_steps": len(open_steps),
            "oldest_step": _clean(open_steps[0].get("text")) if open_steps else "",
        }
        if open_steps:
            item["action"] = "drive"
            item["suggestion"] = (f'{len(open_steps)} open step(s), first: "{item["oldest_step"]}" — '
                                  f'drive it with a goal (case_file op=drive case={case["id"]})')
        else:
            pivot = ""
            try:
                pivots = st.suggest_pivots(owner, case["id"], limit=1)
                pivot = _clean(pivots[0]) if pivots else ""
            except Exception:
                pivot = ""
            if pivot:
                item["action"] = "pivot"
                item["suggestion"] = f"no next step queued — try: {pivot}"
            else:
                item["action"] = "add_step"
                item["suggestion"] = "no next step queued — add one, or close the case if it is done"
        out.append(item)
    out.sort(key=lambda c: c["idle_days"], reverse=True)
    return out[:limit]


def format_nudges(items: List[Dict[str, Any]], header: str = "Cases going cold:") -> str:
    if not items:
        return ""
    lines = [header]
    for c in items:
        lines.append(f"  · {c['title']} — last touched {_ago(c['idle_days'])}; {c['suggestion']}")
    return "\n".join(lines)


# ── dedup state for the scheduled action ────────────────────────────────────

def _state_path(owner: str, store) -> str:
    return os.path.join(store._owner_dir(owner), STATE_FILE)


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
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".nudges-", suffix=".tmp")
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


def due_nudges(owner: str, *, store=None, goal_api=None, now: Optional[float] = None,
               stale_days: float = DEFAULT_STALE_DAYS, renotify_days: float = DEFAULT_RENOTIFY_DAYS,
               state_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Stale cases not yet nudged for this quiet spell, and record them.

    A case is re-nudged when it was touched since the last nudge and went
    quiet again, or when ``renotify_days`` passed without a response.
    Entries for cases that are no longer stale are dropped from the state.
    """
    st = _store(store)
    now = time.time() if now is None else now
    stale = stale_cases(owner, store=st, goal_api=goal_api, now=now,
                        stale_days=stale_days, limit=50)
    path = state_path or _state_path(owner, st)
    state = _load_state(path)
    due = []
    new_state: Dict[str, Any] = {}
    for c in stale:
        prev = state.get(c["id"]) or {}
        same_spell = abs(float(prev.get("updated_at") or -1) - c["updated_at"]) < 1e-6
        recent = now - float(prev.get("at") or 0) < renotify_days * DAY
        if prev and same_spell and recent:
            new_state[c["id"]] = prev
            continue
        due.append(c)
        new_state[c["id"]] = {"at": now, "updated_at": c["updated_at"]}
    if new_state != state:
        _save_state(path, new_state)
    return due
