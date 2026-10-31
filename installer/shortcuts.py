#!/usr/bin/env python3
"""Odysseus installer — cross-platform desktop shortcuts.

Every shortcut goes through the supervisor (installer/launch.py) via a small
platform entry point that survives a broken venv:

  * macOS   — ``~/Applications/Odysseus.app`` (starts in the background, no
              Terminal; opening it again brings the UI up), ``Stop Odysseus.app``
              next to it, and ``~/Desktop/Odysseus.command`` (Terminal mode
              with live logs; Ctrl+C stops).
  * Linux   — ``Odysseus`` and ``Stop Odysseus`` .desktop entries in the app
              menu, plus ``Odysseus`` on the Desktop.
  * Windows — ``Odysseus`` on the Desktop and Start Menu, ``Stop Odysseus`` in
              the Start Menu (both via installer\\run.cmd, no console window).

To change names/icons/targets, edit ONLY this file.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List, Tuple

APP_NAME = "Odysseus"
STOP_NAME = "Stop Odysseus"


def _run_sh(repo_dir: Path) -> Path:
    return repo_dir / "installer" / "run.sh"


def _run_cmd(repo_dir: Path) -> Path:
    return repo_dir / "installer" / "run.cmd"


# ── macOS ────────────────────────────────────────────────────────────────────

def _mac_icon(repo_dir: Path, resources: Path) -> bool:
    src = repo_dir / "docs" / "odysseus.jpg"
    if not src.exists() or not shutil.which("sips"):
        return False
    with tempfile.TemporaryDirectory() as tmp:
        sq, png = Path(tmp) / "sq.png", Path(tmp) / "icon.png"
        subprocess.run(["sips", "-c", "720", "720", str(src), "--out", str(sq)], capture_output=True)
        subprocess.run(["sips", "-z", "512", "512", str(sq if sq.exists() else src), "--out", str(png)],
                       capture_output=True)
        r = subprocess.run(["sips", "-s", "format", "icns", str(png), "--out",
                            str(resources / "odysseus.icns")], capture_output=True)
        return r.returncode == 0


def _mac_bundle(apps: Path, name: str, ident: str, script_body: str, repo_dir: Path) -> Path:
    bundle = apps / f"{name}.app"
    macos_dir = bundle / "Contents" / "MacOS"
    res = bundle / "Contents" / "Resources"
    macos_dir.mkdir(parents=True, exist_ok=True)
    res.mkdir(parents=True, exist_ok=True)
    has_icon = _mac_icon(repo_dir, res)
    (bundle / "Contents" / "Info.plist").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0"><dict>\n'
        f"  <key>CFBundleName</key><string>{name}</string>\n"
        f"  <key>CFBundleDisplayName</key><string>{name}</string>\n"
        f"  <key>CFBundleIdentifier</key><string>{ident}</string>\n"
        "  <key>CFBundleVersion</key><string>2.0</string>\n"
        "  <key>CFBundleShortVersionString</key><string>2.0</string>\n"
        "  <key>CFBundleExecutable</key><string>launcher</string>\n"
        "  <key>CFBundlePackageType</key><string>APPL</string>\n"
        + ("  <key>CFBundleIconFile</key><string>odysseus</string>\n" if has_icon else "")
        + "  <key>LSMinimumSystemVersion</key><string>11.0</string>\n"
        "  <key>NSHighResolutionCapable</key><true/>\n"
        "</dict></plist>\n",
        encoding="utf-8",
    )
    launcher = macos_dir / "launcher"
    launcher.write_text(script_body, encoding="utf-8")
    launcher.chmod(0o755)
    subprocess.run(["touch", str(bundle)], capture_output=True)
    return bundle


def _macos(repo_dir: Path) -> List[Path]:
    run_sh = _run_sh(repo_dir)
    apps = Path.home() / "Applications"
    apps.mkdir(parents=True, exist_ok=True)
    # Detach so the app returns immediately (a bash-script app can't answer
    # the Dock's Quit); the supervisor keeps running in the background.
    start = _mac_bundle(apps, APP_NAME, "ai.odysseus.app",
                        "#!/bin/bash\n"
                        f"nohup /bin/bash '{run_sh}' --background >/dev/null 2>&1 &\n"
                        "exit 0\n", repo_dir)
    stop = _mac_bundle(apps, STOP_NAME, "ai.odysseus.stop",
                       "#!/bin/bash\n"
                       f"/bin/bash '{run_sh}' --stop --background >/dev/null 2>&1\n"
                       "/usr/bin/osascript -e 'display notification \"Odysseus stopped\" "
                       "with title \"Odysseus\"' >/dev/null 2>&1\n", repo_dir)
    desktop = Path.home() / "Desktop"
    desktop.mkdir(parents=True, exist_ok=True)
    cmd = desktop / f"{APP_NAME}.command"
    cmd.write_text("#!/bin/bash\n# Odysseus in a Terminal window (live logs; Ctrl+C stops).\n"
                   f"exec /bin/bash '{run_sh}' \"$@\"\n", encoding="utf-8")
    cmd.chmod(0o755)
    return [start, stop, cmd]


# ── Linux ────────────────────────────────────────────────────────────────────

def _linux_entry(name: str, comment: str, exec_line: str, icon: str) -> str:
    return ("[Desktop Entry]\nType=Application\n"
            f"Name={name}\nComment={comment}\nExec={exec_line}\n"
            + (f"Icon={icon}\n" if icon else "")
            + "Terminal=false\nCategories=Utility;Network;\n")


def _linux(repo_dir: Path) -> List[Path]:
    run_sh = _run_sh(repo_dir)
    icon_src = repo_dir / "docs" / "odysseus.jpg"
    icon = str(icon_src) if icon_src.exists() else ""
    start = _linux_entry(APP_NAME, "Open Odysseus", f"/bin/bash \"{run_sh}\" --background", icon)
    stop = _linux_entry(STOP_NAME, "Stop the Odysseus server", f"/bin/bash \"{run_sh}\" --stop --background", icon)

    menu = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")) / "applications"
    menu.mkdir(parents=True, exist_ok=True)
    desktop_dir = Path.home() / "Desktop"
    try:
        out = subprocess.run(["xdg-user-dir", "DESKTOP"], capture_output=True, text=True).stdout.strip()
        if out:
            desktop_dir = Path(out)
    except (OSError, FileNotFoundError):
        pass
    made: List[Path] = []
    for target, body in ((menu / "odysseus.desktop", start), (menu / "odysseus-stop.desktop", stop),
                         (desktop_dir / "Odysseus.desktop", start)):
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body, encoding="utf-8")
            target.chmod(0o755)
            made.append(target)
        except OSError:
            continue
    # GNOME: mark the desktop file trusted so it launches on double-click.
    if shutil.which("gio") and (desktop_dir / "Odysseus.desktop").exists():
        subprocess.run(["gio", "set", str(desktop_dir / "Odysseus.desktop"), "metadata::trusted", "true"],
                       capture_output=True)
    return made


# ── Windows ──────────────────────────────────────────────────────────────────

def windows_lnk(target: Path, repo_dir: Path, args: str = "", description: str = "Open Odysseus") -> str:
    """Create a .lnk that runs installer\\run.cmd minimized. Returns '' or an error."""
    run_cmd = _run_cmd(repo_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    esc = lambda s: str(s).replace("'", "''")  # noqa: E731
    ps = (
        "$ws = New-Object -ComObject WScript.Shell;"
        f"$sc = $ws.CreateShortcut('{esc(target)}');"
        f"$sc.TargetPath = '{esc(run_cmd)}';"
        f"$sc.Arguments = '{esc(args)}';"
        f"$sc.WorkingDirectory = '{esc(repo_dir)}';"
        "$sc.WindowStyle = 7;"
        f"$sc.Description = '{esc(description)}';"
        "$sc.Save();"
    )
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                       capture_output=True, text=True)
    return r.stderr.strip() if r.returncode != 0 else ""


def _windows(repo_dir: Path) -> Tuple[List[Path], str]:
    desktop = Path(os.environ.get("USERPROFILE", Path.home())) / "Desktop"
    menu = Path(os.environ.get("APPDATA", Path.home())) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    made, errs = [], []
    for target, args, desc in ((desktop / f"{APP_NAME}.lnk", "", "Open Odysseus"),
                               (menu / f"{APP_NAME}.lnk", "", "Open Odysseus"),
                               (menu / f"{STOP_NAME}.lnk", "--stop", "Stop Odysseus")):
        err = windows_lnk(target, repo_dir, args, desc)
        (errs if err else made).append(err or target)
    return made, "; ".join(errs)


# ── Public entrypoints ───────────────────────────────────────────────────────

def create_shortcut(repo_dir: Path) -> Tuple[bool, str]:
    system = platform.system()
    try:
        if system == "Darwin":
            made = _macos(repo_dir)
            return True, "Accesos directos: " + ", ".join(str(p) for p in made)
        if system == "Windows":
            made, err = _windows(repo_dir)
            if not made:
                return False, f"No se pudo crear el acceso directo: {err}"
            return True, "Accesos directos: " + ", ".join(str(p) for p in made)
        made = _linux(repo_dir)
        return bool(made), "Accesos directos: " + ", ".join(str(p) for p in made)
    except Exception as exc:  # noqa: BLE001
        return False, f"Error creando el acceso directo: {exc}"


def remove_shortcuts(repo_dir: Path) -> List[str]:
    """Remove what create_shortcut made. Returns the paths removed."""
    system = platform.system()
    home = Path.home()
    if system == "Darwin":
        cands = [home / "Applications" / f"{APP_NAME}.app", home / "Applications" / f"{STOP_NAME}.app",
                 home / "Desktop" / f"{APP_NAME}.command"]
    elif system == "Windows":
        menu = Path(os.environ.get("APPDATA", home)) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        cands = [Path(os.environ.get("USERPROFILE", home)) / "Desktop" / f"{APP_NAME}.lnk",
                 menu / f"{APP_NAME}.lnk", menu / f"{STOP_NAME}.lnk"]
    else:
        menu = Path(os.environ.get("XDG_DATA_HOME") or (home / ".local" / "share")) / "applications"
        cands = [menu / "odysseus.desktop", menu / "odysseus-stop.desktop", home / "Desktop" / "Odysseus.desktop"]
    removed = []
    for p in cands:
        try:
            # Only remove things that point at THIS install.
            if p.is_dir():
                launcher = p / "Contents" / "MacOS" / "launcher"
                if launcher.exists() and str(repo_dir) not in launcher.read_text(encoding="utf-8", errors="ignore"):
                    continue
                shutil.rmtree(p)
            elif p.exists():
                if p.suffix in (".command", ".desktop") and str(repo_dir) not in p.read_text(encoding="utf-8", errors="ignore"):
                    continue
                p.unlink()
            else:
                continue
            removed.append(str(p))
        except OSError:
            continue
    return removed


if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent
    ok, detail = create_shortcut(root)
    print(("OK: " if ok else "FAIL: ") + detail)
    raise SystemExit(0 if ok else 1)
