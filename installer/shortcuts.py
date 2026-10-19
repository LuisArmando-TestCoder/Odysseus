#!/usr/bin/env python3
"""Odysseus installer — cross-platform desktop shortcut creation.

Creates a convenient launcher on Linux, macOS, and Windows that points at
``installer/launch.py`` (which boots the venv server and opens the browser).
Each OS uses its native mechanism:

  * macOS   — a ``Odysseus.command`` double-clickable on the Desktop plus a
              minimal ``Odysseus.app`` bundle in ``~/Applications``.
  * Linux   — a freedesktop ``.desktop`` entry in ``~/.local/share/applications``
              (menu) and on the Desktop.
  * Windows — ``.lnk`` shortcuts on the Desktop and Start Menu via WScript.Shell.

This module keeps *all* shortcut logic in one place so the installer stays a
thin orchestrator. To change the icon/target/name, edit ONLY this file.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Tuple

APP_NAME = "Odysseus"
# The canonical launcher (lives next to this file) that the shortcuts run.
LAUNCHER_NAME = "launch.py"


def _launcher_path(repo_dir: Path) -> Path:
    return repo_dir / "installer" / LAUNCHER_NAME


def _win_python(venv_py: Path) -> Path:
    """Prefer the venv interpreter; fall back to whatever runs this process."""
    if venv_py.exists():
        return venv_py
    return Path(sys.executable)


def _venv_python(repo_dir: Path) -> Path:
    base = repo_dir / "venv"
    if platform.system() == "Windows":
        return base / "Scripts" / "python.exe"
    return base / "bin" / "python3"


# ── macOS ────────────────────────────────────────────────────────────────────

def _macos_command(desktop: Path, launcher: Path, py: Path) -> Path:
    """A double-clickable ``.command`` script on the Desktop."""
    target = desktop / f"{APP_NAME}.command"
    body = (
        "#!/bin/bash\n"
        f"exec '{py}' '{launcher}'\n"
    )
    target.write_text(body, encoding="utf-8")
    target.chmod(0o755)
    return target


def _macos_app_bundle(apps: Path, launcher: Path, py: Path) -> Path:
    """A minimal .app bundle so it shows up with an icon in /Applications."""
    bundle = apps / f"{APP_NAME}.app"
    macos_dir = bundle / "Contents" / "MacOS"
    macos_dir.mkdir(parents=True, exist_ok=True)

    info_plist = (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
        "<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" "
        "\"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">\n"
        "<plist version=\"1.0\"><dict>\n"
        f"  <key>CFBundleName</key><string>{APP_NAME}</string>\n"
        f"  <key>CFBundleDisplayName</key><string>{APP_NAME}</string>\n"
        "  <key>CFBundleIdentifier</key><string>ai.odysseus.app</string>\n"
        "  <key>CFBundleVersion</key><string>1.0</string>\n"
        "  <key>CFBundleExecutable</key><string>launcher</string>\n"
        "  <key>CFBundlePackageType</key><string>APPL</string>\n"
        "</dict></plist>\n"
    )
    (bundle / "Contents" / "Info.plist").write_text(info_plist, encoding="utf-8")

    launcher_script = macos_dir / "launcher"
    launcher_script.write_text(
        "#!/bin/bash\n"
        f"exec '{py}' '{launcher}'\n",
        encoding="utf-8",
    )
    launcher_script.chmod(0o755)
    return bundle


# ── Linux ────────────────────────────────────────────────────────────────────

def _linux_desktop(repo_dir: Path, launcher: Path, py: Path) -> Tuple[Path, Path]:
    """A freedesktop .desktop entry, placed both on the Desktop and the menu."""
    entry = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={APP_NAME}\n"
        "Comment=Open Odysseus\n"
        f"Exec={py} {launcher}\n"
        "Terminal=false\n"
        "Categories=Utility;Network;\n"
    )
    desktop_dir = Path.home() / "Desktop"
    if not desktop_dir.exists():
        # XDG fallback (some distros / non-English locales).
        desktop_dir = Path(
            subprocess.run(
                ["xdg-user-dir", "DESKTOP"],
                capture_output=True, text=True,
            ).stdout.strip()
            or str(Path.home() / "Desktop")
        )
    desktop_dir.mkdir(parents=True, exist_ok=True)

    menu_dir = Path.home() / ".local" / "share" / "applications"
    menu_dir.mkdir(parents=True, exist_ok=True)

    desktop_target = desktop_dir / f"{APP_NAME}.desktop"
    menu_target = menu_dir / f"{APP_NAME}.desktop"
    for target in (desktop_target, menu_target):
        target.write_text(entry, encoding="utf-8")
        target.chmod(0o755)
    return desktop_target, menu_target


# ── Windows ──────────────────────────────────────────────────────────────────

def _windows_shortcuts(repo_dir: Path, launcher: Path, py: Path) -> Tuple[str, str]:
    """.lnk files on the Desktop and Start Menu, via PowerShell WScript.Shell."""
    desktop = Path(os.environ.get("USERPROFILE", Path.home())) / "Desktop"
    start_menu = (
        Path(os.environ.get("APPDATA", Path.home()))
        / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    )
    desktop.mkdir(parents=True, exist_ok=True)
    start_menu.mkdir(parents=True, exist_ok=True)

    def _make(target: Path) -> str:
        ps = (
            "$ws = New-Object -ComObject WScript.Shell;\n"
            f"$sc = $ws.CreateShortcut('{target}');\n"
            f"$sc.TargetPath = '{py}';\n"
            f"$sc.Arguments = '\"{launcher}\"';\n"
            f"$sc.WorkingDirectory = '{repo_dir}';\n"
            "$sc.Description = 'Open Odysseus';\n"
            "$sc.Save();\n"
        )
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True,
        )
        return r.stderr.strip()

    desktop_target = desktop / f"{APP_NAME}.lnk"
    menu_target = start_menu / f"{APP_NAME}.lnk"
    err = _make(desktop_target) or _make(menu_target)
    return str(desktop_target), (str(menu_target), err)


# ── Public entrypoint ────────────────────────────────────────────────────────

def create_shortcut(repo_dir: Path) -> Tuple[bool, str]:
    """Create the OS-native shortcut(s). Returns (success, human detail)."""
    system = platform.system()
    launcher = _launcher_path(repo_dir)
    if not launcher.exists():
        return False, f"No se encontró {launcher} — el acceso directo no apunta a nada."

    venv_py = _venv_python(repo_dir)
    py = venv_py if venv_py.exists() else Path(sys.executable)

    # Ensure the launcher is runnable on POSIX.
    if system != "Windows":
        launcher.chmod(0o755)

    try:
        if system == "Darwin":
            desktop_dir = Path.home() / "Desktop"
            desktop_dir.mkdir(parents=True, exist_ok=True)
            cmd = _macos_command(desktop_dir, launcher, py)
            apps_dir = Path.home() / "Applications"
            apps_dir.mkdir(parents=True, exist_ok=True)
            bundle = _macos_app_bundle(apps_dir, launcher, py)
            return True, f"Acceso directo en Escritorio ({cmd.name}) y {bundle}"
        elif system == "Windows":
            desktop_target, (menu_target, err) = _windows_shortcuts(repo_dir, launcher, py)
            if err:
                return False, f"No se pudo crear el acceso directo: {err}"
            return True, f"Acceso directo en Escritorio y Menú Inicio ({desktop_target})"
        else:  # Linux and other POSIX
            desktop_target, menu_target = _linux_desktop(repo_dir, launcher, py)
            return True, f"Acceso directo en {desktop_target} y {menu_target}"
    except Exception as exc:  # noqa: BLE001 - surface any platform hiccup
        return False, f"Error creando el acceso directo: {exc}"


if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent
    ok, detail = create_shortcut(root)
    print(("OK: " if ok else "FAIL: ") + detail)
    raise SystemExit(0 if ok else 1)