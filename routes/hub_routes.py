# routes/hub_routes.py
"""Knowledge-hub routes: transcript search, @-mention recaps, cross-session
board, rewind snapshots, usage analytics, the agent dashboard, autopilot
control and live artifacts (/live/<token>). See src/hub.py."""

import json
import logging
import os
import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from src.auth_helpers import effective_user
from src import hub

logger = logging.getLogger(__name__)


def _user(request: Request) -> str:
    u = effective_user(request)
    if not u:
        raise HTTPException(401, "Authentication required")
    return u


def _own_session(user: str, sid: str) -> str:
    s = hub.owned_sessions(user)
    if sid in s:
        return sid
    m = [i for i in s if i.startswith(sid or "\0")]
    if len(m) == 1:
        return m[0]
    raise HTTPException(404, "Session not found")


def setup_hub_routes(session_manager=None, research_handler=None) -> APIRouter:
    router = APIRouter(tags=["hub"])

    # ── search / recap / mention list ─────────────────────────────────────
    @router.get("/api/hub/search")
    async def hub_search(request: Request, q: str = "", limit: int = 40):
        return hub.search(_user(request), q, max(1, min(200, limit)))

    @router.get("/api/hub/recap/{session_id}")
    async def hub_recap(request: Request, session_id: str, q: Optional[str] = None):
        u = _user(request)
        r = hub.recap(u, _own_session(u, session_id), query=q)
        if not r:
            raise HTTPException(404, "Session not found")
        return r

    @router.get("/api/hub/sessions")
    async def hub_sessions(request: Request, q: str = "", limit: int = 12, from_session: str = ""):
        """@-mention picker: chats by name, most recent first. With
        from_session, only chats in that chat's project (cross-chat scope)."""
        u = _user(request)
        s = hub.owned_sessions(u)
        if from_session:
            allowed = hub.peers(u, from_session)
            if allowed is not None:
                s = {k: v for k, v in s.items() if k in allowed}
        ql = (q or "").lower().strip()
        items = [{"id": k, "name": v.get("name") or "Untitled", "project": v.get("folder"),
                  "updated": str(v.get("last_message_at") or v.get("updated_at") or ""),
                  "messages": v.get("message_count") or 0, "archived": bool(v.get("archived"))}
                 for k, v in s.items() if (v.get("message_count") or 0) > 0]
        if ql:
            items = [i for i in items if ql in i["name"].lower() or i["id"].startswith(ql)]
        items.sort(key=lambda i: i["updated"], reverse=True)
        return {"sessions": items[:max(1, min(50, limit))]}

    # ── board ─────────────────────────────────────────────────────────────
    @router.get("/api/hub/board")
    async def hub_board(request: Request, limit: int = 100):
        u = _user(request)
        names = hub.owned_sessions(u)
        items = hub.board(u, limit)
        for it in items:
            it["from_name"] = (names.get(it.get("from_session")) or {}).get("name")
            it["to_name"] = (names.get(it.get("to_session")) or {}).get("name")
        return {"items": items}

    @router.post("/api/hub/board")
    async def hub_board_post(request: Request):
        u = _user(request)
        b = await request.json()
        to = b.get("to_session")
        if to:
            to = _own_session(u, to)
        frm = b.get("from_session")
        if frm:
            try:
                frm = _own_session(u, frm)
            except HTTPException:
                frm = None
        try:
            return hub.share(u, frm, b.get("text") or "", to_session=to,
                             kind=b.get("kind") or "note", title=b.get("title"))
        except ValueError as e:
            raise HTTPException(400, str(e))

    # ── rewind snapshots ──────────────────────────────────────────────────
    @router.get("/api/hub/snapshots/{session_id}")
    async def hub_snaps(request: Request, session_id: str):
        u = _user(request)
        return {"snapshots": hub.list_snapshots(_own_session(u, session_id))}

    @router.post("/api/hub/snapshots/{session_id}")
    async def hub_snap_now(request: Request, session_id: str):
        u = _user(request)
        sid = hub.snapshot(_own_session(u, session_id), "manual")
        return {"id": sid}

    @router.post("/api/hub/snapshots/{session_id}/{snap_id}/restore")
    async def hub_restore(request: Request, session_id: str, snap_id: str):
        u = _user(request)
        sid = _own_session(u, session_id)
        if session_manager is None:
            raise HTTPException(500, "session manager unavailable")
        try:
            from src import agent_runs
            if agent_runs.is_active(sid):
                raise HTTPException(409, "Stop the running turn first")
            n = hub.restore(session_manager, sid, snap_id)
        except KeyError:
            raise HTTPException(404, "Snapshot not found")
        return {"restored": n}

    # ── usage ─────────────────────────────────────────────────────────────
    @router.get("/api/hub/usage")
    async def hub_usage(request: Request, days: int = 30):
        return hub.usage(_user(request), max(1, min(365, days)))

    # ── autopilot ─────────────────────────────────────────────────────────
    @router.get("/api/hub/autopilot/{session_id}")
    async def ap_get(request: Request, session_id: str):
        u = _user(request)
        from src import autopilot
        return autopilot.status(_own_session(u, session_id))

    @router.post("/api/hub/autopilot/{session_id}")
    async def ap_set(request: Request, session_id: str):
        u = _user(request)
        sid = _own_session(u, session_id)
        b = await request.json()
        from src import autopilot
        if b.get("armed") is False:
            autopilot.disarm(sid, "turned off by user")
        else:
            autopilot.on_user_message(sid, None, u)
            autopilot.on_run_finished(sid, "done")      # continue now if a plan is open
        return autopilot.status(sid)

    # ── dashboard ─────────────────────────────────────────────────────────
    @router.get("/api/hub/dashboard")
    async def hub_dashboard(request: Request):
        u = _user(request)
        names = hub.owned_sessions(u)
        nm = lambda sid: (names.get(sid) or {}).get("name") or (sid or "")[:8]
        out: Dict[str, Any] = {"generated_at": time.time()}
        # running work
        runs = []
        try:
            from src import agent_runs, turn_inbox, autopilot
            for r in agent_runs.list_active():
                if r.get("owner") and r["owner"] != u:
                    continue
                if r["session_id"] not in names and r.get("owner") != u:
                    continue
                runs.append({**r, "name": nm(r["session_id"]),
                             "queued": turn_inbox.queued_count(r["session_id"]),
                             "autopilot": autopilot.status(r["session_id"])})
        except Exception as e:
            logger.debug("dashboard runs: %s", e)
        out["runs"] = sorted(runs, key=lambda r: (r["status"] != "running", -(r.get("started_at") or 0)))
        # plans (todo_write) per chat
        plans = []
        try:
            from src.checkpoint import checkpoint_queue_state
            from src.constants import DATA_DIR
            d = os.path.join(DATA_DIR, "agent_state")
            for fn in os.listdir(d):
                if not fn.endswith(".queue.json"):
                    continue
                sid = fn[:-len(".queue.json")]
                if sid not in names:
                    continue
                q = checkpoint_queue_state(sid)
                if not (q.get("remaining") or q.get("done")):
                    continue
                plans.append({"session_id": sid, "name": nm(sid), "remaining": q.get("remaining") or [],
                              "done": q.get("done") or [], "current": q.get("current"),
                              "updated_at": q.get("updated_at") or os.path.getmtime(os.path.join(d, fn))})
        except Exception as e:
            logger.debug("dashboard plans: %s", e)
        out["plans"] = sorted(plans, key=lambda p: -(p.get("updated_at") or 0))[:20]
        # goal
        try:
            from src.goal import get_goal
            g = get_goal(u)
            if g:
                g = dict(g)
                g["session_name"] = nm(g.get("session_id"))
            out["goal"] = g
        except Exception:
            out["goal"] = None
        # sub-agents
        try:
            from src.subagents import list_subs
            subs = list_subs(u)
            for s in subs:
                s["parent_name"] = nm(s.get("parent_id"))
            out["subagents"] = subs[-30:]
        except Exception:
            out["subagents"] = []
        # research
        res = []
        try:
            if research_handler is not None:
                for sid, entry in list(research_handler._active_tasks.items()):
                    if entry.get("owner", "") != u:
                        continue
                    res.append({"session_id": sid, "name": nm(sid), "query": entry.get("query", ""),
                                "status": entry.get("status"), "progress": entry.get("progress", {}),
                                "started_at": entry.get("started_at", 0)})
        except Exception:
            pass
        out["research"] = sorted(res, key=lambda r: -(r.get("started_at") or 0))[:20]
        # outputs: recently changed documents + live artifacts
        try:
            with hub._db() as c:
                rows = c.execute("SELECT id, title, language, session_id, updated_at, version_count FROM documents "
                                 "WHERE (owner=? OR owner IS NULL) AND (archived IS NULL OR archived=0) "
                                 "ORDER BY updated_at DESC LIMIT 15", (u,)).fetchall()
            out["outputs"] = [{**dict(r), "session_name": nm(r["session_id"])} for r in rows]
        except Exception as e:
            logger.debug("dashboard outputs: %s", e)
            out["outputs"] = []
        out["live"] = hub.live_list(u)[:15]
        out["board"] = hub.board(u, 12)
        for it in out["board"]:
            it["from_name"] = nm(it.get("from_session")) if it.get("from_session") else None
            it["to_name"] = nm(it.get("to_session")) if it.get("to_session") else None
        try:
            us = hub.usage(u, 7)
            out["usage"] = {"totals": us["totals"], "by_source": us["by_source"], "by_day": us["by_day"]}
        except Exception:
            out["usage"] = None
        return out

    # ── superpowers (OSINT recon) ──────────────────────────────────────────
    @router.get("/api/hub/powers")
    async def powers(request: Request):
        _user(request)
        from src import superpowers as sp
        try:
            from src.osint_research import sherlock_available
            sher = bool(sherlock_available())
        except Exception:
            sher = False
        return {"capabilities": [{"name": n, "detail": d} for n, d in sp.CAPABILITIES],
                "actions": sp.ACTIONS, "sherlock": sher, "policy": sp.POLICY}

    @router.post("/api/hub/recon")
    async def recon(request: Request):
        u = _user(request)
        b = await request.json()
        from src import superpowers as sp
        return await sp.run(b.get("action") or "auto", str(b.get("target") or ""), owner=u,
                            session_id=b.get("session_id"), run_dorks=bool(b.get("run")))

    # ── live artifacts ────────────────────────────────────────────────────
    @router.post("/api/hub/live")
    async def live_publish(request: Request):
        u = _user(request)
        b = await request.json()
        doc_id = b.get("doc_id")
        with hub._db() as c:
            r = c.execute("SELECT owner FROM documents WHERE id=?", (doc_id,)).fetchone()
        if not r or (r["owner"] and r["owner"] != u):
            raise HTTPException(404, "Document not found")
        it = hub.publish_live(u, doc_id, allow_mcp=bool(b.get("allow_mcp")))
        return {"token": it["token"], "url": f"/live/{it['token']}", "allow_mcp": it.get("allow_mcp")}

    @router.get("/api/hub/live")
    async def live_mine(request: Request):
        return {"live": hub.live_list(_user(request))}

    @router.delete("/api/hub/live/{token}")
    async def live_delete(request: Request, token: str):
        return {"removed": hub.unpublish_live(_user(request), token)}

    @router.get("/live/{token}/data")
    async def live_data(token: str):
        d = hub.live_get(token)
        if not d:
            raise HTTPException(404, "Not found")
        d["mcp_blocks"] = len(hub.live_mcp_blocks(d["content"]))
        return JSONResponse(d, headers={"Cache-Control": "no-store"})

    @router.post("/live/{token}/mcp/{index}")
    async def live_mcp(request: Request, token: str, index: int):
        it = hub.live_entry(token)
        if not it:
            raise HTTPException(404, "Not found")
        as_owner = False
        try:
            as_owner = bool(it.get("owner")) and effective_user(request) == it.get("owner")
        except Exception:
            as_owner = False
        return await hub.live_mcp_call(token, index, as_owner=as_owner)

    @router.get("/live/{token}", response_class=HTMLResponse)
    async def live_page(request: Request, token: str):
        if not hub.live_entry(token):
            raise HTTPException(404, "Not found")
        path = os.path.join("static", "live.html")
        with open(path, encoding="utf-8") as f:
            html = f.read()
        html = html.replace("{{TOKEN}}", token).replace("{{CSP_NONCE}}", getattr(request.state, "csp_nonce", ""))
        return HTMLResponse(html,
                            headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex"})

    return router
