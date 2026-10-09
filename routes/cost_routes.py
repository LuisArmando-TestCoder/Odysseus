# routes/cost_routes.py
"""Per-chat API cost ledger endpoints.

The ledger lives outside session history (see ``src/cost_ledger.py``), so
nothing here is ever fed to a model as context.
"""

import logging

from fastapi import APIRouter, HTTPException, Request

from src.auth_helpers import effective_user

logger = logging.getLogger(__name__)


def _owns(user: str, session_id: str) -> bool:
    """True when the session belongs to ``user`` (or has no recorded owner)."""
    try:
        from core import models as _core_models
        mgr = getattr(_core_models, "_session_manager", None)
        sess = mgr.get_session(session_id) if mgr and hasattr(mgr, "get_session") else None
    except Exception:
        sess = None
    if sess is None:
        return False
    owner = getattr(sess, "owner", None)
    return not owner or str(owner) == str(user)


def setup_cost_routes() -> APIRouter:
    router = APIRouter(prefix="/api/costs", tags=["costs"])

    @router.get("")
    async def cost_totals(request: Request, ids: str = ""):
        """Totals for a comma-separated list of chat ids (only the caller's)."""
        user = effective_user(request)
        if not user:
            raise HTTPException(401, "Authentication required")
        from src.cost_ledger import totals_for
        wanted = [i for i in (ids or "").split(",") if i][:500]
        mine = [i for i in wanted if _owns(user, i)]
        return {"totals": totals_for(mine)}

    @router.get("/{session_id}")
    async def cost_detail(request: Request, session_id: str, limit: int = 50):
        user = effective_user(request)
        if not user:
            raise HTTPException(401, "Authentication required")
        if not _owns(user, session_id):
            raise HTTPException(404, "Not found")
        from src.cost_ledger import detail
        return detail(session_id, max(1, min(200, limit)))

    @router.post("/realtime")
    async def cost_realtime(request: Request):
        """GPT-Live reports usage per response in the browser; price + record it."""
        user = effective_user(request)
        if not user:
            raise HTTPException(401, "Authentication required")
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "JSON body required")
        usage = body.get("usage")
        if not isinstance(usage, dict):
            raise HTTPException(400, "usage object required")
        sid = body.get("chat_session_id") or None
        if sid and not _owns(user, sid):
            sid = None
        from src.cost_ledger import price_realtime_usage, record, summary
        priced = price_realtime_usage(usage)
        record(provider="openai", model=str(body.get("model") or "gpt-live")[:80],
               input_tokens=priced["in"], output_tokens=priced["out"],
               base_usd=priced["usd"], source="gpt-live", session_id=sid,
               owner=str(user), apply_fee=False, detail=priced["detail"])
        return {"ok": True, "usd": priced["usd"],
                "chat_total_usd": (summary(sid)["total_usd"] if sid else None)}

    return router
