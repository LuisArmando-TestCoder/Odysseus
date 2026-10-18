"""Question routes — Cluster B / Q4.

GET  /api/question/{session_id}          → pending questions (page-refresh recovery)
POST /api/question/{session_id}/answer   → resolve with the human's answers

Both verify session ownership. Answering appends a visible user message to the
transcript (metadata `answer_to_question`) so history stays honest, then the
awaiting tool call resumes with the answer as its tool result.
"""

import json
import logging

from fastapi import APIRouter, HTTPException, Request

logger = logging.getLogger(__name__)


def _answer_text(answers: list) -> str:
    """Render the human's answer as a short readable transcript line."""
    parts = []
    for a in answers:
        if not isinstance(a, dict):
            continue
        custom = (a.get("custom") or "").strip()
        selected = a.get("selected") or []
        if custom:
            parts.append(custom)
        elif selected:
            parts.append(", ".join(str(s) for s in selected))
    text = " | ".join(parts) if parts else json.dumps(answers, ensure_ascii=False)
    return f"Answer: {text}"


def setup_question_routes(session_manager) -> APIRouter:
    router = APIRouter(prefix="/api/question", tags=["question"])

    @router.get("/{session_id}")
    async def question_get(request: Request, session_id: str):
        from routes.session_routes import _verify_session_owner
        _verify_session_owner(request, session_id, session_manager=session_manager)
        from src.user_questions import get_pending
        return {"questions": get_pending(session_id)}

    @router.post("/{session_id}/answer")
    async def question_answer(request: Request, session_id: str):
        from routes.session_routes import _verify_session_owner
        _verify_session_owner(request, session_id, session_manager=session_manager)
        body = {}
        try:
            raw = await request.body()
            if raw:
                body = await request.json()
        except Exception:
            body = {}
        answers = body.get("answers")
        if not isinstance(answers, list) or not answers:
            raise HTTPException(400, "answers must be a non-empty array")

        from src.user_questions import answer as _answer
        resolved = _answer(session_id, answers)
        if not resolved:
            raise HTTPException(404, "no pending question for this session")

        # Append a visible user message so the transcript stays honest.
        try:
            from core.models import ChatMessage
            sess = session_manager.get_session(session_id)
            _ids = [a.get("id") for a in answers if isinstance(a, dict) and a.get("id")]
            sess.add_message(ChatMessage(
                "user", _answer_text(answers),
                metadata={"answer_to_question": True, "question_ids": _ids},
            ))
        except Exception as e:
            logger.warning(f"[questions] persisting answer message failed: {e}")

        return {"resolved": True}

    return router
