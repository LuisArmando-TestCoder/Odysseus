#!/usr/bin/env python3
"""Odysseus — launcher + supervisor (the target of every shortcut).

Opening Odysseus from the desktop shortcut, the Applications folder, the Start
menu or login autostart runs THIS file. It keeps the server running, keeps it
current and keeps it healthy, with no terminal needed:

  * single instance — opening it again just brings the UI back up
  * repairs the virtualenv when it is missing or broken (e.g. after a
    Homebrew Python upgrade) before starting
  * applies a pending update before the server starts (``updater --on-launch``)
  * checks for updates every hour while running, and when one lands restarts
    the server at a quiet moment (no agent run or research in progress; at
    most ``ODYSSEUS_UPDATE_RESTART_MAX_WAIT`` seconds later)
  * health-checks every new version; if it doesn't come up it rolls back to
    the previous version automatically and restarts that
  * restarts the server if it crashes (with back-off); repeated crashes right
    after an update also trigger a rollback
  * keeps the computer awake while Odysseus works (macOS ``caffeinate``,
    Windows execution state; opt out with ``ODYSSEUS_ALLOW_SLEEP=1``)
  * opens the browser once the server answers, not before

Usage:
  python installer/launch.py                start (or bring the UI up if running)
  python installer/launch.py --background   no console output (shortcuts, autostart)
  python installer/launch.py --no-browser   don't open the browser
  python installer/launch.py --stop         stop Odysseus
  python installer/launch.py --restart      restart the server now
  python installer/launch.py --status       print what is running and the update state
  python installer/launch.py --no-update    skip update checks for this run

Standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import secrets
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import Dict, List, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import updater  # noqa: E402  (stdlib-only sibling module)

REPO_DIR = updater.REPO_DIR
RUN_DIR = updater.DATA_DIR / "run"
SUPERVISOR_PID = updater.SUPERVISOR_PID
SERVER_PID = RUN_DIR / "server.pid"
LOG_DIR = REPO_DIR / "logs"
LAUNCH_LOG = LOG_DIR / "launcher.log"
SERVER_LOG = LOG_DIR / "server.log"

IS_WINDOWS = platform.system() == "Windows"
IS_MAC = platform.system() == "Darwin"

CRASH_WINDOW_S = 10 * 60
CRASH_LIMIT = 5
IDLE_POLL_S = 15


# ── logging / notifications ─────────────────────────────────────────────────

_QUIET = False


def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with LAUNCH_LOG.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass
    if not _QUIET:
        print(line, flush=True)


def notify(text: str) -> None:
    """Best-effort desktop notification (silent when unavailable)."""
    try:
        if IS_MAC:
            safe = text.replace('"', "'")
            subprocess.run(["/usr/bin/osascript", "-e",
                            f'display notification "{safe}" with title "Odysseus"'],
                           capture_output=True, timeout=5)
        elif not IS_WINDOWS and shutil.which("notify-send"):
            subprocess.run(["notify-send", "Odysseus", text], capture_output=True, timeout=5)
    except Exception:  # noqa: BLE001
        pass


# ── settings ────────────────────────────────────────────────────────────────

def settings() -> Dict[str, str]:
    s = updater.read_settings()
    for k in ("APP_PORT", "APP_BIND"):
        if k in os.environ:
            s[k] = os.environ[k]
    return s


def host_port(s: Dict[str, str]):
    port = str(s.get("ODYSSEUS_PORT") or s.get("APP_PORT") or "7860")
    host = s.get("ODYSSEUS_HOST") or s.get("APP_BIND") or "127.0.0.1"
    url_host = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    return host, port, f"http://{url_host}:{port}"


def _int(s: Dict[str, str], key: str, default: int) -> int:
    try:
        return int(str(s.get(key) or default))
    except ValueError:
        return default


# ── http probes ─────────────────────────────────────────────────────────────

def http_json(url: str, timeout: float = 3) -> Optional[dict]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        return {"_status": e.code}
    except Exception:  # noqa: BLE001
        return None


def healthy(base: str) -> bool:
    d = http_json(base + "/api/health", timeout=3)
    return bool(d) and d.get("status") == "healthy"


def server_idle(base: str) -> bool:
    d = http_json(base + "/api/update/idle", timeout=5)
    if d is None:
        return True            # not answering: nothing to protect
    if d.get("_status"):
        return True            # older server without the endpoint
    return bool(d.get("idle", True))


PORT_IN_USE_MARKERS = ("address already in use", "errno 48", "errno 98", "errno 10048",
                       "only one usage of each socket address")


def port_conflict_since(offset: int) -> bool:
    """Did the server log 'port in use' since byte ``offset`` of logs/server.log?
    That's the machine's problem, not the new version's: no rollback for it."""
    try:
        with SERVER_LOG.open("rb") as fh:
            fh.seek(offset)
            tail = fh.read(64_000).decode("utf-8", "replace").lower()
    except OSError:
        return False
    return any(m in tail for m in PORT_IN_USE_MARKERS)


