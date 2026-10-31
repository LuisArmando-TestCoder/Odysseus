#!/bin/bash
# Odysseus — scheduled self-update (macOS & Linux).
#
# The OS scheduler (launchd / cron, registered by the installer) runs this
# every hour. All the logic lives in installer/updater.py (stdlib only); this
# wrapper just finds a Python 3 to run it with, so updates keep working even
# if the venv is broken. See docs/auto-update.md.
#
#   ./installer/self_update.sh            scheduled run
#   ./installer/self_update.sh --check    report only
#
# Everything is inside main() so bash has parsed the whole file before an
# update replaces it on disk.
main() {
  local repo_dir py p
  repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
  mkdir -p "$repo_dir/logs"
  py=""
  for p in "$repo_dir/venv/bin/python" "$repo_dir/venv/bin/python3" python3 python; do
    p="$(command -v "$p" 2>/dev/null)" || continue
    if "$p" -c 'import sys; raise SystemExit(0 if sys.version_info[0] == 3 and sys.version_info[1] >= 8 else 1)' >/dev/null 2>&1; then
      py="$p"; break
    fi
  done
  if [ -z "$py" ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: no python3 found for the updater" >> "$repo_dir/logs/self-update.log"
    exit 1
  fi
  ODYSSEUS_UPDATE_QUIET=1 "$py" "$repo_dir/installer/updater.py" --scheduled "$@"
  exit $?
}
main "$@"
