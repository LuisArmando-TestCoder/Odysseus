# routes/realtime_routes.py
"""OpenAI Realtime API — unified WebRTC interface.

The browser creates an SDP offer and POSTs it here; this backend combines it
with the session config and exchanges it for OpenAI's SDP answer over
``POST /v1/realtime/calls``. The API key never leaves the server, and the
browser only ever talks to this origin (CSP-friendly).
"""

import json
import logging

import httpx
from fastapi import APIRouter, HTTPException, Request, Response

from src.auth_helpers import effective_user
from src.settings import load_settings

logger = logging.getLogger(__name__)


def setup_realtime_routes() -> APIRouter:
    router = APIRouter(prefix="/api/realtime", tags=["realtime"])

    @router.post("/session")
    async def create_realtime_session(request: Request):
        """Exchange a browser SDP offer for OpenAI's SDP answer (raw text)."""
        if not effective_user(request):
            raise HTTPException(401, "Authentication required")

        settings = load_settings()
        api_key = settings.get("openai_api_key", "")
        if not api_key:
            raise HTTPException(400, "OpenAI API key not configured")

        sdp = (await request.body()).decode("utf-8", "replace").strip()
        if not sdp:
            raise HTTPException(400, "An SDP offer is required")

        model = settings.get("realtime_model", "gpt-realtime-2.1")
        voice = settings.get("realtime_voice", "alloy")
        session_config = json.dumps({
            "type": "realtime",
            "model": model,
            "audio": {"output": {"voice": voice}},
        })

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.post(
                    "https://api.openai.com/v1/realtime/calls",
                    headers={"Authorization": f"Bearer {api_key}"},
                    files={
                        "sdp": (None, sdp, "application/sdp"),
                        "session": (None, session_config, "application/json"),
                    },
                )
        except httpx.HTTPError as e:
            logger.error(f"Realtime session exchange failed: {e}")
            raise HTTPException(502, f"OpenAI unreachable: {e}")

        if r.status_code not in (200, 201):
            logger.error(f"Realtime session exchange: {r.status_code} {r.text[:500]}")
            raise HTTPException(r.status_code, r.text[:500])

        return Response(content=r.text, media_type="application/sdp")

    return router
