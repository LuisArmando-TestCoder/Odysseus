"""Static + unit checks for the installer "executables" and OS integration."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BOOT = ROOT / "installer" / "bootstrap"
sys.path.insert(0, str(ROOT / "installer"))

import schedulers  # noqa: E402
import shortcuts  # noqa: E402

SH_SCRIPTS = [BOOT / "install-macos.sh", BOOT / "install-linux.sh",
              ROOT / "installer" / "self_update.sh", ROOT / "installer" / "run.sh"]


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not installed")
@pytest.mark.parametrize("script", SH_SCRIPTS, ids=lambda p: p.name)
def test_shell_scripts_parse(script):
    r = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("name", ["install-macos.sh", "install-linux.sh"])
def test_bootstrap_is_pipe_safe(name):
    s = (BOOT / name).read_text()
    # Whole body in a function, called on the last line: a partial download never runs.
    assert s.rstrip().endswith('main "$@"')
    # The wizard reads the terminal, not the piped script.
    assert "</dev/tty" in s and "--non-interactive" in s
    assert "ODYSSEUS_HOME" in s


def test_macos_bootstrap_checks_real_command_line_tools():
    s = (BOOT / "install-macos.sh").read_text()
    assert "xcode-select -p" in s          # /usr/bin/git alone is only a stub
    assert "until xcode-select -p" in s     # waits for the install to finish


def test_windows_bootstrap_has_double_clickable_wrapper():
    cmd = (BOOT / "install-windows.cmd").read_text()
    assert "-ExecutionPolicy Bypass" in cmd
    assert "raw.githubusercontent.com/LuisArmando-TestCoder/Odysseus/main/installer/bootstrap/install-windows.ps1" in cmd
    ps1 = (BOOT / "install-windows.ps1").read_text()
    assert "$launcherArgs" in ps1 and "function Get-PyVersion($launcher, $args)" not in ps1
    assert "WindowsApps" in ps1             # skips the Microsoft Store python alias


def test_website_serves_the_same_installers():
    public = ROOT / "website" / "public"
    for src in BOOT.iterdir():
        if src.name.startswith("install-"):
            dst = public / src.name
            assert dst.exists(), f"{dst} missing — run `npm run sync-installers` in website/"
            assert dst.read_bytes() == src.read_bytes(), f"{dst.name} differs from installer/bootstrap"


def test_self_update_wrapper_parses_fully_before_running():
    s = (ROOT / "installer" / "self_update.sh").read_text()
    assert s.rstrip().endswith('main "$@"')
    assert "updater.py" in s and "--scheduled" in s


def test_launchd_plist_runs_hourly_and_at_login(tmp_path):
    plist = schedulers._launchd_plist("com.odysseus.selfupdate", tmp_path / "self_update.sh", tmp_path)
    assert f"<integer>{schedulers.INTERVAL_S}</integer>" in plist
    assert "StartInterval" in plist and "<key>RunAtLoad</key><true/>" in plist
    assert "/opt/homebrew/bin" in plist


def test_launchd_domain_uses_real_uid():
    src = (ROOT / "installer" / "schedulers.py").read_text()
    assert "gui/$(id -u)" not in src.replace('"gui/$(id -u)" not in', "")
    assert 'f"gui/{os.getuid()}"' in src


def test_cron_replaces_old_daily_entry():
    old = ("MAILTO=x\n0 1 * * * other-job\n# Odysseus Self-Update\n"
           "30 3 * * * /bin/bash /home/u/odysseus/installer/self_update.sh >> log 2>&1\n")
    base = schedulers._strip_ours(old)
    assert "other-job" in base and "self_update" not in base and "Odysseus" not in base
    entry = schedulers._cron_entry(Path("/home/u/odysseus"))
    assert entry.startswith("7 * * * * ")  # hourly


def test_schtasks_is_hourly_and_hidden():
    src = (ROOT / "installer" / "schedulers.py").read_text()
    assert '"/SC", "HOURLY"' in src and "-WindowStyle Hidden" in src


def test_shortcut_removal_only_touches_this_install(tmp_path, monkeypatch):
    if os.name == "nt":
        pytest.skip("POSIX paths")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    monkeypatch.setattr(shortcuts.platform, "system", lambda: "Linux")
    menu = tmp_path / "share" / "applications"
    menu.mkdir(parents=True)
    mine = menu / "odysseus.desktop"
    mine.write_text("Exec=/bin/bash \"/opt/inst/installer/run.sh\"\n")
    other = menu / "odysseus-stop.desktop"
    other.write_text("Exec=/bin/bash \"/somewhere/else/installer/run.sh\" --stop\n")
    removed = shortcuts.remove_shortcuts(Path("/opt/inst"))
    assert str(mine) in removed and not mine.exists()
    assert other.exists()


def test_shortcuts_go_through_the_supervisor():
    src = (ROOT / "installer" / "shortcuts.py").read_text()
    assert "run.sh" in src and "run.cmd" in src and "--stop" in src
    run_sh = (ROOT / "installer" / "run.sh").read_text()
    assert "installer/launch.py" in run_sh


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_update_notice_logic():
    js = r"""
    const m = await import(process.argv[1]);
    const now = Date.now();
    const out = {
      none: m.noticeFor({}, null, now),
      restart: m.noticeFor({restart_needed: true, is_admin: true, supervised: true}, null, now),
      restartNoSup: m.noticeFor({restart_needed: true, is_admin: true, supervised: false}, null, now),
      updated: m.noticeFor({updated_at: now/1000, head: 'abc', changes: [{subject: 'x'}]}, null, now),
      seen: m.noticeFor({updated_at: now/1000, head: 'abc', changes: [{subject: 'x'}]}, 'abc', now),
      old: m.noticeFor({updated_at: now/1000 - 10*86400, head: 'abc', changes: [{subject: 'x'}]}, null, now),
      avail: m.noticeFor({is_admin: true, mode: 'dev', available: {count: 2, commits: []}}, null, now),
      availManaged: m.noticeFor({is_admin: true, mode: 'managed', enabled: true, available: {count: 2}}, null, now),
      rb: m.noticeFor({is_admin: true, rolled_back_from: 'bad', history: [{event: 'rollback', t: now/1000}]}, null, now),
    };
    console.log(JSON.stringify(out));
    """
    r = subprocess.run(["node", "--input-type=module", "-e", js,
                        (ROOT / "static" / "js" / "update-notice.js").as_uri()],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    import json
    o = json.loads(r.stdout)
    assert o["none"] is None
    assert o["restart"]["kind"] == "restart" and o["restart"]["action"] == "Restart now"
    assert o["restartNoSup"]["action"] is None
    assert o["updated"]["kind"] == "updated" and o["updated"]["seen"] == "abc"
    assert o["seen"] is None and o["old"] is None
    assert o["avail"]["kind"] == "available" and o["availManaged"] is None
    assert o["rb"]["kind"] == "rollback"


def test_launchd_job_does_not_boot_itself_out(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(schedulers.subprocess, "run", lambda *a, **k: calls.append(a[0]) or
                        type("R", (), {"returncode": 0, "stderr": "", "stdout": ""})())
    monkeypatch.setattr(schedulers, "_launch_agents", lambda: tmp_path / "LaunchAgents")
    monkeypatch.setattr(schedulers.os, "getuid", lambda: 501, raising=False)
    monkeypatch.setenv("XPC_SERVICE_NAME", schedulers.UPDATE_LABEL)
    ok, _ = schedulers._register_launchd_update(tmp_path)
    assert ok and (tmp_path / "LaunchAgents" / f"{schedulers.UPDATE_LABEL}.plist").exists()
    assert not any("bootout" in c for c in calls)
    monkeypatch.delenv("XPC_SERVICE_NAME")
    ok, _ = schedulers._register_launchd_update(tmp_path)
    assert ok and any("bootout" in c for c in calls) and any("bootstrap" in c for c in calls)


def test_port_conflict_is_not_blamed_on_the_update(tmp_path, monkeypatch):
    import launch
    log = tmp_path / "server.log"
    log.write_text("old run: [Errno 98] address already in use\n")
    monkeypatch.setattr(launch, "SERVER_LOG", log)
    offset = log.stat().st_size
    assert launch.port_conflict_since(0) is True
    assert launch.port_conflict_since(offset) is False          # only this start counts
    with log.open("a") as fh:
        fh.write("ERROR: [WinError 10048] Only one usage of each socket address is normally permitted\n")
    assert launch.port_conflict_since(offset) is True


def test_supervisor_waits_for_a_running_update(monkeypatch):
    import launch
    states = iter([True, True, False])
    monkeypatch.setattr(launch.updater, "update_running", lambda: next(states))
    monkeypatch.setattr(launch.time, "sleep", lambda s: None)
    assert launch.wait_for_update_lock(max_s=60, why="starting") is True
