"""Intel Board badge: new cross-case links + case-bound goal state."""
import pytest

import src.casefiles as cf
from src.casefiles import CaseStore
from src.case_nudges import DAY
from src.intel_status import intel_status

HOUR = 3600.0


class FakeGoals:
    def __init__(self, goal=None, boom=False):
        self.goal, self.boom = goal, boom

    def get_goal(self, owner):
        if self.boom:
            raise OSError("goal store gone")
        return self.goal


class NoWatches:
    def list(self, owner):
        return []


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


def _status(st, now, goals=None, owner="u"):
    return intel_status(owner, store=st, watch_store=NoWatches(),
                        goal_api=goals or FakeGoals(), now=now)


def test_new_cross_case_link_counts_toward_attention(env):
    st, clock = env
    a = st.create_case("u", "Shell company trail")
    b = st.create_case("u", "Phishing kit")
    st.add_entity("u", a["id"], "ops@acme.io")
    clock.t += HOUR
    st.add_entity("u", b["id"], "OPS@acme.io")  # link forms now
    s = _status(st, clock.t)
    assert s["new_links"] == 1
    assert s["links"][0]["value"].lower() == "ops@acme.io"
    assert set(s["links"][0]["cases"]) == {"Shell company trail", "Phishing kit"}
    assert s["attention"] == 1
    assert "1 new cross-case link" in s["label"]


def test_old_link_and_other_owner_not_counted(env):
    st, clock = env
    a = st.create_case("u", "A")
    b = st.create_case("u", "B")
    st.add_entity("u", a["id"], "shared.example")
    st.add_entity("u", b["id"], "shared.example")
    x = st.create_case("eve", "Eve case")
    st.add_entity("eve", x["id"], "shared.example")
    clock.t += 2 * DAY  # link is older than 24h now
    s = _status(st, clock.t)
    assert s["new_links"] == 0 and s["links"] == []
    assert "cross-case" not in s["label"]
    # eve's single case never forms a link with u's cases
    assert _status(st, clock.t, owner="eve")["new_links"] == 0


def test_blocked_case_goal_needs_attention_with_reason(env):
    st, clock = env
    a = st.create_case("u", "Driven case")
    goals = FakeGoals({"phase": "blocked", "case_id": a["id"],
                       "blockedReason": {"code": "model-reported",
                                         "message": "need API key\nfor registry"}})
    s = _status(st, clock.t, goals)
    assert s["goal"] == {"phase": "blocked", "case_id": a["id"], "title": "Driven case",
                         "reason": "need API key for registry"}
    assert s["attention"] == 1
    assert "goal blocked on “Driven case”: need API key for registry" in s["label"]


def test_blocked_goal_on_cold_case_not_double_counted(env):
    st, clock = env
    a = st.create_case("u", "Stalled")
    st.add_step("u", a["id"], "next move")
    clock.t += 10 * DAY
    goals = FakeGoals({"phase": "blocked", "case_id": a["id"], "blockedReason": None})
    s = _status(st, clock.t, goals)
    assert s["cold_cases"] == 1
    assert s["goal"]["phase"] == "blocked" and s["goal"]["reason"] == ""
    assert s["attention"] == 1


def test_active_and_paused_goal_state_no_attention(env):
    st, clock = env
    a = st.create_case("u", "Work")
    s = _status(st, clock.t, FakeGoals({"phase": "paused", "case_id": a["id"]}))
    assert s["goal"]["phase"] == "paused" and s["goal"]["reason"] == ""
    assert s["attention"] == 0 and "goal paused on “Work”" in s["label"]
    s = _status(st, clock.t, FakeGoals({"phase": "active", "case_id": a["id"]}))
    assert s["goal"]["phase"] == "active"
    assert "goal driving “Work”" in s["label"]


def test_goal_ignored_when_complete_unbound_closed_or_foreign(env):
    st, clock = env
    a = st.create_case("u", "Mine")
    closed = st.create_case("u", "Done")
    st.update_case("u", closed["id"], status="closed")
    foreign = st.create_case("eve", "Eve's")
    for g in ({"phase": "complete", "case_id": a["id"]},
              {"phase": "blocked"},
              {"phase": "blocked", "case_id": closed["id"]},
              {"phase": "blocked", "case_id": foreign["id"]},
              "not-a-dict"):
        s = _status(st, clock.t, FakeGoals(g))
        assert s["goal"] is None and s["attention"] == 0, g


def test_goal_store_failure_degrades_quietly(env):
    st, clock = env
    st.create_case("u", "Mine")
    s = _status(st, clock.t, FakeGoals(boom=True))
    assert s["goal"] is None and s["attention"] == 0


def test_link_scan_failure_is_reported_degraded(env):
    st, clock = env
    st.create_case("u", "Mine")

    def boom(*a, **k):
        raise OSError("bad case file")
    st.recent_overlaps = boom
    s = _status(st, clock.t)
    assert "cross_case_links" in s["degraded"] and s["new_links"] == 0
