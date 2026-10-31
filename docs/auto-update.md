# Installing and auto-updating Odysseus

Odysseus installed with the downloadable installer keeps itself on the latest
`main` (repos that still use `master` are detected automatically). Nothing
needs to be run by hand: new commits are installed, the server restarts into
them at a quiet moment, and a version that doesn't start is rolled back.

## Install

| OS | Easiest | Alternative |
|---|---|---|
| macOS | Paste in Terminal: `/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/LuisArmando-TestCoder/Odysseus/main/installer/bootstrap/install-macos.sh)"` | Download `install-macos.sh`, run `bash ~/Downloads/install-macos.sh` |
| Linux | Paste in a terminal: `bash -c "$(curl -fsSL https://raw.githubusercontent.com/LuisArmando-TestCoder/Odysseus/main/installer/bootstrap/install-linux.sh)"` | Download `install-linux.sh`, run `bash install-linux.sh` |
| Windows | Download and double-click `install-windows.cmd` (SmartScreen: *More info → Run anyway*) | PowerShell: `irm https://raw.githubusercontent.com/LuisArmando-TestCoder/Odysseus/main/installer/bootstrap/install-windows.ps1 \| iex` |

The installer gets git and Python 3.11+ if they're missing (Xcode Command Line
Tools + Homebrew on macOS, the package manager on Linux, winget on Windows),
clones into `~/odysseus` (set `ODYSSEUS_HOME` to choose another folder), asks
for credentials, builds the environment, registers the hourly update, creates
shortcuts, optionally starts Odysseus at login, and launches it. Running the
installer again on an existing install updates and repairs it in place.

Why a one-liner on macOS/Linux: a downloaded `.sh` opens in a text editor when
double-clicked and macOS Gatekeeper blocks unsigned downloaded apps, while a
script fetched with `curl` runs as-is. The one-liners always fetch the
installer from the tip of `main`.

## Opening, stopping, start at login

| | macOS | Windows | Linux |
|---|---|---|---|
| Open | `~/Applications/Odysseus.app` (or Spotlight) | Desktop / Start menu *Odysseus* | App menu / Desktop *Odysseus* |
| Stop | `~/Applications/Stop Odysseus.app` | Start menu *Stop Odysseus* | App menu *Stop Odysseus* |
| With live logs | `~/Desktop/Odysseus.command` (Ctrl+C stops) | `venv\Scripts\python installer\launch.py` | `installer/run.sh` |
| Start at login | LaunchAgent `com.odysseus.app` | Startup-folder shortcut | `~/.config/autostart/odysseus.desktop` |

Opening Odysseus while it's already running just brings the UI up. From a
terminal: `installer/run.sh --status | --stop | --restart`.

## How updating works

```
OS schedule (hourly + at login)  ─┐
supervisor (hourly, while open)  ─┼─▶ installer/updater.py ──▶ restart request
opening the app (before start)   ─┘                                 │
                                                                    ▼
          installer/launch.py (supervisor) waits for a quiet moment, restarts,
          health-checks the new version ── fails? ──▶ rollback + restart
```

1. **Check.** `git fetch` of the update branch. Nothing new → done.
2. **Gate.** The tip is skipped if it failed on this machine before, or if the
   GitHub check `update-gate` (`.github/workflows/ci.yml`) failed for it. While
   that check is still running the update waits (up to 2 hours). No network to
   GitHub's API never blocks.
3. **Protect local work.** Edits to tracked files go to `git stash` and are
   restored afterwards; if they conflict with the update they stay in the
   stash (`git stash list`). Local commits that aren't on `origin`, or history
   that diverged (a force-push upstream), are kept on a branch named
   `odysseus/backup-<time>`. `.env` and `data/` are never touched.
4. **Apply.** The checkout moves to the tip of the branch.
5. **Verify.** Changed Python files are byte-compiled; dependencies are
   reinstalled when `requirements.txt` changed; `setup.py` runs its idempotent
   pass (migrations, new folders). A syntax error or failed dependency install
   puts the previous version back immediately.
6. **Restart.** The supervisor restarts the server when no agent run or
   research is in progress (`GET /api/update/idle`), or after 30 minutes at
   most. It then waits for `/api/health`; if the new version doesn't answer
   within 5 minutes, or exits, or crashes repeatedly right after the update, it
   rolls back to the previous version, remembers the bad commit and restarts.