def wait_for_update_lock(max_s: float = 3600, why: str = "") -> bool:
    """Block while an updater is mid-change (code moved, deps installing)."""
    if not updater.update_running():
        return True
    log(f"an update is in progress — waiting for it to finish{(' before ' + why) if why else ''}")
    end = time.time() + max_s
    while time.time() < end:
        time.sleep(3)
        if not updater.update_running():
            return True
    log("WARN: the update is still running after a long wait — continuing anyway")
    return False


# ── virtualenv health / repair ──────────────────────────────────────────────

def _py_ok(py: Path, probe: str = "pass") -> bool:
    try:
        return subprocess.run([str(py), "-c", probe], capture_output=True, timeout=120,
                              **updater.NO_WINDOW).returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _base_python() -> Optional[str]:
    cands: List[str] = []
    if IS_MAC:
        cands += [f"/opt/homebrew/bin/python3.{v}" for v in (13, 12, 11)]
        cands += [f"/usr/local/bin/python3.{v}" for v in (13, 12, 11)]
    cands += ["python3.13", "python3.12", "python3.11", "python3", "python"]
    if IS_WINDOWS:
        cands = ["py"] + cands
    me = sys.executable
    seen = set()
    for c in [me] + cands:
        p = c if os.path.isabs(c) else shutil.which(c)
        if not p or p in seen:
            continue
        seen.add(p)
        args = [p, "-3"] if os.path.basename(p).lower().startswith("py.") or os.path.basename(p).lower() == "py" else [p]
        try:
            r = subprocess.run(args + ["-c", "import sys; print(sys.executable); "
                                       "raise SystemExit(0 if sys.version_info[:2] >= (3, 11) else 1)"],
                               capture_output=True, text=True, timeout=20, **updater.NO_WINDOW)
            if r.returncode == 0:
                return r.stdout.strip() or p
        except Exception:  # noqa: BLE001
            continue
    return None


def ensure_venv() -> bool:
    """Make sure venv exists and can import the server. Repairs it if not."""
    vpy = updater.venv_python()
    probe = "import uvicorn, fastapi"
    if vpy.exists() and _py_ok(vpy, probe):
        return True
    venv = REPO_DIR / "venv"
    if vpy.exists() and _py_ok(vpy):
        log("dependencies missing — reinstalling into the existing venv")
        notify("Repairing Odysseus dependencies…")
    else:
        base = _base_python()
        if not base:
            log("ERROR: no Python 3.11+ found to rebuild the venv")
            notify("Odysseus needs Python 3.11+ — re-run the installer.")
            return False
        if venv.exists():
            broken = venv.with_name(f"venv.broken-{time.strftime('%Y%m%d%H%M%S')}")
            try:
                venv.rename(broken)
                log(f"venv was broken — moved aside to {broken.name}")
            except OSError as exc:
                log(f"ERROR: cannot move the broken venv: {exc}")
                return False
        log(f"creating venv with {base}")
        notify("Rebuilding Odysseus' Python environment (a few minutes)…")
        if subprocess.run([base, "-m", "venv", str(venv)], capture_output=True, **updater.NO_WINDOW).returncode != 0:
            log("ERROR: venv creation failed")
            return False
    err = updater.pip_install()
    if err:
        log(f"ERROR: dependency install failed: {err[-400:]}")
        notify("Odysseus could not install its dependencies — see logs/launcher.log")
        return False
    updater.run_setup_pass()
    return _py_ok(updater.venv_python(), probe)


# ── pid files ───────────────────────────────────────────────────────────────

