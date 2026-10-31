#!/bin/bash
# Odysseus — POSIX entry point for shortcuts, the .app bundle and autostart.
#
# Finds a Python that can run the launcher even when the venv is broken (a
# Homebrew Python upgrade is the usual cause) and hands over to
# installer/launch.py, which repairs the venv, applies updates and supervises
# the server. Arguments are passed through (e.g. --background, --stop).
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
cd "$REPO_DIR" || exit 1

ok() { "$1" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 11) else 1)' >/dev/null 2>&1; }

for py in "$REPO_DIR/venv/bin/python" "$REPO_DIR/venv/bin/python3" \
          /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.11 \
          /usr/local/bin/python3.13 /usr/local/bin/python3.12 /usr/local/bin/python3.11 \
          python3.13 python3.12 python3.11 python3; do
  p="$(command -v "$py" 2>/dev/null)" || continue
  if ok "$p"; then
    # On Apple Silicon make sure an arm64 interpreter runs natively.
    if [ "$(uname -s)" = "Darwin" ] && [ "$(uname -m)" = "arm64" ] && arch -arm64 "$p" -c pass >/dev/null 2>&1; then
      exec arch -arm64 "$p" "$REPO_DIR/installer/launch.py" "$@"
    fi
    exec "$p" "$REPO_DIR/installer/launch.py" "$@"
  fi
done

MSG="Odysseus needs Python 3.11 or newer. Re-run the installer: https://github.com/LuisArmando-TestCoder/Odysseus"
if [ "$(uname -s)" = "Darwin" ]; then
  /usr/bin/osascript -e "display dialog \"$MSG\" with title \"Odysseus\" buttons {\"OK\"} default button 1 with icon stop" >/dev/null 2>&1
elif command -v notify-send >/dev/null 2>&1; then
  notify-send "Odysseus" "$MSG"
fi
echo "$MSG" >&2
exit 1
