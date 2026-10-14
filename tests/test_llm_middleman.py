"""Tests for the LLM response middleman (src/llm_middleman.py).

The middleman sits between the raw LLM call layer and every caller. On each
response it asks JEV whether an undetermined feature (e.g. PII) is present and,
when flagged, rewrites the text through a reducer LLM — without taking the
response to zero. These tests stub JEV and the reducer so no network is needed.
"""

import asyncio

from src import llm_middleman


def _settings(monkeypatch, enabled=True, threshold=0.5):
    monkeypatch.setattr(
        llm_middleman, "middleman_settings",
        lambda: {
            "enabled": enabled,
            "model": "",
            "endpoint_id": "",
            "threshold": threshold,
            "question": "PII?",
            "prompt": "REDACT: {text}",
        },
    )


def test_guard_passes_through_when_disabled(monkeypatch):
    _settings(monkeypatch, enabled=False)
    assert llm_middleman.guard_response_sync("hello", url="u", model="m") == "hello"


def test_guard_passes_through_when_not_flagged(monkeypatch):
    _settings(monkeypatch, enabled=True)
    monkeypatch.setattr(
        llm_middleman, "guard_response_sync",
        lambda text, **kw: text,
    )
    # Directly exercise the flag path via a stubbed JEV returning low probability.
    import src.decisions as decisions
    monkeypatch.setattr(
        decisions, "jev_decide_sync",
        lambda state, questions, model=None: {"flag": {"type": "noul", "value": 0.1}},
    )
    assert llm_middleman.guard_response_sync("hello", url="u", model="m") == "hello"


def test_guard_reduces_when_flagged(monkeypatch):
    _settings(monkeypatch, enabled=True, threshold=0.5)
    import src.decisions as decisions
    monkeypatch.setattr(
        decisions, "jev_decide_sync",
        lambda state, questions, model=None: {"flag": {"type": "noul", "value": 0.9}},
    )
    import src.llm_core as llm_core
    monkeypatch.setattr(
        llm_core, "_llm_call_raw",
        lambda url, model, messages, **kw: "[REDACTED]",
    )
    out = llm_middleman.guard_response_sync("John Doe lives at 123 Main St", url="u", model="m")
    assert out == "[REDACTED]"


def test_guard_returns_original_when_reducer_fails(monkeypatch):
    _settings(monkeypatch, enabled=True, threshold=0.5)
    import src.decisions as decisions
    monkeypatch.setattr(
        decisions, "jev_decide_sync",
        lambda state, questions, model=None: {"flag": {"type": "noul", "value": 0.9}},
    )
    import src.llm_core as llm_core
    def _boom(*a, **k):
        raise RuntimeError("reducer down")
    monkeypatch.setattr(llm_core, "_llm_call_raw", _boom)
    original = "John Doe lives at 123 Main St"
    assert llm_middleman.guard_response_sync(original, url="u", model="m") == original


def test_guard_async_reduces_when_flagged(monkeypatch):
    _settings(monkeypatch, enabled=True, threshold=0.5)
    import src.decisions as decisions
    async def _decide(state, questions, model=None):
        return {"flag": {"type": "noul", "value": 0.9}}
    monkeypatch.setattr(decisions, "jev_decide", _decide)
    import src.llm_core as llm_core
    async def _raw(url, model, messages, **kw):
        return "[REDACTED]"
    monkeypatch.setattr(llm_core, "_llm_call_async_raw", _raw)
    out = asyncio.run(
        llm_middleman.guard_response_async("Jane Doe, jane@example.com", url="u", model="m")
    )
    assert out == "[REDACTED]"


def test_guard_stream_rewrites_text_deltas(monkeypatch):
    _settings(monkeypatch, enabled=True, threshold=0.5)
    import src.decisions as decisions
    async def _decide(state, questions, model=None):
        return {"flag": {"type": "noul", "value": 0.9}}
    monkeypatch.setattr(decisions, "jev_decide", _decide)
    import src.llm_core as llm_core
    async def _raw(url, model, messages, **kw):
        return "[REDACTED]"
    monkeypatch.setattr(llm_core, "_llm_call_async_raw", _raw)

    async def _agen():
        yield 'data: {"delta": "John"}\n\n'
        yield 'data: {"delta": " Doe"}\n\n'
        yield 'data: {"type": "usage", "data": {"input_tokens": 1}}\n\n'
        yield "data: [DONE]\n\n"

    async def _run():
        out = []
        async for c in llm_middleman.guard_stream(_agen(), url="u", model="m"):
            out.append(c)
        return out

    chunks = asyncio.run(_run())
    # usage + DONE preserved verbatim
    assert any('"type": "usage"' in c for c in chunks)
    assert "data: [DONE]\n\n" in chunks
    # text deltas replaced with the reduced text
    deltas = [c for c in chunks if '"delta"' in c]
    assert deltas, f"no delta chunks in {chunks}"
    assert all("[REDACTED]" in c for c in deltas)


def test_guard_stream_flushes_longer_rewrite(monkeypatch):
    """A rewrite longer than the original must be fully emitted, not truncated.

    Regression: the re-emission loop used to emit one re-chunk per original
    delta, silently dropping the extra chunks when the reducer expanded the text
    (which the default prompt explicitly asks for).
    """
    _settings(monkeypatch, enabled=True, threshold=0.5)
    import src.decisions as decisions
    async def _decide(state, questions, model=None):
        return {"flag": {"type": "noul", "value": 0.9}}
    monkeypatch.setattr(decisions, "jev_decide", _decide)
    import src.llm_core as llm_core
    # Reducer returns a long answer (>> 80 chars, so it spans multiple chunks).
    long_answer = "A" * 500
    async def _raw(url, model, messages, **kw):
        return long_answer
    monkeypatch.setattr(llm_core, "_llm_call_async_raw", _raw)

    async def _agen():
        # Original is a single short delta (a refusal).
        yield 'data: {"delta": "I cannot help."}\n\n'
        yield "data: [DONE]\n\n"

    async def _run():
        out = []
        async for c in llm_middleman.guard_stream(_agen(), url="u", model="m"):
            out.append(c)
        return out

    chunks = asyncio.run(_run())
    deltas = [c for c in chunks if '"delta"' in c]
    # Reassemble the emitted text and confirm nothing was dropped.
    emitted = ""
    for c in deltas:
        import json as _json
        emitted += _json.loads(c[6:])["delta"]
    assert emitted == long_answer, (
        f"rewrite truncated: emitted {len(emitted)} chars, expected {len(long_answer)}"
    )
    assert "data: [DONE]\n\n" in chunks
