"""Odysseus installer — OS integration: update schedule + start at login.

Update schedule (``register_daily_update`` — name kept for compatibility):
  * macOS   — LaunchAgent ``com.odysseus.selfupdate``: every hour and at login
              (launchd also runs a missed interval when the Mac wakes up)
  * Linux   — crontab entry, every hour (replaces older entries in place)
  * Windows — Scheduled Task "Odysseus Self-Update", every hour, hidden window

Start at login (``register_autostart``), optional:
  * macOS   — LaunchAgent ``com.odysseus.app`` (RunAtLoad)
  * Linux   — ``~/.config/autostart/odysseus.desktop``
  * Windows — shortcut in the Startup folder

Each scheduled run calls installer/self_update.(sh|ps1) -> installer/updater.py.
When the registration format changes, bump ``updater.SCHEDULE_VERSION``: the
updater re-registers existing installs on its next run.

Every function returns (ok: bool, detail: str) and never raises.
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Tuple

INTERVAL_S = 3600            # check for updates every hour
UPDATE_LABEL = "com.odysseus.selfupdate"
APP_LABEL = "com.odysseus.app"
CRON_MARKER = "Odysseus Self-Update"
WIN_TASK = "Odysseus Self-Update"

# Kept for anything importing the old constants.
HOUR = 3
MINUTE = 30


def detect_scheduler() -> str:
    system = platform.system()
    if system == "Darwin":
        return "launchd"
    if system == "Windows":
        return "schtasks"
    return "cron"


def register_daily_update(repo_dir: Path) -> Tuple[bool, str]:
    """Register the (hourly) self-update for this platform. Idempotent."""
    system = platform.system()
    if system == "Darwin":
        return _register_launchd_update(repo_dir)
    if system == "Windows":
        return _register_schtasks(repo_dir)
    return _register_cron(repo_dir)


register_update = register_daily_update


def unregister_update(repo_dir: Path) -> Tuple[bool, str]:
    system = platform.system()
    if system == "Darwin":
        return _launchd_remove(UPDATE_LABEL)
    if system == "Windows":
        r = subprocess.run(["schtasks", "/Delete", "/TN", WIN_TASK, "/F"], capture_output=True, text=True)
        return (r.returncode == 0, "tarea programada eliminada" if r.returncode == 0 else r.stderr.strip())
    return _cron_remove()


# ── macOS ───────────────────────────────────────────────────────────────────

def _launch_agents() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def _plist(label: str, args, repo_dir: Path, extra: str, out_name: str) -> str:
    arg_xml = "\n".join(f"        <string>{a}</string>" for a in args)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>{label}</string>
    <key>ProgramArguments</key>
    <array>
{arg_xml}
    </array>
    <key>WorkingDirectory</key><string>{repo_dir}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    </dict>
{extra}
    <key>StandardOutPath</key><string>{repo_dir}/logs/{out_name}.out.log</string>
    <key>StandardErrorPath</key><string>{repo_dir}/logs/{out_name}.err.log</string>
    <key>ProcessType</key><string>Background</string>
</dict>
</plist>
"""


def _launchd_plist(label: str, script: Path, repo_dir: Path) -> str:
    """The self-update agent (hourly + at login)."""
    extra = (f"    <key>StartInterval</key><integer>{INTERVAL_S}</integer>\n"
             "    <key>RunAtLoad</key><true/>")
    return _plist(label, ["/bin/bash", str(script)], repo_dir, extra, "self-update")


def _launchd_load(plist_path: Path, label: str) -> Tuple[bool, str]:
    domain = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", f"{domain}/{label}"], capture_output=True)
    r = subprocess.run(["launchctl", "bootstrap", domain, str(plist_path)], capture_output=True, text=True)
    if r.returncode == 0:
        return True, f"LaunchAgent instalado en {plist_path}"
    # Older macOS: legacy load/unload.
    subprocess.run(["launchctl", "unload", str(plist_path)], capture_output=True)
    r2 = subprocess.run(["launchctl", "load", "-w", str(plist_path)], capture_output=True, text=True)
    if r2.returncode == 0:
        return True, f"LaunchAgent instalado en {plist_path}"
    return False, f"No se pudo cargar el LaunchAgent: {(r.stderr or r2.stderr).strip()}"


def _launchd_remove(label: str) -> Tuple[bool, str]:
    plist_path = _launch_agents() / f"{label}.plist"
    try:
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{label}"], capture_output=True)
        subprocess.run(["launchctl", "unload", str(plist_path)], capture_output=True)
    except Exception:  # noqa: BLE001
        pass
    try:
        if plist_path.exists():
            plist_path.unlink()
        return True, f"{label} eliminado"
    except OSError as e:
        return False, f"No se pudo eliminar {plist_path}: {e}"


def _register_launchd_update(repo_dir: Path) -> Tuple[bool, str]:
    try:
        (repo_dir / "logs").mkdir(parents=True, exist_ok=True)
        _launch_agents().mkdir(parents=True, exist_ok=True)
        script = repo_dir / "installer" / "self_update.sh"
        plist_path = _launch_agents() / f"{UPDATE_LABEL}.plist"
        plist_path.write_text(_launchd_plist(UPDATE_LABEL, script, repo_dir), encoding="utf-8")
        if os.environ.get("XPC_SERVICE_NAME") == UPDATE_LABEL:
            # We ARE that job: `bootout` would kill this process before the
            # `bootstrap`. The new plist is on disk; launchd loads it at the
            # next login, and a run from the app reloads it sooner.
            return True, f"LaunchAgent actualizado en {plist_path} (se recarga al iniciar sesión)"
        ok, detail = _launchd_load(plist_path, UPDATE_LABEL)
        return ok, (detail + " (cada hora y al iniciar sesión)") if ok else detail
    except Exception as e:  # noqa: BLE001
        return False, f"Error al registrar LaunchAgent: {e}"


