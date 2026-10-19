"""Odysseus installer — register a daily self-update task per platform.

Schedulers supported:
  * macOS  — LaunchAgent plist under ~/Library/LaunchAgents
  * Linux  — crontab entry (idempotent)
  * Windows— Scheduled Task via schtasks.exe

Every function returns (ok: bool, detail: str) so the wizard can report a clear
result without raising. Registering is idempotent: each helper checks whether the
task already exists before creating it.
"""

import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Tuple

# Daily run time (24h). 03:30 local — outside peak, machine usually idle.
HOUR = 3
MINUTE = 30


def detect_scheduler() -> str:
    """Return a stable label 'launchd' | 'cron' | 'schtasks' for this host."""
    system = platform.system()
    if system == "Darwin":
        return "launchd"
    if system == "Windows":
        return "schtasks"
    return "cron"


def register_daily_update(repo_dir: Path) -> Tuple[bool, str]:
    """Register the daily self-update task for the current platform."""
    system = platform.system()
    if system == "Darwin":
        return _register_launchd(repo_dir)
    if system == "Windows":
        return _register_schtasks(repo_dir)
    return _register_cron(repo_dir)


# ── macOS: LaunchAgent ──────────────────────────────────────────────────────

def _register_launchd(repo_dir: Path) -> Tuple[bool, str]:
    label = "com.odysseus.selfupdate"
    plist_dir = Path.home() / "Library" / "LaunchAgents"
    plist_path = plist_dir / f"{label}.plist"
    script = repo_dir / "installer" / "self_update.sh"

    try:
        plist_dir.mkdir(parents=True, exist_ok=True)
        plist_path.write_text(
            _launchd_plist(label, script, repo_dir), encoding="utf-8"
        )
        subprocess.run(
            ["launchctl", "unload", str(plist_path)],
            check=False,
            capture_output=True,
        )
        # `bootstrap` is the modern (non-deprecated) load on current macOS.
        r = subprocess.run(
            ["launchctl", "bootstrap", "gui/$(id -u)", str(plist_path)],
            check=False,
            capture_output=True,
            text=True,
        )
        if r.returncode == 0:
            return True, f"LaunchAgent instalado en {plist_path}"
        # Fallback to legacy `load` for older macOS.
        r2 = subprocess.run(
            ["launchctl", "load", str(plist_path)],
            check=False,
            capture_output=True,
            text=True,
        )
        if r2.returncode == 0:
            return True, f"LaunchAgent instalado en {plist_path}"
        return False, f"No se pudo cargar el LaunchAgent: {r2.stderr.strip()}"
    except Exception as e:  # noqa: BLE001
        return False, f"Error al registrar LaunchAgent: {e}"


def _launchd_plist(label: str, script: Path, repo_dir: Path) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>{label}</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>{script}</string>
    </array>
    <key>WorkingDirectory</key><string>{repo_dir}</string>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key><integer>{HOUR}</integer>
        <key>Minute</key><integer>{MINUTE}</integer>
    </dict>
    <key>StandardOutPath</key><string>{repo_dir}/logs/self-update.out.log</string>
    <key>StandardErrorPath</key><string>{repo_dir}/logs/self-update.err.log</string>
    <key>RunAtLoad</key><false/>
</dict>
</plist>
"""


# ── Linux / POSIX: crontab ─────────────────────────────────────────────────

def _register_cron(repo_dir: Path) -> Tuple[bool, str]:
    script = repo_dir / "installer" / "self_update.sh"
    entry = f"{MINUTE} {HOUR} * * * /bin/bash {script} >> {repo_dir}/logs/self-update.log 2>&1"
    marker = "Odysseus Self-Update"

    try:
        # Read current crontab (may be empty / absent).
        r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        current = r.stdout if r.returncode == 0 else ""
        if marker in current:
            return True, "Entrada de cron ya existe"

        # Append our entry, then install the merged crontab.
        new = (current.rstrip("\n") + "\n" if current.strip() else "") + \
              f"# {marker}\n{entry}\n"
        p = subprocess.run(
            ["crontab", "-"],
            input=new,
            capture_output=True,
            text=True,
        )
        if p.returncode == 0:
            return True, f"Entrada de cron añadida ({MINUTE:02d}:{HOUR:02d} diario)"
        return False, f"No se pudo instalar crontab: {p.stderr.strip()}"
    except Exception as e:  # noqa: BLE001
        return False, f"Error al registrar crontab: {e}"


# ── Windows: schtasks ──────────────────────────────────────────────────────

def _register_schtasks(repo_dir: Path) -> Tuple[bool, str]:
    script = repo_dir / "installer" / "self_update.ps1"
    task_name = "Odysseus Self-Update"
    cmd = (
        f'powershell -NoProfile -ExecutionPolicy Bypass -File "{script}" '
        f'>> "{repo_dir}\\logs\\self-update.log" 2>&1'
    )

    try:
        # Idempotency: if it already exists, replace it.
        subprocess.run(
            ["schtasks", "/Delete", "/TN", task_name, "/F"],
            check=False,
            capture_output=True,
        )
        r = subprocess.run(
            [
                "schtasks", "/Create", "/TN", task_name,
                "/SC", "DAILY",
                "/ST", f"{HOUR:02d}:{MINUTE:02d}",
                "/TR", cmd,
                "/F",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if r.returncode == 0:
            return True, f"Tarea programada '{task_name}' creada (diario {HOUR:02d}:{MINUTE:02d})"
        return False, f"No se pudo crear la tarea: {r.stderr.strip()}"
    except Exception as e:  # noqa: BLE001
        return False, f"Error al crear tarea programada: {e}"


if __name__ == "__main__":
    repo = Path.cwd()
    ok, detail = register_daily_update(repo)
    print(f"[{'ok' if ok else 'error'}] {detail}")
    sys.exit(0 if ok else 1)