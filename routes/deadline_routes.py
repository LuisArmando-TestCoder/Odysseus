# routes/deadline_routes.py
"""REST API for the Deadlines page (``/deadlines``): what Deadline Watch is
following up on, plus snooze / mark done / undo. Owner-scoped through
``get_current_user``; notes and email asks are loaded for that owner only.
"""

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request

from src.auth_helpers import get_current_user

logger = logging.getLogger(__name__)


def _owner_items(owner: Optional[str]) -> list:
    from src.deadline_watch import load_owner_notes
    notes = list(load_owner_notes(owner or ""))
    try:
        from src.email_deadlines import load_owner_email_deadlines
        notes += load_owner_email_deadlines(owner or "")
    except Exception:
        logger.debug("deadlines: email deadlines unavailable", exc_info=True)
    return notes


def setup_deadline_routes() -> APIRouter:
    router = APIRouter(tags=["deadlines"])

    @router.get("/api/deadlines")
    async def list_deadlines(request: Request):
        from src.deadline_followups import deadline_overview, load_followups
        owner = get_current_user(request) or ""
        notes = await asyncio.to_thread(_owner_items, owner)
        return deadline_overview(notes, load_followups(owner))

    @router.post("/api/deadlines/{action}")
    async def act_on_deadline(action: str, request: Request):
        from src.deadline_followups import ACTIONS, apply_action
        if action not in ACTIONS:
            raise HTTPException(404, "unknown action")
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "JSON body required")
        if not isinstance(body, dict):
            raise HTTPException(400, "JSON object required")
        owner = get_current_user(request) or ""
        notes = await asyncio.to_thread(_owner_items, owner)
        try:
            return await asyncio.to_thread(apply_action, owner, action, body, notes)
        except KeyError as e:
            raise HTTPException(404, str(e).strip("'\""))
        except ValueError as e:
            raise HTTPException(400, str(e))

    return router
