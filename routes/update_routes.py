#!/usr/bin/env python3
"""Odysseus — auto-update API (see docs/auto-update.md).

The work is done by installer/updater.py (checks/applies updates) and
installer/launch.py (the supervisor that restarts the server and rolls back a
bad version). These routes let the UI show the state and let an admin act:

  GET  /api/update/status    state for the update notice (any signed-in user;
                             admin-only fields are left out for others)
  GET  /api/update/idle      is the server busy? loopback-only, no auth —
                             the supervisor asks before restarting
  POST /api/update/check     admin: check now (no changes)
  POST /api/update/nudge     any signed-in user: "a browser just (re)opened the
                             app" — runs the normal background update when the
                             last check is stale (throttled; the install mode
                             still decides whether anything is applied)
  POST /api/update/apply     admin: install the available update now
  POST /api/update/restart   admin: restart the server now (needs the supervisor)
  POST /api/update/rollback  admin: go back to the previous version
  POST /api/update/shutdown  supervisor only (loopback + per-start token):
                             clean shutdown — on Windows the supervisor can't
                             signal a console-less server any other way
"""

from __future__ import annotations

import asyncio
import hmac
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request

from core.middleware import require_admin

_INSTALLER_DIR = Path(__file__).resolve().parent.parent / "installer"
if str(_INSTALLER_DIR) not in sys.path:
    sys.path.insert(0, str(_INSTALLER_DIR))

import updater  # noqa: E402

_LOOPBACK = {"127.0.0.1", "::1", "localhost"}
_PUBLIC_KEYS = ("current", "running", "updated_at", "changes", "pending_restart", "last_result",
                "last_check", "branch", "available", "rolled_back_from", "rollback_reason", "notice")


def _is_admin(request: Request) -> bool:
    try:
        require_admin(request)
        return True
    except HTTPException:
        return False


def _busy_count(research_handler=None) -> Dict[str, int]:
    counts = {"agent_runs": 0, "research": 0}
    try:
        from src import agent_runs
        counts["agent_runs"] = sum(1 for r in agent_runs.list_active() if r.get("status") == "running")
    except Exception:  # noqa: BLE001
        pass
    try:
        if research_handler is not None:
            counts["research"] = sum(1 for e in list(research_handler._active_tasks.values())
                                     if e.get("status") == "running")
    except Exception:  # noqa: BLE001
        pass
    return counts


def _spawn(*args: str) -> None:
    """Run the updater detached so it survives the restart it may trigger."""
    kwargs: Dict[str, Any] = {"cwd": str(updater.REPO_DIR), "stdin": subprocess.DEVNULL,
                              "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
                              "env": dict(os.environ, ODYSSEUS_UPDATE_QUIET="1")}
    if os.name == "nt":
        kwargs["creationflags"] = 0x00000008 | 0x00000200
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen([sys.executable, str(_INSTALLER_DIR / "updater.py"), *args], **kwargs)


def _status_payload(admin: bool) -> Dict[str, Any]:
    st = updater.status()
    out: Dict[str, Any] = {k: st.get(k) for k in _PUBLIC_KEYS}
    out["head"] = (st.get("head") or "")[:12]
    out["supervised"] = bool(st.get("supervisor"))
    out["restart_requested"] = bool(st.get("restart_requested"))
    out["updating"] = updater.update_running()
    # The code on disk is newer than what this process runs -> restart needed.
    out["restart_needed"] = bool(_BOOT_SHA and st.get("head") and st.get("head") != _BOOT_SHA)
    out["boot_sha"] = (_BOOT_SHA or "")[:12]
    out["is_admin"] = admin
    try:
        from core.constants import APP_VERSION
        out["version"] = APP_VERSION
    except Exception:  # noqa: BLE001
        out["version"] = None
    if admin:
        out.update({"mode": st.get("mode"), "enabled": st.get("enabled"), "error": st.get("error"),
                    "backup_branch": st.get("backup_branch"), "ci": st.get("ci"),
                    "history": (st.get("history") or [])[-10:]})
    return out


_BOOT_SHA: Optional[str] = None
_NUDGE_MIN_S = int(os.environ.get("ODYSSEUS_UPDATE_NUDGE_MIN_S", "300") or 300)
_last_nudge = 0.0


