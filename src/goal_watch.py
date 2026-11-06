"""
goal_watch.py — tell the owner when their goal needs them, without them
having to keep the goal card open.

A goal runs unattended, round after round. When it stops on its own the only
signal today is the sidebar card, which nobody sees when they are away. This
module turns the goal's state into one short, actionable line:

* ``blocked``  — the goal stopped itself (round cap, 3 failed rounds, or the
                 model reported a blocker); says why and how to unblock.
* ``complete`` — the goal finished; points at the chat with the result.
* ``stalled``  — active and armed, but no round has run for ``stalled_hours``
                 and nothing is running (chat deleted, model down, …).
* ``paused``   — paused for ``paused_days`` or more; resume or finish it.

``goal_status_line`` feeds the daily brief (always one line while a goal
exists and is not long finished). ``due_goal_alerts`` feeds the
``check_goals`` scheduled action and remembers what it already said (per
owner, in a dotfile next to the goal files) so each state is reported once
per spell: a goal that is resumed and blocks again is reported again.

The objective and blocked reason are user/model text: they are cleaned and
capped before they reach a notification. Stdlib only; goal API, run probe,
clock and state path are injectable for tests.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from typing import Any, Callable, Dict, List, Optional

HOUR = 3600.0
DAY = 86400.0
DEFAULT_STALLED_HOURS = 6.0
DEFAULT_PAUSED_DAYS = 2.0
# A finished goal stays in the brief for a day, then drops out.
COMPLETE_BRIEF_HOURS = 24.0
STATE_FILE = ".goal_alerts.json"
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")


def _clean(text: Any, limit: int = 140) -> str:
    s = " ".join(_CTRL_RE.sub(" ", str(text or "")).split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _goal_api(goal_api=None):
    if goal_api is not None:
        return goal_api
    from src import goal as goal_mod
    return goal_mod


def _default_is_running(session_id: Optional[str]) -> bool:
    if not session_id:
        return False
    try:
        from src import agent_runs
        return bool(agent_runs.is_active(session_id))
    except Exception:
        return False


def _blocked_reason(goal: Dict[str, Any]) -> str:
    r = goal.get("blockedReason")
    if isinstance(r, dict):
        r = r.get("message") or r.get("code")
    return _clean(r, 160)


def _ago(seconds: float) -> str:
    h = seconds / HOUR
    if h < 1:
        return "under an hour"
    if h < 48:
        n = int(h)
        return "1 hour" if n == 1 else f"{n} hours"
    n = int(seconds / DAY)
    return f"{n} days"


def assess_goal(goal: Optional[Dict[str, Any]], *, now: Optional[float] = None,
                is_running: Optional[Callable[[Optional[str]], bool]] = None,
                stalled_hours: float = DEFAULT_STALLED_HOURS,
                paused_days: float = DEFAULT_PAUSED_DAYS) -> Optional[Dict[str, Any]]:
    """Classify a goal into something the owner should hear about, or None.

    Returns ``{kind, goal_id, objective, rounds, max_rounds, updated_at,
    idle_seconds, case_id, message}`` where ``kind`` is one of
    ``blocked | complete | stalled | paused``.
    """
    if not isinstance(goal, dict) or not goal.get("id"):
        return None
    now = time.time() if now is None else now
    is_running = is_running or _default_is_running
    phase = goal.get("phase")
    updated = float(goal.get("updatedAt") or goal.get("createdAt") or now)
    idle = max(0.0, now - updated)
    objective = _clean(goal.get("objective"), 120) or "(no objective)"
    rounds = int(goal.get("roundsStarted") or 0)
    max_rounds = int(goal.get("maxGoalRounds") or 256)
    item: Dict[str, Any] = {
        "goal_id": goal["id"], "objective": objective, "rounds": rounds,
        "max_rounds": max_rounds, "updated_at": updated, "idle_seconds": idle,
        "case_id": goal.get("case_id") or None,
    }
    if phase == "blocked":
        reason = _blocked_reason(goal) or "no reason given"
        if "maxgoalrounds" in reason.lower().replace(" ", ""):
            fix = f"it used all {max_rounds} rounds — raise the round limit or narrow the objective, then Resume"
        elif "consecutive" in reason.lower() or "failed" in reason.lower():
            fix = "rounds kept failing — check the model/provider is up, then Resume"
        else:
            fix = "answer the blocker in the goal's chat or edit the objective, then Resume"
        item.update(kind="blocked", reason=reason,
                    message=f'Goal blocked: "{objective}" — {reason}. Next: {fix}.')
        return item
    if phase == "complete":
        item.update(kind="complete",
                    message=f'Goal complete: "{objective}" after {rounds} round(s) — the result is in its chat.')
        return item
    if phase == "paused":
        if idle < paused_days * DAY:
            return None
        item.update(kind="paused",
                    message=(f'Goal paused for {_ago(idle)}: "{objective}" ({rounds}/{max_rounds} rounds). '
                             f"Resume it or mark it done so it stops waiting."))
        return item
    if phase == "active":
        if goal.get("activation") != "armed":
            return None
        if float(goal.get("holdUntil") or 0) > now:
            return None
        if idle < stalled_hours * HOUR:
            return None
        if is_running(goal.get("session_id")):
            return None
        where = "its chat may have been deleted" if not goal.get("session_id") else "the model may be failing or the app was offline"
        item.update(kind="stalled",
                    message=(f'Goal stalled: "{objective}" — no round in {_ago(idle)} ({rounds}/{max_rounds} done); '
                             f"{where}. Open the goal card and Resume, or pause it."))
        return item
    return None


def goal_status_line(owner: str, *, goal_api=None, now: Optional[float] = None,
                     is_running: Optional[Callable[[Optional[str]], bool]] = None) -> str:
    """One line for the daily brief describing the owner's goal, or ''."""
    if not owner:
        return ""
    try:
        goal = _goal_api(goal_api).get_goal(owner)
    except Exception:
        return ""
    if not isinstance(goal, dict) or not goal.get("id"):
        return ""
    now = time.time() if now is None else now
    alert = assess_goal(goal, now=now, is_running=is_running)
    if alert:
        if alert["kind"] == "complete" and alert["idle_seconds"] > COMPLETE_BRIEF_HOURS * HOUR:
            return ""
        return "Goal: " + alert["message"]
    objective = _clean(goal.get("objective"), 120) or "(no objective)"
    phase = goal.get("phase") or "active"
    rounds = int(goal.get("roundsStarted") or 0)
    max_rounds = int(goal.get("maxGoalRounds") or 256)
    return f'Goal: "{objective}" — {phase}, {rounds}/{max_rounds} rounds.'