7. **Tell the user.** The app shows a small notice: *updated · what's new*,
   *restart to finish* (when Odysseus wasn't started by its launcher), or that
   an update was rolled back. Admins get *Restart now* / *Install* buttons.

The supervisor also repairs a broken virtualenv (common after a Homebrew Python
upgrade) by rebuilding it, restarts a crashed server with back-off, adopts an
orphaned server left by a force quit, and keeps the computer awake while
Odysseus runs (`ODYSSEUS_ALLOW_SLEEP=1` to opt out).

### Failure handling

- **One updater at a time.** The lock is an OS file lock, released the moment
  its holder dies, so it never goes stale and is never taken from a live run.
  The supervisor never starts or restarts the server while an update is
  half-applied; it waits for the updater to finish.
- **Interrupted updates undo themselves.** If anything goes wrong after the
  checkout moved (a timeout, an error, Ctrl+C), the previous version and the
  user's edits are put back before the run ends.
- **Debris from a killed git** (`.git/index.lock` and friends older than 10
  minutes) is removed under the update lock, so a power cut mid-update doesn't
  block every later update. Timeouts stop git gracefully first.
- **Stashes are popped by exact commit**, never "whatever is on top", so a
  user's own `git stash` entries are never touched.
- **Windows:** dependency changes are installed with the server stopped
  (Windows can't replace loaded `.pyd`/`.dll` files): the supervisor stops the
  server, installs, and starts it. Shutdowns go through a loopback-only,
  per-start-token endpoint so the server always shuts down cleanly, and no
  helper process opens a console window.
- **Not every failure is the update's fault.** If the server can't start
  because its port is taken, nothing is rolled back; the user is told which
  port to free. After repeated crashes the supervisor doesn't quit: it keeps
  checking for updates (the next commit may be the fix) and retries every 10
  minutes.

### Install modes (`data/install.json`)

| mode | who | behaviour |
|---|---|---|
| `managed` | installed by the installer | fully autonomous, follows the branch even across force-pushes (after a backup) |
| `dev` | a developer clone | only checks and reports; never moves the checkout unless forced |
| *(none)* | installs from before this system | fast-forward only; the first scheduled run upgrades them to `managed` |

Existing installs that registered the old daily 03:30 job are moved to the new
hourly schedule by the updater itself on its next run (`SCHEDULE_VERSION`).

### Settings (`.env` or environment)

| Variable | Default | |
|---|---|---|
| `ODYSSEUS_AUTO_UPDATE` | `on` | `off` = check and show *available*, never install |
| `ODYSSEUS_UPDATE_BRANCH` | origin's default (`main`) | branch to follow |
| `ODYSSEUS_UPDATE_CI_GATE` | `on` | skip commits whose `update-gate` check failed |
| `ODYSSEUS_UPDATE_INTERVAL_MIN` | `60` | supervisor check interval while running |
| `ODYSSEUS_UPDATE_RESTART_MAX_WAIT` | `1800` | seconds to wait for a quiet moment before restarting anyway |
| `ODYSSEUS_HEALTH_TIMEOUT` | `300` | seconds a new version gets to answer `/api/health` |
| `ODYSSEUS_ALLOW_SLEEP` | off | `1` = don't keep the computer awake |

### Commands

```bash
python installer/updater.py            # check + install now
python installer/updater.py --check    # check only
python installer/updater.py --status   # state as JSON
python installer/updater.py --rollback # back to the previous version
python installer/launch.py --status    # what's running
python installer/uninstall.py          # remove schedule, autostart, shortcuts (keeps data)
```

Logs: `logs/self-update.log` (updater), `logs/launcher.log` (supervisor),
`logs/server.log` (server output when started from a shortcut). State:
`data/update/state.json`.

### API

`GET /api/update/status` (signed-in users; admin-only fields hidden for
others), `GET /api/update/idle` (loopback only, no auth — the supervisor's
probe), admin: `POST /api/update/check | apply | restart | rollback`.

## For maintainers: keeping `main` safe to ship

Every push to `main` is installed on every managed copy within about an hour.
The `update-gate` job in `.github/workflows/ci.yml` installs `requirements.txt`
in a clean venv, byte-compiles the tree, runs `setup.py`, boots the server and
waits for `/api/health`, and runs the installer/updater/supervisor tests.
Installs don't take a commit whose gate failed, and wait while it runs. Keep the
job name `update-gate` (it's `CI_GATE_JOB` in `installer/updater.py`).

The download page (`website/`) copies the scripts from `installer/bootstrap/`
before every build (`npm run sync-installers`); edit them there only.
