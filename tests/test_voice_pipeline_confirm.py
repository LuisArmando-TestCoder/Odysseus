"""Voice → chat pipeline regressions from the 2026-10-28 transcript.

- (2026-10-31) an ambiguous request is sent at once — no "shall I send it?" question
- asking for the same thing again is caught as a duplicate
- the chat agent's narration read aloud (relay) is never forged into a request
- no "follow-through" forge while the chat is already working
- "what does the chat say?" is answered from the thread, not stalled
- a duplicate names the earlier message instead of "similar"

Offline: the decider, guard and utility LLM are monkeypatched.
"""
import asyncio

import pytest

MSG = "I want to benchmark grassroots neighbor collaborations across countries."


@pytest.fixture
def pipe(monkeypatch, tmp_path):
    import services.voice.memory as M
    import services.voice.pipeline as P

    monkeypatch.setattr(M, "_memory_dir", lambda: str(tmp_path))
    monkeypatch.setattr(M, "_registry", {})
    monkeypatch.setattr(P, "chat_summary_spine", lambda *a, **k: "CHAT_AI: Transition Towns, SEWA, Mondragon")
    monkeypatch.setattr(P, "session_documents_spine", lambda *a, **k: "")
    monkeypatch.setattr(P, "jev_available", lambda: False)
    st = {"decision": {}, "guard": {"coherent": 1, "duplicate": 0, "faithful": 1, "directed": 0.5}}

    async def _decide(owner, state, use_jev):
        d = dict(engine="llm", should_send=0, ready=1, already_sent=0, intent="none", answerable=0,
                 commitment=0, stop_talking=0, interrupt=0, negative_directive=0, directed_to_chat=0,
                 confirms_pending=0, urgency=0)
        d.update(st["decision"])
        st["state"] = state
        return d

    async def _guard(owner, message, ctx, use_jev):
        return dict(st["guard"])

    async def _utility(owner, system, user, max_tokens=400):
        if "extract" in system.lower():
            return ('{"found": true, "answer": "The chat found Transition Towns, SEWA and Mondragon.", '
                    '"quote": "Transition Towns, SEWA, Mondragon"}')
        return MSG

    monkeypatch.setattr(P, "_decide", _decide)
    monkeypatch.setattr(P, "_guard", _guard)
    monkeypatch.setattr(P, "_utility", _utility)
    monkeypatch.setattr(P, "_parse_forged", lambda raw: {"message": raw, "title": "", "malformed": False})

    def run(text, speaker="You", voice="v-confirm", **kw):
        async def go():
            turn = {"speaker": speaker, "text": text}
            if kw.pop("relay", False):
                turn["relay"] = True
            return [e async for e in P.run_pipeline("owner", voice, [turn], "sess", **kw)]
        return asyncio.run(go())

    st["run"] = run
    st["mem"] = lambda voice="v-confirm": M.get_voice_memory("owner", voice)
    return st


def _types(evs, t):
    return [e for e in evs if e["type"] == t]


def test_ambiguous_request_is_sent_without_asking(pipe):
    """2026-10-31: no "Send this to the chat? Say yes" round-trip. An ambiguous
    direction score (0.5) sends at once; nothing waits for a yes."""
    pipe["decision"] = {"should_send": 0.9, "intent": "research"}
    evs = pipe["run"]("So I want to benchmark the countries that did this, so tell me", voice="v1")
    assert not _types(evs, "needs_clarification")
    forged = _types(evs, "forged")
    assert forged and forged[0]["message"] == MSG
    assert pipe["mem"]("v1").pending_confirm is None


def test_repeating_the_request_is_a_duplicate_not_a_second_send(pipe):
    pipe["decision"] = {"should_send": 0.9, "intent": "research"}
    assert _types(pipe["run"]("Okay so tell me about the grassroots benchmark across countries", voice="v2"), "forged")
    evs = pipe["run"]("Yeah, benchmark the grassroots collaborations across the countries", voice="v2")
    assert not _types(evs, "forged") and not _types(evs, "needs_clarification")


def test_relayed_narration_and_busy_chat_never_forge(pipe):
    pipe["decision"] = {"commitment": 0.95}
    evs = pipe["run"]("I'll write it up as a document so it's well organized.", speaker="AI",
                      voice="v3", relay=True)
    assert not _types(evs, "forged")
    assert not pipe["mem"]("v3").new_assistant_turns()
    evs = pipe["run"]("I'll compile the benchmark into a document now.", speaker="AI",
                      voice="v3", chat_busy=True)
    assert not _types(evs, "forged")


def test_what_does_the_chat_say_is_answered_from_context(pipe):
    pipe["decision"] = {"answerable": 0.9, "directed_to_chat": 0.8}
    evs = pipe["run"]("Based on what you already have in the chat, what does the chat say", voice="v4")
    assert _types(evs, "answer_from_context")


def test_duplicate_names_the_original(pipe):
    pipe["guard"]["directed"] = 1.0
    pipe["decision"] = {"should_send": 0.9, "intent": "research"}
    assert _types(pipe["run"]("Benchmark grassroots neighbor collaborations please", voice="v5"), "forged")
    evs = pipe["run"]("Benchmark the grassroots neighbor collaborations across countries", voice="v5")
    dup = [e for e in evs if e.get("reason") == "duplicate"]
    assert dup and dup[0]["duplicate_of"] == MSG


def test_plain_yes_after_a_send_sends_nothing(pipe):
    pipe["decision"] = {"should_send": 0.9, "intent": "research"}
    pipe["run"]("So I want to benchmark the countries that did this, so tell me", voice="v6")
    pipe["decision"] = {"confirms_pending": 0.95}
    evs = pipe["run"]("Sí", voice="v6")
    assert not _types(evs, "forged")
