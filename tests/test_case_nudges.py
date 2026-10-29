"""Cold-case nudges: staleness, goal-driven exclusion, suggestions, dedup."""
import asyncio
import json
import os

import pytest

import src.casefiles as cf
from src.casefiles import CaseStore
from src.case_nudges import DAY, due_nudges, format_nudges, stale_cases


class FakeGoals:
    def __init__(self, goal=None):
        self.goal = goal

    def get_goal(self, owner):
        return self.goal


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def env(tmp_path, monkeypatch):
    clock = Clock(1_000_000.0)
    monkeypatch.setattr(cf, "_now", clock)
    return CaseStore(str(tmp_path)), clock


def test_fresh_and_closed_cases_are_not_stale(env):
    st, clock = env
    st.create_case("u", "Fresh")
    closed = st.create_case("u", "Done")
    st.update_case("u", closed["id"], status="closed")
    assert stale_cases("u", store=st, goal_api=FakeGoals(), now=clock.t + 2 * DAY) == []
    assert stale_cases("", store=st, goal_api=FakeGoals()) == []


def test_stale_case_with_steps_suggests_drive(env):
    st, clock = env
    c = st.create_case("u", "Acme\x00 shell co")
    st.add_step("u", c["id"], "Pull corporate registry filing")
    st.add_step("u", c["id"], "Check WHOIS for acme.example")
    st.complete_step("u", c["id"], "Check WHOIS for acme.example")
    items = stale_cases("u", store=st, goal_api=FakeGoals(), now=clock.t + 4 * DAY)
    assert len(items) == 1
    it = items[0]
    assert it["action"] == "drive" and it["open_steps"] == 1
    assert it["title"] == "Acme shell co"
    assert "Pull corporate registry filing" in it["suggestion"] and c["id"] in it["suggestion"]
    text = format_nudges(items)
    assert text.startswith("Cases going cold:") and "4 days ago" in text


def test_stale_case_without_steps_suggests_pivot_or_step(env):
    st, clock = env
    a = st.create_case("u", "Selector case")
    st.add_entity("u", a["id"], "jdoe@example.com")
    b = st.create_case("u", "Bare case")
    items = {i["id"]: i for i in stale_cases("u", store=st, goal_api=FakeGoals(), now=clock.t + 5 * DAY)}
    assert items[a["id"]]["action"] == "pivot" and "try:" in items[a["id"]]["suggestion"]
    assert items[b["id"]]["action"] == "add_step"


def test_goal_driven_case_is_skipped_and_order_is_most_idle_first(env):
    st, clock = env
    old = st.create_case("u", "Old")
    clock.t += 2 * DAY
    driven = st.create_case("u", "Driven")
    clock.t += 1 * DAY
    newer = st.create_case("u", "Newer")
    now = clock.t + 3 * DAY
    goals = FakeGoals({"phase": "active", "case_id": driven["id"]})
    ids = [i["id"] for i in stale_cases("u", store=st, goal_api=goals, now=now)]
    assert ids == [old["id"], newer["id"]]
    # A finished goal no longer counts as driving the case.
    goals.goal["phase"] = "done"
    assert driven["id"] in [i["id"] for i in stale_cases("u", store=st, goal_api=goals, now=now)]


def test_owner_scoped(env):
    st, clock = env
    st.create_case("alice", "Alice case")
    assert stale_cases("bob", store=st, goal_api=FakeGoals(), now=clock.t + 9 * DAY) == []


def test_due_nudges_dedups_per_quiet_spell(env):
    st, clock = env
    c = st.create_case("u", "Quiet")
    g = FakeGoals()
    t0 = clock.t + 4 * DAY
    assert [i["id"] for i in due_nudges("u", store=st, goal_api=g, now=t0)] == [c["id"]]
    # Same spell, next day: silent. State file lives beside the cases but is
    # invisible to list_cases.
    assert due_nudges("u", store=st, goal_api=g, now=t0 + DAY) == []
    assert [x["id"] for x in st.list_cases("u")] == [c["id"]]
    # Ignored for a week → nudge again.
    assert len(due_nudges("u", store=st, goal_api=g, now=t0 + 8 * DAY)) == 1
    # Touched, then quiet again → new spell → nudge again.
    clock.t = t0 + 9 * DAY
    st.add_step("u", c["id"], "Re-check filings")
    assert due_nudges("u", store=st, goal_api=g, now=clock.t + DAY) == []
    assert len(due_nudges("u", store=st, goal_api=g, now=clock.t + 4 * DAY)) == 1


def test_due_nudges_tolerates_corrupt_state(env, tmp_path):
    st, clock = env
    st.create_case("u", "Quiet")
    path = str(tmp_path / "state.json")
    with open(path, "w") as f:
        f.write("{not json")
    assert len(due_nudges("u", store=st, goal_api=FakeGoals(), now=clock.t + 4 * DAY, state_path=path)) == 1
    with open(path) as f:
        assert len(json.load(f)) == 1


def test_check_stale_cases_action_and_registration(env, monkeypatch):
    ba = pytest.importorskip("src.builtin_actions")
    ts = pytest.importorskip("src.task_scheduler")
    import src.case_nudges as cn
    st, clock = env
    monkeypatch.setattr(cf, "get_case_store", lambda data_dir=None: st)
    monkeypatch.setattr(cn, "_driven_case_id", lambda owner, goal_api=None: None)
    with pytest.raises(ba.TaskNoop):
        asyncio.run(ba.action_check_stale_cases("u"))
    st.create_case("u", "Cold one")
    monkeypatch.setattr(cn.time, "time", lambda: clock.t + 4 * DAY)
    body, ok = asyncio.run(ba.action_check_stale_cases("u"))
    assert ok and "Cold one" in body
    with pytest.raises(ba.TaskNoop):
        asyncio.run(ba.action_check_stale_cases("u"))
    assert "check_stale_cases" in ba.BUILTIN_ACTIONS and "check_stale_cases" in ba.BUILTIN_ACTION_INFO
    assert "check_stale_cases" in ts.HOUSEKEEPING_DEFAULTS
