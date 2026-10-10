#!/usr/bin/env bash
# Opens the YouTube video "Grimes - ACCELERANDO (Official Music Video)" in the default browser.
set -euo pipefail

URL="https://www.youtube.com/watch?v=AVu5p2DMIf8"

if command -v open >/dev/null 2>&1; then
  # macOS
  open "$URL"
elif command -v xdg-open >/dev/null 2>&1; then
  # Linux
  xdg-open "$URL"
elif command -v start >/dev/null 2>&1; then
  # Windows (via Git Bash / WSL interop)
  start "$URL"
else
  echo "No browser-opening command found. Open this URL manually:" >&2
  echo "$URL" >&2
  exit 1
fi
