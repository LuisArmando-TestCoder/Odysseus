# src/hooks.py
"""Hook-point seam (Cluster E / H2) — mirror of DSH's packages/hooks waterfall.

Four hook points with ordered registries and contained errors (a failing hook
is logged and skipped — it can never break a response):

* USER_PROMPT_SUBMIT — before context build; may rewrite the user message.
* PRE_TOOL_USE      — before a tool executes; may rewrite the tool content.
* POST_TOOL_USE     — after a tool executes; may rewrite the result dict.
* POST_RESPONSE     — wraps every LLM stream. No built-in hook: the response
  "middleman" rewriter was removed (2026-10-31) because rewriting answers
  after the fact produced hallucinations.

POST_RESPONSE stream hooks compose in registration order (async gen → async
gen); the other points are async dict → dict transforms.
"""

import inspect
import logging
from collections import defaultdict
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List

logger = logging.getLogger(__name__)


class HookPoint(str, Enum):
    USER_PROMPT_SUBMIT = "user_prompt_submit"
    PRE_TOOL_USE = "pre_tool_use"
    POST_TOOL_USE = "post_tool_use"
    POST_RESPONSE = "post_response"


# point → ordered list of async dict-transforms: fn(payload) -> dict|awaitable
_REGISTRY: Dict[HookPoint, List[Callable[..., Any]]] = defaultdict(list)
# point → ordered list of stream transforms: fn(agen, **ctx) -> AsyncGenerator
_STREAM_REGISTRY: Dict[HookPoint, List[Callable[..., Any]]] = defaultdict(list)
_builtins_done = False


def register_hook(point: HookPoint, fn: Callable[..., Any]) -> None:
    """Register a dict-transform hook. Idempotent per fn (no duplicates)."""
    reg = _REGISTRY[point]
    if fn not in reg:
        reg.append(fn)


def register_stream_hook(point: HookPoint, fn: Callable[..., Any]) -> None:
    """Register a stream-transform hook: fn(agen, **ctx) → async generator."""
    reg = _STREAM_REGISTRY[point]
    if fn not in reg:
        reg.append(fn)


async def run_hooks(point: HookPoint, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Run every registered dict hook in order; each may transform the payload.
    Hook failures are logged and skipped (the original payload passes through)."""
    register_builtins()  # idempotent — ensures file-guard hooks are mounted
    for fn in list(_REGISTRY.get(point, ())):
        try:
            out = fn(payload)
            if inspect.isawaitable(out):
                out = await out
            if isinstance(out, dict):
                payload = out
        except Exception as e:
            logger.warning("[hooks] %s hook failed: %s", point.value, e)
    return payload


def register_builtins() -> None:
    """Register the built-in hooks: the
    file-edit guardrails (read-before-write + edit diff, src/file_guard.py).
    Idempotent; called lazily by run_hooks / run_post_response_stream."""
    global _builtins_done
    if _builtins_done:
        return
    _builtins_done = True
    from src.file_guard import post_tool_use_hook, pre_tool_use_hook
    register_hook(HookPoint.PRE_TOOL_USE, pre_tool_use_hook)
    register_hook(HookPoint.POST_TOOL_USE, post_tool_use_hook)


async def run_post_response_stream(agen, *, url, model, headers):
    """Apply the POST_RESPONSE stream-hook chain over a raw chunk generator.

    Registered stream hooks compose in registration order. With none
    registered the model's stream passes through unchanged."""
    register_builtins()
    cur = agen
    for fn in list(_STREAM_REGISTRY.get(HookPoint.POST_RESPONSE, ())):
        try:
            cur = fn(cur, url=url, model=model, headers=headers)
        except Exception as e:
            logger.warning("[hooks] post_response hook composition failed: %s", e)
    async for chunk in cur:
        yield chunk
