"""End-to-end tests for installer/updater.py against a real local "origin".

Each test builds a bare repo (standing in for GitHub), clones it like the
installer does, then runs the real updater script as a subprocess.
"""

from __future__ import annotations

import http.server
import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
UPDATER_SRC = ROOT / "installer" / "updater.py"

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def sh(*args, cwd=None, check=True, env=None):
    r = subprocess.run(list(args), cwd=cwd, capture_output=True, text=True, env=env)
    if check and r.returncode != 0:
        raise AssertionError(f"{args} failed: {r.stderr}")
    return r


def g(repo, *args, check=True):
    return sh("git", "-C", str(repo), *args, check=check)


class Rig:
    """origin (bare) + work (a publisher clone) + inst (the installed copy)."""

    def __init__(self, tmp: Path, branch: str = "main"):
        self.tmp = tmp
        self.branch = branch
        self.origin = tmp / "origin.git"
        self.work = tmp / "work"
        self.inst = tmp / "inst"
        sh("git", "init", "-q", "--bare", f"--initial-branch={branch}", str(self.origin))
        sh("git", "init", "-q", f"--initial-branch={branch}", str(self.work))
        for repo in (self.work,):
            g(repo, "config", "user.email", "t@example.com")
            g(repo, "config", "user.name", "T")
        (self.work / "installer").mkdir()
        shutil.copy(UPDATER_SRC, self.work / "installer" / "updater.py")
        (self.work / "requirements.txt").write_text("fastapi\n")
        (self.work / "mod.py").write_text("X = 1\n")
        (self.work / "notes.txt").write_text("line1\nline2\nline3\n")
        (self.work / ".gitignore").write_text("data/\nlogs/\nvenv/\n")
        self.commit("initial")
        g(self.work, "remote", "add", "origin", str(self.origin))
        g(self.work, "push", "-q", "origin", branch)
        sh("git", "clone", "-q", str(self.origin), str(self.inst))
        g(self.inst, "config", "user.email", "u@example.com")
        g(self.inst, "config", "user.name", "U")

    def commit(self, msg, files=None):
        for name, content in (files or {}).items():
            p = self.work / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
        g(self.work, "add", "-A")
        g(self.work, "commit", "-q", "-m", msg)
        return g(self.work, "rev-parse", "HEAD").stdout.strip()

    def publish(self, msg, files=None, force=False):
        sha = self.commit(msg, files)
        g(self.work, "push", "-q", *(["-f"] if force else []), "origin", self.branch)
        return sha

    def mark(self, mode):
        (self.inst / "data").mkdir(exist_ok=True)
        (self.inst / "data" / "install.json").write_text(json.dumps({"mode": mode}))

    def run(self, *args, env_extra=None):
        env = dict(os.environ, ODYSSEUS_UPDATE_QUIET="1", ODYSSEUS_UPDATE_CI_GATE="off",
                   ODYSSEUS_SKIP_SCHEDULE="1", GIT_CONFIG_NOSYSTEM="1")
        for k in ("ODYSSEUS_AUTO_UPDATE", "ODYSSEUS_UPDATE_BRANCH"):
            env.pop(k, None)
        env.update(env_extra or {})
        return subprocess.run([sys.executable, str(self.inst / "installer" / "updater.py"), *args],
                              cwd=str(self.inst), capture_output=True, text=True, env=env, timeout=120)

    def head(self):
        return g(self.inst, "rev-parse", "HEAD").stdout.strip()

    def state(self):
        return json.loads((self.inst / "data" / "update" / "state.json").read_text())

    def restart_flag(self):
        p = self.inst / "data" / "update" / "restart-requested.json"
        return json.loads(p.read_text()) if p.exists() else None

    def fake_venv(self, pip_rc=0):
        """venv/bin/python that forwards to the real interpreter but fakes pip."""
        vbin = self.inst / "venv" / ("Scripts" if os.name == "nt" else "bin")
        vbin.mkdir(parents=True, exist_ok=True)
        log = self.inst / "pip-calls.log"
        script = vbin / "python"
        script.write_text(
            "#!/bin/bash\n"
            f'if [ "$1" = "-m" ] && [ "$2" = "pip" ]; then echo "$@" >> "{log}"; exit {pip_rc}; fi\n'
            f'exec "{sys.executable}" "$@"\n')
        script.chmod(0o755)
        return log