def setup_update_routes(research_handler=None) -> APIRouter:
    global _BOOT_SHA
    try:
        _BOOT_SHA = updater.head_sha() or None
    except Exception:  # noqa: BLE001
        _BOOT_SHA = None
    router = APIRouter(tags=["update"])

    @router.get("/api/update/status")
    async def update_status(request: Request):
        return await asyncio.to_thread(_status_payload, _is_admin(request))

    @router.get("/api/update/idle")
    async def update_idle(request: Request):
        host = (request.client.host if request.client else "") or ""
        if host not in _LOOPBACK:
            raise HTTPException(404, "Not found")
        counts = _busy_count(research_handler)
        return {"idle": sum(counts.values()) == 0, **counts, "t": time.time()}

    @router.post("/api/update/shutdown")
    async def update_shutdown(request: Request):
        host = (request.client.host if request.client else "") or ""
        token = os.environ.get("ODYSSEUS_SHUTDOWN_TOKEN", "")
        given = request.headers.get("X-Odysseus-Shutdown", "")
        if host not in _LOOPBACK or not token or not hmac.compare_digest(given, token):
            raise HTTPException(404, "Not found")
        # Same path as Ctrl+C: uvicorn's own handler runs the normal shutdown.
        asyncio.get_running_loop().call_later(0.3, signal.raise_signal, signal.SIGINT)
        return {"stopping": True}

    @router.post("/api/update/check")
    async def update_check(request: Request):
        require_admin(request)
        env = dict(os.environ, ODYSSEUS_UPDATE_QUIET="1")

        def _run():
            subprocess.run([sys.executable, str(_INSTALLER_DIR / "updater.py"), "--check"],
                           cwd=str(updater.REPO_DIR), env=env, capture_output=True, timeout=120,
                           **updater.NO_WINDOW)
        try:
            await asyncio.to_thread(_run)
        except subprocess.TimeoutExpired:
            raise HTTPException(504, "Update check timed out (offline?)")
        return _status_payload(True)

    @router.post("/api/update/nudge")
    async def update_nudge(request: Request):
        # Opening / refocusing the app is a natural moment to pull updates in.
        # Cheap and idempotent: a stale check (> _NUDGE_MIN_S) starts the same
        # detached updater run the hourly schedule does; anything fresher, or a
        # run already in progress, is a no-op. Applying + restarting is still
        # governed by the install mode, CI gate and the supervisor's quiet-moment
        # restart, so a nudge can never force anything.
        global _last_nudge
        now = time.time()
        st = await asyncio.to_thread(updater.load_state)
        stale = now - float(st.get("last_check") or 0) >= _NUDGE_MIN_S
        started = False
        if (stale and now - _last_nudge >= _NUDGE_MIN_S
                and updater._truthy(updater.read_settings().get("ODYSSEUS_AUTO_UPDATE"), True)
                and not await asyncio.to_thread(updater.update_running)):
            _last_nudge = now
            _spawn()
            started = True
        payload = await asyncio.to_thread(_status_payload, _is_admin(request))
        payload["checking"] = started
        return payload

    @router.post("/api/update/apply")
    async def update_apply(request: Request):
        require_admin(request)
        # A dev clone only moves when asked explicitly; this click is that.
        if updater.install_mode() == "dev":
            _spawn("--force")
        else:
            _spawn()
        return {"started": True, "message": "Installing the update — the server restarts when it's done."}

    @router.post("/api/update/restart")
    async def update_restart(request: Request):
        require_admin(request)
        if not updater.supervisor_alive():
            raise HTTPException(409, "Odysseus wasn't started by its launcher, so it can't restart itself. "
                                     "Close it and open it again from the Odysseus shortcut.")
        updater.request_restart("requested from the app", updater.head_sha(), force=True)
        return {"restarting": True}

    @router.post("/api/update/rollback")
    async def update_rollback(request: Request):
        require_admin(request)
        st = updater.load_state()
        if not (st.get("previous") or st.get("last_good")):
            raise HTTPException(409, "No earlier version is recorded on this install.")
        _spawn("--rollback")
        return {"started": True}

    return router
