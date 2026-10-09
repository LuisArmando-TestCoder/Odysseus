# routes/realtime_routes.py
"""OpenAI Realtime API — mint a short-lived ephemeral key so the browser can
open a WebRTC voice session without ever seeing our API key.

The seamless "Advanced Voice Mode" experience is: the browser streams mic audio
over WebRTC directly to OpenAI, receives streamed TTS audio back, and OpenAI's
server-side VAD handles turn-taking and barge-in. The backend's only job here is
to exchange our long-lived API key for a short-lived `ek_…` client secret.
"""

import logging
import httpx
from fastapi import APIRouter, HTTPException, Request

from src.auth_helpers import effective_user
from src.settings import load_settings

logger = logging.getLogger(__name__)


def setup_realtime_routes() -> APIRouter:
    router = APIRouter(prefix="/api/realtime", tags=["realtime"])

    @router.post("/ephemeral")
    async def mint_ephemeral_key(request: Request):
        """POST body (optional): { model, voice, instructions }. Returns an
        ephemeral `client_secret` the browser uses as the WebRTC bearer token."""
        if not effective_user(request):
            raise HTTPException(403, "Authentication required")

        settings = load_settings()
        api_key = settings.get("openai_api_key", "")
        if not api_key:
            raise HTTPException(400, "OpenAI API key not configured")

        try:
            body = await request.json()
        except Exception:
            body = {}
        model = body.get("model") or settings.get("realtime_model", "gpt-realtime-2.1")
        voice = body.get("voice") or settings.get("realtime_voice", "alloy")
        instructions = body.get("instructions", "")

        # GA session shape: session.type + session.audio.output.voice.
        session_payload = {
            "session": {
                "type": "realtime",
                "model": model,
                "audio": {"output": {"voice": voice}},
            }
        }
        if instructions:
            session_payload["session"]["instructions"] = instructions

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.post(
                    "https://api.openai.com/v1/realtime/client_secrets",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json=session_payload,
                )
        except httpx.HTTPError as e:
            logger.error(f"Realtime ephemeral mint failed: {e}")
            raise HTTPException(502, f"OpenAI unreachable: {e}")

        if r.status_code != 200:
            logger.error(f"Realtime ephemeral mint: {r.status_code} {r.text[:500]}")
            raise HTTPException(r.status_code, r.text[:500])

        data = r.json()
        # GA response: { value: "ek_...", expires_at, session }.
        client_secret = data.get("value", "")
        if not client_secret:
            raise HTTPException(502, "No ephemeral key (value) in OpenAI response")

        return {"value": client_secret, "model": model, "voice": voice}

    return router
