"""Goal routes — GET /api/goal + POST /api/goal/{action}.

The goal card lives in the global sidebar (not per-session), so goals are keyed
by the effective user rather than a session id. The frontend (goalCard.js) calls
these routes without a session parameter.
"""
from fastapi import APIRouter, Request, HTTPException

from src.auth_helpers import effective_user
from src.goal import get_goal, update_goal, GoalConflict


def _is_top_level_human(request: Request) -> bool:
    """True for a direct human (cookie session), false for API tokens and the
    in-process internal-tool loopback. Mirrors the DSH rule that edit/pause/
    resume require a direct human request, not auto-continuation."""
    if getattr(request.state, "api_token", False):
        return False
    user = effective_user(request)
    return bool(user) and user != "internal-tool"


def setup_goal_routes() -> APIRouter:
    router = APIRouter(prefix="/api/goal", tags=["goal"])

    @router.get("")
    async def goal_get(request: Request):
        owner = effective_user(request)
        if not owner:
            raise HTTPException(403, "Authentication required")
        g = get_goal(owner)
        return {"goal": g} if g else {"goal": None}

    @router.get("/queue")
    async def goal_queue(request: Request):
        owner = effective_user(request)
        if not owner:
            raise HTTPException(403, "Authentication required")
        from src.checkpoint import checkpoint_queue_state
        return checkpoint_queue_state(owner)

    @router.get("/checkpoint")
    async def goal_checkpoint(request: Request):
        owner = effective_user(request)
        if not owner:
            raise HTTPException(403, "Authentication required")
        from src.checkpoint import checkpoint_read
        return {"checkpoint": checkpoint_read(owner=owner)}

    @router.post("/{action}")
    async def goal_update(request: Request, action: str):
        owner = effective_user(request)
        if not owner:
            raise HTTPException(403, "Authentication required")
        body = {}
        try:
            raw = await request.body()
            if raw:
                body = await request.json()
        except Exception:
            body = {}

        g = get_goal(owner)
        if not g:
            raise HTTPException(404, "no active goal")

        if action in ("edit", "pause", "resume") and not _is_top_level_human(request):
            raise HTTPException(403, "this action requires a direct human request")

        try:
            g = update_goal(
                owner,
                int(body.get("revision", g["revision"])),
                action,
                objective=body.get("objective"),
                max_goal_rounds=body.get("max_goal_rounds"),
                blocked_reason=body.get("blocked_reason"),
            )
        except GoalConflict:
            raise HTTPException(409, "stale revision — reload and retry")
        except KeyError:
            raise HTTPException(404, "no active goal")
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"goal": g}

    return router