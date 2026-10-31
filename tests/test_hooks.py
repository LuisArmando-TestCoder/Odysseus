"""Tests for src/hooks.py — hook-point seam (Cluster E / H2)."""

import asyncio

import pytest

from src.hooks import HookPoint, register_hook, register_stream_hook, run_hooks, run_post_response_stream


@pytest.fixture(autouse=True)
def _clean():
    from src import hooks
    hooks._REGISTRY.clear()
    hooks._STREAM_REGISTRY.clear()
    hooks._builtins_done = True  # keep the file-guard builtins out of these tests
    yield
    hooks._REGISTRY.clear()
    hooks._STREAM_REGISTRY.clear()
    hooks._builtins_done = False


async def test_dict_hook_transforms_payload():
    async def _upper(payload):
        return {"message": payload["message"].upper(), "session_id": payload["session_id"]}

    register_hook(HookPoint.USER_PROMPT_SUBMIT, _upper)
    out = await run_hooks(HookPoint.USER_PROMPT_SUBMIT, {"message": "hi", "session_id": "s1"})
    assert out["message"] == "HI"


async def test_dict_hook_failure_is_contained():
    def _boom(payload):
        raise RuntimeError("hook boom")

    register_hook(HookPoint.PRE_TOOL_USE, _boom)
    out = await run_hooks(HookPoint.PRE_TOOL_USE, {"tool": "bash", "content": "ls"})
    assert out["content"] == "ls"  # original passes through


async def test_hooks_run_in_order():
    order = []

    async def _a(payload):
        order.append("a")
        return payload

    async def _b(payload):
        order.append("b")
        return payload

    register_hook(HookPoint.POST_TOOL_USE, _a)
    register_hook(HookPoint.POST_TOOL_USE, _b)
    await run_hooks(HookPoint.POST_TOOL_USE, {"tool": "x"})
    assert order == ["a", "b"]


async def test_stream_hook_chain_composes():
    async def _gen():
        yield "data: one\n\n"
        yield "data: [DONE]\n\n"

    async def _upper_hook(agen, *, url, model, headers):
        async for chunk in agen:
            yield chunk.replace("one", "ONE")

    register_stream_hook(HookPoint.POST_RESPONSE, _upper_hook)
    out = [c async for c in run_post_response_stream(_gen(), url="u", model="m", headers=None)]
    assert "ONE" in out[0]
    assert out[-1] == "data: [DONE]\n\n"


async def test_stream_hook_failure_keeps_passthrough():
    async def _gen():
        yield "data: ok\n\n"

    def _broken(agen, *, url, model, headers):
        raise RuntimeError("composition boom")

    register_stream_hook(HookPoint.POST_RESPONSE, _broken)
    out = [c async for c in run_post_response_stream(_gen(), url="u", model="m", headers=None)]
    assert out == ["data: ok\n\n"]  # failed hook skipped, stream intact


async def test_duplicate_registration_ignored():
    calls = []

    async def _h(payload):
        calls.append(1)
        return payload

    register_hook(HookPoint.PRE_TOOL_USE, _h)
    register_hook(HookPoint.PRE_TOOL_USE, _h)
    await run_hooks(HookPoint.PRE_TOOL_USE, {})
    assert len(calls) == 1