@pytest.fixture
def rig(tmp_path):
    if os.name == "nt":
        pytest.skip("POSIX test rig")
    return Rig(tmp_path)


def test_up_to_date(rig):
    rig.mark("managed")
    r = rig.run()
    assert r.returncode == 0, r.stderr
    assert rig.state()["last_result"] == "up_to_date"
    assert rig.restart_flag() is None


def test_managed_update_moves_to_tip_and_requests_restart(rig):
    rig.mark("managed")
    old = rig.head()
    new = rig.publish("feat: two", {"mod.py": "X = 2\n"})
    r = rig.run()
    assert r.returncode == 0, r.stdout + r.stderr
    assert rig.head() == new
    st = rig.state()
    assert st["last_result"] == "updated"
    assert st["previous"] == old and st["current"] == new
    assert st["pending_restart"] is True
    assert [c["subject"] for c in st["changes"]] == ["feat: two"]
    assert rig.restart_flag()["sha"] == new
    assert (rig.inst / "logs" / "self-update.log").exists()


def test_local_edits_survive_update(rig):
    rig.mark("managed")
    (rig.inst / "notes.txt").write_text("line1\nline2\nline3\nmy local line\n")
    new = rig.publish("touch mod", {"mod.py": "X = 3\n"})
    assert rig.run().returncode == 0
    assert rig.head() == new
    assert "my local line" in (rig.inst / "notes.txt").read_text()


def test_conflicting_local_edits_go_to_stash(rig):
    rig.mark("managed")
    (rig.inst / "mod.py").write_text("X = 'mine'\n")
    new = rig.publish("upstream mod", {"mod.py": "X = 'theirs'\n"})
    assert rig.run().returncode == 0
    assert rig.head() == new
    assert (rig.inst / "mod.py").read_text() == "X = 'theirs'\n"
    assert "stash" in rig.state().get("notice", "")
    assert g(rig.inst, "stash", "list").stdout.strip()
    assert not g(rig.inst, "status", "--porcelain", "--untracked-files=no").stdout.strip()


def test_force_pushed_origin_keeps_backup_branch(rig):
    rig.mark("managed")
    rig.publish("a", {"mod.py": "X = 10\n"})
    assert rig.run().returncode == 0
    local_commit = rig.head()
    # Upstream rewrites history.
    g(rig.work, "reset", "-q", "--hard", "HEAD~1")
    new = rig.publish("rewritten", {"mod.py": "X = 11\n"}, force=True)
    assert rig.run().returncode == 0
    assert rig.head() == new
    backups = g(rig.inst, "branch", "--list", "odysseus/backup-*").stdout
    assert backups.strip()
    assert g(rig.inst, "merge-base", "--is-ancestor", local_commit, backups.split()[-1], check=False).returncode == 0


def test_local_commit_is_backed_up(rig):
    rig.mark("managed")
    (rig.inst / "mine.txt").write_text("x")
    g(rig.inst, "add", "mine.txt")
    g(rig.inst, "commit", "-q", "-m", "local work")
    mine = rig.head()
    new = rig.publish("upstream", {"mod.py": "X = 5\n"})
    assert rig.run().returncode == 0
    assert rig.head() == new
    branches = g(rig.inst, "branch", "--contains", mine).stdout
    assert "odysseus/backup-" in branches


