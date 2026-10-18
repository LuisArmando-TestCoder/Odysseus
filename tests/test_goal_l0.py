"""Tests for src/goal.py — owner-keyed identity, session scoping, migration (F0.3)."""

import os

import pytest

from src import constants
from src import goal


@pytest.fixture(autouse=True)
def _tmp_data(monkeypatch, tmp_path):
    # Redirect data/goals to the pytest tmp dir so tests never touch real data.
    monkeypatch.setattr(constants, "DATA_DIR", str(tmp_path))
    yield


def test_owner_keyed_create_get_update():
    g = goal.create_goal("alice", "do the thing", session_id="sess-1")
    assert g["owner"] == "alice"
    assert g["session_id"] == "sess-1"
    assert g["phase"] == "active" and g["activation"] == "armed"

    got = goal.get_goal("alice")
    assert got["id"] == g["id"]

    upd = goal.update_goal("alice", got["revision"], "pause")
    assert upd["phase"] == "paused" and upd["activation"] == "disarmed"

    # Bob has no goal — identity is the owner.
    assert goal.get_goal("bob") is None


def test_stale_revision_conflicts():
    goal.create_goal("alice", "x")
    with pytest.raises(goal.GoalConflict):
        goal.update_goal("alice", 999, "complete")


def test_finish_goal_transitions_by_owner():
    goal.create_goal("alice", "x")
    res = goal.finish_goal("alice", "done")
    assert res["exit_code"] == 0
    assert res["goal"]["phase"] == "complete"
    assert res["goal"]["activation"] == "disarmed"
    # Unknown owner degrades to a clean error, never a crash.
    assert goal.finish_goal("bob", "done")["exit_code"] == 1


def test_finish_goal_blocked_with_reason():
    goal.create_goal("alice", "x")
    res = goal.finish_goal("alice", "blocked", reason="api key revoked")
    assert res["goal"]["phase"] == "blocked"
    assert res["goal"]["blockedReason"]["message"] == "api key revoked"


def test_get_goal_for_session_scoped():
    goal.create_goal("alice", "x", session_id="sess-1")
    g = goal.get_goal_for_session("sess-1", owner="alice")
    assert g is not None and g["phase"] == "active"
    # A different session of the same owner does NOT see the goal.
    assert goal.get_goal_for_session("sess-2", owner="alice") is None


def test_get_goal_for_session_stamps_missing_session():
    goal.create_goal("alice", "x")  # no session_id at creation
    g = goal.get_goal_for_session("sess-9", owner="alice")
    assert g is not None
    assert g["session_id"] == "sess-9"


def test_legacy_session_keyed_file_migrates(tmp_path):
    # Simulate pre-F0.3 storage: the file was keyed by SESSION id.
    legacy = {
        "id": "goal-legacy",
        "session_id": "sess-legacy",
        "objective": "legacy objective",
        "revision": 3,
        "phase": "active",
        "roundsStarted": 2,
        "maxGoalRounds": 256,
        "blockedReason": None,
        "activation": "armed",
        "createdAt": 1.0,
        "updatedAt": 1.0,
    }
    goal._write("sess-legacy", legacy)

    g = goal.get_goal_for_session("sess-legacy", owner="alice")
    assert g is not None
    assert g["owner"] == "alice"
    assert g["session_id"] == "sess-legacy"
    # Migrated: owner-keyed file exists and the legacy session-keyed file is gone.
    assert goal.get_goal("alice") is not None
    assert not os.path.exists(goal._path("sess-legacy"))


def test_update_goal_actions():
    goal.create_goal("alice", "x")
    g = goal.get_goal("alice")
    g = goal.update_goal("alice", g["revision"], "blocked", blocked_reason="no funds")
    assert g["phase"] == "blocked" and g["blockedReason"]["message"] == "no funds"
    # edit reopens + re-arms.
    g = goal.update_goal("alice", g["revision"], "edit", objective="new objective")
    assert g["phase"] == "active" and g["activation"] == "armed"
    assert g["objective"] == "new objective"


def test_unknown_action_raises():
    goal.create_goal("alice", "x")
    g = goal.get_goal("alice")
    with pytest.raises(ValueError):
        goal.update_goal("alice", g["revision"], "nonsense")
