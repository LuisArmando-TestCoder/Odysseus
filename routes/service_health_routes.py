# routes/service_health_routes.py
"""Service Health status for the main SPA's sidebar dot.

``GET  /api/health/services``        — saved state from the scheduled
                                        ``check_services`` task (no probes).
``POST /api/health/services/check``  — probe now and return the live result.
                                        Not saved, so the scheduled task still
                                        owns down/recovered notifications.

Both are owner-scoped via ``get_current_user`` and behind normal auth (only
the exact path ``/api/health`` is auth-exempt).
"""

import asyncio
import logging
import threading
import time
from typing import Dict, Optional

from fastapi import APIRouter, HTTPException, Request

from src.auth_helpers import get_current_user

logger = logging.getLogger(__name__)

CHECK_COOLDOWN = 20.0   # seconds between manual checks per owner


class _Cooldown:
    def __init__(self, seconds: float):
        self.seconds = seconds
        self._last: Dict[str, float] = {}
        self._lock = threading.Lock()

    def try_acquire(self, key: str, now: Optional[float] = None) -> float:
        """0.0 when allowed (and recorded), else seconds left to wait."""
        now = time.monotonic() if now is None else now
        with self._lock:
            last = self._last.get(key)
            if last is not None and now - last < self.seconds:
                return self.seconds - (now - last)
            self._last[key] = now
            if len(self._last) > 1000:  # bound memory on large installs
                cutoff = now - self.seconds
                self._last = {k: v for k, v in self._last.items() if v >= cutoff}
            return 0.0


def setup_service_health_routes() -> APIRouter:
    router = APIRouter(tags=["health"])
    cooldown = _Cooldown(CHECK_COOLDOWN)

    def _owner(request: Request) -> str:
        return get_current_user(request) or ""

    @router.get("/api/health/services")
    def service_health_status(request: Request):
        from src.service_health import health_status
        try:
            return health_status(_owner(request))
        except Exception:
            logger.exception("service health status failed")
            return {"status": "unknown", "label": "Service health unavailable",
                    "down": 0, "flaky": 0, "total": 0, "checked_at": None,
                    "live": False, "services": []}

    @router.post("/api/health/services/check")
    async def service_health_check(request: Request):
        from src.service_health import live_status, run_live_probes
        owner = _owner(request)
        wait = cooldown.try_acquire(owner)
        if wait:
            raise HTTPException(429, f"Checked just now — try again in {int(wait) + 1}s")
        try:
            results = await asyncio.to_thread(run_live_probes, owner)
        except Exception as e:
            logger.exception("service health live check failed")
            raise HTTPException(500, f"Check failed: {type(e).__name__}")
        return live_status(results)

    return router
