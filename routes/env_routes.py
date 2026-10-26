#!/usr/bin/env python3
"""Odysseus — environment-variable (.env) management routes.

Exposes the installer's credential registry (installer/credentials.py) to the
UI so the user can fill in missing environment variables from a modal instead
of editing .env by hand. Admin-gated: only admins may read/write the file.

Endpoints:
  GET  /api/env          -> registry + current values (secrets masked) + missing set
  POST /api/env          -> merge {KEY: value} into .env (0600), idempotent
  GET  /api/env/status   -> {complete: bool, missing: [keys]} for the gate
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Dict, List

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from core.middleware import require_admin

# Make installer/ importable regardless of CWD.
_INSTALLER_DIR = Path(__file__).resolve().parent.parent / "installer"
if str(_INSTALLER_DIR) not in sys.path:
    sys.path.insert(0, str(_INSTALLER_DIR))

from credentials import CREDENTIALS, GROUPS  # noqa: E402
from env_writer import read_env, write_env  # noqa: E402

_REPO_DIR = Path(__file__).resolve().parent.parent
_ENV_PATH = _REPO_DIR / ".env"

# Keys that are "required" for the app to be meaningfully usable. The modal
# gate only blocks on these; everything else is optional and can be filled
# later without nagging.
_REQUIRED_KEYS = {
    entry["key"]
    for entry in CREDENTIALS
    if entry.get("required", False)
}


def _mask(value: str, secret: bool) -> str:
    """Return a value safe to send to the browser. Secrets are never echoed."""
    if secret and value:
        return "••••••••"
    return value


def _registry_payload() -> Dict:
    """Build the full registry payload with current values merged in."""
    current = read_env(_ENV_PATH)
    entries = []
    for entry in CREDENTIALS:
        key = entry["key"]
        value = current.get(key, "")
        entries.append(
            {
                "key": key,
                "label": entry["label"],
                "category": entry["category"],
                "group": GROUPS.get(entry["category"], entry["category"]),
                "required": entry.get("required", False),
                "default": entry.get("default", ""),
                "secret": entry.get("secret", False),
                "help_text": entry.get("help_text", ""),
                "tutorial_urls": entry.get("tutorial_urls", []),
                "choices": entry.get("choices"),
                "value": _mask(value, entry.get("secret", False)),
                "is_set": bool(value),
            }
        )
    return {"entries": entries, "groups": GROUPS}


def _missing_keys() -> List[str]:
    """Keys that are required and currently unset (or empty)."""
    current = read_env(_ENV_PATH)
    missing = []
    for entry in CREDENTIALS:
        key = entry["key"]
        if entry.get("required", False) and not current.get(key, "").strip():
            missing.append(key)
    return missing


def setup_env_routes() -> APIRouter:
    router = APIRouter(tags=["env"])

    @router.get("/api/env")
    async def get_env(request: Request):
        require_admin(request)
        return JSONResponse(_registry_payload())

    @router.get("/api/env/status")
    async def env_status(request: Request):
        require_admin(request)
        missing = _missing_keys()
        return JSONResponse(
            {"complete": not missing, "missing": missing}
        )

    @router.post("/api/env")
    async def save_env(request: Request):
        require_admin(request)
        body = await request.json()
        updates: Dict[str, str] = {}
        if isinstance(body, dict):
            for key, value in body.items():
                # Only accept keys that exist in the registry.
                if key in {e["key"] for e in CREDENTIALS}:
                    updates[key] = "" if value is None else str(value)
        if updates:
            write_env(
                _ENV_PATH,
                updates,
                comment_header="Configurado desde la UI de Odysseus",
            )
        return JSONResponse(
            {"ok": True, "saved": list(updates.keys()), "missing": _missing_keys()}
        )

    return router