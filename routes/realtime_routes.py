# routes/realtime_routes.py
"""GPT-Live — full-duplex WebRTC voice + a reasoning agent that decides what to
send from the voice conversation into the chat.

- ``POST /api/realtime/session`` exchanges a browser SDP offer for GPT-Live's
  SDP answer (the API key never leaves the server; CSP-friendly).
- ``POST /api/realtime/reason`` watches the live voice transcript and, using the
  configured utility/default LLM, decides whether a message should be queued
  for the chat. Returns ``{send, message}``; the frontend queues and dispatches
  it one-at-a-time when the chat is idle.
"""

import json
import logging

import httpx
from fastapi import APIRouter, HTTPException, Request, Response

from src.auth_helpers import effective_user
from src.settings import load_settings
from src.endpoint_resolver import resolve_endpoint
from src.llm_core import llm_call_async

logger = logging.getLogger(__name__)

REASON_PROMPT = """You are deciding whether anything from a live voice conversation should be sent to a separate chat thread for action.

Rules:
- Only send a message when the user made a concrete request, asked a question, gave a task, or shared information the chat agent should act on or remember.
- Do NOT send small talk, greetings, acknowledgements, or conversational filler.
- If sending, write ONE concise, self-contained message the chat agent can act on directly. Include all necessary context from the conversation, because the chat agent cannot hear the voice.
- Output ONLY valid JSON, one of:
  {"send": false}
  {"send": true, "message": "<the message>"}"""


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

    @router.post("/reason")
    async def reason_to_chat(request: Request):
        """Decide whether the recent voice transcript should become a chat message."""
        if not effective_user(request):
            raise HTTPException(401, "Authentication required")

        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "JSON body required")

        transcript = (body.get("transcript") or "").strip()
        if not transcript:
            return {"send": False}

        url, model, headers = resolve_endpoint("utility")
        if not url or not model:
            return {"send": False}

        messages = [
            {"role": "system", "content": REASON_PROMPT},
            {"role": "user", "content": f"Voice transcript:\n{transcript}"},
        ]
        try:
            raw = await llm_call_async(
                url, model, messages,
                temperature=0.1, max_tokens=200,
                headers=headers, timeout=30,
            )
        except Exception as e:
            logger.error(f"Reason-to-chat failed: {e}")
            return {"send": False}

        try:
            import re as _re
            m = _re.search(r"\{.*\}", raw or "", _re.DOTALL)
            data = json.loads(m.group(0) if m else (raw or ""))
        except Exception:
            logger.warning(f"Reason-to-chat unparseable: {raw[:200]!r}")
            return {"send": False}

        send = bool(data.get("send"))
        message = (data.get("message") or "").strip()
        if send and not message:
            return {"send": False}
        return {"send": send, "message": message}

    return router
