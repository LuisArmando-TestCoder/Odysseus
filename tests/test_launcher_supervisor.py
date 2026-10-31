"""End-to-end test of installer/launch.py (the supervisor) with a real server.

A throwaway install runs a tiny FastAPI app under the supervisor. Updates are
published to a local "origin" and applied by the real updater; the test then
checks that the supervisor restarts into them, waits while the server is busy,
rolls a broken version back on its own, revives a crashed server and stops.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

pytest.importorskip("fastapi")
pytest.importorskip("uvicorn")
pytestmark = [
    pytest.mark.skipif(os.name == "nt", reason="POSIX test rig"),
    pytest.mark.skipif(shutil.which("git") is None, reason="git not installed"),
]

APP_TMPL = '''import os
from fastapi import FastAPI
{extra}
app = FastAPI()

@app.get("/api/health")
def health():
    return {{"status": "healthy", "version": "{version}"}}

@app.get("/api/update/idle")
def idle():
    return {{"idle": not os.path.exists("BUSY")}}
'''


def app_src(version, broken=False):
    return APP_TMPL.format(version=version, extra='raise RuntimeError("broken build")' if broken else "")


def g(repo, *args):
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def get_version(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2) as r:
            return json.loads(r.read())["version"]
    except Exception:
        return None


def wait_for(pred, timeout=40, step=0.3):
    end = time.time() + timeout
    while time.time() < end:
        v = pred()
        if v:
            return v
        time.sleep(step)
    return None


@pytest.fixture
def install(tmp_path):
    origin, work, inst = tmp_path / "origin.git", tmp_path / "work", tmp_path / "inst"
    subprocess.run(["git", "init", "-q", "--bare", "--initial-branch=main", str(origin)], check=True)
    subprocess.run(["git", "init", "-q", "--initial-branch=main", str(work)], check=True)
    g(work, "config", "user.email", "t@example.com")
    g(work, "config", "user.name", "T")
    (work / "installer").mkdir()
    for f in ("updater.py", "launch.py"):
        shutil.copy(ROOT / "installer" / f, work / "installer" / f)
    (work / "requirements.txt").write_text("fastapi\nuvicorn\n")
    (work / ".gitignore").write_text("data/\nlogs/\nvenv/\nBUSY\n__pycache__/\n")
    (work / "app.py").write_text(app_src("v1"))
    g(work, "add", "-A")
    g(work, "commit", "-q", "-m", "v1")
    g(work, "remote", "add", "origin", str(origin))
    g(work, "push", "-q", "origin", "main")
    subprocess.run(["git", "clone", "-q", str(origin), str(inst)], check=True)
    (inst / "data").mkdir()
    (inst / "data" / "install.json").write_text(json.dumps({"mode": "managed"}))
    vbin = inst / "venv" / "bin"
    vbin.mkdir(parents=True)
    (vbin / "python").write_text(f'#!/bin/bash\nexec "{sys.executable}" "$@"\n')
    (vbin / "python").chmod(0o755)

    port = free_port()
    env = dict(os.environ, ODYSSEUS_PORT=str(port), ODYSSEUS_HOST="127.0.0.1",
               ODYSSEUS_HEALTH_TIMEOUT="25", ODYSSEUS_UPDATE_RESTART_MAX_WAIT="8",
               ODYSSEUS_UPDATE_CI_GATE="off", ODYSSEUS_ALLOW_SLEEP="1", ODYSSEUS_NO_OPEN="1",
               ODYSSEUS_SKIP_SCHEDULE="1", ODYSSEUS_UPDATE_QUIET="1")
    env.pop("ODYSSEUS_AUTO_UPDATE", None)

    def publish(msg, src):
        (work / "app.py").write_text(src)
        g(work, "commit", "-q", "-am", msg)
        g(work, "push", "-q", "origin", "main")
        return g(work, "rev-parse", "HEAD")

    def update():
        return subprocess.run([sys.executable, str(inst / "installer" / "updater.py")],
                              cwd=str(inst), env=env, capture_output=True, text=True, timeout=120)

    def state():
        return json.loads((inst / "data" / "update" / "state.json").read_text())

    sup = subprocess.Popen([sys.executable, str(inst / "installer" / "launch.py"),
                            "--background", "--no-browser", "--no-update"],
                           cwd=str(inst), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ns = type("Install", (), {})()
    ns.__dict__.update(dict(inst=inst, port=port, env=env, publish=publish, update=update,
                            state=state, sup=sup))
    yield ns
    if sup.poll() is None:
        sup.send_signal(signal.SIGTERM)
        try:
            sup.wait(30)
        except subprocess.TimeoutExpired:
            sup.kill()
    srv = inst / "data" / "run" / "server.pid"
    if srv.exists():
        try:
            os.kill(json.loads(srv.read_text())["pid"], signal.SIGKILL)
        except Exception:
            pass


def test_supervisor_full_update_cycle(install):
    port, inst = install.port, install.inst
    assert wait_for(lambda: get_version(port) == "v1"), (inst / "logs" / "launcher.log").read_text()
    assert wait_for(lambda: install.state().get("last_good") if (inst / "data/update/state.json").exists() else None)

    # 1. A good update is picked up: restart, health check, marked good.
    v2 = install.publish("v2", app_src("v2"))
    assert install.update().returncode == 0
    assert wait_for(lambda: get_version(port) == "v2"), (inst / "logs" / "launcher.log").read_text()
    assert wait_for(lambda: install.state().get("last_good") == v2)
    assert not (inst / "data" / "update" / "restart-requested.json").exists()

    # 2. A broken update is rolled back automatically.
    bad = install.publish("v3 broken", app_src("v3", broken=True))
    assert install.update().returncode == 0          # passes the syntax check
    assert wait_for(lambda: bad in install.state().get("bad_commits", []), timeout=60), \
        (inst / "logs" / "launcher.log").read_text()
    assert wait_for(lambda: get_version(port) == "v2", timeout=40)
    assert g(inst, "rev-parse", "HEAD") == v2
    assert wait_for(lambda: install.state().get("last_good") == v2 and
                    "server healthy on " + v2[:9] in (inst / "logs" / "launcher.log").read_text().split("rolling back")[-1])
    assert install.update().returncode == 2          # the bad tip is not retried

    # 3. While the server is busy the restart waits (up to the max wait).
    (inst / "BUSY").write_text("1")
    v4 = install.publish("v4", app_src("v4"))
    assert install.update().returncode == 0
    time.sleep(4)
    assert get_version(port) == "v2", (inst / "logs" / "launcher.log").read_text()  # waiting for quiet
    (inst / "BUSY").unlink()
    assert wait_for(lambda: get_version(port) == "v4", timeout=30)
    assert wait_for(lambda: install.state().get("last_good") == v4)

    # 4. A crashed server is started again (once v4 is confirmed healthy;
    #    dying before that is treated as a bad update — covered in step 2).
    assert wait_for(lambda: "server healthy on " + v4[:9] in (inst / "logs" / "launcher.log").read_text())
    pid = json.loads((inst / "data" / "run" / "server.pid").read_text())["pid"]
    os.kill(pid, signal.SIGKILL)
    assert wait_for(lambda: get_version(port) == "v4" and
                    json.loads((inst / "data" / "run" / "server.pid").read_text())["pid"] != pid, timeout=30)
    assert g(inst, "rev-parse", "HEAD") == v4
    new_pid = json.loads((inst / "data" / "run" / "server.pid").read_text())["pid"]
    assert new_pid != pid

    # 5. Status and stop.
    st = subprocess.run([sys.executable, str(inst / "installer" / "launch.py"), "--status"],
                        cwd=str(inst), env=install.env, capture_output=True, text=True, timeout=30)
    info = json.loads(st.stdout)
    assert info["healthy"] is True and info["supervisor_pid"] and info["mode"] == "managed"
    r = subprocess.run([sys.executable, str(inst / "installer" / "launch.py"), "--stop"],
                       cwd=str(inst), env=install.env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0
    assert wait_for(lambda: install.sup.poll() is not None, timeout=30)
    assert wait_for(lambda: get_version(port) is None, timeout=15)


def test_second_launch_is_a_no_op(install):
    assert wait_for(lambda: get_version(install.port) == "v1")
    r = subprocess.run([sys.executable, str(install.inst / "installer" / "launch.py"),
                        "--background", "--no-browser", "--no-update"],
                       cwd=str(install.inst), env=install.env, capture_output=True, text=True, timeout=30)
    assert r.returncode == 0
    assert "already running" in (install.inst / "logs" / "launcher.log").read_text()
    assert install.sup.poll() is None