def test_legacy_install_fast_forwards_but_never_rewrites(rig):
    # No marker: legacy. Fast-forward works…
    new = rig.publish("ff", {"mod.py": "X = 6\n"})
    assert rig.run().returncode == 0
    assert rig.head() == new
    # …but diverged history is left alone.
    g(rig.work, "reset", "-q", "--hard", "HEAD~1")
    rig.publish("rewrite", {"mod.py": "X = 7\n"}, force=True)
    r = rig.run()
    assert r.returncode == 1
    assert rig.head() == new
    assert rig.state()["last_result"] == "diverged"


def test_scheduled_run_upgrades_legacy_to_managed(rig):
    rig.publish("x", {"mod.py": "X = 8\n"})
    assert rig.run("--scheduled").returncode == 0
    assert json.loads((rig.inst / "data" / "install.json").read_text())["mode"] == "managed"


def test_dev_clone_only_checks_unless_forced(rig):
    rig.mark("dev")
    old = rig.head()
    new = rig.publish("dev change", {"mod.py": "X = 9\n"})
    assert rig.run().returncode == 0
    assert rig.head() == old
    st = rig.state()
    assert st["last_result"] == "available" and st["available"]["sha"] == new
    assert rig.restart_flag() is None
    assert rig.run("--force").returncode == 0
    assert rig.head() == new


def test_follow_mode_fast_forwards_and_requests_restart(rig):
    rig.mark("follow")
    new = rig.publish("follow change", {"mod.py": "X = 30\n"})
    assert rig.run().returncode == 0
    assert rig.head() == new
    assert rig.state()["last_result"] == "updated"
    assert rig.restart_flag()["sha"] == new


def test_follow_mode_updates_around_uncommitted_edits_without_stashing(rig):
    rig.mark("follow")
    (rig.inst / "notes.txt").write_text("my draft\n")
    new = rig.publish("touches mod only", {"mod.py": "X = 31\n"})
    assert rig.run().returncode == 0
    assert rig.head() == new
    assert (rig.inst / "notes.txt").read_text() == "my draft\n"
    assert g(rig.inst, "stash", "list").stdout.strip() == ""


def test_follow_mode_waits_when_update_touches_edited_file(rig):
    rig.mark("follow")
    old = rig.head()
    (rig.inst / "mod.py").write_text("X = 'mine'\n")
    rig.publish("touches mod", {"mod.py": "X = 32\n"})
    r = rig.run()
    assert r.returncode == 2
    assert rig.head() == old
    assert (rig.inst / "mod.py").read_text() == "X = 'mine'\n"
    st = rig.state()
    assert st["last_result"] == "blocked_local_edits" and "mod.py" in st["error"]
    assert g(rig.inst, "stash", "list").stdout.strip() == ""


def test_follow_mode_leaves_unpushed_and_diverged_work_alone(rig):
    rig.mark("follow")
    (rig.inst / "mine.txt").write_text("x")
    g(rig.inst, "add", "mine.txt")
    g(rig.inst, "commit", "-q", "-m", "unpushed")
    mine = rig.head()
    assert rig.run().returncode == 0
    assert rig.head() == mine and rig.state()["last_result"] == "ahead"
    rig.publish("upstream moves too", {"mod.py": "X = 33\n"})
    assert rig.run().returncode == 1
    assert rig.head() == mine and rig.state()["last_result"] == "diverged"
    assert "odysseus/backup-" not in g(rig.inst, "branch").stdout


def test_auto_update_off_only_checks(rig):
    rig.mark("managed")
    old = rig.head()
    rig.publish("y", {"mod.py": "X = 12\n"})
    assert rig.run(env_extra={"ODYSSEUS_AUTO_UPDATE": "off"}).returncode == 0
    assert rig.head() == old
    assert rig.state()["last_result"] == "available"


def test_syntax_error_is_undone_and_remembered(rig):
    rig.mark("managed")
    rig.fake_venv()
    old = rig.head()
    bad = rig.publish("broken", {"mod.py": "def oops(:\n"})
    r = rig.run()
    assert r.returncode == 1
    assert rig.head() == old
    st = rig.state()
    assert st["last_result"] == "failed" and bad in st["bad_commits"]
    assert rig.restart_flag() is None
    # The same broken tip is skipped next time…
    assert rig.run().returncode == 2
    assert rig.state()["last_result"] == "skipped_bad"
    # …and a fix on top is taken.
    good = rig.publish("fix", {"mod.py": "X = 13\n"})
    assert rig.run().returncode == 0
    assert rig.head() == good


