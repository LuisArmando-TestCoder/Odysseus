#!/bin/bash
# Odysseus — double-click launcher (macOS / Linux).
#
# Every start is a clean restart onto the latest code:
#
#   1. TERMINATE  ask the running supervisor + server to shut down cleanly
#                 (SIGTERM → uvicorn finishes requests, runs checkpoint)
#   2. SEARCH     find anything left over: the recorded PIDs, whatever listens
#                 on our port, and any uvicorn / supervisor started from THIS
#                 project folder (by hand, by an older launcher, a force quit…)
#   3. KILL       whatever is still alive after the grace period gets SIGKILL;
#                 then wait until the port is actually free
#   4. UPDATE     pull what's new on the update branch (installer/updater.py:
#                 CI-gated, syntax-checked, deps reinstalled when needed,
#                 rolled back if the new version doesn't come up)
#   5. START      run under the supervisor (installer/launch.py), which keeps
#                 checking for updates while it runs and restarts into them at
#                 a quiet moment (no agent run or research in progress)
#
#   bash Odysseus.command            start / restart (pulls updates first)
#   bash Odysseus.command stop       terminate → search → kill, then exit
#   bash Odysseus.command status     what is running + update state
#   bash Odysseus.command update     check + install updates now (server keeps
#                                    running; the supervisor restarts into them)
#   bash Odysseus.command logs       follow the server / update logs
#
# Ctrl+C in this window stops Odysseus. Settings live in .env — see
# docs/auto-update.md (ODYSSEUS_AUTO_UPDATE, ODYSSEUS_UPDATE_BRANCH, …).

set -u

# ── Locate the project: next to this script, else the known install path ──
SELF="${BASH_SOURCE[0]}"
while [ -L "$SELF" ]; do
  t="$(readlink "$SELF")"; case "$t" in /*) SELF="$t" ;; *) SELF="$(dirname "$SELF")/$t" ;; esac
done
PROJECT="$(cd "$(dirname "$SELF")" && pwd -P)"
[ -f "$PROJECT/installer/launch.py" ] || PROJECT="${ODYSSEUS_HOME:-/Users/luisarmandooriens/Desktop/projects/odysseus}"
if [ ! -f "$PROJECT/installer/launch.py" ]; then
  echo "Can't find the Odysseus project (looked in $PROJECT). Set ODYSSEUS_HOME." >&2
  exit 1
fi
cd "$PROJECT"
mkdir -p data/run logs

LEGACY_PIDFILE="$PROJECT/data/.odysseus-server.pid"   # written by the old launcher
LOGFILE="$PROJECT/logs/launcher.log"

# .env → APP_PORT / APP_BIND (shell vars win)
if [ -f .env ]; then
  while IFS='=' read -r key value; do
    [[ "$key" =~ ^[[:space:]]*# ]] && continue
    [[ -z "${key// }" ]] && continue
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    value="${value%%#*}"
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    value="${value%\"}"; value="${value#\"}"; value="${value%\'}"; value="${value#\'}"
    [ -z "${!key+x}" ] && export "$key=$value"
  done < .env
fi
PORT="${ODYSSEUS_PORT:-${APP_PORT:-7860}}"
HOST="${ODYSSEUS_HOST:-${APP_BIND:-127.0.0.1}}"
URL_HOST="$HOST"; { [ "$URL_HOST" = "0.0.0.0" ] || [ "$URL_HOST" = "::" ]; } && URL_HOST="127.0.0.1"
URL="http://$URL_HOST:$PORT"

if [ -t 1 ]; then B=$'\e[1m'; D=$'\e[2m'; C=$'\e[36m'; Y=$'\e[33m'; G=$'\e[32m'; R=$'\e[0m'; else B=; D=; C=; Y=; G=; R=; fi
log()  { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOGFILE"; }
say()  { echo "${C}▸${R} $*"; log "$*"; }
step() { printf '%s\n' "${B}$1${R} ${D}$2${R}"; log "$1 $2"; }

# ── Python: the venv if it runs, else system (launch.py repairs the venv) ──
PY=""
if [ -x venv/bin/python ] && venv/bin/python -c pass >/dev/null 2>&1; then PY="$PROJECT/venv/bin/python"; fi
[ -z "$PY" ] && PY="$(command -v python3 || command -v python || true)"
[ -z "$PY" ] && { echo "Python 3 is not installed." >&2; exit 1; }

# ── process discovery ──────────────────────────────────────────────────────
is_alive()  { [ -n "${1:-}" ] && kill -0 "$1" 2>/dev/null; }
port_pids() { lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null | sort -u; }
json_pid()  { [ -f "$1" ] && sed -n 's/.*"pid"[^0-9]*\([0-9][0-9]*\).*/\1/p' "$1" 2>/dev/null | head -1; }

