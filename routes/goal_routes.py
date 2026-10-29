"""Goal routes — GET /api/goal + POST /api/goal/{action}.

The goal card lives in the global sidebar (not per-session), so goals are keyed
by the effective user rather than a session id. The frontend (goalCard.js) calls
these routes without a session parameter.
"""
from fastapi import APIRouter, Request, HTTPException

from src.auth_helpers import effective_user
from src.goal import get_goal, update_goal, create_goal, GoalConflict


def _is_top_level_human(request: Request) -> bool:
    """True for a direct human (cookie session), false for API tokens and the
    in-process internal-tool loopback. Mirrors the DSH rule that edit/pause/
    resume require a direct human request, not auto-continuation."""
    if getattr(request.state, "api_token", False):
        return False
    user = effective_user(request)
    return bool(user) and user != "internal-tool"


def _session_owned(session_id: str, owner: str) -> bool:
    try:
        from src.database import SessionLocal, Session as DbSession
        db = SessionLocal()
        try:
            row = db.query(DbSession).filter(DbSession.id == session_id).first()
            if not row:
                return False
            o = getattr(row, "owner", None)
            return (not o) or o == owner
        finally:
            db.close()
    except Exception:
        return False


def setup_goal_routes() -> APIRouter:
    router = APIRouter(prefix="/api/goal", tags=["goal"])

    @router.get("")
    async def goal_get(request: Request):
        owner = effective_user(request)
        if not owner:
            raise HTTPException(403, "Authentication required")
        g = get_goal(owner)
        return {"goal": g} if g else {"goal": None}

    @router.post("")
    async def goal_create(request: Request):
        """Layer 0 / F0.4 — the goal creation path that did not exist before.

        Body: {objective, session_id?, max_goal_rounds?}. Goals are owner-keyed;
        `session_id` (explicit or the owner's most recent non-archived chat) is
        where the goal's rounds will execute.
        """
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
        objective = (body.get("objective") or "").strip()
        if not objective:
            raise HTTPException(400, "objective is required")

        session_id = (body.get("session_id") or "").strip() or None
        if session_id:
            from routes.session_routes import _verify_session_owner
            _verify_session_owner(request, session_id)
        else:
            # Default: the owner's most recently accessed, non-archived chat.
            try:
                from core.database import SessionLocal
                from core.database import Session as DBSession
                _db = SessionLocal()
                try:
                    _row = (
                        _db.query(DBSession)
                        .filter(DBSession.owner == owner, DBSession.archived == False)
                        .order_by(DBSession.updated_at.desc())
                        .first()
                    )
                    session_id = getattr(_row, "id", None)
                finally:
                    _db.close()
            except Exception:
                session_id = None

        existing = get_goal(owner)
        if existing and existing.get("phase") in ("active", "paused"):
            raise HTTPException(409, "an active goal already exists")

        try:
            _maxr = int(body.get("max_goal_rounds") or 0) or None
        except (TypeError, ValueError):
            _maxr = None
        goal = create_goal(owner, objective, max_goal_rounds=_maxr, session_id=session_id)
        # G1 — kick the round driver (idle-gated; no-op if the session is busy).
        try:
            from src import goal_driver
            goal_driver.maybe_start_goal_round(owner=owner, goal=goal)
        except Exception:
            pass
        return {"goal": goal}

    @router.get("/queue")
    async def goal_queue(request: Request):
        owner = effective_user(request)
        if not owner:
            raise HTTPException(403, "Authentication required")
        from src.checkpoint import checkpoint_queue_state
        # The task list is session-scoped (todo_write writes it per chat). Use
        # the requested chat if it is the caller's, else the goal's chat.
        sid = (request.query_params.get("session_id") or "").strip()
        if sid and not _session_owned(sid, owner):
            raise HTTPException(404, "Session not found")
        if not sid:
            g = get_goal(owner) or {}
            sid = g.get("session_id") or ""
        if not sid:
            return {"remaining": [], "done": []}
        st = checkpoint_queue_state(sid)
        st["session_id"] = sid
        return st

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