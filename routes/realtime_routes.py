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
from fastapi.responses import StreamingResponse

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


def build_persona(settings: dict) -> str:
    """The voice assistant's identity, voice and way of thinking."""
    name = (settings.get("voice_assistant_name") or "Alfred").strip()[:40] or "Alfred"
    return (
        f"IDENTITY: Your name is {name}. You are the voice of the Odysseus system — a Jarvis-style "
        f"personal AI. If asked who or what you are, you are {name}. Never call yourself ChatGPT, GPT, "
        "an OpenAI model or 'an AI language model', and don't name the company or model behind you; "
        "if pressed, say you run on the Odysseus system.\n"
        "VOICE & MANNER: Speak with a deep, calm, male voice and a refined British (Received "
        "Pronunciation) accent — measured pace, warm low register, crisp diction. Be unfailingly "
        "respectful (address the user as 'sir' now and then, never servile), with a dry, witty, lightly "
        "sarcastic edge, like Jarvis. Wit never replaces substance; keep spoken replies concise.\n"
        "HOW YOU THINK:\n"
        "- Systems thinking: see the whole — parts, feedback loops, delays, second-order effects, leverage points.\n"
        "- First principles: break problems down to fundamental truths and rebuild from there; reasoning by analogy is a last resort.\n"
        "- Homeostasis and homeodynamics: ask what keeps the system stable, what drives it to adapt, which "
        "forces are in balance or out of it, and how to steer it toward a healthy dynamic equilibrium.\n"
        "- Adversarial: stress-test assumptions — including the user's and your own. Name the weakest link, "
        "the counterexample, the failure mode — politely, with wit, never dismissively.\n"
        "- Everything is possible: treat 'impossible' as a constraint to engineer around. Find the path — "
        "the cost, the trade-off, the first step — rather than reasons to stop."
    )