# The working directory of a pid (macOS lsof / Linux /proc).
pid_cwd() {
  if [ -r "/proc/$1/cwd" ]; then readlink "/proc/$1/cwd" 2>/dev/null
  else lsof -a -p "$1" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -1; fi
}

# Every process that belongs to THIS Odysseus: recorded pids, port listeners,
# and uvicorn app:app / installer/launch.py whose cwd or args are this project.
find_odysseus() {
  {
    json_pid data/run/supervisor.pid
    json_pid data/run/server.pid
    [ -f "$LEGACY_PIDFILE" ] && cat "$LEGACY_PIDFILE" 2>/dev/null
    port_pids
    pgrep -f "$PROJECT/installer/launch.py" 2>/dev/null
    for p in $(pgrep -f "uvicorn app:app" 2>/dev/null; pgrep -f "installer/launch.py" 2>/dev/null); do
      [ "$(pid_cwd "$p")" = "$PROJECT" ] && echo "$p"
    done
  } | grep -E '^[0-9]+$' | grep -vx "$$" | grep -vx "$PPID" | sort -u | while read -r p; do
    is_alive "$p" || continue
    # never our own helpers (launch.py --stop/--status) or an updater run
    ps -o args= -p "$p" 2>/dev/null | grep -qE -- 'launch\.py +--(stop|status)|updater\.py' && continue
    echo "$p"
  done | tr '\n' ' '
}

# An updater that is moving the checkout must finish (it undoes itself on
# failure, but SIGKILL mid-git can leave lock files). Wait for it, max 10 min.
wait_for_updater() {
  local u n=0
  while :; do
    u=""
    for p in $(pgrep -f "installer/updater.py" 2>/dev/null); do
      [ "$(pid_cwd "$p")" = "$PROJECT" ] && u="$u $p"
    done
    [ -z "${u// }" ] && return 0
    [ $n -eq 0 ] && step "  … update in progress" "letting it finish (pid$u)"
    n=$((n + 1)); [ $n -gt 1200 ] && return 1
    sleep 0.5
  done
}

