"""S4 — concurrency soak: a goal round, a queued user turn, a pending
question, and a background subagent all make progress simultaneously, with no
run left dangling and no cross-session interference."""

import asyncio
import json

import pytest

import src.agent_runs as agent_runs
import src.goal_driver as gd
import src.subagents as subs
import src.turn_inbox as turn_inbox
import src.user_questions as uq
from src import constants
from src import goal as goal_mod


class _Sess:
    def __init__(self, sid, owner="alice"):
        self.id = sid
        self.name = f"chat-{sid}"
        self.owner = owner
        self.endpoint_url = "http://e"
        self.model = "m"
        self.headers = {}
        self.context_length = 0
        self.history = []

    def get_context_messages(self):
        return [{"role": m.role, "content": m.content} for m in self.history]

    def add_message(self, msg):
        self.history.append(msg)


class _Msg:
    def __init__(self, role, content):
        self.role = role
        self.content = content


class _SM:
    def __init__(self):
        self.sessions = {}

    def create_session(self, **kw):
        s = _Sess(kw["session_id"], owner=kw.get("owner", "alice"))
        s.name = kw.get("name") or s.name
        self.sessions[kw["session_id"]] = s
        return s

    def get_session(self, sid):
        return self.sessions.get(sid)


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setattr(constants, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(agent_runs, "_evict_grace", lambda: 3600.0)
    sm = _SM()
    monkeypatch.setattr(subs, "_session_manager", lambda: sm)
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
    monkeypatch.setattr(ai, "get_session_manager", lambda: sm, raising=False)
    agent_runs._RUNS.clear()
    agent_runs._FINISH_CALLBACKS.clear()
    gd._ERRORS.clear()
    gd._LOCKS.clear()
    turn_inbox._PENDING.clear()
    turn_inbox._factory = None
    uq.PENDING.clear()
    subs.SUBS.clear()
    subs._BY_SESSION.clear()
    subs._WAIT_FUTURES.clear()
    subs._HOOK_REGISTERED = False

    async def _stub_loop(endpoint_url, model, messages, **kw):
        for _ in range(2):
            yield 'data: {"delta": "working..."}\n\n'
            await asyncio.sleep(0.01)
        yield "data: [DONE]\n\n"

    import src.agent_loop as al
    monkeypatch.setattr(al, "stream_agent_loop", _stub_loop)
    yield
    for run in list(agent_runs._RUNS.values()):
        if run.evict_task and not run.evict_task.done():
            run.evict_task.cancel()
        if run.task and not run.task.done():
            run.task.cancel()
    for st in list(uq.PENDING.values()):
        if st.timeout_task and not st.timeout_task.done():
            st.timeout_task.cancel()
    agent_runs._RUNS.clear()
    agent_runs._FINISH_CALLBACKS.clear()
    turn_inbox._PENDING.clear()
    turn_inbox._factory = None
    uq.PENDING.clear()
    subs.SUBS.clear()
    subs._BY_SESSION.clear()
    subs._WAIT_FUTURES.clear()
    subs._HOOK_REGISTERED = False


async def _wait_done(sid, timeout=3.0):
    for _ in range(int(timeout * 50)):
        if not agent_runs.is_active(sid):
            return True
        await asyncio.sleep(0.02)
    return False


async def test_soak_four_workloads_parallel():
    sm = subs._session_manager()
    sm.create_session(session_id="goal-s", owner="alice")
    sm.create_session(session_id="queue-s", owner="alice")
    sm.create_session(session_id="parent", owner="alice")

    # 1) Goal round in goal-s (detached).
    g = goal_mod.create_goal("alice", "soak goal", session_id="goal-s")
    await gd._start_round("alice", g)
    assert agent_runs.is_active("goal-s")

    # 2) A pending question in ask-s (no run — registry only).
    res = uq.ask("ask-s", [{"id": "q1", "question": "confirm?"}], can_ask_user=True)
    assert res["status"] == "pending"

    # 3) A running turn in queue-s + two queued messages (drain on finish).
    async def _slow_turn():
        yield 'data: {"delta": "t"}\n\n'
        await asyncio.sleep(0.02)
        yield "data: [DONE]\n\n"

    async def _factory_gen(sid, message):
        yield f'data: {"delta": "queued: %s"}\n\n' % (message or "",)
        yield "data: [DONE]\n\n"

    def _factory(sid, message):
        return (_factory_gen(sid, message), "alice")

    turn_inbox.register_stream_factory(_factory)
    agent_runs.on_finish(turn_inbox.drain_on_finish)
    agent_runs.start("queue-s", _slow_turn(), owner="alice")
    turn_inbox.enqueue("queue-s", "m1")
    turn_inbox.enqueue("queue-s", "m2")

    # 4) A background subagent from parent.
    r = await subs.run_subagent(
        parent_session_id="parent", owner="alice", mode="spawn",
        task="soak task", run_in_background=True,
    )
    assert r.get("subagent_started") is True
    child_sid = r["child_session_id"]

    # Everything settles independently.
    assert await _wait_done("goal-s")
    assert await _wait_done("queue-s")
    assert await _wait_done(child_sid)
    # The queued turns drained one after another.
    assert turn_inbox.queued_count("queue-s") == 0
    # The question was untouched by the other workloads and can still resolve.
    assert uq.get_pending("ask-s") is not None
    assert uq.answer("ask-s", [{"id": "q1", "selected": ["yes"]}]) is True
    # The goal round persisted output into its session.
    goal_sess = sm.get_session("goal-s")
    assert any(getattr(m, "role", None) == "assistant" for m in goal_sess.history)
    # No run left running.
    assert all(r["status"] != "running" for r in agent_runs.list_active())
