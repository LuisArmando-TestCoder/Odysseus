"""Long-horizon detection → goals that start on their own, steer, or suggest."""
import pytest

from src import long_horizon as lh

LONG = [
    "Investigate corruption in Costa Rican public contracts case by case, track every chain of evidence, and keep going until each case is documented.",
    "Research everything about Acme Holdings: all subsidiaries, officers, domains and lawsuits. Build a complete dossier.",
    "Monitor these three sites every day and tell me when anything about the tender changes.",
    "Hazlo de forma autónoma y sin parar: investiga a fondo todas las licitaciones de la CCSS de 2025 y arma un expediente completo.",
    "1. Pull the registry records\n2. Map every officer\n3. Cross-check court cases\n4. Write the report",
    "Refactor the entire backend to async, migrate all routes, and keep working until the test suite is green.",
]
SHORT = [
    "What is the capital of France?",
    "thanks!",
    "Write me a haiku about dogs.",
    "Quickly, what does this error mean?",
    "¿Qué hora es en Tokio?",
    "Fix the typo in line 3 of the README.",
    "Summarize this article in one sentence.",
]


@pytest.mark.parametrize("msg", LONG)
def test_long_requests_score_above_threshold(msg):
    s = lh.score_message(msg)
    assert s["score"] >= lh.DEFAULT_THRESHOLD, (msg, s)
    assert s["reasons"]


@pytest.mark.parametrize("msg", SHORT)
def test_short_requests_stay_below_threshold(msg):
    s = lh.score_message(msg)
    assert s["score"] < lh.DEFAULT_THRESHOLD, (msg, s)


class FakeGoals:
    def __init__(self, goal=None):
        self.goal = goal
        self.written = []

    def get_goal(self, owner):
        return self.goal

    def create_goal(self, owner, objective, session_id=None):
        self.goal = {"owner": owner, "objective": objective, "session_id": session_id,
                     "phase": "active", "activation": "armed", "roundsStarted": 0}
        return dict(self.goal)

    def _write(self, owner, g):
        self.written.append(g)
        self.goal = g


def test_auto_starts_goal_in_this_chat():
    api = FakeGoals()
    d = lh.decide(LONG[0], "alice", "s1", goal_api=api, mode="auto")
    assert d["action"] == "started"
    assert api.goal["session_id"] == "s1" and api.goal["autoStarted"]
    assert api.goal["holdUntil"] > 0          # the user's own turn runs first
    ctx = lh.turn_context(d)
    assert "LONG-HORIZON GOAL STARTED AUTOMATICALLY" in ctx and "finish_goal" in ctx


def test_short_message_does_nothing():
    api = FakeGoals()
    d = lh.decide("What is 2+2?", "alice", "s1", goal_api=api, mode="auto")
    assert d["action"] == "none" and api.goal is None
    assert lh.turn_context(d) == ""


def test_goal_in_same_chat_is_steered_not_replaced():
    api = FakeGoals({"objective": "Map Acme", "phase": "active", "session_id": "s1", "roundsStarted": 4})
    d = lh.decide("also check their 2019 filings", "alice", "s1", goal_api=api)
    assert d["action"] == "steer"
    assert "steers this goal" in lh.turn_context(d)
    assert api.written == []


def test_goal_elsewhere_means_suggest_never_hijack():
    api = FakeGoals({"objective": "Other work", "phase": "active", "session_id": "s9"})
    d = lh.decide(LONG[1], "alice", "s1", goal_api=api, mode="auto")
    assert d["action"] == "suggest" and api.goal["session_id"] == "s9"
    assert "Other work" in lh.turn_context(d)


def test_modes_incognito_and_missing_ids():
    api = FakeGoals()
    assert lh.decide(LONG[0], "alice", "s1", goal_api=api, mode="off")["action"] == "none"
    assert lh.decide(LONG[0], "alice", "s1", goal_api=api, mode="suggest")["action"] == "suggest"
    assert lh.decide(LONG[0], "alice", "s1", goal_api=api, incognito=True)["action"] == "none"
    assert lh.decide(LONG[0], None, "s1", goal_api=api)["action"] == "none"
    assert api.goal is None


def test_create_failure_degrades_to_suggest():
    class Boom(FakeGoals):
        def create_goal(self, *a, **k):
            raise OSError("disk full")
    d = lh.decide(LONG[0], "alice", "s1", goal_api=Boom(), mode="auto")
    assert d["action"] == "suggest" and "disk full" in d["why"]


def test_objective_is_clipped():
    assert len(lh.objective_from("x " * 2000)) <= 600


# ── goal rounds: visible self-prompts + steering from the chat ────────────────

class _Msg:
    def __init__(self, role, content, metadata=None):
        self.role, self.content, self.metadata = role, content, metadata


class _Sess:
    def __init__(self, history):
        self.history = history


def test_round_sees_latest_user_messages_and_previous_round():
    from src import goal_driver as gd
    sess = _Sess([
        _Msg("user", "investigate Acme case by case"),
        _Msg("assistant", "Plan: 1… 2… 3…"),
        _Msg("assistant", "Round 1 output: found 3 subsidiaries", {"goal_round": True}),
        _Msg("user", "also include their 2019 filings"),
    ])
    block = gd._steering_block(sess)
    assert "also include their 2019 filings" in block
    assert "investigate Acme case by case" in block
    assert "Round 1 output: found 3 subsidiaries" in block
    assert gd._steering_block(None) == ""
    assert gd._steering_block(_Sess([])) == ""


def test_self_message_names_round_and_objective():
    from src import goal_driver as gd
    msg = gd.self_message({"roundsStarted": 3, "maxGoalRounds": 256, "objective": "Map Acme"})
    assert msg.startswith("Goal round 3/256") and "Map Acme" in msg


def test_auto_started_goal_is_held_until_user_turn_registers():
    import inspect
    from src import goal_driver as gd
    src = inspect.getsource(gd._start_round)
    assert 'goal.get("holdUntil")' in src