# terminate → search → kill. Returns 0 if something was running.
stop_all() {
  local found; found="$(find_odysseus)"
  if [ -z "${found// }" ]; then
    rm -f "$LEGACY_PIDFILE" data/run/server.pid data/run/supervisor.pid
    return 1
  fi
  step "■ terminate" "pid ${found% }"

  # 1. Graceful: the supervisor stops its server cleanly (and won't kill git
  #    mid-update — it finishes that step first).
  local sup; sup="$(json_pid data/run/supervisor.pid)"
  if is_alive "$sup"; then
    "$PY" installer/launch.py --stop >/dev/null 2>&1 &
    local stopper=$!
  fi
  kill -TERM $found 2>/dev/null || true

  # 2. Search: wait for everything we know about AND anything that shows up.
  local left=""
  for i in $(seq 1 60); do      # up to 30 s
    left="$(find_odysseus)"
    [ -z "${left// }" ] && break
    [ $((i % 10)) -eq 0 ] && kill -TERM $left 2>/dev/null
    sleep 0.5
  done

  # 3. Kill whatever ignored SIGTERM (but never in the middle of an update).
  if [ -n "${left// }" ]; then
    wait_for_updater
    left="$(find_odysseus)"
  fi
  if [ -n "${left// }" ]; then
    step "  ✕ kill" "still alive after 30s: pid ${left% }"
    kill -KILL $left 2>/dev/null || true
    sleep 0.5
    left="$(find_odysseus)"
    [ -n "${left// }" ] && kill -KILL $left 2>/dev/null
  fi
  [ -n "${stopper:-}" ] && wait "$stopper" 2>/dev/null

  # Port really free? (TIME_WAIT is fine; a LISTEN socket is not.)
  for _ in $(seq 1 20); do [ -z "$(port_pids)" ] && break; sleep 0.25; done
  if [ -n "$(port_pids)" ]; then
    echo "${Y}Port $PORT is still taken by pid $(port_pids | tr '\n' ' ')— not Odysseus?${R}" >&2
    log "port $PORT still busy after stop"
  fi
  rm -f "$LEGACY_PIDFILE" data/run/server.pid data/run/supervisor.pid
  step "  ✓ stopped" ""
  return 0
}

show_status() {
  "$PY" installer/launch.py --status 2>/dev/null || true
  local f; f="$(find_odysseus)"
  [ -n "${f// }" ] && echo "processes: $f" || echo "processes: none"
}

case "${1:-start}" in
  stop)    stop_all || echo "Odysseus isn't running."; exit 0 ;;
  status)  show_status; exit 0 ;;
  update)  exec "$PY" installer/updater.py ;;
  logs)    exec tail -n 40 -F logs/launcher.log logs/self-update.log logs/server.log ;;
  start|restart|"") ;;
  -h|--help|help) sed -n '2,30p' "$0"; exit 0 ;;
  *) echo "usage: $0 [start|stop|status|update|logs]"; exit 2 ;;
esac

echo
echo "${B}${C}  ⟁  O D Y S S E U S${R}  ${D}$URL${R}"
echo

WAS_RUNNING=0
stop_all && WAS_RUNNING=1

before="$(git rev-parse --short HEAD 2>/dev/null || echo '?')"
step "⟳ update" "checking origin…"
# launch.py runs `updater.py --on-launch` itself before starting; we only show
# the result here so the window tells you what you're about to run.

ARGS=()
# A restart: the app is already open in a tab, and that tab reconnects and
# reloads itself — don't pile up new tabs. ODYSSEUS_OPEN_ON_RESTART=1 opts out.
if [ "$WAS_RUNNING" = 1 ] && [ -z "${ODYSSEUS_OPEN_ON_RESTART:-}" ]; then ARGS+=(--no-browser); fi
[ -n "${ODYSSEUS_NO_OPEN:-}" ] && ARGS+=(--no-browser)

# Report what the pre-start update did once the supervisor has run it.
( for _ in $(seq 1 600); do
    sleep 1
    [ -f data/run/server.pid ] || continue
    after="$(git rev-parse --short HEAD 2>/dev/null || echo '?')"
    if [ "$after" != "$before" ]; then
      n="$(git rev-list --count "$before..$after" 2>/dev/null || echo '?')"
      step "  ✓ updated" "$before → $after ($n commit(s))"
      git log --format='      · %s' "$before..$after" 2>/dev/null | head -12
    else
      res="$(sed -n 's/.*"last_result": *"\([a-z_]*\)".*/\1/p' data/update/state.json 2>/dev/null | head -1)"
      step "  ✓ ${res:-current}" "at $after"
    fi
    step "▶ start" "$URL"
    break
  done ) &

log "▶ launcher start (pid $$) → supervisor"
exec "$PY" installer/launch.py "${ARGS[@]+"${ARGS[@]}"}"