def _write_pid(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _read_pid(path: Path) -> int:
    try:
        return int(json.loads(path.read_text(encoding="utf-8")).get("pid") or 0)
    except (OSError, ValueError, TypeError):
        return 0


def _rm(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def _kill_tree(pid: int, graceful_s: float = 20) -> None:
    if pid <= 0 or not updater._pid_alive(pid):
        return
    if IS_WINDOWS:
        subprocess.run(["taskkill", "/PID", str(pid), "/T"], capture_output=True, **updater.NO_WINDOW)
        deadline = time.time() + graceful_s
        while time.time() < deadline and updater._pid_alive(pid):
            time.sleep(0.5)
        if updater._pid_alive(pid):
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, **updater.NO_WINDOW)
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return
    deadline = time.time() + graceful_s
    while time.time() < deadline and updater._pid_alive(pid):
        time.sleep(0.5)
    if updater._pid_alive(pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


# ── keep-awake ──────────────────────────────────────────────────────────────

class KeepAwake:
    def __init__(self, enabled: bool):
        self.enabled = enabled
        self.proc: Optional[subprocess.Popen] = None

    def attach(self, pid: int) -> None:
        if not self.enabled:
            return
        self.release()
        if IS_MAC and shutil.which("caffeinate"):
            self.proc = subprocess.Popen(["caffeinate", "-ims", "-w", str(pid)],
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif IS_WINDOWS:
            try:
                import ctypes
                ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
                ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
            except Exception:  # noqa: BLE001
                pass

    def release(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
        self.proc = None
        if IS_WINDOWS and self.enabled:
            try:
                import ctypes
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
            except Exception:  # noqa: BLE001
                pass


# ── the supervisor ──────────────────────────────────────────────────────────

class Supervisor:
    def __init__(self, args):
        self.args = args
        self.s = settings()
        self.host, self.port, self.base = host_port(self.s)
        self.child: Optional[subprocess.Popen] = None
        self.child_sha = ""
        self.child_log_offset = 0
        self.critical = False          # inside a rollback / stopped-server update
        self.retry_at = 0.0
        self.stopping = False
        self.crashes: List[float] = []
        self.awaiting_health_until = 0.0
        self.awaiting_sha: Optional[str] = None
        self.restart_wait_since = 0.0
        self.opened_browser = bool(args.no_browser or os.environ.get("ODYSSEUS_NO_OPEN"))
        self.update_enabled = (not args.no_update) and updater._truthy(self.s.get("ODYSSEUS_AUTO_UPDATE"), True)
        self.update_every = max(10, _int(self.s, "ODYSSEUS_UPDATE_INTERVAL_MIN", 60)) * 60
        self.restart_max_wait = _int(self.s, "ODYSSEUS_UPDATE_RESTART_MAX_WAIT", 1800)
        self.health_timeout = _int(self.s, "ODYSSEUS_HEALTH_TIMEOUT", 300)
        self.next_update = time.time() + self.update_every
        self.update_proc: Optional[subprocess.Popen] = None
        self.awake = KeepAwake(not updater._truthy(self.s.get("ODYSSEUS_ALLOW_SLEEP"), False))
        self.server_log = None

    # child process -----------------------------------------------------------
    def start_child(self) -> None:
        vpy = updater.venv_python()
        cmd = [str(vpy), "-m", "uvicorn", "app:app", "--host", self.host, "--port", self.port]
        self.shutdown_token = secrets.token_urlsafe(24)
        kwargs = {"cwd": str(REPO_DIR), "env": dict(os.environ, ODYSSEUS_SHUTDOWN_TOKEN=self.shutdown_token)}
        try:
            self.child_log_offset = SERVER_LOG.stat().st_size
        except OSError:
            self.child_log_offset = 0
        if self.args.background or not sys.stdout or not sys.stdout.isatty():
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            self.server_log = open(SERVER_LOG, "a", encoding="utf-8")  # noqa: SIM115
            kwargs.update(stdout=self.server_log, stderr=subprocess.STDOUT)
        if IS_WINDOWS:
            flags = subprocess.CREATE_NEW_PROCESS_GROUP
            if self.args.background:
                flags |= 0x08000000  # CREATE_NO_WINDOW
            kwargs["creationflags"] = flags
        self.child_sha = updater.head_sha()
        self.child = subprocess.Popen(cmd, **kwargs)
        _write_pid(SERVER_PID, {"pid": self.child.pid, "t": time.time(), "port": self.port,
                                "sha": self.child_sha})
        self.awake.attach(self.child.pid)
        log(f"server started (pid {self.child.pid}) at {self.base} — {self.child_sha[:9]}")

    def stop_child(self) -> None:
        if self.child and self.child.poll() is None:
            log(f"stopping server (pid {self.child.pid})")
            try:
                if IS_WINDOWS:
                    # No shared console in background mode, so CTRL_BREAK can't
                    # reach it: ask the server itself to shut down cleanly.
                    if not self.http_shutdown():
                        self.child.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    self.child.terminate()
                self.child.wait(20)
            except Exception:  # noqa: BLE001 — timeout or signal failure
                _kill_tree(self.child.pid, 0)
                try:
                    self.child.wait(10)
                except Exception:  # noqa: BLE001
                    pass
        self.child = None
        self.awake.release()
        _rm(SERVER_PID)
        if self.server_log:
            self.server_log.close()
            self.server_log = None

    def http_shutdown(self) -> bool:
        req = urllib.request.Request(self.base + "/api/update/shutdown", method="POST", data=b"",
                                     headers={"X-Odysseus-Shutdown": getattr(self, "shutdown_token", "")})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status == 200
        except Exception:  # noqa: BLE001
            return False

    def restart_child(self, reason: str) -> None:
        log(f"restarting server: {reason}")
        self.stop_child()
        # The port can take a moment to free up.
        for _ in range(20):
            if not healthy(self.base):
                break
            time.sleep(0.5)
        self.start_child()

    # update handling ---------------------------------------------------------
    def run_updater_now(self, *extra: str, wait: bool) -> Optional[int]:
        cmd = [sys.executable, str(HERE / "updater.py"), *extra]
        env = dict(os.environ, ODYSSEUS_UPDATE_QUIET="1")
        if wait:
            try:
                return subprocess.run(cmd, cwd=str(REPO_DIR), env=env, timeout=50 * 60,
                                      **updater.NO_WINDOW).returncode
            except subprocess.TimeoutExpired:
                log("update before launch timed out — starting the current version")
                return 1
        if self.update_proc and self.update_proc.poll() is None:
            return None
        self.update_proc = subprocess.Popen(cmd, cwd=str(REPO_DIR), env=env, stdout=subprocess.DEVNULL,
                                            stderr=subprocess.DEVNULL, **updater.NO_WINDOW)
        return None

    def pending_restart(self) -> Optional[dict]:
        if not updater.RESTART_FLAG.exists():
            return None
        return updater._read_json(updater.RESTART_FLAG, {"reason": "requested"})

    def handle_restart_flag(self) -> None:
        flag = self.pending_restart()
        if not flag:
            self.restart_wait_since = 0.0
            return
        if not self.restart_wait_since:
            self.restart_wait_since = time.time()
            log(f"restart requested ({flag.get('reason')}) — waiting for a quiet moment")
        waited = time.time() - self.restart_wait_since
        if flag.get("force"):
            why = "now"
        elif waited >= self.restart_max_wait:
            why = f"still busy after {int(waited)}s — not waiting longer"
        elif server_idle(self.base):
            why = "quiet moment"
        else:
            return
        _rm(updater.RESTART_FLAG)
        self.restart_wait_since = 0.0
        if flag.get("apply_before_start"):
            # Windows dependency update: install it with the server stopped.
            log(f"stopping the server to install an update ({why})")
            self.critical = True
            try:
                self.stop_child()
                wait_for_update_lock(why="installing")
                self.run_updater_now("--on-launch", wait=True)
            finally:
                self.critical = False
            self.start_child()
            self.expect_health(None)
            return
        self.restart_child(f"{flag.get('reason') or 'requested'} ({why})")
        self.expect_health(flag.get("sha"))

    def expect_health(self, sha: Optional[str]) -> None:
        self.awaiting_sha = sha or updater.head_sha()
        self.awaiting_health_until = time.time() + self.health_timeout

    def do_rollback(self, why: str) -> bool:
        if port_conflict_since(self.child_log_offset):
            log(f"port {self.port} is used by another program — not blaming the update")
            notify(f"Port {self.port} is in use by another program. Close it or set APP_PORT in .env.")
            return False
        log(f"new version unhealthy ({why}) — rolling back")
        self.critical = True
        try:
            lock = updater.Lock()
            end = time.time() + 3600
            while not lock.acquire():
                if time.time() > end:
                    log("ERROR: could not take the update lock for rollback")
                    return False
                time.sleep(3)
            try:
                rc = updater.rollback(why, mark_bad=True, restart=False)
            finally:
                lock.release()
        finally:
            self.critical = False
        if rc != 0:
            return False
        notify("An update didn't start correctly — Odysseus went back to the previous version.")
        _rm(updater.RESTART_FLAG)
        self.crashes.clear()
        self.restart_child("rollback")
        self.expect_health(updater.head_sha())
        return True

    # main loop ---------------------------------------------------------------
    def first_boot(self) -> int:
        updater.LOG_DIR.mkdir(parents=True, exist_ok=True)
        wait_for_update_lock(why="starting")
        if self.update_enabled:
            log("checking for updates before start…")
            self.run_updater_now("--on-launch", wait=True)
        if not ensure_venv():
            return 1
        wait_for_update_lock(why="starting")
        _rm(updater.RESTART_FLAG)   # we're starting fresh on the current code anyway
        self.start_child()
        self.expect_health(updater.head_sha())
        return 0

    def loop(self) -> int:
        rc = self.first_boot()
        if rc:
            return rc
        while not self.stopping:
            time.sleep(2)
            now = time.time()

            # health gate after a (re)start
            if self.awaiting_health_until:
                if healthy(self.base):
                    self.awaiting_health_until = 0.0
                    updater.mark_good(self.child_sha)
                    log(f"server healthy on {self.child_sha[:9]}")
                    if not self.opened_browser:
                        self.opened_browser = True
                        try:
                            webbrowser.open(self.base)
                        except Exception:  # noqa: BLE001
                            pass
                    continue
                died = self.child is None or self.child.poll() is not None
                if died and updater.update_running():
                    # It started on half-applied code; wait for the updater, then retry.
                    wait_for_update_lock(why="restarting")
                    self.restart_child("an update finished")
                    self.expect_health(None)
                    continue
                if died or now > self.awaiting_health_until:
                    why = "server exited during start" if died else f"no health answer in {self.health_timeout}s"
                    st = updater.load_state()
                    recently_updated = st.get("previous") and now - float(st.get("updated_at") or 0) < 6 * 3600
                    if recently_updated and self.do_rollback(why):
                        continue
                    self.awaiting_health_until = 0.0
                    if not died:
                        log(f"WARN: {why} (no recent update to roll back)")
                        continue

            # crash supervision
            if self.child is not None and self.child.poll() is not None:
                code = self.child.returncode
                self.child = None
                self.awake.release()
                self.crashes = [t for t in self.crashes if now - t < CRASH_WINDOW_S] + [now]
                log(f"server exited with code {code} ({len(self.crashes)} in 10 min)")
                if updater.update_running():
                    # Probably started on half-applied code: wait it out, don't count it.
                    self.crashes.pop()
                    wait_for_update_lock(why="restarting")
                elif len(self.crashes) >= CRASH_LIMIT and port_conflict_since(self.child_log_offset):
                    notify(f"Port {self.port} is in use by another program. Close it or set APP_PORT in .env.")
                    log(f"port {self.port} is in use — retrying in 60s")
                    self.crashes.clear()
                    time.sleep(60)
                elif len(self.crashes) >= CRASH_LIMIT:
                    st = updater.load_state()
                    if st.get("previous") and now - float(st.get("updated_at") or 0) < 6 * 3600:
                        if self.do_rollback("repeated crashes after an update"):
                            continue
                    # Stay up: keep checking for updates (the next commit may be
                    # the fix) and try again every 10 minutes.
                    notify("Odysseus keeps stopping — retrying every 10 minutes. See logs/server.log")
                    log("repeated crashes — pausing 10 min before the next try")
                    self.crashes.clear()
                    self.retry_at = now + 600
                    continue
                time.sleep(min(60, 2 ** len(self.crashes)))
                self.start_child()
                self.expect_health(None)
                continue

            if self.child is None and self.retry_at and now >= self.retry_at:
                self.retry_at = 0.0
                self.start_child()
                self.expect_health(None)
                continue

            # periodic update check (the OS schedule covers times we're not running)
            if self.update_enabled and now >= self.next_update:
                self.next_update = now + self.update_every
                self.run_updater_now(wait=False)

            self.handle_restart_flag()
        return 0

    def shutdown(self, *_):
        self.stopping = True
        if self.critical:
            # Mid-rollback / mid-install: let it finish (the loop then exits);
            # killing git here would leave the checkout half-moved.
            log("stop requested — finishing the current update step first")
            return
        self.stop_child()
        _rm(SUPERVISOR_PID)
        log("Odysseus stopped")
        raise SystemExit(0)


# ── commands ────────────────────────────────────────────────────────────────

def cmd_status() -> int:
    s = settings()
    _, port, base = host_port(s)
    sup = _read_pid(SUPERVISOR_PID)
    srv = _read_pid(SERVER_PID)
    st = updater.status()
    print(json.dumps({
        "url": base,
        "supervisor_pid": sup if updater._pid_alive(sup) else None,
        "server_pid": srv if updater._pid_alive(srv) else None,
        "healthy": healthy(base),
        "version": st.get("head", "")[:9],
        "mode": st.get("mode"),
        "branch": st.get("branch") or st.get("branch_checked_out"),
        "last_check": st.get("last_check"),
        "last_result": st.get("last_result"),
        "update_available": (st.get("available") or {}).get("count"),
        "restart_pending": st.get("restart_requested"),
    }, indent=2))
    return 0


def cmd_stop() -> int:
    pid = _read_pid(SUPERVISOR_PID)
    if pid and updater._pid_alive(pid):
        log(f"stopping Odysseus (supervisor pid {pid})")
        _kill_tree(pid, graceful_s=30)
        _rm(SUPERVISOR_PID)
    srv = _read_pid(SERVER_PID)
    if srv and updater._pid_alive(srv):
        _kill_tree(srv)
    _rm(SERVER_PID)
    print("Odysseus stopped.")
    return 0


def cmd_restart() -> int:
    if not updater.supervisor_alive():
        print("Odysseus isn't running under the launcher — starting it.")
        return -1
    updater.request_restart("manual", updater.head_sha(), force=True)
    print("Restart requested.")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    global _QUIET
    p = argparse.ArgumentParser(description="Odysseus launcher / supervisor")
    p.add_argument("--background", action="store_true")
    p.add_argument("--no-browser", action="store_true")
    p.add_argument("--no-update", action="store_true")
    p.add_argument("--stop", action="store_true")
    p.add_argument("--restart", action="store_true")
    p.add_argument("--status", action="store_true")
    a = p.parse_args(argv)
    _QUIET = a.background

    if a.status:
        return cmd_status()
    if a.stop:
        return cmd_stop()
    if a.restart:
        rc = cmd_restart()
        if rc >= 0:
            return rc

    sup = Supervisor(a)
    # A server we started whose supervisor was killed (force quit, crash):
    # take it over, so updates and crash recovery work again.
    orphan = _read_pid(SERVER_PID)
    if not updater.supervisor_alive() and orphan and updater._pid_alive(orphan):
        log(f"adopting orphaned server (pid {orphan}) — restarting it under the launcher")
        _kill_tree(orphan)
        _rm(SERVER_PID)
    # Single instance: a supervisor is already up, or some other server owns the port.
    if updater.supervisor_alive() or healthy(sup.base):
        log(f"Odysseus is already running at {sup.base}")
        if not a.no_browser and not os.environ.get("ODYSSEUS_NO_OPEN"):
            webbrowser.open(sup.base)
        return 0

    _write_pid(SUPERVISOR_PID, {"pid": os.getpid(), "t": time.time(), "port": sup.port})
    for sig in (signal.SIGTERM, signal.SIGINT) + ((signal.SIGHUP,) if hasattr(signal, "SIGHUP") else ()):
        try:
            signal.signal(sig, sup.shutdown)
        except (OSError, ValueError):
            pass
    if IS_WINDOWS and hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, sup.shutdown)
    if not a.background:
        print(f"▶ Odysseus — {sup.base}  (Ctrl+C to stop; updates install themselves)")
    try:
        return sup.loop()
    except KeyboardInterrupt:
        sup.shutdown()
        return 0
    finally:
        if not sup.stopping:
            sup.stop_child()
            _rm(SUPERVISOR_PID)


if __name__ == "__main__":
    raise SystemExit(main())
