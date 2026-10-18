"""Persistent, versioned goal state — mirror of DSH's goal system.

IDENTITY RULE (Layer 0 / F0.3): a goal is keyed by its **owner** (one active
goal per user — the goal card is global in the sidebar, see
``routes/goal_routes.py``). The goal object carries ``session_id``: the chat
where the goal's rounds execute. Pre-F0.3 code keyed files by session id and
wrote/read *different files* than the card; ``get_goal_for_session`` migrates
those legacy files on first read.
"""

import json
import os
import re
import time
import uuid
from typing import Any, Dict, Optional

PHASES = ("active", "paused", "blocked", "complete")


class GoalConflict(Exception):
    """update_goal called with a stale revision."""


def _safe_key(owner: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", str(owner or "default"))


def _path(owner: str) -> str:
    from src.constants import DATA_DIR
    d = os.path.join(DATA_DIR, "goals")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{_safe_key(owner)}.json")


def _read(owner: str) -> Optional[Dict[str, Any]]:
    if not owner:
        return None
    p = _path(owner)
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            goal = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
    if isinstance(goal, dict):
        goal.setdefault("owner", owner)
    return goal


def _write(owner: str, goal: Dict[str, Any]) -> None:
    from core.atomic_io import atomic_write_json
    atomic_write_json(_path(owner), goal)


def _delete(key: str) -> None:
    p = _path(key)
    try:
        if os.path.exists(p):
            os.remove(p)
    except OSError:
        pass


def create_goal(owner: str, objective: str,
                max_goal_rounds: Optional[int] = None,
                session_id: Optional[str] = None) -> Dict[str, Any]:
    """Create + arm a goal keyed by owner. `session_id` is the chat where
    rounds run (may be resolved/stamped later via get_goal_for_session)."""
    goal = {
        "id": f"goal-{uuid.uuid4()}",
        "owner": owner,
        "session_id": session_id,
        "objective": (objective or "").strip(),
        "revision": 1,
        "phase": "active",
        "roundsStarted": 0,
        "maxGoalRounds": max_goal_rounds or 256,
        "blockedReason": None,
        "activation": "armed",
        "createdAt": time.time(),
        "updatedAt": time.time(),
    }
    _write(owner, goal)
    return goal


def get_goal(owner: str) -> Optional[Dict[str, Any]]:
    """Read the goal by owner identity (the canonical key)."""
    return _read(owner)


def _resolve_owner(session_id: str, owner: Optional[str]) -> Optional[str]:
    """Resolve the owner for a session when the caller didn't supply one."""
    if owner:
        return owner
    if not session_id:
        return None
    try:
        from core.database import SessionLocal
        from core.database import Session as DBSession
        db = SessionLocal()
        try:
            row = db.query(DBSession).filter(DBSession.id == session_id).first()
            return getattr(row, "owner", None) if row else None
        finally:
            db.close()
    except Exception:
        return None


def resolve_owner(session_id: Optional[str], owner: Optional[str] = None) -> Optional[str]:
    """Public alias of _resolve_owner for tool/routes callers."""
    return _resolve_owner(session_id, owner)


def get_goal_for_session(session_id: Optional[str],
                         owner: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Read the goal belonging to this chat session (agent-injection path).

    Identity is the owner; the goal is returned only when its ``session_id``
    matches (the chat where the goal's rounds run). One-time migration: a
    legacy session-keyed file (pre-F0.3) is moved to the owner key.
    """
    resolved = _resolve_owner(session_id, owner)
    goal = _read(resolved) if resolved else None
    if goal is not None:
        if not goal.get("session_id") or goal.get("session_id") == session_id:
            if goal.get("session_id") != session_id:
                goal["session_id"] = session_id
                _write(resolved, goal)
            return goal
        return None
    # Legacy migration: old code stored the goal under the session id.
    if resolved and session_id:
        legacy = _read(session_id)
        if legacy:
            legacy["owner"] = resolved
            legacy["session_id"] = session_id
            _write(resolved, legacy)
            _delete(session_id)
            return legacy
    return None


def finish_goal(owner: str, status: str, reason: Optional[str] = None) -> Dict[str, Any]:
    """Explicit end-of-round contract for the agent (DSH `update_goal`).

    The agent calls this tool to signal the goal is DONE or BLOCKED. The
    harness (not the model) owns the state transition. Returns the updated
    goal, or a no-op result when there is no active goal.
    """
    goal = _read(owner)
    if not goal:
        return {"error": "no active goal for this owner", "exit_code": 1}
    if status == "done":
        goal = update_goal(owner, goal["revision"], "complete")
        return {"output": "goal marked complete", "exit_code": 0, "goal": goal}
    if status == "blocked":
        goal = update_goal(owner, goal["revision"], "blocked",
                           blocked_reason=reason or "blocked")
        return {"output": "goal marked blocked", "exit_code": 0, "goal": goal}
    return {"error": f"finish_goal: unknown status {status!r} (use 'done' or 'blocked')", "exit_code": 1}


def update_goal(owner: str, revision: int, action: str,
                objective: Optional[str] = None,
                max_goal_rounds: Optional[int] = None,
                blocked_reason: Optional[str] = None) -> Dict[str, Any]:
    goal = _read(owner)
    if not goal:
        raise KeyError("no goal for this owner")
    if goal["revision"] != revision:
        raise GoalConflict(f"stale revision: have {goal['revision']}, got {revision}")

    if action == "edit":
        if objective is not None:
            goal["objective"] = objective.strip()
        if max_goal_rounds is not None:
            goal["maxGoalRounds"] = max_goal_rounds
        if goal["phase"] in ("complete", "blocked"):
            goal["phase"] = "active"
            goal["blockedReason"] = None
        goal["activation"] = "armed"
    elif action == "pause":
        goal["phase"] = "paused"
        goal["activation"] = "disarmed"
    elif action == "resume":
        if goal["phase"] in ("paused", "blocked"):
            goal["phase"] = "active"
            goal["blockedReason"] = None
        goal["activation"] = "armed"
    elif action == "complete":
        goal["phase"] = "complete"
        goal["activation"] = "disarmed"
    elif action == "blocked":
        goal["phase"] = "blocked"
        goal["blockedReason"] = {"code": "model-reported", "message": blocked_reason or ""}
        goal["activation"] = "disarmed"
    else:
        raise ValueError(f"unknown action {action!r}")

    goal["revision"] += 1
    goal["updatedAt"] = time.time()
    _write(owner, goal)
    return goal
