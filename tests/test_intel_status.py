"""Intel Board badge: /api/intel/status summary + main-SPA wiring."""
import os
import re

import pytest

import src.casefiles as cf
from src.casefiles import CaseStore
from src.case_nudges import DAY
from src.intel_status import intel_status

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class FakeGoals:
    def __init__(self, goal=None):
        self.goal = goal

    def get_goal(self, owner):
        return self.goal


class FakeWatches:
    def __init__(self, by_owner):
        self.by_owner = by_owner

    def list(self, owner):
        return list(self.by_owner.get(owner, []))


class Broken:
    def list_cases(self, *a, **k):
        raise OSError("disk gone")

    def list(self, *a, **k):
        raise OSError("disk gone")


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


def test_empty_owner_and_empty_store(env):
    st, clock = env
    s = intel_status(None, store=st, watch_store=FakeWatches({}), goal_api=FakeGoals())
    assert s["attention"] == 0 and s["open_cases"] == 0
    s = intel_status("u", store=st, watch_store=FakeWatches({}), goal_api=FakeGoals(), now=clock.t)
    assert s["attention"] == 0
    assert s["label"] == "Intel Board — no open cases yet"
    assert s["degraded"] == []


def test_counts_cold_cases_steps_and_watches(env):
    st, clock = env
    a = st.create_case("u", "Shell company trail")
    st.add_step("u", a["id"], "pull registry filings")
    st.add_step("u", a["id"], "map directors")
    b = st.create_case("u", "Fresh lead")
    closed = st.create_case("u", "Old closed")
    st.update_case("u", closed["id"], status="closed")
    clock.t += 5 * DAY
    st.add_step("u", b["id"], "check domain whois")  # touches b → not cold
    now = clock.t
    watches = FakeWatches({"u": [
        {"id": "w1", "active": True, "last_changed": now - 3600, "error_count": 0},
        {"id": "w2", "active": True, "last_changed": now - 3 * DAY, "error_count": 4},
        {"id": "w3", "active": False, "last_changed": now, "error_count": 9},
    ], "other": [{"id": "x", "active": True, "last_changed": now, "error_count": 0}]})
    s = intel_status("u", store=st, watch_store=watches, goal_api=FakeGoals(), now=now)
    assert s["open_cases"] == 2
    assert s["open_steps"] == 3
    assert s["cold_cases"] == 1 and s["cold"][0]["id"] == a["id"]
    assert s["cold"][0]["action"] == "drive"
    assert s["watches_active"] == 2
    assert s["watches_changed"] == 1
    assert s["watches_failing"] == 1
    assert s["attention"] == 3
    assert "2 open cases (3 open steps)" in s["label"]
    assert "1 going cold" in s["label"] and "2 watches" in s["label"]
    assert "1 changed today" in s["label"] and "1 failing" in s["label"]


def test_goal_driven_case_is_not_cold_and_is_named(env):
    st, clock = env
    a = st.create_case("u", "Driven case")
    st.add_step("u", a["id"], "step")
    clock.t += 10 * DAY
    goals = FakeGoals({"phase": "active", "case_id": a["id"]})
    s = intel_status("u", store=st, watch_store=FakeWatches({}), goal_api=goals, now=clock.t)
    assert s["cold_cases"] == 0 and s["attention"] == 0
    assert s["driven"] == {"id": a["id"], "title": "Driven case"}
    assert "goal driving “Driven case”" in s["label"]


def test_owner_scoped(env):
    st, clock = env
    st.create_case("alice", "Alice's case")
    clock.t += 10 * DAY
    s = intel_status("bob", store=st, watch_store=FakeWatches({}), goal_api=FakeGoals(), now=clock.t)
    assert s["open_cases"] == 0 and s["cold_cases"] == 0


def test_titles_are_cleaned(env):
    st, clock = env
    st.create_case("u", "Bad\x00title\nwith\tcontrols " + "x" * 200)
    clock.t += 10 * DAY
    s = intel_status("u", store=st, watch_store=FakeWatches({}), goal_api=FakeGoals(), now=clock.t)
    t = s["cold"][0]["title"]
    assert "\x00" not in t and "\n" not in t and len(t) <= 80


def test_broken_stores_degrade_instead_of_raising():
    s = intel_status("u", store=Broken(), watch_store=Broken(), goal_api=FakeGoals(), now=1.0)
    assert s["attention"] == 0
    assert set(s["degraded"]) == {"cases", "watchlist"}
    s2 = intel_status("u", store=Broken(), watch_store=FakeWatches({"u": [
        {"active": True, "last_changed": 1.0, "error_count": 0}]}), goal_api=FakeGoals(), now=1.0)
    assert s2["watches_changed"] == 1 and "some intel unavailable" in s2["label"]


# ── main SPA wiring ─────────────────────────────────────────────────────────

def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def test_spa_has_intel_board_entry_points():
    html = _read("static/index.html")
    assert 'id="tool-cases-btn"' in html
    assert 'id="rail-cases"' in html
    assert 'id="intel-badge"' in html
    assert 'data-ui-key="tool-cases"' in html
    assert re.search(r'<script type="module" src="/static/js/intelLink\.js"></script>', html)
    app = _read("static/app.js")
    assert "'tool-cases':" in app and "#tool-cases-btn" in app
    js = _read("static/js/intelLink.js")
    assert "/api/intel/status" in js and "'/cases'" in js
    slash = _read("static/js/slashCommands.js")
    assert re.search(r"\n  cases: \{", slash)


def test_status_route_registered():
    src = _read("routes/case_routes.py")
    assert '@router.get("/api/intel/status")' in src
