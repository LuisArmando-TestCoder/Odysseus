# routes/case_routes.py
"""REST API for Case Files (organized intel) and the Watchlist (change monitor).

Everything is owner-scoped through ``get_current_user``. The same operations
are exposed to the agent as the ``case_file`` / ``watch_target`` tools; these
routes back the Intel Board page (``/cases``).
"""

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

from src.auth_helpers import get_current_user
from src.casefiles import get_case_store
from src.watchlist import get_watch_store

logger = logging.getLogger(__name__)


async def _body(request: Request) -> dict:
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(400, "JSON body required")
    if not isinstance(data, dict):
        raise HTTPException(400, "JSON object required")
    return data


def _guard(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except KeyError as e:
        raise HTTPException(404, str(e).strip("'\""))
    except ValueError as e:
        raise HTTPException(400, str(e))


def setup_case_routes() -> APIRouter:
    router = APIRouter(tags=["cases"])

    def _owner(request: Request) -> Optional[str]:
        return get_current_user(request)

    # ----- cases -----------------------------------------------------------
    @router.get("/api/cases")
    def list_cases(request: Request, status: Optional[str] = "open"):
        st = None if status in ("", "all") else status
        return {"cases": get_case_store().list_cases(_owner(request), status=st)}

    @router.post("/api/cases")
    async def create_case(request: Request):
        b = await _body(request)
        c = _guard(get_case_store().create_case, _owner(request), b.get("title", ""),
                   b.get("objective", ""), b.get("tags"))
        return {"case": c}

    @router.get("/api/cases/search")
    def search_cases(request: Request, q: str = ""):
        return {"hits": get_case_store().search(_owner(request), q)}

    @router.get("/api/cases/{case_id}")
    def get_case(case_id: str, request: Request):
        owner = _owner(request)
        st = get_case_store()
        c = _guard(st.get_case, owner, case_id)
        return {"case": c, "pivots": st.suggest_pivots(owner, c["id"])}

    @router.patch("/api/cases/{case_id}")
    async def update_case(case_id: str, request: Request):
        b = await _body(request)
        c = _guard(get_case_store().update_case, _owner(request), case_id,
                   title=b.get("title"), objective=b.get("objective"),
                   status=b.get("status"), tags=b.get("tags"))
        return {"case": c}

    @router.delete("/api/cases/{case_id}")
    def delete_case(case_id: str, request: Request):
        _guard(get_case_store().delete_case, _owner(request), case_id)
        return {"ok": True}

    @router.post("/api/cases/{case_id}/entities")
    async def add_entity(case_id: str, request: Request):
        b = await _body(request)
        e = _guard(get_case_store().add_entity, _owner(request), case_id, str(b.get("value", "")),
                   b.get("type"), notes=b.get("notes", ""), source=b.get("source", ""),
                   confidence=b.get("confidence"), label=b.get("label", ""))
        return {"entity": e}

    @router.post("/api/cases/{case_id}/findings")
    async def add_finding(case_id: str, request: Request):
        b = await _body(request)
        f = _guard(get_case_store().add_finding, _owner(request), case_id, b.get("text", ""),
                   source_url=b.get("source_url", ""), confidence=float(b.get("confidence", 0.6)),
                   kind=b.get("kind", "fact"), entities=b.get("entities"))
        return {"finding": f}

    @router.post("/api/cases/{case_id}/links")
    async def add_link(case_id: str, request: Request):
        b = await _body(request)
        l = _guard(get_case_store().link, _owner(request), case_id, str(b.get("a", "")),
                   str(b.get("b", "")), b.get("relation", "related_to"), b.get("evidence", ""))
        return {"link": l}

    @router.post("/api/cases/{case_id}/steps")
    async def add_step(case_id: str, request: Request):
        b = await _body(request)
        if b.get("pivots"):
            owner = _owner(request)
            st = get_case_store()
            added = [_guard(st.add_step, owner, case_id, p) for p in _guard(st.suggest_pivots, owner, case_id)]
            return {"steps": added}
        s = _guard(get_case_store().add_step, _owner(request), case_id, b.get("text", ""))
        return {"step": s}

    @router.post("/api/cases/{case_id}/steps/{step_id}/done")
    async def done_step(case_id: str, step_id: str, request: Request):
        try:
            b = await request.json()
        except Exception:
            b = {}
        s = _guard(get_case_store().complete_step, _owner(request), case_id, step_id,
                   (b or {}).get("result", ""))
        return {"step": s}

    @router.get("/api/cases/{case_id}/graph")
    def case_graph(case_id: str, request: Request, max_nodes: int = 80):
        return _guard(get_case_store().graph, _owner(request), case_id, max_nodes=max_nodes)

    @router.get("/api/cases/{case_id}/export")
    def export_case(case_id: str, request: Request):
        md = _guard(get_case_store().export_markdown, _owner(request), case_id)
        return PlainTextResponse(md, media_type="text/markdown; charset=utf-8")

    # ----- watchlist -------------------------------------------------------
    @router.get("/api/watchlist")
    def list_watches(request: Request):
        return {"watches": get_watch_store().list(_owner(request))}

    @router.post("/api/watchlist")
    async def add_watch(request: Request):
        b = await _body(request)
        owner = _owner(request)
        case_id = (b.get("case_id") or "").strip()
        if case_id:
            case_id = _guard(get_case_store().resolve_case_id, owner, case_id)
        w = _guard(get_watch_store().add, owner, b.get("url", ""), label=b.get("label", ""),
                   interval_minutes=int(b.get("interval_minutes") or 360), case_id=case_id,
                   keywords=b.get("keywords"), kind=b.get("kind") or "")
        return {"watch": w}

    @router.delete("/api/watchlist/{watch_id}")
    def remove_watch(watch_id: str, request: Request):
        _guard(get_watch_store().remove, _owner(request), watch_id)
        return {"ok": True}

    @router.post("/api/watchlist/{watch_id}/active")
    async def set_watch_active(watch_id: str, request: Request):
        b = await _body(request)
        w = _guard(get_watch_store().set_active, _owner(request), watch_id, bool(b.get("active", True)))
        return {"watch": w}

    @router.post("/api/watchlist/{watch_id}/check")
    async def check_watch(watch_id: str, request: Request):
        owner = _owner(request)
        ev = await asyncio.to_thread(_guard, get_watch_store().check_one, owner, watch_id)
        return {"change": ev}

    return router