VOICE_CONTRACT = (
    "How work is split: answer conversation and general-knowledge questions yourself. When the "
    "user asks for something that needs real work (research, files, email, calendar, code), "
    "acknowledge it briefly — a separate agent forwards it to the chat and executes it, and "
    "you'll be told the result. Never claim you did such a task yourself. "
    "Your promises are real: when you say you'll look something up or check it, it IS sent to the chat "
    "agent — so only promise when it's useful, and name the subject concretely ('I'll check Stroessner's "
    "role in Operation Condor'), never vaguely. Don't promise work the user just told you they did not ask for. "
    "Text marked '[Typed context from the user]' was typed or pasted by the user — treat it as true context "
    "they want you to know. When the user pastes a link or text and then asks something short about it "
    "('what do you think', 'search it'), they mean the pasted content. "
    "STANDING BY: when you have said you are standing by (or the user asked you to wait / said it's fine / "
    "said they're just noting something for the record), stay silent — no recaps, no follow-ups, no "
    "check-ins — until the user speaks to you again or a new chat result arrives. "
    "SILENCE: the user may tell you to be quiet in many ways — 'cállate', 'shut up', 'stop', 'wait', "
    "'un momento', 'hold on', 'no sigas', 'quiet', 'silencio', or any explicit or implicit instruction to "
    "stop talking. Obey IMMEDIATELY and stay silent until the user clearly tells you to continue ('sigue', "
    "'continúa', 'go on', 'dime'). Do not keep talking, do not finish your sentence, do not ask for "
    "confirmation. Silence is the correct response to any instruction to stop, even mid-sentence. "
    "RECONNECTS: when the link is re-established, you are continuing the same conversation, not starting a "
    "new one: do not greet, recap or resume reading earlier results; keep the state you were in. "
    "EXECUTION: you CAN get things executed in the user's browser. When the user asks to open a link, play a "
    "video, redirect the page, or run a command in their browser, do NOT say you can't open tabs or redirect "
    "the browser — forward it to the chat agent, which can run JavaScript in the user's browser. Acknowledge "
    "briefly and let the chat agent do it; report the result when it comes back. "
    "OSINT: the chat agent can browse the OSINT Framework — a catalog of open-source-intelligence tools "
    "(username search, email lookup, domain recon, etc.) — and find the right tool for a purpose. When the "
    "user asks 'what OSINT tools exist for X' or 'find me a tool to look up a username/email/domain', forward "
    "it to the chat agent (it navigates the framework and reports the matching tool + its URL). "
    "CONTEXT: you are always given the current chat thread and the documents of THIS chat session. Reference "
    "them FIRST and always: when the user asks about something already discussed in the chat, or about a file "
    "they are working on, answer from that context — do not re-ask what is already there. Only ask the user "
    "for information when it is genuinely NOT available in the chat thread or the session's documents. Never "
    "ask the user to repeat something the context already contains. "
    "WHAT THE CHAT HAS: when the user asks what the chat said, found, has so far, or what is in your context, "
    "answer RIGHT AWAY from the chat material you were given (chat answers, live chat output, documents) — "
    "summarise it in a few spoken sentences. Nobody fetches that for you: never say 'let me check', 'pulling it "
    "up' or 'one moment' for it, and never stall. If the chat is still working, say what it has so far and that "
    "the rest is coming. If you genuinely have nothing from the chat yet, say so plainly. "
    "RELAYING: text marked '[Chat agent …]' is the chat agent's work that you read out for it. Its 'I'll…' / "
    "'let me…' lines are the chat agent's own plan, already underway — relay them as its progress, never as a "
    "new promise of yours. "
    "LISTENING: do not interject 'mm-hmm', 'yeah', 'okay' or similar while the user is still talking; wait until "
    "they finish, then answer once."
)


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

        # Do NOT .strip() the SDP: every SDP line (including the last) must end
        # in CRLF. Stripping removed the final "\r\n" and OpenAI's SDP parser
        # failed with "failed to unmarshal SDP: EOF". Normalize line endings
        # instead and guarantee the trailing CRLF.
        sdp = body.get("sdp") or ""
        if not isinstance(sdp, str) or not sdp.strip():
            raise HTTPException(400, "An SDP offer is required")
        sdp = sdp.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
        sdp = "\r\n".join(line.rstrip() for line in sdp.split("\n") if line.strip()) + "\r\n"
        if not sdp.startswith("v=0"):
            raise HTTPException(400, "Malformed SDP offer")
        logger.info(f"GPT-Live SDP offer received: {len(sdp)} bytes | head={sdp[:80]!r}")

        model = settings.get("realtime_model", "gpt-live-1")
        instructions = settings.get(
            "realtime_instructions", "Be concise and conversational."
        )
        # Persona first, then the user's own instructions, then how work is
        # split with the chat agent (always present, even with old settings).
        instructions = (build_persona(settings) + "\n\n" + (instructions or "").strip()
                        + "\n\n" + VOICE_CONTRACT)

        # Trusted user claims — the highest-trust tier of identity
        # reconnaissance. The user is the authority on their own identity, so
        # their first-person claims ("my name is X") are injected as trusted
        # context the voice assistant may rely on (e.g. to address the user by
        # name). Scoped PER-CHAT: a claim stated in this chat only surfaces in
        # this chat's voice session, never another chat's.
        try:
            from src.trusted_claims import get_trusted_claims_manager
            _tcm = get_trusted_claims_manager()
            _chat_sid = (body.get("chat_session_id") or None)
            _trusted_block = _tcm.prompt_block(
                owner=effective_user(request), session_id=_chat_sid,
            )
            if _trusted_block:
                instructions = instructions + "\n\n" + _trusted_block
        except Exception as _tce:
            logger.debug(f"voice trusted-claims injection skipped: {_tce}")

        # Inject the CURRENT chat thread + this session's documents so the
        # voice assistant can contextually reference them while speaking. This
        # is the "always reference the context at hand" requirement: the
        # assistant answers from what's already in the chat/documents and only
        # asks the user for what is genuinely missing.
        try:
            from services.voice.memory import chat_summary_spine, session_documents_spine
            _chat_sid = (body.get("chat_session_id") or None)
            _owner = effective_user(request)
            _chat_ctx = chat_summary_spine(_chat_sid, _owner)
            _docs_ctx = session_documents_spine(_chat_sid, _owner)
            _ctx_parts = []
            if _chat_ctx:
                _ctx_parts.append("=== CURRENT CHAT THREAD ===\n" + _chat_ctx[:4000])
            if _docs_ctx:
                _ctx_parts.append("=== DOCUMENTS IN THIS CHAT ===\n" + _docs_ctx[:4000])
            if _ctx_parts:
                instructions = instructions + "\n\n" + "\n\n".join(_ctx_parts)
        except Exception as _ctx_err:
            logger.debug(f"voice chat/document context injection skipped: {_ctx_err}")

        session_cfg = {"model": model, "instructions": instructions}
        voice = (settings.get("voice_assistant_voice") or "").strip()
        if voice:
            # GPT-Live reads the voice from session.audio.output.voice, NOT a
            # flat session.voice. A flat "voice" key is silently ignored and the
            # session falls back to its default (marin, a female voice) — which
            # is why the configured male voice never took effect.
            session_cfg["audio"] = {"output": {"voice": voice}}
        logger.info(f"GPT-Live session config: model={model!r} voice={voice!r}")
        payload = {
            "session": session_cfg,
            "transport": {"type": "webrtc", "sdp": sdp},
        }

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.post(
                    "https://api.openai.com/v1/live/sessions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json=payload,
                )
                # If GPT-Live doesn't accept the voice field/value, connect
                # anyway with its default voice rather than failing the call.
                if r.status_code == 400 and voice and "voice" in r.text.lower():
                    logger.warning(f"GPT-Live rejected voice {voice!r}; retrying with default voice: {r.text[:200]}")
                    session_cfg.pop("audio", None)
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

    # ── Advanced voice mode: multi-agent pipeline ──────────────────────────

    def _voice_id(raw, chat_session_id=None) -> str:
        # Per-chat isolation: when the request is tied to a chat, the voice
        # memory key IS that chat — a stale/shared client key ('global',
        # 'default') can never merge two chats' voice memory.
        if chat_session_id:
            raw = f"chat-{chat_session_id}"
        vid = str(raw or "default").strip()[:80]
        return "".join(c for c in vid if c.isalnum() or c in "-_") or "default"

    @router.get("/status")
    async def voice_status(request: Request):
        """Capabilities for the voice HUD: realtime key, JEV, utility model."""
        if not effective_user(request):
            raise HTTPException(401, "Authentication required")
        from src.decisions import jev_available, jev_settings
        settings = load_settings()
        url, model, _ = resolve_endpoint("utility")
        return {
            "realtime": bool(settings.get("openai_api_key")),
            "realtime_model": settings.get("realtime_model", "gpt-live-1"),
            "jev": jev_available(),
            "jev_model": jev_settings()["model"],
            "utility_model": model or "",
            "utility": bool(url and model),
            "threshold": settings.get("voice_act_threshold", 0.5),
            "assistant_name": (settings.get("voice_assistant_name") or "Alfred").strip()[:40] or "Alfred",
        }

    @router.post("/pipeline")
    async def voice_pipeline(request: Request):
        """Run observer → compressor → JEV decider → forger, streaming each
        stage as Server-Sent Events (``data: {json}``)."""
        user = effective_user(request)
        if not user:
            raise HTTPException(401, "Authentication required")
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "JSON body required")
        turns = body.get("turns") or []
        if not isinstance(turns, list):
            raise HTTPException(400, "turns must be a list")
        turns = [
            {"speaker": str(t.get("speaker", ""))[:16], "text": str(t.get("text", ""))[:4000],
             "relay": bool(t.get("relay"))}
            for t in turns[:80] if isinstance(t, dict)
        ]
        from services.voice.pipeline import run_pipeline

        async def _gen():
            try:
                async for ev in run_pipeline(
                    owner=str(user),
                    voice_id=_voice_id(body.get("voice_id"), body.get("chat_session_id")),
                    turns=turns,
                    chat_session_id=(body.get("chat_session_id") or None),
                    force=bool(body.get("force")),
                    settle=bool(body.get("settle")),
                    # A promise the voice assistant made ("I'll check X") —
                    # forged into a chat request so the promise is kept.
                    commitment=(str(body.get("commitment") or "").strip()[:600] or None),
                    # The chat is already running a request: the assistant's
                    # talk is status about THAT work, not a new promise.
                    chat_busy=bool(body.get("chat_busy")),
                ):
                    yield "data: " + json.dumps(ev, ensure_ascii=False, default=str) + "\n\n"
            except Exception as e:
                logger.error(f"voice pipeline crashed: {e}", exc_info=True)
                yield "data: " + json.dumps({"type": "done", "send": False, "reason": "error",
                                             "error": str(e)[:200]}) + "\n\n"

        return StreamingResponse(_gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @router.post("/code-pipeline")
    async def code_pipeline(request: Request):
        """Run the code-execution neural pipeline (decide → forge → execute),
        streaming each stage as Server-Sent Events (``data: {json}``)."""
        user = effective_user(request)
        if not user:
            raise HTTPException(401, "Authentication required")
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "JSON body required")
        req_text = str(body.get("request") or "").strip()
        if not req_text:
            raise HTTPException(400, "request is required")
        session_id = body.get("session_id") or None

        from services.code.pipeline import run_pipeline

        async def _gen():
            import asyncio
            q: asyncio.Queue = asyncio.Queue()

            # The browser executor needs a progress_cb to push the eval_request
            # to the frontend. It lands in the same queue as the pipeline events
            # so it streams out as an SSE event in the same stream.
            async def _progress(payload: dict):
                await q.put(payload)

            async def _runner():
                try:
                    async for ev in run_pipeline(
                        owner=str(user),
                        request=req_text[:8000],
                        session_id=session_id,
                        progress_cb=_progress,
                    ):
                        await q.put(ev)
                except Exception as e:
                    logger.error(f"code pipeline crashed: {e}", exc_info=True)
                    await q.put({"type": "done", "executed": False, "reason": "error",
                                 "error": str(e)[:200]})
                finally:
                    await q.put(None)  # sentinel

            task = asyncio.create_task(_runner())
            while True:
                ev = await q.get()
                if ev is None:
                    break
                yield "data: " + json.dumps(ev, ensure_ascii=False, default=str) + "\n\n"
            await task

        return StreamingResponse(_gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @router.get("/memory")
    async def voice_memory(request: Request, voice_id: str = "default"):
        user = effective_user(request)
        if not user:
            raise HTTPException(401, "Authentication required")
        from services.voice.memory import get_voice_memory
        mem = get_voice_memory(str(user), _voice_id(voice_id))
        return {"stats": mem.stats(), "spine": mem.spine(), "transcript": mem.transcript,
                "tasks": mem.tasks, "tasks_summary": mem.tasks_summary(),
                "vectors": mem.vectors}

    @router.post("/vector")
    async def voice_vector(request: Request):
        """Append one conversation vector (radar dot) for this chat. The body
        carries {chat_session_id, type, label, ts, x, y, color}. Cheap and
        non-blocking on the frontend — the HUD already knows the position and
        renders instantly; this only persists it for later retrieval."""
        user = effective_user(request)
        if not user:
            raise HTTPException(401, "Authentication required")
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "JSON body required")
        from services.voice.memory import get_voice_memory
        mem = get_voice_memory(str(user), _voice_id(body.get("voice_id"), body.get("chat_session_id")))
        vec = mem.add_vector(
            vtype=str(body.get("type") or "message")[:32],
            label=str(body.get("label") or "")[:240],
            ts=float(body.get("ts") or 0),
            x=float(body.get("x") or 0),
            y=float(body.get("y") or 0),
            color=str(body.get("color") or "")[:32],
            extra=body.get("extra") or {},
        )
        return {"ok": True, "vector": vec}

    @router.post("/memory/ingest")
    async def voice_memory_ingest(request: Request):
        """Fold chat-thread turns (what was sent + what the chat agent answered)
        into the voice memory so they're compressed alongside the voice
        conversation. Non-blocking: compaction runs in the background."""
        user = effective_user(request)
        if not user:
            raise HTTPException(401, "Authentication required")
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "JSON body required")
        turns = body.get("turns") or []
        if not isinstance(turns, list):
            raise HTTPException(400, "turns must be a list")
        # user_note = text the user typed/pasted into the HUD as context.
        allowed = {"chat_user", "chat_ai", "user_note"}
        clean = [
            {"speaker": str(t.get("speaker")), "text": str(t.get("text", ""))[:6000], "final_break": True}
            for t in turns[:40]
            if isinstance(t, dict) and str(t.get("speaker")) in allowed
        ]
        from services.voice.memory import get_voice_memory
        from src.cost_ledger import set_cost_scope
        set_cost_scope(body.get("chat_session_id") or None, "voice-compress", str(user))
        mem = get_voice_memory(str(user), _voice_id(body.get("voice_id"), body.get("chat_session_id")))
        added = mem.add_turns(clean)
        if added:
            mem.schedule_compaction()
        return {"ok": True, "added": added, "stats": mem.stats()}

    @router.post("/task")
    async def voice_task(request: Request):
        """Record or update a task dispatched to the chat, so in-progress and
        queued tasks survive a server restart and can be summarized on restore."""
        user = effective_user(request)
        if not user:
            raise HTTPException(401, "Authentication required")
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "JSON body required")
        task_id = str(body.get("task_id") or "").strip()[:80]
        if not task_id:
            raise HTTPException(400, "task_id is required")
        from services.voice.memory import get_voice_memory
        mem = get_voice_memory(str(user), _voice_id(body.get("voice_id"), body.get("chat_session_id")))
        status = str(body.get("status") or "queued").strip()[:20]
        if body.get("title") or body.get("message"):
            mem.record_task(task_id, str(body.get("title") or "")[:120],
                            str(body.get("message") or "")[:4000], status)
        else:
            mem.update_task(task_id, status)
        return {"ok": True, "tasks": mem.tasks, "tasks_summary": mem.tasks_summary()}

    @router.post("/memory/reset")
    async def voice_memory_reset(request: Request):
        user = effective_user(request)
        if not user:
            raise HTTPException(401, "Authentication required")
        try:
            body = await request.json()
        except Exception:
            body = {}
        from services.voice.memory import get_voice_memory
        mem = get_voice_memory(str(user), _voice_id(body.get("voice_id")))
        mem.reset()
        return {"ok": True, "stats": mem.stats()}

    return router
