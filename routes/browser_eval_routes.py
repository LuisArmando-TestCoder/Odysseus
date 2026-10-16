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


class LogBody(BaseModel):
    level: str = "info"
    message: str = ""


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

    @router.post("/log")
    async def browser_log(body: LogBody, request: Request):
        # The extension forwards its console.log/console.error lines here so
        # the bridge's browser-side activity is visible in the SERVER's tool
        # process (the terminal running Odysseus), not just the browser's
        # DevTools / service-worker console. Fire-and-forget; always 200.
        msg = (body.message or "").strip()
        if not msg:
            return JSONResponse({"ok": True})
        line = f"[ODY-BROWSER] {msg}"
        if (body.level or "info").lower() in ("error", "warn", "warning"):
            print(line, flush=True)
            logger.warning(msg)
        else:
            print(line, flush=True)
            logger.info(msg)
        return JSONResponse({"ok": True})

    return router
