#!/bin/bash
# Odysseus — macOS bootstrap installer.
#
# Downloads and runs on a fresh Mac. It:
#   1. ensures git is present (installs Xcode CLT if missing)
#   2. ensures Python 3.11+ is present (Homebrew python@3.11 if missing)
#   3. clones the Odysseus repo
#   4. runs the interactive credential wizard + environment setup
#
# Safe to re-run: every step is idempotent.
set -e

REPO_URL="https://github.com/LuisArmando-TestCoder/Odysseus.git"
INSTALL_DIR="$HOME/odysseus"

echo "▶ Odysseus — macOS installer"
echo

# ── 1. git ────────────────────────────────────────────────────────────────
if ! command -v git >/dev/null 2>&1; then
  echo "git not found — installing Xcode Command Line Tools (this opens a system prompt)."
  xcode-select --install 2>/dev/null || true
  echo "After the CLT install finishes, re-run this script."
  exit 1
fi
echo "  ✓ git: $(git --version)"

# ── 2. Python 3.11+ ───────────────────────────────────────────────────────
PY=""
for cand in /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.11 /usr/local/bin/python3.13 /usr/local/bin/python3.12 /usr/local/bin/python3.11 python3; do
  p="$(command -v "$cand" 2>/dev/null)" || continue
  if "$p" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3,11) else 1)' 2>/dev/null; then
    PY="$p"; break
  fi
done

if [ -z "$PY" ]; then
  echo "Python 3.11+ not found — installing via Homebrew."
  if ! command -v brew >/dev/null 2>&1; then
    echo "Installing Homebrew…"
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    if [ -x /opt/homebrew/bin/brew ]; then
      eval "$(/opt/homebrew/bin/brew shellenv)"
    elif [ -x /usr/local/bin/brew ]; then
      eval "$(/usr/local/bin/brew shellenv)"
    fi
  fi
  brew install python@3.11
  PY="$(command -v /opt/homebrew/bin/python3.11 || command -v /usr/local/bin/python3.11 || command -v python3.11)"
fi

if [ -z "$PY" ] || [ ! -x "$PY" ]; then
  echo "✗ Could not obtain Python 3.11+. Install it manually and re-run."
  exit 1
fi
echo "  ✓ Python: $("$PY" --version 2>&1) at $PY"

# ── 3. Clone ──────────────────────────────────────────────────────────────
if [ -d "$INSTALL_DIR/.git" ]; then
  echo "  ✓ Repo already present — updating…"
  git -C "$INSTALL_DIR" pull --ff-only || true
else
  echo "  Cloning $REPO_URL …"
  git clone "$REPO_URL" "$INSTALL_DIR"
fi

# ── 4. Run the installer wizard ───────────────────────────────────────────
cd "$INSTALL_DIR"
echo
echo "▶ Running the Odysseus installer (credentials + environment setup)…"
"$PY" installer/install.py