# ── Linux / POSIX: crontab ──────────────────────────────────────────────────

def _cron_entry(repo_dir: Path) -> str:
    script = repo_dir / "installer" / "self_update.sh"
    return f"7 * * * * /bin/bash '{script}' >/dev/null 2>&1"


def _strip_ours(crontab: str) -> str:
    """Drop our marker comment and the line right after it (old or new format)."""
    out, skip = [], False
    for line in crontab.splitlines():
        if skip:
            skip = False
            continue
        if line.strip() == f"# {CRON_MARKER}":
            skip = True
            continue
        out.append(line)
    return "\n".join(out).strip("\n")


def _register_cron(repo_dir: Path) -> Tuple[bool, str]:
    try:
        r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        current = r.stdout if r.returncode == 0 else ""
        base = _strip_ours(current)
        new = (base + "\n" if base else "") + f"# {CRON_MARKER}\n{_cron_entry(repo_dir)}\n"
        if new == current:
            return True, "Entrada de cron ya existe (cada hora)"
        p = subprocess.run(["crontab", "-"], input=new, capture_output=True, text=True)
        if p.returncode == 0:
            return True, "Entrada de cron instalada (cada hora)"
        return False, f"No se pudo instalar crontab: {p.stderr.strip()}"
    except FileNotFoundError:
        return False, "crontab no está disponible; Odysseus se actualizará al abrirse y cada hora mientras corre"
    except Exception as e:  # noqa: BLE001
        return False, f"Error al registrar crontab: {e}"


def _cron_remove() -> Tuple[bool, str]:
    try:
        r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        if r.returncode != 0 or CRON_MARKER not in r.stdout:
            return True, "sin entrada de cron"
        base = _strip_ours(r.stdout)
        p = subprocess.run(["crontab", "-"], input=(base + "\n") if base else "", capture_output=True, text=True)
        return p.returncode == 0, "entrada de cron eliminada" if p.returncode == 0 else p.stderr.strip()
    except Exception as e:  # noqa: BLE001
        return False, str(e)


# ── Windows: schtasks ───────────────────────────────────────────────────────

def _register_schtasks(repo_dir: Path) -> Tuple[bool, str]:
    script = repo_dir / "installer" / "self_update.ps1"
    cmd = f'powershell -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{script}"'
    try:
        r = subprocess.run(
            ["schtasks", "/Create", "/TN", WIN_TASK, "/SC", "HOURLY", "/MO", "1",
             "/TR", cmd, "/F"],
            capture_output=True, text=True,
        )
        if r.returncode == 0:
            return True, f"Tarea programada '{WIN_TASK}' creada (cada hora)"
        return False, f"No se pudo crear la tarea: {r.stderr.strip()}"
    except Exception as e:  # noqa: BLE001
        return False, f"Error al crear tarea programada: {e}"


# ── start at login ──────────────────────────────────────────────────────────

def _win_startup_dir() -> Path:
    return (Path(os.environ.get("APPDATA", Path.home())) / "Microsoft" / "Windows"
            / "Start Menu" / "Programs" / "Startup")


def _linux_autostart_file() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    return base / "autostart" / "odysseus.desktop"


def register_autostart(repo_dir: Path) -> Tuple[bool, str]:
    """Start Odysseus in the background when the user logs in."""
    system = platform.system()
    try:
        if system == "Darwin":
            _launch_agents().mkdir(parents=True, exist_ok=True)
            plist_path = _launch_agents() / f"{APP_LABEL}.plist"
            plist_path.write_text(_plist(
                APP_LABEL, ["/bin/bash", str(repo_dir / "installer" / "run.sh"), "--background", "--no-browser"],
                repo_dir, "    <key>RunAtLoad</key><true/>", "autostart"), encoding="utf-8")
            # Don't bootstrap now (that would start a second copy during install);
            # launchd picks it up at the next login.
            return True, f"Odysseus arrancará al iniciar sesión ({plist_path})"
        if system == "Windows":
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            import shortcuts  # noqa: WPS433
            target = _win_startup_dir() / "Odysseus.lnk"
            err = shortcuts.windows_lnk(target, repo_dir, args="--no-browser")
            return (not err, f"Odysseus arrancará al iniciar sesión ({target})" if not err else err)
        f = _linux_autostart_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(
            "[Desktop Entry]\nType=Application\nName=Odysseus\nComment=Start Odysseus in the background\n"
            f"Exec=/bin/bash \"{repo_dir / 'installer' / 'run.sh'}\" --background --no-browser\n"
            "X-GNOME-Autostart-enabled=true\nTerminal=false\n", encoding="utf-8")
        return True, f"Odysseus arrancará al iniciar sesión ({f})"
    except Exception as e:  # noqa: BLE001
        return False, f"No se pudo configurar el arranque al iniciar sesión: {e}"


def unregister_autostart(repo_dir: Path) -> Tuple[bool, str]:
    system = platform.system()
    try:
        if system == "Darwin":
            return _launchd_remove(APP_LABEL)
        target = _win_startup_dir() / "Odysseus.lnk" if system == "Windows" else _linux_autostart_file()
        if target.exists():
            target.unlink()
        return True, "arranque al iniciar sesión desactivado"
    except Exception as e:  # noqa: BLE001
        return False, str(e)


if __name__ == "__main__":
    repo = Path(__file__).resolve().parent.parent
    ok, detail = register_daily_update(repo)
    print(f"[{'ok' if ok else 'error'}] {detail}")
    sys.exit(0 if ok else 1)