# ── dedup state for the scheduled action ────────────────────────────────────

def _state_path(owner: str) -> str:
    from src.constants import DATA_DIR
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", str(owner or "default"))
    return os.path.join(DATA_DIR, "goals", f".{safe}{STATE_FILE}")


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
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".goal-alerts-", suffix=".tmp")
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


def _signature(alert: Dict[str, Any]) -> str:
    # updated_at changes on every round / resume / edit, so a goal that goes
    # quiet (or blocks) again after being worked on is a new spell.
    return f'{alert["goal_id"]}|{alert["kind"]}|{int(alert["updated_at"])}'


def due_goal_alerts(owner: str, *, goal_api=None, now: Optional[float] = None,
                    is_running: Optional[Callable[[Optional[str]], bool]] = None,
                    stalled_hours: float = DEFAULT_STALLED_HOURS,
                    paused_days: float = DEFAULT_PAUSED_DAYS,
                    state_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """The goal alert not yet sent for this spell (0 or 1 items); records it."""
    if not owner:
        return []
    try:
        goal = _goal_api(goal_api).get_goal(owner)
    except Exception:
        return []
    now = time.time() if now is None else now
    alert = assess_goal(goal, now=now, is_running=is_running,
                        stalled_hours=stalled_hours, paused_days=paused_days)
    if not alert:
        return []
    path = state_path or _state_path(owner)
    state = _load_state(path)
    sig = _signature(alert)
    if sig in state:
        return []
    # Keep only the latest entry: one goal per owner, old spells are moot.
    _save_state(path, {sig: now})
    return [alert]


def format_goal_alerts(items: List[Dict[str, Any]], links: bool = False, base_url: str = "") -> str:
    """Alert lines. With ``links``, a goal that drives a case gets a deep link
    to that case on the Intel Board — with a one-click Resume offer when it is
    blocked or paused."""
    out = []
    for a in items:
        msg = a["message"]
        if links and a.get("case_id"):
            from src.case_goal import case_action_link
            act = "resume" if a.get("kind") in ("blocked", "paused") else None
            url = case_action_link(a["case_id"], act, base_url)
            if url:
                msg += f" → {url}"
        out.append(msg)
    return "\n".join(out)
