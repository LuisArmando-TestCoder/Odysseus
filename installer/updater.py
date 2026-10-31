#!/usr/bin/env python3
"""Odysseus — autonomous updater (macOS, Linux, Windows).

Keeps an installed copy of Odysseus on the tip of the upstream ``main`` branch
(``master`` is detected automatically for repos that still use it) and makes
sure the running server actually picks the new code up.

One run does, in order:

  1. take a lock (one updater at a time; stale locks expire)
  2. fetch the update branch from ``origin``
  3. stop early when already current, when the tip is a commit that failed
     here before, or when GitHub CI says the tip is broken ("update gate")
  4. save local edits (``git stash``) and, if history diverged, a backup
     branch (``odysseus/backup-<time>``) — nothing local is ever lost
  5. move the checkout to the new tip
  6. syntax-check the changed Python files       -> roll back on error
  7. reinstall dependencies when requirements changed -> roll back on error
  8. run the idempotent ``setup.py`` pass (migrations, new data dirs)
  9. record the update and ask the supervisor (installer/launch.py) to restart
     the server; the supervisor health-checks the new version and calls
     ``--rollback`` if it does not come up

Install modes (``data/install.json`` -> ``mode``):

  managed  installed by the executable. Fully autonomous: follows the branch
           even across force-pushes or diverged history (after a backup).
  dev      a developer clone. Only checks and reports; never moves the
           checkout unless ``--force`` is given.
  (none)   legacy install from before this file existed: fast-forward only.
           A scheduled run (``--scheduled``) on a legacy install upgrades it
           to ``managed``, because only the installer registers schedules.

Settings (environment or ``.env``; environment wins):

  ODYSSEUS_AUTO_UPDATE      on | off            (default on)
  ODYSSEUS_UPDATE_BRANCH    branch to follow    (default: origin's HEAD, i.e. main)
  ODYSSEUS_UPDATE_CI_GATE   on | off            (default on) skip tips whose CI failed

Usage:

  python installer/updater.py              check, and apply if there is something new
  python installer/updater.py --check      check only (no changes)
  python installer/updater.py --status     print the saved state as JSON
  python installer/updater.py --rollback   go back to the previous version
  python installer/updater.py --on-launch  quick variant used before the server starts
  python installer/updater.py --scheduled  marks a run started by the OS scheduler
  python installer/updater.py --force      also apply on a dev clone

Exit codes: 0 ok / nothing to do, 1 error, 2 skipped (disabled, locked, gated).
Standard library only — it must run even when the venv is broken.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple

REPO_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_DIR / "data"
UPDATE_DIR = DATA_DIR / "update"
STATE_PATH = UPDATE_DIR / "state.json"
LOCK_PATH = UPDATE_DIR / "update.lock"
RESTART_FLAG = UPDATE_DIR / "restart-requested.json"
INSTALL_MARKER = DATA_DIR / "install.json"
LOG_DIR = REPO_DIR / "logs"
LOG_PATH = LOG_DIR / "self-update.log"

GIT_LOCK_STALE_S = 10 * 60          # a .git/*.lock older than this, with our lock held, is debris
HISTORY_KEEP = 25
BAD_KEEP = 50
CI_GATE_JOB = "update-gate"          # job name in .github/workflows/ci.yml
CI_PENDING_GRACE_S = 2 * 3600        # a tip whose CI never finishes is taken after this
REQUIREMENT_FILES = ("requirements.txt",)
SCHEDULE_VERSION = 2                 # bump when schedulers.py changes what it registers

IS_WINDOWS = platform.system() == "Windows"
# Child processes never pop up a console window on Windows.
NO_WINDOW = {"creationflags": 0x08000000} if IS_WINDOWS else {}


# ── small helpers ───────────────────────────────────────────────────────────

def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass
    if os.environ.get("ODYSSEUS_UPDATE_QUIET") != "1":
        print(line, flush=True)


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def run(cmd, timeout: float, **kw) -> subprocess.CompletedProcess:
    """subprocess.run, but a timeout first asks the process to stop (so git
    can remove its own lock files) and only then kills it. No console windows."""
    kw.setdefault("stdout", subprocess.PIPE)
    kw.setdefault("stderr", subprocess.PIPE)
    kw.setdefault("text", True)
    for k, v in NO_WINDOW.items():
        kw.setdefault(k, v)
    with subprocess.Popen(cmd, **kw) as proc:
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.communicate(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
            raise subprocess.TimeoutExpired(cmd, timeout)
        return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


def git(*args: str, timeout: float = 120, check: bool = False) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.setdefault("GIT_TERMINAL_PROMPT", "0")   # never hang on a credential prompt
    env.setdefault("GIT_ASKPASS", "echo")
    env["LC_ALL"] = "C"
    r = run(["git", "-C", str(REPO_DIR), *args], timeout=timeout, env=env)
    if check and r.returncode != 0:
        raise UpdateError(f"git {' '.join(args)} failed: {(r.stderr or r.stdout).strip()[-400:]}")
    return r


def git_out(*args: str, timeout: float = 60) -> str:
    r = git(*args, timeout=timeout)
    return r.stdout.strip() if r.returncode == 0 else ""


class UpdateError(Exception):
    pass


def read_settings() -> Dict[str, str]:
    """Merge .env (lower priority) with the process environment."""
    vals: Dict[str, str] = {}
    env_path = REPO_DIR / ".env"
    try:
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[7:]
            k, _, v = line.partition("=")
            v = v.strip()
            if v[:1] in ("'", '"') and v[-1:] == v[:1]:
                v = v[1:-1]
            else:
                v = v.split(" #", 1)[0].strip()
            vals[k.strip()] = v
    except OSError:
        pass
    for k, v in os.environ.items():
        if k.startswith("ODYSSEUS_"):
            vals[k] = v
    return vals


def _truthy(v: Optional[str], default: bool = True) -> bool:
    if v is None or v.strip() == "":
        return default
    return v.strip().lower() not in ("0", "off", "false", "no", "disabled")


def venv_python() -> Path:
    base = REPO_DIR / "venv"
    return base / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")


def python_for_tools() -> str:
    vp = venv_python()
    if vp.exists():
        return str(vp)
    alt = REPO_DIR / "venv" / "bin" / "python3"
    if alt.exists():
        return str(alt)
    return sys.executable


# ── install marker / mode ───────────────────────────────────────────────────

def load_marker() -> Dict:
    return _read_json(INSTALL_MARKER, {})


def save_marker(marker: Dict) -> None:
    _write_json(INSTALL_MARKER, marker)


def install_mode() -> str:
    mode = (load_marker().get("mode") or "").strip().lower()
    return mode if mode in ("managed", "dev") else "legacy"


def mark_install(mode: str = "managed", **extra) -> Dict:
    """Called by the installer: record how this copy was installed."""
    marker = load_marker()
    marker.update({"mode": mode, **extra})
    marker.setdefault("installed_at", time.time())
    save_marker(marker)
    return marker


# ── state ───────────────────────────────────────────────────────────────────

def load_state() -> Dict:
    st = _read_json(STATE_PATH, {})
    st.setdefault("history", [])
    st.setdefault("bad_commits", [])
    return st


def save_state(st: Dict) -> None:
    st["history"] = st.get("history", [])[-HISTORY_KEEP:]
    st["bad_commits"] = st.get("bad_commits", [])[-BAD_KEEP:]
    _write_json(STATE_PATH, st)


def _history(st: Dict, event: str, **kw) -> None:
    st.setdefault("history", []).append({"t": time.time(), "event": event, **kw})


# ── lock ────────────────────────────────────────────────────────────────────

def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if IS_WINDOWS:
        try:
            r = run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], timeout=10)
            return str(pid) in r.stdout
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


class Lock:
    """Exclusive update lock held through an OS file lock (flock / msvcrt).

    The OS drops it the moment the holder dies, so it can never go stale and
    can never be taken from a live process, however long its pip run lasts.
    The file also records the holder's pid for humans.
    """

    def __init__(self, path: Path = LOCK_PATH):
        self.path = path
        self.held = False
        self._fh = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "a+")  # noqa: SIM115 — kept open while held
        try:
            if IS_WINDOWS:
                import msvcrt
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False
        try:
            fh.seek(0)
            fh.truncate()
            fh.write(json.dumps({"pid": os.getpid(), "t": time.time()}))
            fh.flush()
        except OSError:
            pass
        self._fh = fh
        self.held = True
        return True

    def release(self) -> None:
        if not self.held or self._fh is None:
            return
        try:
            if IS_WINDOWS:
                import msvcrt
                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        self._fh.close()
        self._fh = None
        self.held = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.release()


def update_running() -> bool:
    """True while some updater holds the lock (it releases it immediately)."""
    probe = Lock()
    if probe.acquire():
        probe.release()
        return False
    return True


def clear_stale_git_locks(max_age_s: float = GIT_LOCK_STALE_S) -> List[str]:
    """Remove .git/*.lock debris left by a killed git (power loss, logout,
    a killed updater). Only call while holding the update lock: then no git
    process of ours can be using them."""
    gitdir = REPO_DIR / ".git"
    removed = []
    cands = [gitdir / n for n in ("index.lock", "HEAD.lock", "packed-refs.lock", "config.lock",
                                  "ORIG_HEAD.lock", "FETCH_HEAD.lock")]
    try:
        cands += list((gitdir / "refs").rglob("*.lock"))
    except OSError:
        pass
    now = time.time()
    for p in cands:
        try:
            if p.is_file() and now - p.stat().st_mtime > max_age_s:
                p.unlink()
                removed.append(str(p.relative_to(gitdir)))
        except OSError:
            continue
    if removed:
        log(f"removed stale git lock file(s) left by an interrupted git: {', '.join(removed)}")
    return removed


# ── git facts ───────────────────────────────────────────────────────────────

def detect_branch(settings: Dict[str, str]) -> str:
    """Which upstream branch to follow: setting > marker > origin/HEAD > main/master."""
    b = (settings.get("ODYSSEUS_UPDATE_BRANCH") or load_marker().get("channel") or "").strip()
    if b:
        return b
    head = git_out("symbolic-ref", "--quiet", "refs/remotes/origin/HEAD")
    if head.startswith("refs/remotes/origin/"):
        return head[len("refs/remotes/origin/"):]
    for cand in ("main", "master"):
        if git_out("rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{cand}"):
            return cand
    # Ask the remote directly (one round trip).
    out = git_out("ls-remote", "--symref", "origin", "HEAD", timeout=30)
    m = re.search(r"ref: refs/heads/(\S+)\s+HEAD", out)
    return m.group(1) if m else "main"


def current_branch() -> str:
    return git_out("rev-parse", "--abbrev-ref", "HEAD")


def head_sha() -> str:
    return git_out("rev-parse", "HEAD")


def is_ancestor(a: str, b: str) -> bool:
    return git("merge-base", "--is-ancestor", a, b).returncode == 0


def tracked_dirty() -> bool:
    return bool(git_out("status", "--porcelain", "--untracked-files=no"))


def commits_between(old: str, new: str, limit: int = 30) -> List[Dict[str, str]]:
    out = git_out("log", f"--max-count={limit}", "--format=%h%x09%s", f"{old}..{new}")
    items = []
    for line in out.splitlines():
        sha, _, subj = line.partition("\t")
        items.append({"sha": sha, "subject": subj})
    return items


def count_between(old: str, new: str) -> int:
    out = git_out("rev-list", "--count", f"{old}..{new}")
    return int(out) if out.isdigit() else 0


def _file_hash_at(rev: str, path: str) -> str:
    r = git("show", f"{rev}:{path}")
    return hashlib.sha256(r.stdout.encode()).hexdigest() if r.returncode == 0 else ""


def requirements_changed(old: str, new: str) -> bool:
    return any(_file_hash_at(old, f) != _file_hash_at(new, f) for f in REQUIREMENT_FILES)


def origin_slug() -> Optional[Tuple[str, str]]:
    override = (read_settings().get("ODYSSEUS_UPDATE_REPO") or "").strip()
    if override.count("/") == 1:
        owner, name = override.split("/")
        return owner, name
    url = git_out("remote", "get-url", "origin")
    m = re.search(r"github\.com[:/]+([^/]+)/([^/]+?)(?:\.git)?/?$", url)
    return (m.group(1), m.group(2)) if m else None


# ── CI gate ─────────────────────────────────────────────────────────────────

def ci_verdict(sha: str, timeout: float = 8) -> str:
    """'pass' | 'fail' | 'pending' | 'unknown' for the update-gate job on ``sha``.

    Unknown (no network, rate-limited, no CI on that commit) never blocks:
    the post-restart health check still protects the install.
    """
    slug = origin_slug()
    if not slug:
        return "unknown"
    api = (os.environ.get("ODYSSEUS_GITHUB_API") or "https://api.github.com").rstrip("/")
    url = f"{api}/repos/{slug[0]}/{slug[1]}/commits/{sha}/check-runs?per_page=100"
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "odysseus-updater",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8") or "{}")
    except (urllib.error.URLError, OSError, ValueError):
        return "unknown"
    runs = [r for r in data.get("check_runs", []) if r.get("name") == CI_GATE_JOB]
    if not runs:
        return "unknown"
    if any(r.get("conclusion") in ("failure", "timed_out", "startup_failure") for r in runs):
        return "fail"
    if any(r.get("status") != "completed" for r in runs):
        return "pending"
    if all(r.get("conclusion") in ("success", "neutral", "skipped") for r in runs):
        return "pass"
    return "unknown"


def commit_age_s(sha: str) -> float:
    ts = git_out("show", "-s", "--format=%ct", sha)
    return time.time() - int(ts) if ts.isdigit() else 0.0


# ── checks that run on the new code ─────────────────────────────────────────

def syntax_check(old: str, new: str) -> Optional[str]:
    """Byte-compile the Python files that changed. Returns an error or None."""
    changed = [p for p in git_out("diff", "--name-only", "--diff-filter=AM", old, new, "--", "*.py").splitlines() if p]
    changed = [p for p in changed if (REPO_DIR / p).is_file()]
    if not changed:
        return None
    code = (
        "import sys, py_compile\n"
        "bad = []\n"
        "for p in sys.argv[1:]:\n"
        "    try: py_compile.compile(p, doraise=True, cfile=None)\n"
        "    except py_compile.PyCompileError as e: bad.append(str(e))\n"
        "print('\\n'.join(bad)); sys.exit(1 if bad else 0)\n"
    )
    # Compile in batches so a huge diff doesn't hit argv limits.
    for i in range(0, len(changed), 200):
        r = run([python_for_tools(), "-c", code, *changed[i:i + 200]],
                timeout=float(os.environ.get("ODYSSEUS_SYNTAX_TIMEOUT") or 300), cwd=str(REPO_DIR))
        if r.returncode != 0:
            return (r.stdout or r.stderr).strip()[-1500:] or "syntax check failed"
    return None


def pip_install(timeout: float = 45 * 60) -> Optional[str]:
    py = python_for_tools()
    if not venv_python().exists():
        return "venv not found — run the installer to recreate it"
    for req in REQUIREMENT_FILES:
        if not (REPO_DIR / req).exists():
            continue
        log(f"installing dependencies from {req} …")
        try:
            r = run([py, "-m", "pip", "install", "--disable-pip-version-check",
                     "-q", "-r", str(REPO_DIR / req)], timeout=timeout, cwd=str(REPO_DIR))
        except subprocess.TimeoutExpired:
            return f"pip install -r {req} timed out"
        if r.returncode != 0:
            return (r.stderr or r.stdout).strip()[-1500:] or "pip install failed"
    return None


def run_setup_pass() -> None:
    if not (REPO_DIR / "setup.py").exists() or not venv_python().exists():
        return
    env = dict(os.environ, ODYSSEUS_SKIP_RUN_HINT="1")
    try:
        r = run([python_for_tools(), str(REPO_DIR / "setup.py")], timeout=600, cwd=str(REPO_DIR), env=env)
        log("setup.py pass ok" if r.returncode == 0 else
            f"WARN: setup.py pass failed: {(r.stderr or r.stdout).strip()[-300:]}")
    except subprocess.TimeoutExpired:
        log("WARN: setup.py pass timed out")


# ── supervisor hand-off ─────────────────────────────────────────────────────

SUPERVISOR_PID = DATA_DIR / "run" / "supervisor.pid"


def supervisor_alive() -> bool:
    info = _read_json(SUPERVISOR_PID, {})
    try:
        return _pid_alive(int(info.get("pid") or 0))
    except (TypeError, ValueError):
        return False


def request_restart(reason: str, sha: str, force: bool = False, apply_before_start: bool = False) -> None:
    _write_json(RESTART_FLAG, {"reason": reason, "sha": sha, "t": time.time(), "force": bool(force),
                               "apply_before_start": bool(apply_before_start)})


# ── schedule migration ──────────────────────────────────────────────────────

def ensure_schedule_current() -> None:
    """Re-register the OS schedule when this version registers it differently.

    Existing installs registered a daily 03:30 job; the first run of this file
    moves them to the current schedule (hourly) without reinstalling.
    """
    if os.environ.get("ODYSSEUS_SKIP_SCHEDULE"):
        return
    marker = load_marker()
    if marker.get("mode") != "managed" or marker.get("schedule") == "none":
        return
    if int(marker.get("schedule_version") or 0) >= SCHEDULE_VERSION:
        return
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import schedulers  # noqa: WPS433 — local module
        ok, detail = schedulers.register_daily_update(REPO_DIR)
        log(f"schedule {'updated' if ok else 'NOT updated'}: {detail}")
        # Inside the launchd job itself only the file was rewritten; let a run
        # from the app (supervisor) do the reload, then record the version.
        in_own_job = os.environ.get("XPC_SERVICE_NAME") == getattr(schedulers, "UPDATE_LABEL", "")
        if ok and not in_own_job:
            marker["schedule_version"] = SCHEDULE_VERSION
            save_marker(marker)
    except Exception as exc:  # noqa: BLE001
        log(f"WARN: could not refresh the schedule: {exc}")


# ── the update itself ───────────────────────────────────────────────────────

def _move_to(branch: str, target: str, mode: str) -> None:
    """Point the checkout at ``target`` on ``branch``."""
    if mode == "managed":
        git("checkout", "-q", "-B", branch, target, check=True, timeout=300)
        git("branch", "-q", f"--set-upstream-to=origin/{branch}", branch)
    else:  # legacy: fast-forward only, on the branch itself
        if current_branch() != branch:
            raise UpdateError(f"checked out on '{current_branch()}', not '{branch}' — not touching it")
        git("merge", "-q", "--ff-only", target, check=True, timeout=300)


def _stash_top() -> str:
    return git_out("rev-parse", "-q", "--verify", "refs/stash")


def _stash_push(message: str) -> Optional[str]:
    """Stash tracked edits. Returns the stash commit sha, or None if nothing was saved."""
    before = _stash_top()
    r = git("stash", "push", "-q", "-m", message)
    after = _stash_top()
    if r.returncode != 0 or not after or after == before:
        return None
    return after


def _restore_stash(stash_sha: Optional[str], st: Dict) -> None:
    """Pop exactly the stash we pushed (by sha), never whatever is on top."""
    if not stash_sha:
        return
    shas = git_out("stash", "list", "--format=%H").splitlines()
    if stash_sha not in shas:
        log(f"WARN: our stash {stash_sha[:9]} is gone — nothing to restore")
        return
    ref = f"stash@{{{shas.index(stash_sha)}}}"
    r = git("stash", "pop", "-q", ref)
    if r.returncode == 0:
        log("restored local edits")
        return
    # Conflict with the new code. The tree was clean before the pop (we
    # stashed everything), so resetting only drops the half-applied pop; the
    # edits stay in the stash because a conflicting pop doesn't drop it.
    git("reset", "-q", "--hard", "HEAD")
    msg = f"local edits conflict with the update; kept in `git stash list` ({stash_sha[:9]})"
    log("WARN: " + msg)
    st["notice"] = msg


SERVER_PID_PATH = DATA_DIR / "run" / "server.pid"


def server_running() -> bool:
    info = _read_json(SERVER_PID_PATH, {})
    try:
        return _pid_alive(int(info.get("pid") or 0))
    except (TypeError, ValueError):
        return False


def rollback(reason: str, mark_bad: bool = True, restart: bool = True) -> int:
    """Return to the previous version recorded in the state."""
    st = load_state()
    prev = st.get("previous") or st.get("last_good")
    cur = head_sha()
    if not prev or prev == cur:
        log(f"rollback requested ({reason}) but there is no earlier version recorded")
        return 1
    branch = st.get("branch") or detect_branch(read_settings())
    log(f"ROLLBACK {cur[:9]} → {prev[:9]}: {reason}")
    clear_stale_git_locks()
    stash = None
    if tracked_dirty():
        stash = _stash_push(f"odysseus-rollback {time.strftime('%Y%m%d%H%M%S')}")
        if not stash:
            log("ERROR: rollback needs to save local edits first, and `git stash` failed")
            return 1
    try:
        git("checkout", "-q", "-B", branch, prev, check=True, timeout=300)
    except UpdateError as exc:
        log(f"ERROR: rollback failed: {exc}")
        return 1
    if requirements_changed(prev, cur) and venv_python().exists():
        err = pip_install()
        if err:
            log(f"WARN: dependency reinstall after rollback failed: {err[:300]}")
    _restore_stash(stash, st)
    if mark_bad and cur not in st["bad_commits"]:
        st["bad_commits"].append(cur)
    st.update({"current": prev, "previous": None, "rolled_back_from": cur,
               "rollback_reason": reason, "pending_restart": True, "updated_at": time.time(),
               "last_result": "rolled_back"})
    _history(st, "rollback", sha=prev, from_sha=cur, reason=reason)
    save_state(st)
    if restart:
        request_restart("rollback", prev, force=True)
    return 0


def mark_good(sha: Optional[str] = None) -> None:
    """The supervisor calls this once the new version answered its health check."""
    st = load_state()
    sha = sha or head_sha()
    st.update({"last_good": sha, "running": sha, "healthy_at": time.time()})
    if sha == head_sha():
        st["pending_restart"] = False
    _history(st, "healthy", sha=sha)
    save_state(st)


def run_update(check_only: bool = False, force: bool = False, scheduled: bool = False,
               on_launch: bool = False) -> int:
    settings = read_settings()
    if not _truthy(settings.get("ODYSSEUS_AUTO_UPDATE"), True) and not force and not check_only:
        log("auto-update is off (ODYSSEUS_AUTO_UPDATE=off) — checking only")
        check_only = True

    if not (REPO_DIR / ".git").exists():
        log("ERROR: not a git checkout — reinstall with the Odysseus installer")
        return 1

    mode = install_mode()
    if mode == "legacy" and scheduled and not check_only:
        # Only the installer registers the OS schedule, so this is an installed copy.
        mark_install("managed", upgraded_from="legacy")
        mode = "managed"
        log("install marked as managed (scheduled run on a pre-marker install)")
    if mode == "dev" and not force and not check_only:
        check_only = True

    lock = Lock()
    if not lock.acquire():
        log("another update is running — skipping")
        return 2

    st = load_state()
    try:
        clear_stale_git_locks()
        branch = detect_branch(settings)
        st["branch"] = branch
        st["mode"] = mode
        st["last_check"] = time.time()
        fetch_timeout = 20 if on_launch else 120
        r = git("fetch", "--prune", "--quiet", "origin",
                f"+refs/heads/{branch}:refs/remotes/origin/{branch}", timeout=fetch_timeout)
        if r.returncode != 0:
            st["last_result"] = "offline"
            st["error"] = (r.stderr or "").strip()[-300:]
            log(f"fetch failed (offline?): {st['error']}")
            return 1
        remote = git_out("rev-parse", f"refs/remotes/origin/{branch}")
        head = head_sha()
        st.update({"remote": remote, "current": head,
                   "behind": count_between(head, remote) if remote else 0,
                   "ahead": count_between(remote, head) if remote else 0,
                   "error": None})
        if not remote:
            st["last_result"] = "error"
            st["error"] = f"origin has no branch '{branch}'"
            log("ERROR: " + st["error"])
            return 1

        if head == remote and (mode != "managed" or current_branch() == branch):
            st["last_result"] = "up_to_date"
            st["available"] = None
            log(f"up to date at {head[:9]} ({branch})")
            if mode == "managed" and not check_only:
                ensure_schedule_current()
            return 0

        st["available"] = {"sha": remote, "count": st["behind"],
                           "commits": commits_between(head, remote, 15)}

        if remote in st["bad_commits"] and not force:
            st["last_result"] = "skipped_bad"
            log(f"tip {remote[:9]} failed here before — waiting for a newer commit")
            return 2

        if check_only:
            if st["behind"]:
                st["last_result"] = "available"
            elif is_ancestor(remote, head):
                st["last_result"] = "ahead"      # local commits not pushed yet; nothing to install
                st["available"] = None
            else:
                st["last_result"] = "diverged"
            log(f"{st['behind']} new commit(s) on {branch} (check only, mode={mode})")
            return 0

        if _truthy(settings.get("ODYSSEUS_UPDATE_CI_GATE"), True) and not force:
            verdict = ci_verdict(remote, timeout=5 if on_launch else 8)
            st["ci"] = verdict
            if verdict == "fail":
                st["last_result"] = "skipped_ci_failed"
                log(f"CI failed for {remote[:9]} — not updating to it")
                return 2
            if verdict == "pending" and commit_age_s(remote) < CI_PENDING_GRACE_S:
                st["last_result"] = "waiting_ci"
                log(f"CI still running for {remote[:9]} — will retry")
                return 2

        # Windows can't replace .pyd/.dll files the running server has loaded,
        # so a dependency change is installed with the server stopped: the
        # supervisor stops it, runs `--on-launch`, then starts it again.
        if (IS_WINDOWS and not on_launch and server_running() and requirements_changed(head, remote)
                and venv_python().exists()):
            st["last_result"] = "deferred_to_restart"
            if supervisor_alive():
                request_restart("update (dependencies)", remote, apply_before_start=True)
                log("dependencies change — the supervisor will install it with the server stopped")
            else:
                log("dependencies change — will be installed the next time Odysseus starts")
            return 0

        diverged = not is_ancestor(head, remote)
        if diverged and mode != "managed":
            st["last_result"] = "diverged"
            log("history diverged from origin — legacy install, not touching it "
                "(re-run the installer to enable full auto-update)")
            return 1

        old = head
        stash_ref = None
        if tracked_dirty():
            stash_ref = _stash_push(f"odysseus-auto-update {time.strftime('%Y%m%d%H%M%S')}")
            if not stash_ref:
                st["last_result"] = "error"
                st["error"] = "could not save local edits with git stash — not updating"
                log("ERROR: " + st["error"])
                return 1
            log("saved local edits to the stash")
        # Keep every local commit reachable before the checkout moves: HEAD and
        # the local copy of the branch (which `checkout -B` resets).
        stamp = time.strftime('%Y%m%d-%H%M%S')
        local_branch = git_out("rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")
        for label, sha in (("", old), ("-" + branch, local_branch)):
            if sha and not is_ancestor(sha, remote):
                backup = f"odysseus/backup-{stamp}{label}"
                if git("branch", "-q", backup, sha).returncode == 0:
                    st["backup_branch"] = backup
                    log(f"local commits not on origin kept on branch {backup}")

        try:
            _move_to(branch, remote, mode)
        except UpdateError as exc:
            _restore_stash(stash_ref, st)
            st["last_result"] = "error"
            st["error"] = str(exc)
            log(f"ERROR: {exc}")
            return 1
        log(f"code moved {old[:9]} → {remote[:9]} ({count_between(old, remote)} commit(s))")

        def _undo(why: str) -> int:
            try:
                clear_stale_git_locks(max_age_s=0)
                git("checkout", "-q", "-B", branch, old, timeout=300)
                if requirements_changed(old, remote) and venv_python().exists():
                    pip_install()
            finally:
                _restore_stash(stash_ref, st)
            st["last_result"] = "failed"
            st["error"] = why[-600:]
            st["current"] = old
            _history(st, "failed", sha=remote, reason=why[:300])
            log(f"update to {remote[:9]} undone: {why[:300]}")
            return 1

        # From here on the checkout is on the new code: anything that goes
        # wrong (a timeout, an OSError, Ctrl+C) must put the old version and
        # the user's edits back, or the next run would see "up to date" with
        # half-installed dependencies.
        try:
            err = syntax_check(old, remote)
            if err:
                st["bad_commits"].append(remote)
                return _undo(f"syntax check failed: {err}")

            if requirements_changed(old, remote):
                if venv_python().exists():
                    err = pip_install()
                    if err:
                        # Often a network hiccup: undo, but don't blacklist the commit.
                        return _undo(f"dependency install failed: {err}")
                else:
                    log("requirements changed but there is no venv yet — the launcher installs them on start")
            run_setup_pass()
        except BaseException as exc:  # noqa: BLE001 — includes timeouts and interrupts
            _undo(f"interrupted after moving the code: {type(exc).__name__}: {exc}")
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return 1
        _restore_stash(stash_ref, st)

        st.update({"previous": old, "current": remote, "updated_at": time.time(),
                   "pending_restart": True, "last_result": "updated", "available": None,
                   "behind": 0, "changes": commits_between(old, remote, 30)})
        _history(st, "updated", sha=remote, from_sha=old, count=count_between(old, remote))
        request_restart("update", remote)
        log(f"updated to {remote[:9]}; restart requested"
            + ("" if supervisor_alive() else " (no supervisor running — takes effect on next launch)"))
        ensure_schedule_current()
        return 0
    except subprocess.TimeoutExpired as exc:
        st["last_result"] = "error"
        st["error"] = f"timed out: {exc.cmd}"
        log("ERROR: " + st["error"])
        return 1
    except UpdateError as exc:
        st["last_result"] = "error"
        st["error"] = str(exc)
        log(f"ERROR: {exc}")
        return 1
    finally:
        save_state(st)
        lock.release()


def status() -> Dict:
    st = load_state()
    st["mode"] = install_mode()
    st["head"] = head_sha()
    st["branch_checked_out"] = current_branch()
    st["supervisor"] = supervisor_alive()
    st["restart_requested"] = RESTART_FLAG.exists()
    st["enabled"] = _truthy(read_settings().get("ODYSSEUS_AUTO_UPDATE"), True)
    return st


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Odysseus autonomous updater")
    p.add_argument("--check", action="store_true", help="check only, change nothing")
    p.add_argument("--status", action="store_true", help="print saved state as JSON")
    p.add_argument("--rollback", action="store_true", help="go back to the previous version")
    p.add_argument("--mark-good", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--on-launch", action="store_true", help="quick check before the server starts")
    p.add_argument("--scheduled", action="store_true", help="started by the OS scheduler")
    p.add_argument("--force", action="store_true", help="apply even on a dev clone / bad commit")
    a = p.parse_args(argv)

    if a.status:
        print(json.dumps(status(), indent=2, sort_keys=True))
        return 0
    if a.mark_good:
        mark_good()
        return 0
    if a.rollback:
        lock = Lock()
        if not lock.acquire():
            log("another update is running — try again in a minute")
            return 2
        try:
            return rollback("requested by the user", mark_bad=True)
        finally:
            lock.release()
    log(f"== update run ({'scheduled' if a.scheduled else 'launch' if a.on_launch else 'manual'}) ==")
    return run_update(check_only=a.check, force=a.force, scheduled=a.scheduled, on_launch=a.on_launch)


if __name__ == "__main__":
    raise SystemExit(main())
