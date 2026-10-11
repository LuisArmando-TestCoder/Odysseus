# routes/trusted_claims_routes.py
"""Routes for the trusted-claims store — the highest-trust tier of identity
reconnaissance.

These claims are facts the user stated directly in the first person ("my name
is X", "I live in Y"). They are injected into the model as TRUSTED system
information (unlike memory.json, which is wrapped as untrusted data). This
route exposes them for inspection and manual management.
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request

from src.auth_helpers import get_current_user
from src.trusted_claims import get_trusted_claims_manager

logger = logging.getLogger(__name__)


def setup_trusted_claims_routes() -> APIRouter:
    router = APIRouter(prefix="/api/trusted-claims", tags=["trusted-claims"])

    def _owner(request: Request) -> Optional[str]:
        return get_current_user(request)

    @router.get("")
    def list_claims(request: Request):
        """List the current user's trusted claims."""
        user = _owner(request)
        mgr = get_trusted_claims_manager()
        return {"claims": mgr.load(owner=user)}

    @router.post("")
    async def upsert_claim(request: Request):
        """Manually add or edit a trusted claim.

        Body: {"key": "name", "value": "Ada Lovelace", "category": "identity"}
        """
        user = _owner(request)
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "JSON body required")

        key = (body.get("key") or "").strip().lower()
        value = (body.get("value") or "").strip()
        session_id = (body.get("session_id") or "").strip() or None
        if not key or not value:
            raise HTTPException(400, "key and value are required")
        if not session_id:
            raise HTTPException(400, "session_id is required (trusted claims are per-chat)")

        mgr = get_trusted_claims_manager()
        res = mgr.upsert(
            key=key,
            value=value,
            category=body.get("category") or "identity",
            source="manual",
            session_id=session_id,
            owner=user,
        )
        return {"ok": True, "action": res.get("action"), "claim": res.get("claim")}

    @router.delete("/{claim_id}")
    def delete_claim(request: Request, claim_id: str):
        """Delete a trusted claim by id."""
        user = _owner(request)
        mgr = get_trusted_claims_manager()
        if not mgr.delete(claim_id, owner=user):
            raise HTTPException(404, "Claim not found")
        return {"ok": True}

    return router