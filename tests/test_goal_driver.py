"""Tests for src/goal_driver.py — idle-gated rounds, fences, error blocking (G1–G3)."""

import asyncio
import json

import pytest

import src.agent_runs as agent_runs
import src.goal_driver as gd
import src.turn_inbox as turn_inbox
from src import constants
from src import goal as goal_mod


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setattr(constants, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(agent_runs, "_evict_grace", lambda: 3600.0)
    gd._ERRORS.clear()
    gd._LOCKS.clear()
    agent_runs._RUNS.clear()
    agent_runs._FINISH_CALLBACKS.clear()
    turn_inbox._PENDING.clear()
    turn_inbox._factory = None
    yield
    for run in list(agent_runs._RUNS.values()):
        if run.evict_task and not run.evict_task.done():
            run.evict_task.cancel()
        if run.task and not run.task.done():
            run.task.cancel()
    agent_runs._RUNS.clear()
    gd._ERRORS.clear()
    gd._LOCKS.clear()


async def _gen():
    yield "data: [DONE]\n\n"


def _mk_goal(owner="alice", session="s1", **kw):
    g = goal_mod.create_goal(owner, "do the thing", session_id=session)
    g.update(kw)
    if kw:
        goal_mod._write(owner, g)
    return g


async def test_round_messages_frame_and_checkpoint():
    from src.checkpoint import checkpoint_append
    checkpoint_append("found endpoint X", "s1", "alice")
    g = _mk_goal()
    msgs = gd._round_messages(g, "alice")
    assert any("ACTIVE GOAL" in m["content"] for m in msgs if m["role"] == "system")
    assert any("found endpoint X" in m["content"] for m in msgs if m["role"] == "system")
    assert msgs[-1]["role"] == "user"
    assert "do not re-derive" in msgs[-1]["content"]


async def test_idle_fence_active_run_skips():
    g = _mk_goal()
    agent_runs.start("s1", _gen(), owner="alice")
    await gd._start_round("alice", g)
    assert goal_mod.get_goal("alice")["roundsStarted"] == 0  # untouched


async def test_idle_fence_queued_turn_skips():
    g = _mk_goal()
    turn_inbox.enqueue("s1", "user message")
    await gd._start_round("alice", g)
    assert goal_mod.get_goal("alice")["roundsStarted"] == 0


async def test_disarmed_goal_skips():
    g = _mk_goal()
    g = goal_mod.update_goal("alice", g["revision"], "pause")
    await gd._start_round("alice", g)
    assert goal_mod.get_goal("alice")["roundsStarted"] == 0


async def test_round_cap_marks_blocked():
    g = _mk_goal(maxGoalRounds=2)
    g["roundsStarted"] = 2
    goal_mod._write("alice", g)
    await gd._start_round("alice", g)
    assert goal_mod.get_goal("alice")["phase"] == "blocked"
    assert "maxGoalRounds" in goal_mod.get_goal("alice")["blockedReason"]["message"]


class _FakeSess:
    id = "s1"
    endpoint_url = "http://llm"
    model = "m"
    headers = {}
    context_length = 0
    owner = "alice"

    def __init__(self):
        self.added = []

    def add_message(self, msg):
        self.added.append(msg)


async def test_idle_round_starts_detached_run(monkeypatch):
    g = _mk_goal()
    sess = _FakeSess()

    class _FakeSM:
        def get_session(self, sid):
            assert sid == "s1"
            return sess

    monkeypatch.setattr(gd, "_round_stream", lambda s, m, o: _gen())
    # Robust to sibling-test sys.modules pollution (companion tests stub
    # endpoint_resolver, which breaks a fresh `import src.ai_interaction`).
    try:
        import src.ai_interaction as ai
    except ImportError:
        import sys as _sys
        import types as _types
        ai = _types.ModuleType("src.ai_interaction")
        _sys.modules["src.ai_interaction"] = ai
    # raising=False: under sibling-test pollution the cached module may be a
    # partially-initialized object without the attribute — set it anyway.
    monkeypatch.setattr(ai, "get_session_manager", lambda: _FakeSM(), raising=False)

    await gd._start_round("alice", g)
    assert agent_runs.is_active("s1")
    assert goal_mod.get_goal("alice")["roundsStarted"] == 1
    await asyncio.sleep(0.05)
    assert agent_runs.get_status("s1") == "done"
    # The goal_round event was fanned into the run buffer.
    joined = "".join(agent_runs._RUNS["s1"].buffer)
    assert '"goal_round"' in joined


async def test_round_stream_persists_output(monkeypatch):
    async def _fake_loop(*a, **kw):
        yield 'data: {"delta": "progress note"}\n\n'
        yield 'data: {"type": "tool_output", "tool": "bash", "command": "ls", "output": "x", "exit_code": 0}\n\n'
        yield 'data: {"delta": " done"}\n\n'
        yield "data: [DONE]\n\n"

    import src.agent_loop as al
    monkeypatch.setattr(al, "stream_agent_loop", _fake_loop)
    from src.cost_ledger import set_cost_scope
    try:
        set_cost_scope("s1", "chat", "alice")
    except Exception:
        pass

    sess = _FakeSess()
    chunks = []
    async for c in gd._round_stream(sess, [{"role": "user", "content": "go"}], "alice"):
        chunks.append(c)
    assert chunks[-1] == "data: [DONE]\n\n"
    assert len(sess.added) == 1
    assert sess.added[0].content == "progress note done"
    assert sess.added[0].metadata.get("goal_round") is True
    assert len(sess.added[0].metadata.get("tool_events", [])) == 1


async def test_on_run_finished_blocks_after_3_errors():
    _mk_goal(owner="alice", session="s1")
    gd.on_run_finished("s1", "error")
    gd.on_run_finished("s1", "error")
    g = goal_mod.get_goal("alice")
    assert g["phase"] == "active"  # not yet
    gd.on_run_finished("s1", "error")
    g = goal_mod.get_goal("alice")
    assert g["phase"] == "blocked"
    assert "3 consecutive goal rounds failed" in g["blockedReason"]["message"]


async def test_on_run_finished_resets_error_count_on_success():
    _mk_goal(owner="alice", session="s1")
    gd.on_run_finished("s1", "error")
    gd.on_run_finished("s1", "done")
    gd.on_run_finished("s1", "error")
    gd.on_run_finished("s1", "error")
    assert goal_mod.get_goal("alice")["phase"] == "active"  # only 2 consecutive


async def test_find_goal_by_session():
    _mk_goal(owner="alice", session="s1")
    assert gd._find_goal_by_session("s1") is not None
    assert gd._find_goal_by_session("other") is None
