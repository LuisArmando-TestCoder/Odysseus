# routes/browser_eval_routes.py
"""HTTP bridge for the chat-side browser JavaScript executor.

The agent tool ``run_javascript`` registers an ``asyncio.Future`` keyed by a
single-use ``token`` (see ``src/browser_eval.py``). It pushes that token to the
open web page via an SSE ``tool_progress`` event; the page evaluates the code
and POSTs the result back here to resolve the future. There is no polling — one
request, one resolution.
"""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class EvalResultBody(BaseModel):
    token: str = Field(min_length=8, max_length=64)
    output: str = ""
    error: str = ""


def setup_browser_eval_routes() -> APIRouter:
    router = APIRouter(prefix="/api/browser", tags=["browser"])

    @router.post("/eval-result")
    async def eval_result(body: EvalResultBody, request: Request):
        # Single-use token: at most one resolution per request. An unknown or
        # already-resolved token is a no-op — nothing is executed server-side
        # here, only a pending future is resolved.
        from src.browser_eval import resolve_eval
        ok = resolve_eval(body.token, {"output": body.output, "error": body.error})
        return JSONResponse({"ok": ok})

    return router