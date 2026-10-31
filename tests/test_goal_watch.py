"""Goal check-in: blocked/complete/stalled/paused alerts, brief line, dedup."""
import asyncio
import json

import pytest

from src.goal_watch import (DAY, HOUR, assess_goal, due_goal_alerts,
                            format_goal_alerts, goal_status_line)

NOW = 2_000_000.0


class FakeGoals:
    def __init__(self, goal=None):
        self.goal = goal
        self.calls = []

    def get_goal(self, owner):
        self.calls.append(owner)
        return self.goal if owner == "u" else None


def goal(**kw):
    g = {"id": "goal-1", "owner": "u", "session_id": "s1", "objective": "Map the shell companies",
         "revision": 3, "phase": "active", "roundsStarted": 4, "maxGoalRounds": 20,
         "blockedReason": None, "activation": "armed", "createdAt": NOW - DAY, "updatedAt": NOW - 60}
    g.update(kw)
    return g


idle = lambda sid: False  # noqa: E731
busy = lambda sid: True  # noqa: E731


def test_healthy_active_goal_is_quiet():
    assert assess_goal(goal(), now=NOW, is_running=idle) is None
    assert assess_goal(None, now=NOW) is None
    assert assess_goal({"phase": "blocked"}, now=NOW) is None  # no id


def test_blocked_round_cap_suggests_raising_limit():
    a = assess_goal(goal(phase="blocked", activation="disarmed",
                         blockedReason={"code": "model-reported", "message": "maxGoalRounds reached"}),
                    now=NOW, is_running=idle)
    assert a["kind"] == "blocked"
    assert "Map the shell companies" in a["message"] and "raise the round limit" in a["message"]


def test_blocked_failures_and_model_reason():
    a = assess_goal(goal(phase="blocked", blockedReason={"message": "3 consecutive goal rounds failed"}),
                    now=NOW, is_running=idle)
    assert "provider is up" in a["message"]
    b = assess_goal(goal(phase="blocked", blockedReason={"message": "need the API key"}), now=NOW)
    assert "need the API key" in b["message"] and "answer the blocker" in b["message"]
    c = assess_goal(goal(phase="blocked", blockedReason=None), now=NOW)
    assert "no reason given" in c["message"]


def test_untrusted_text_is_cleaned_and_capped():
    a = assess_goal(goal(phase="blocked", objective="x\n\x07" * 200,
                         blockedReason={"message": "line1\r\nline2\x1b[31m"}), now=NOW)
    assert "\n" not in a["message"] and "\x1b" not in a["message"] and "\x07" not in a["message"]
    assert len(a["objective"]) <= 120


def test_complete():
    a = assess_goal(goal(phase="complete", activation="disarmed"), now=NOW)
    assert a["kind"] == "complete" and "4 round(s)" in a["message"]


def test_stalled_needs_idle_armed_not_running_not_held():
    old = NOW - 7 * HOUR
    assert assess_goal(goal(updatedAt=old), now=NOW, is_running=idle)["kind"] == "stalled"
    assert assess_goal(goal(updatedAt=old), now=NOW, is_running=busy) is None
    assert assess_goal(goal(updatedAt=NOW - 5 * HOUR), now=NOW, is_running=idle) is None
    assert assess_goal(goal(updatedAt=old, activation="disarmed"), now=NOW, is_running=idle) is None
    assert assess_goal(goal(updatedAt=old, holdUntil=NOW + 60), now=NOW, is_running=idle) is None
    nochat = assess_goal(goal(updatedAt=old, session_id=None), now=NOW, is_running=idle)
    assert "chat may have been deleted" in nochat["message"]


def test_paused_only_after_days():
    assert assess_goal(goal(phase="paused", updatedAt=NOW - DAY), now=NOW) is None
    a = assess_goal(goal(phase="paused", updatedAt=NOW - 3 * DAY), now=NOW)
    assert a["kind"] == "paused" and "3 days" in a["message"]


def test_status_line_for_brief():
    g = FakeGoals(goal())
    assert goal_status_line("u", goal_api=g, now=NOW, is_running=idle) == \
        'Goal: "Map the shell companies" — active, 4/20 rounds.'
    g.goal = goal(phase="blocked", blockedReason={"message": "stuck"})
    assert goal_status_line("u", goal_api=g, now=NOW).startswith("Goal: Goal blocked")
    g.goal = goal(phase="complete", updatedAt=NOW - 2 * DAY)
    assert goal_status_line("u", goal_api=g, now=NOW) == ""  # long finished
    assert goal_status_line("other", goal_api=g, now=NOW) == ""  # owner scoped
    assert goal_status_line("", goal_api=g, now=NOW) == ""


def test_status_line_never_raises():
    class Boom:
        def get_goal(self, owner):
            raise RuntimeError("disk")
    assert goal_status_line("u", goal_api=Boom(), now=NOW) == ""
    assert due_goal_alerts("u", goal_api=Boom(), now=NOW) == []


def test_due_alerts_once_per_spell(tmp_path):
    path = str(tmp_path / "state.json")
    g = FakeGoals(goal(phase="blocked", blockedReason={"message": "stuck"}, updatedAt=NOW - 100))
    first = due_goal_alerts("u", goal_api=g, now=NOW, state_path=path)
    assert len(first) == 1 and first[0]["kind"] == "blocked"
    assert "Goal blocked" in format_goal_alerts(first)
    assert due_goal_alerts("u", goal_api=g, now=NOW + HOUR, state_path=path) == []
    # resumed, worked, blocked again → new spell
    g.goal = goal(phase="blocked", blockedReason={"message": "stuck again"}, updatedAt=NOW + 2 * HOUR)
    again = due_goal_alerts("u", goal_api=g, now=NOW + 3 * HOUR, state_path=path)
    assert len(again) == 1 and "stuck again" in again[0]["message"]
    with open(path) as f:
        assert len(json.load(f)) == 1
    # healthy goal → nothing; other owner → nothing
    g.goal = goal(updatedAt=NOW + 4 * HOUR)
    assert due_goal_alerts("u", goal_api=g, now=NOW + 4 * HOUR, is_running=idle, state_path=path) == []
    assert due_goal_alerts("x", goal_api=g, now=NOW, state_path=path) == []


def test_due_alerts_tolerates_corrupt_state(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not json")
    g = FakeGoals(goal(phase="complete"))
    assert len(due_goal_alerts("u", goal_api=g, now=NOW, state_path=str(path))) == 1


def test_check_goals_action_and_registration(monkeypatch, tmp_path):
    ba = pytest.importorskip("src.builtin_actions")
    ts = pytest.importorskip("src.task_scheduler")
    import src.goal_watch as gw
    fake = FakeGoals(goal(phase="complete"))
    monkeypatch.setattr(gw, "_goal_api", lambda goal_api=None: fake)
    monkeypatch.setattr(gw, "_state_path", lambda owner: str(tmp_path / "s.json"))
    body, ok = asyncio.run(ba.action_check_goals("u"))
    assert ok and "Goal complete" in body
    with pytest.raises(ba.TaskNoop):
        asyncio.run(ba.action_check_goals("u"))
    assert "check_goals" in ba.BUILTIN_ACTIONS and "check_goals" in ba.BUILTIN_ACTION_INFO
    assert "check_goals" in ts.HOUSEKEEPING_DEFAULTS
