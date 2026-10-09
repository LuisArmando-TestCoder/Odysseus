# routes/realtime_routes.py
"""GPT-Live — full-duplex WebRTC voice with a separate backend.

The browser creates an SDP offer and POSTs it here (as JSON). This backend
exchanges it for GPT-Live's SDP answer over ``POST /v1/live/sessions``. The API
key never leaves the server, and the browser only talks to this origin
(CSP-friendly). GPT-Live handles the spoken conversation (listen-while-speaking,
barge-in) and can delegate reasoning/tools to a backend — our existing chat
workflow.
"""

import logging

import httpx
from fastapi import APIRouter, HTTPException, Request, Response

from src.auth_helpers import effective_user
from src.settings import load_settings

logger = logging.getLogger(__name__)


def setup_realtime_routes() -> APIRouter:
    router = APIRouter(prefix="/api/realtime", tags=["realtime"])

    @router.post("/session")
    async def create_live_session(request: Request):
        """Exchange a browser SDP offer for GPT-Live's SDP answer."""
        if not effective_user(request):
            raise HTTPException(401, "Authentication required")

        settings = load_settings()
        api_key = settings.get("openai_api_key", "")
        if not api_key:
            raise HTTPException(400, "OpenAI API key not configured")

        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "JSON body required")

        sdp = (body.get("sdp") or "").strip()
        if not sdp:
            raise HTTPException(400, "An SDP offer is required")
        logger.info(f"GPT-Live SDP offer received: {len(sdp)} bytes | head={sdp[:80]!r}")

        model = settings.get("realtime_model", "gpt-live-1")
        instructions = settings.get(
            "realtime_instructions", "Be concise and conversational."
        )

        payload = {
            "session": {
                "model": model,
                "instructions": instructions,
            },
            "transport": {"type": "webrtc", "sdp": sdp},
        }

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.post(
                    "https://api.openai.com/v1/live/sessions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json=payload,
                )
        except httpx.HTTPError as e:
            logger.error(f"GPT-Live session exchange failed: {e}")
            raise HTTPException(502, f"OpenAI unreachable: {e}")

        if r.status_code not in (200, 201):
            logger.error(f"GPT-Live session exchange: {r.status_code} {r.text[:500]}")
            raise HTTPException(r.status_code, r.text[:500])

        # Forward OpenAI's JSON: { session: { id }, transport: { sdp } }.
        return Response(content=r.text, media_type="application/json")

    return router
