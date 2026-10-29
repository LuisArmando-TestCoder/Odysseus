#!/bin/bash
# Odysseus — double-click launcher (macOS).
# Runs the project from its absolute path, no matter where you invoke it from.
# Double-click this file (or run `bash ~/Desktop/Odysseus.command`) to start.
#
# SINGLE INSTANCE: opening this file again RESTARTS the server — the instance
# that is already running (started by this launcher, start-macos.sh, or by
# hand on the same port) is stopped first, then a fresh one starts. So
# "open it again" == "restart Odysseus" (e.g. to pick up backend changes).
#
#   bash Odysseus.command          start (or restart) the server
#   bash Odysseus.command stop     stop the running server and exit
#   bash Odysseus.command status   show whether it is running
#
# Press Ctrl+C in the Terminal window to stop.

set -e

# ── Absolute path to the project (edit if you ever move it) ────────────────
PROJECT="/Users/luisarmandooriens/Desktop/projects/odysseus"
PIDFILE="$PROJECT/data/.odysseus-server.pid"
LOGFILE="$PROJECT/logs/launcher.log"

cd "$PROJECT"
mkdir -p "$PROJECT/data" "$PROJECT/logs"

# Load .env so APP_PORT / APP_BIND are honored (shell vars win over .env).
if [ -f .env ]; then
  while IFS='=' read -r key value; do
    [[ "$key" =~ ^[[:space:]]*# ]] && continue
    [[ -z "${key// }" ]] && continue
    value="${value%%#*}"
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    [ -n "$key" ] && [ -z "${!key+x}" ] && export "$key=$value"
  done < .env
fi
PORT="${ODYSSEUS_PORT:-${APP_PORT:-7860}}"
HOST="${ODYSSEUS_HOST:-${APP_BIND:-127.0.0.1}}"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" | tee -a "$LOGFILE"; }

# PIDs of whatever is LISTENING on our port (the running server, whoever started it).
port_pids() { lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null | sort -u; }

is_alive() { [ -n "$1" ] && kill -0 "$1" 2>/dev/null; }

# Stop the previous instance: the PID we recorded plus anything on the port.
# SIGTERM first (uvicorn shuts down cleanly, detached runs get to checkpoint),
# then SIGKILL after 10 s if it is still there.
stop_previous() {
  local pids=""
  if [ -f "$PIDFILE" ]; then
    local old; old="$(cat "$PIDFILE" 2>/dev/null || true)"
    is_alive "$old" && pids="$old"
  fi
  pids="$(printf '%s\n%s\n' "$pids" "$(port_pids)" | grep -E '^[0-9]+$' | grep -v "^$$\$" | sort -u | tr '\n' ' ')"
  if [ -z "${pids// }" ]; then
    rm -f "$PIDFILE"
    return 0
  fi
  log "■ Stopping previous Odysseus (pid: $pids)"
  kill -TERM $pids 2>/dev/null || true
  for _ in $(seq 1 20); do
    local alive=""
    for p in $pids; do is_alive "$p" && alive="$alive $p"; done
    [ -z "$alive" ] && [ -z "$(port_pids)" ] && break
    sleep 0.5
  done
  local left=""
  for p in $pids; do is_alive "$p" && left="$left $p"; done
  left="$left $(port_pids | tr '\n' ' ')"
  if [ -n "${left// }" ]; then
    log "  still running after 10s — forcing (pid:$left)"
    kill -KILL $left 2>/dev/null || true
    sleep 0.5
  fi
  rm -f "$PIDFILE"
  log "  previous instance stopped"
}

case "${1:-start}" in
  stop)
    stop_previous
    exit 0
    ;;
  status)
    p="$(port_pids | tr '\n' ' ')"
    if [ -n "${p// }" ]; then echo "Odysseus is running on port $PORT (pid: $p)"; else echo "Odysseus is not running on port $PORT"; fi
    exit 0
    ;;
  start|restart|"") ;;
  *) echo "usage: $0 [start|stop|status]"; exit 2 ;;
esac

# ── Use the project venv if present, else system python ────────────────────
if [ -x "$PROJECT/venv/bin/python" ]; then
  PY="$PROJECT/venv/bin/python"
else
  PY="$(command -v python3 || command -v python)"
fi

URL_HOST="$HOST"
[ "$URL_HOST" = "0.0.0.0" ] || [ "$URL_HOST" = "::" ] && URL_HOST="127.0.0.1"
URL="http://$URL_HOST:$PORT"

stop_previous

echo "▶ Starting Odysseus at $URL  (project: $PROJECT)"
echo "  Press Ctrl+C here to stop. Opening this launcher again restarts it."
echo
log "▶ start (pid $$) on $URL"

# Open the browser once the server is up (skip with ODYSSEUS_NO_OPEN=1).
# On a restart the app is usually already open in a tab, so don't spawn a
# new one unless asked (ODYSSEUS_OPEN_ON_RESTART=1).
if [ -z "$ODYSSEUS_NO_OPEN" ] && command -v open >/dev/null 2>&1; then
  if [ -z "$_ODY_RESTARTED" ] || [ -n "$ODYSSEUS_OPEN_ON_RESTART" ]; then
    ( for _ in $(seq 1 60); do
        (exec 3<>"/dev/tcp/$URL_HOST/$PORT") 2>/dev/null && { open "$URL"; break; }
        sleep 1
      done ) &
  fi
fi

# exec keeps this PID, so the PID file points at the server itself.
echo $$ > "$PIDFILE"
exec "$PY" -m uvicorn app:app --host "$HOST" --port "$PORT"
