#!/bin/bash
# Odysseus — daily self-update (POSIX: macOS & Linux).
#
# Safe, non-destructive:
#   * fetch + fast-forward only (never rewrites local commits)
#   * if the working tree is dirty, stash first and pop after
#   * reinstall dependencies ONLY when requirements.txt changed
#   * never touches .env or data/ (both are gitignored per project config)
#
# Invoked by the installer-registered daily task (launchd on macOS, cron on
# Linux). Can also be run manually:  ./installer/self_update.sh
set -u

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$REPO_DIR/logs"
LOG="$LOG_DIR/self-update.log"

mkdir -p "$LOG_DIR"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"; }

log "== self-update start =="
cd "$REPO_DIR" || { log "ERROR: cannot cd $REPO_DIR"; exit 1; }

# 1. Fetch all remotes, prune deleted branches.
if ! git fetch --all --prune >/dev/null 2>>"$LOG"; then
  log "ERROR: git fetch failed"
  exit 1
fi

# Remember whether requirements changed so we only reinstall when needed.
REQ_BEFORE=""
[ -f requirements.txt ] && REQ_BEFORE="$(git rev-parse HEAD:requirements.txt 2>/dev/null || cat requirements.txt)"

# 2. Stash local changes (if any) so fast-forward can proceed, then pop after.
STASHED=0
if ! git diff --quiet || ! git diff --cached --quiet; then
  if git stash push -m "odysseus-auto-update $(date '+%Y%m%d%H%M%S')" >/dev/null 2>>"$LOG"; then
    STASHED=1
    log "stashed local changes before pull"
  else
    log "WARN: git stash failed — attempting pull anyway"
  fi
fi

# 3. Fast-forward only. Never rewrite history.
PULL_OK=0
if git pull --ff-only >/dev/null 2>>"$LOG"; then
  PULL_OK=1
  log "pull: up to date / fast-forwarded"
else
  log "WARN: git pull --ff-only failed (diverged history). Manual reconciliation needed."
fi

# 4. Restore local changes.
if [ "$STASHED" = "1" ]; then
  git stash pop >/dev/null 2>>"$LOG" && log "restored stashed changes" || log "WARN: stash pop failed (see git stash list)"
fi

# 5. Reinstall dependencies if requirements.txt changed.
if [ "$PULL_OK" = "1" ] && [ -f requirements.txt ]; then
  REQ_AFTER="$(git rev-parse HEAD:requirements.txt 2>/dev/null || cat requirements.txt)"
  if [ "$REQ_BEFORE" != "$REQ_AFTER" ]; then
    log "requirements.txt changed — reinstalling"
    if [ -x "$REPO_DIR/venv/bin/pip" ]; then
      "$REPO_DIR/venv/bin/pip" install -r requirements.txt >>"$LOG" 2>&1 \
        && log "dependencies reinstalled" \
        || log "WARN: pip install failed"
    else
      log "WARN: venv not found — skipping dependency reinstall (run the installer)"
    fi
  else
    log "requirements.txt unchanged — skipping reinstall"
  fi
fi

# 6. Idempotent first-run/setup pass (does nothing if already configured).
if [ -x "$REPO_DIR/venv/bin/python" ]; then
  ODYSSEUS_SKIP_RUN_HINT=1 "$REPO_DIR/venv/bin/python" "$REPO_DIR/setup.py" >>"$LOG" 2>&1 \
    && log "setup.py pass ok" \
    || log "WARN: setup.py pass failed"
fi

# 7. Acceptance: verify git is now at origin's tip (best effort).
log "git HEAD: $(git rev-parse --short HEAD 2>/dev/null || echo '?')"
log "== self-update end =="

exit 0