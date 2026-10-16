"""Persistent, versioned goal state — mirror of DSH's goal system."""
import json, os, re, time, uuid
from typing import Any, Dict, Optional

PHASES = ("active", "paused", "blocked", "complete")


class GoalConflict(Exception):
    """update_goal called with a stale revision."""


def _path(session_id: str) -> str:
    from src.constants import DATA_DIR
    d = os.path.join(DATA_DIR, "goals")
    os.makedirs(d, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", str(session_id or "default"))
    return os.path.join(d, f"{safe}.json")


def _read(session_id: str) -> Optional[Dict[str, Any]]:
    p = _path(session_id)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _write(session_id: str, goal: Dict[str, Any]) -> None:
    from core.atomic_io import atomic_write_json
    atomic_write_json(_path(session_id), goal)


def create_goal(session_id: str, objective: str,
                max_goal_rounds: Optional[int] = None) -> Dict[str, Any]:
    goal = {
        "id": f"goal-{uuid.uuid4()}",
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
    _write(session_id, goal)
    return goal


def get_goal(session_id: str) -> Optional[Dict[str, Any]]:
    return _read(session_id)


def finish_goal(session_id: str, status: str, reason: Optional[str] = None) -> Dict[str, Any]:
    """Explicit end-of-round contract for the agent (DSH `update_goal`).

    The agent calls this tool to signal the goal is DONE or BLOCKED. The
    harness (not the model) owns the state transition. Returns the updated
    goal, or a no-op result when there is no active goal.
    """
    goal = _read(session_id)
    if not goal:
        return {"error": "no active goal for this session", "exit_code": 1}
    if status == "done":
        goal = update_goal(session_id, goal["revision"], "complete")
        return {"output": "goal marked complete", "exit_code": 0, "goal": goal}
    if status == "blocked":
        goal = update_goal(session_id, goal["revision"], "blocked",
                           blocked_reason=reason or "blocked")
        return {"output": "goal marked blocked", "exit_code": 0, "goal": goal}
    return {"error": f"finish_goal: unknown status {status!r} (use 'done' or 'blocked')", "exit_code": 1}


def update_goal(session_id: str, revision: int, action: str,
                objective: Optional[str] = None,
                max_goal_rounds: Optional[int] = None,
                blocked_reason: Optional[str] = None) -> Dict[str, Any]:
    goal = _read(session_id)
    if not goal:
        raise KeyError("no goal for this session")
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
    _write(session_id, goal)
    return goal