def test_requirements_change_installs_dependencies(rig):
    rig.mark("managed")
    log = rig.fake_venv(pip_rc=0)
    new = rig.publish("deps", {"requirements.txt": "fastapi\nhttpx\n"})
    assert rig.run().returncode == 0
    assert rig.head() == new
    assert "install" in log.read_text() and "requirements.txt" in log.read_text()


def test_failed_dependency_install_rolls_code_back_without_blacklisting(rig):
    rig.mark("managed")
    rig.fake_venv(pip_rc=1)
    old = rig.head()
    new = rig.publish("deps2", {"requirements.txt": "fastapi\nnot-a-real-pkg\n"})
    assert rig.run().returncode == 1
    assert rig.head() == old
    st = rig.state()
    assert st["last_result"] == "failed"
    assert new not in st["bad_commits"]


def test_rollback_returns_to_previous_and_marks_bad(rig):
    rig.mark("managed")
    old = rig.head()
    new = rig.publish("z", {"mod.py": "X = 14\n"})
    assert rig.run().returncode == 0 and rig.head() == new
    r = rig.run("--rollback")
    assert r.returncode == 0, r.stdout + r.stderr
    assert rig.head() == old
    st = rig.state()
    assert new in st["bad_commits"] and st["rolled_back_from"] == new
    assert rig.restart_flag()["reason"] == "rollback"
    # And the bad tip isn't re-applied on the next run.
    assert rig.run().returncode == 2
    assert rig.head() == old


