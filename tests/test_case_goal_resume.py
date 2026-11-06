"""One-click case goals: resume a blocked/paused case goal, deep links in
cold-case and goal notifications, public-URL validation, /cases wiring."""
import copy
import os

import pytest

from src import case_goal
from src.case_goal import case_action_link, public_base_url, resume_case_goal
from src.case_nudges import format_nudges
from src.goal_watch import format_goal_alerts

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CID = "case_0123abcdef"


class GoalConflict(Exception):
    pass


class FakeGoals:
    def __init__(self, goal=None, conflict=False):
        self.goal = goal
        self.conflict = conflict
        self.updates = []

    def get_goal(self, owner):
        return copy.deepcopy(self.goal) if self.goal and self.goal.get("owner") == owner else None

    def update_goal(self, owner, revision, action, **kw):
        if self.conflict:
            raise GoalConflict("stale revision")
        assert revision == self.goal["revision"]
        self.updates.append(action)
        if action == "resume" and self.goal["phase"] in ("paused", "blocked"):
            self.goal.update(phase="active", blockedReason=None)
        self.goal["activation"] = "armed"
        self.goal["revision"] += 1
        return copy.deepcopy(self.goal)


def goal(**kw):
    g = {"id": "goal-1", "owner": "u", "objective": "Advance case", "revision": 4, "phase": "blocked",
         "blockedReason": {"code": "model-reported", "message": "need an API key"},
         "activation": "disarmed", "roundsStarted": 7, "maxGoalRounds": 20, "case_id": CID}
    g.update(kw)
    return g


# ── resume ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("phase", ["blocked", "paused"])
def test_resume_blocked_or_paused_goal(phase):
    ga = FakeGoals(goal(phase=phase))
    r = resume_case_goal("u", CID, goal_api=ga)
    assert r["resumed"] and r["goal"]["phase"] == "active"
    assert ga.updates == ["resume"] and ga.goal["blockedReason"] is None
    assert case_goal.binding_for("u", CID, goal_api=ga)["phase"] == "active"


def test_resume_active_goal_is_a_noop():
    ga = FakeGoals(goal(phase="active", blockedReason=None))
    r = resume_case_goal("u", CID, goal_api=ga)
    assert r["resumed"] is False and ga.updates == []


def test_resume_refuses_other_case_other_owner_and_finished_goal():
    with pytest.raises(ValueError, match="no goal is driving"):
        resume_case_goal("u", "case_ffffffffff", goal_api=FakeGoals(goal()))
    with pytest.raises(ValueError, match="no goal is driving"):
        resume_case_goal("other", CID, goal_api=FakeGoals(goal()))
    with pytest.raises(ValueError, match="no goal is driving"):
        resume_case_goal("u", CID, goal_api=FakeGoals(None))
    with pytest.raises(ValueError, match="already finished"):
        resume_case_goal("u", CID, goal_api=FakeGoals(goal(phase="complete")))
    with pytest.raises(ValueError, match="owner"):
        resume_case_goal(None, CID, goal_api=FakeGoals(goal()))


def test_resume_conflict_becomes_friendly_error():
    with pytest.raises(ValueError, match="reload"):
        resume_case_goal("u", CID, goal_api=FakeGoals(goal(), conflict=True))


# ── links ────────────────────────────────────────────────────────────────────

def test_case_action_link_shapes():
    assert case_action_link(CID) == f"/cases?case={CID}"
    assert case_action_link(CID, "drive") == f"/cases?case={CID}&do=drive"
    assert case_action_link(CID, "resume", "https://ody.example/") == f"https://ody.example/cases?case={CID}&do=resume"
    assert case_action_link(CID, "delete") == f"/cases?case={CID}"  # unknown actions are dropped
    for bad in ("", None, "case_XYZ", "case_123&do=x", "../case_0123abcdef", "javascript:alert(1)"):
        assert case_action_link(bad, "drive") == ""


@pytest.mark.parametrize("value,expected", [
    ("https://ody.example/", "https://ody.example"),
    ("http://100.64.0.5:8000/odysseus", "http://100.64.0.5:8000/odysseus"),
    ("", ""), (None, ""), ("javascript:alert(1)", ""), ("ody.example", ""),
    ("https://evil.example/x?y=1", ""), ("https://a b.example", ""),
])
def test_public_base_url_validation(value, expected):
    assert public_base_url(lambda k: value) == expected


def test_public_base_url_never_raises():
    def boom(k):
        raise RuntimeError("db down")
    assert public_base_url(boom) == ""


def test_nudges_link_drive_only_when_driving_is_the_move():
    items = [
        {"id": CID, "title": "Acme", "idle_days": 4, "action": "drive", "suggestion": "drive it"},
        {"id": "case_aaaaaaaaaa", "title": "Beta", "idle_days": 5, "action": "pivot", "suggestion": "try X"},
    ]
    plain = format_nudges(items)
    assert "/cases" not in plain  # default output unchanged (daily brief)
    text = format_nudges(items, links=True, base_url="https://ody.example")
    a, b = text.splitlines()[1:]
    assert a.endswith(f"→ https://ody.example/cases?case={CID}&do=drive")
    assert b.endswith("→ https://ody.example/cases?case=case_aaaaaaaaaa")


def test_goal_alerts_offer_resume_for_bound_blocked_goal():
    items = [
        {"kind": "blocked", "case_id": CID, "message": "Goal blocked: x"},
        {"kind": "complete", "case_id": CID, "message": "Goal complete: y"},
        {"kind": "blocked", "case_id": None, "message": "Goal blocked: z"},
    ]
    assert format_goal_alerts(items) == "Goal blocked: x\nGoal complete: y\nGoal blocked: z"
    lines = format_goal_alerts(items, links=True).splitlines()
    assert lines[0] == f"Goal blocked: x → /cases?case={CID}&do=resume"
    assert lines[1] == f"Goal complete: y → /cases?case={CID}"
    assert lines[2] == "Goal blocked: z"


# ── wiring ───────────────────────────────────────────────────────────────────

def test_route_and_page_wiring():
    routes = open(os.path.join(ROOT, "routes", "case_routes.py")).read()
    assert '"/api/cases/{case_id}/resume"' in routes and "_is_top_level_human" in routes
    page = open(os.path.join(ROOT, "static", "cases.html")).read()
    assert "/resume`" in page and "c-resume" in page
    assert "get('do')" in page  # deep-link offer, never an automatic action
    actions = open(os.path.join(ROOT, "src", "builtin_actions.py")).read()
    assert actions.count("public_base_url()") >= 2
