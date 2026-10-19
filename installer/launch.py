#!/usr/bin/env python3
"""Odysseus — desktop launcher (the target of OS shortcuts).

Boots the virtualenv server in the foreground and opens the browser at the
configured URL. Reads APP_BIND/APP_PORT from ``.env`` so the shortcut works
exactly like the installer's own "start" step, without re-running the wizard.

The shortcuts created by ``installer/shortcuts.py`` all launch THIS file.
"""

from __future__ import annotations

import platform
import subprocess
import sys
import webbrowser
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent


def _venv_python() -> Path:
    base = REPO_DIR / "venv"
    if platform.system() == "Windows":
        return base / "Scripts" / "python.exe"
    return base / "bin" / "python3"


def _read_env_value(key: str, default: str) -> str:
    env_path = REPO_DIR / ".env"
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k.strip() == key:
                return v.strip().strip("\"'")
    except OSError:
        pass
    return default


def main() -> int:
    venv_py = _venv_python()
    if not venv_py.exists():
        print("[Odysseus] No se encontró el entorno virtual (venv). "
              "Ejecuta primero el instalador.", file=sys.stderr)
        return 1

    port = _read_env_value("APP_PORT", "7860")
    host = _read_env_value("APP_BIND", "127.0.0.1")
    url_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    url = f"http://{url_host}:{port}"

    # Open the browser once the server is listening (best effort).
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001 - browser open is a nicety
        pass

    try:
        subprocess.run(
            [str(venv_py), "-m", "uvicorn", "app:app", "--host", host, "--port", port]
        )
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())