def test_live_lock_blocks_second_run(rig):
    rig.mark("managed")
    holder = subprocess.Popen(
        [sys.executable, "-c",
         "import sys, time; sys.path.insert(0, sys.argv[1]); import updater; "
         "l = updater.Lock(); assert l.acquire(); print('held', flush=True); time.sleep(60)",
         str(rig.inst / "installer")],
        stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        assert rig.run().returncode == 2          # a live holder is never overridden
    finally:
        holder.kill()
        holder.wait()
    assert rig.run().returncode == 0              # the OS released it when the holder died


def test_stale_git_lock_is_cleared_but_a_fresh_one_is_respected(rig):
    rig.mark("managed")
    new = rig.publish("after crash", {"mod.py": "X = 30\n"})
    lock = rig.inst / ".git" / "index.lock"
    lock.write_text("")
    r = rig.run()                                  # fresh: maybe a real git is running
    assert r.returncode == 1 and rig.head() != new
    old = __import__("time").time() - 3600
    os.utime(lock, (old, old))
    assert rig.run().returncode == 0               # an hour old: debris, removed
    assert rig.head() == new and not lock.exists()


def test_timeout_after_checkout_restores_old_code_and_edits(rig):
    rig.mark("managed")
    vbin = rig.inst / "venv" / "bin"
    vbin.mkdir(parents=True)
    (vbin / "python").write_text(
        "#!/bin/bash\n"
        'if [ "$1" = "-c" ] && [[ "$2" == *py_compile* ]]; then sleep 30; fi\n'
        f'exec "{sys.executable}" "$@"\n')
    (vbin / "python").chmod(0o755)
    (rig.inst / "notes.txt").write_text("line1\nline2\nline3\nmine\n")
    old = rig.head()
    rig.publish("slow", {"mod.py": "X = 31\n", "requirements.txt": "fastapi\nhttpx\n"})
    r = rig.run(env_extra={"ODYSSEUS_SYNTAX_TIMEOUT": "2"})
    assert r.returncode == 1
    assert rig.head() == old                                   # code put back
    assert "mine" in (rig.inst / "notes.txt").read_text()      # edits put back
    assert not g(rig.inst, "stash", "list").stdout.strip()
    assert rig.state()["last_result"] == "failed"
    assert not list((rig.inst / ".git").glob("*.lock"))


def test_users_own_stash_is_never_popped(rig):
    rig.mark("managed")
    (rig.inst / "notes.txt").write_text("line1\nline2\nline3\nusers stash\n")
    g(rig.inst, "stash", "push", "-q", "-m", "my own work")
    # Update with no local edits: the user's stash must stay untouched.
    rig.publish("u1", {"mod.py": "X = 32\n"})
    assert rig.run().returncode == 0
    assert "my own work" in g(rig.inst, "stash", "list").stdout
    # Update with local edits: only our stash entry is popped.
    (rig.inst / "notes.txt").write_text("line1\nline2\nline3\ncurrent edit\n")
    rig.publish("u2", {"mod.py": "X = 33\n"})
    assert rig.run().returncode == 0
    assert "current edit" in (rig.inst / "notes.txt").read_text()
    stashes = g(rig.inst, "stash", "list").stdout
    assert "my own work" in stashes and "odysseus-auto-update" not in stashes


def test_status_json(rig):
    rig.mark("managed")
    rig.run()
    r = rig.run("--status")
    st = json.loads(r.stdout)
    assert st["mode"] == "managed" and st["head"] == rig.head() and st["enabled"] is True


def test_master_branch_is_detected(tmp_path):
    if os.name == "nt":
        pytest.skip("POSIX test rig")
    rig = Rig(tmp_path, branch="master")
    rig.mark("managed")
    new = rig.publish("on master", {"mod.py": "X = 15\n"})
    assert rig.run().returncode == 0
    assert rig.head() == new
    assert rig.state()["branch"] == "master"


# ── CI gate against a fake GitHub API ───────────────────────────────────────

class _FakeAPI(http.server.BaseHTTPRequestHandler):
    verdicts: dict = {}

    def do_GET(self):  # noqa: N802
        sha = self.path.split("/commits/")[1].split("/")[0]
        status, conclusion = self.verdicts.get(sha, ("none", None))
        runs = [] if status == "none" else [{"name": "update-gate", "status": status, "conclusion": conclusion}]
        body = json.dumps({"check_runs": runs}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def fake_api():
    srv = http.server.HTTPServer(("127.0.0.1", 0), _FakeAPI)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_port}", _FakeAPI.verdicts
    srv.shutdown()
    _FakeAPI.verdicts.clear()


def test_ci_gate_blocks_failed_tip_and_waits_for_pending(rig, fake_api):
    url, verdicts = fake_api
    rig.mark("managed")
    env = {"ODYSSEUS_UPDATE_CI_GATE": "on", "ODYSSEUS_GITHUB_API": url, "ODYSSEUS_UPDATE_REPO": "o/r"}
    old = rig.head()
    bad = rig.publish("ci red", {"mod.py": "X = 20\n"})
    verdicts[bad] = ("completed", "failure")
    assert rig.run(env_extra=env).returncode == 2
    assert rig.head() == old and rig.state()["last_result"] == "skipped_ci_failed"

    pending = rig.publish("ci running", {"mod.py": "X = 21\n"})
    verdicts[pending] = ("in_progress", None)
    assert rig.run(env_extra=env).returncode == 2
    assert rig.head() == old and rig.state()["last_result"] == "waiting_ci"

    verdicts[pending] = ("completed", "success")
    assert rig.run(env_extra=env).returncode == 0
    assert rig.head() == pending


def test_ci_gate_unknown_does_not_block(rig, fake_api):
    url, _ = fake_api
    rig.mark("managed")
    new = rig.publish("no ci", {"mod.py": "X = 22\n"})
    env = {"ODYSSEUS_UPDATE_CI_GATE": "on", "ODYSSEUS_GITHUB_API": url, "ODYSSEUS_UPDATE_REPO": "o/r"}
    assert rig.run(env_extra=env).returncode == 0
    assert rig.head() == new
