"""I1 queue-UX round: backend follower primitives + frontend queue wiring.

The user hit the classic bug: sending a second message while a turn was live
CANCELLED the first run (the frontend aborted it). Queue-don't-cancel now:
the second POST returns a follower stream (quiet while the current run
drains, then the queued turn's own stream) and the frontend hands the
renderer over at `turn_resumed` instead of stopping anything.
"""

from pathlib import Path

from src import agent_runs, turn_inbox


async def test_follower_handover_primitives(monkeypatch):
    sid = "follower-session"
    turn_inbox.clear(sid)

    async def run1():
        yield 'data: {"type": "delta", "delta": "one"}\n\n'
        yield "data: [DONE]\n\n"

    agent_runs.start(sid, run1(), owner="u")
    run_obj = agent_runs.current_run(sid)
    assert run_obj is not None and run_obj.status == "running"

    # Drain run 1 exactly like a subscriber would.
    got1 = [ch async for ch in agent_runs.subscribe(sid)]
    assert any("one" in ch for ch in got1)
    assert not agent_runs.is_active(sid)

    # Queue a second message and drain it via start_next (what
    # agent_runs.on_finish → drain_on_finish does).
    turn_inbox.enqueue(sid, "second")

    def factory(s, m):
        async def gen():
            yield 'data: {"type": "turn_resumed", "data": {"session_id": "' + s + '"}}\n\n'
            yield 'data: {"type": "delta", "delta": "two"}\n\n'
            yield "data: [DONE]\n\n"
        return gen(), "u"

    monkeypatch.setattr(turn_inbox, "_factory", factory)
    assert turn_inbox.start_next(sid) is True
    assert agent_runs.current_run(sid).status == "running"

    got2 = [ch async for ch in agent_runs.subscribe(sid)]
    assert any("turn_resumed" in ch for ch in got2), got2
    assert any("two" in ch for ch in got2)


def test_frontend_queue_wiring_pins():
    chat_js = (Path(__file__).resolve().parents[1] / "static/js/chat.js").read_text(encoding="utf-8")
    # Send side: queue, never cancel.
    assert "await _sendQueuedMessage(sessionId, submitBtn, _qmsg);" in chat_js
    assert "// If currently streaming, stop it" not in chat_js  # old cancel-on-resend path is gone
    # Follower handover.
    assert "if (j.type === 'turn_resumed')" in chat_js
    assert "api/chat/resume/" in chat_js
    # Primary finalize keeps streaming state while queued.
    assert "if (!_queuePending) updateSubmitButton('idle', submitBtn);" in chat_js


def test_backend_follower_stream_pins():
    cr = (Path(__file__).resolve().parents[1] / "routes/chat_routes.py").read_text(encoding="utf-8")
    assert "def _queued_follower_stream" in cr
    assert '"type": "turn_resumed"' in cr
    ar = (Path(__file__).resolve().parents[1] / "src/agent_runs.py").read_text(encoding="utf-8")
    assert "def current_run(session_id" in ar
