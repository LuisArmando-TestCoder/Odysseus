"""Tests for llm_core's fallback chain retry policy (T1.5).

Monkeypatches src.llm_core.stream_llm so the retry/fallback logic is tested
offline: same-candidate retries emit `llm_retry` events (only when
max_retries > 1); the default (max_retries=1) preserves today's behavior and
emits `fallback` instead.
"""

import json

import src.llm_core as lc


def _make_fake(calls):
    """Fake stream_llm: `calls` maps model → list of per-CALL chunk lists.
    Each invocation consumes the next entry (last entry repeats)."""
    counters: dict = {}

    async def _fake(url, model, messages, headers=None, **kwargs):
        seqs = calls.get(model) or []
        n = counters.get(model, 0)
        counters[model] = n + 1
        seq = seqs[n] if n < len(seqs) else seqs[-1]
        for chunk in seq:
            yield chunk

    return _fake


async def _collect(agen):
    return [c async for c in agen]


async def test_same_candidate_retry_emits_llm_retry(monkeypatch):
    monkeypatch.setattr(lc, "stream_llm", _make_fake({
        "m1": [
            ['event: error\ndata: {"error": "flaky"}\n\n'],
            ['data: {"delta": "recovered"}\n\n', "data: [DONE]\n\n"],
        ],
    }))
    out = await _collect(lc._stream_llm_with_fallback_raw(
        [("u", "m1", None)], [], max_retries=2,
    ))
    assert any('"llm_retry"' in c for c in out)
    assert not any('"fallback"' in c for c in out)
    assert any('"recovered"' in c for c in out)


async def test_default_no_retry_switches_candidate_with_fallback_event(monkeypatch):
    monkeypatch.setattr(lc, "stream_llm", _make_fake({
        "m1": [['event: error\ndata: {"error": "down"}\n\n']],
        "m2": [['data: {"delta": "answer"}\n\n', "data: [DONE]\n\n"]],
    }))
    out = await _collect(lc._stream_llm_with_fallback_raw(
        [("u", "m1", None), ("u2", "m2", None)], [],
    ))
    assert not any('"llm_retry"' in c for c in out)
    assert any('"fallback"' in c for c in out)
    assert any('"answer"' in c for c in out)


async def test_retries_exhausted_then_fallback(monkeypatch):
    monkeypatch.setattr(lc, "stream_llm", _make_fake({
        "m1": [['event: error\ndata: {"error": "always"}\n\n']],
        "m2": [['data: {"delta": "ok"}\n\n', "data: [DONE]\n\n"]],
    }))
    out = await _collect(lc._stream_llm_with_fallback_raw(
        [("u", "m1", None), ("u2", "m2", None)], [], max_retries=3,
    ))
    retries = [c for c in out if '"llm_retry"' in c]
    assert len(retries) == 2  # two failed attempts announce; third switches
    assert any('"fallback"' in c for c in out)
    assert any('"ok"' in c for c in out)


async def test_retry_cap_clamped(monkeypatch):
    monkeypatch.setattr(lc, "stream_llm", _make_fake({
        "m1": [['event: error\ndata: {"error": "always"}\n\n']],
    }))
    out = await _collect(lc._stream_llm_with_fallback_raw(
        [("u", "m1", None)], [], max_retries=99,
    ))
    retries = [c for c in out if '"llm_retry"' in c]
    assert len(retries) == 4  # clamped to 5 total attempts


async def test_real_output_never_triggers_retry(monkeypatch):
    # A mid-stream error AFTER content must pass through unchanged — never
    # retried (retrying would duplicate streamed tokens).
    monkeypatch.setattr(lc, "stream_llm", _make_fake({
        "m1": [['data: {"delta": "partial"}\n\n',
                'event: error\ndata: {"error": "late"}\n\n',
                "data: [DONE]\n\n"]],
    }))
    out = await _collect(lc._stream_llm_with_fallback_raw(
        [("u", "m1", None), ("u2", "m2", None)], [], max_retries=3,
    ))
    assert not any('"llm_retry"' in c for c in out)
    assert not any('"fallback"' in c for c in out)
    assert any('"partial"' in c for c in out)
    assert any('"late"' in c for c in out)  # the late error passed through
