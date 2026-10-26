#!/bin/bash
# Odysseus — Linux bootstrap installer.
#
# Downloads and runs on a fresh Linux box. It:
#   1. ensures git is present (apt / dnf / pacman / apk)
#   2. ensures Python 3.11+ is present
#   3. clones the Odysseus repo
#   4. runs the interactive credential wizard + environment setup
#
# Safe to re-run: every step is idempotent.
set -e

REPO_URL="https://github.com/LuisArmando-TestCoder/Odysseus.git"
INSTALL_DIR="$HOME/odysseus"

echo "▶ Odysseus — Linux installer"
echo

# Detect the package manager once.
PM=""
if command -v apt-get >/dev/null 2>&1; then PM="apt"
elif command -v dnf >/dev/null 2>&1; then PM="dnf"
elif command -v pacman >/dev/null 2>&1; then PM="pacman"
elif command -v apk >/dev/null 2>&1; then PM="apk"
fi

install_pkg() {
  case "$PM" in
    apt)    sudo apt-get update -y && sudo apt-get install -y "$@" ;;
    dnf)    sudo dnf install -y "$@" ;;
    pacman) sudo pacman -S --noconfirm "$@" ;;
    apk)    sudo apk add "$@" ;;
    *)      echo "✗ Unsupported package manager. Install $* manually and re-run."; exit 1 ;;
  esac
}

# ── 1. git ────────────────────────────────────────────────────────────────
if ! command -v git >/dev/null 2>&1; then
  echo "git not found — installing…"
  install_pkg git
fi
echo "  ✓ git: $(git --version)"

# ── 2. Python 3.11+ ───────────────────────────────────────────────────────
PY=""
for cand in python3.13 python3.12 python3.11 python3; do
  p="$(command -v "$cand" 2>/dev/null)" || continue
  if "$p" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3,11) else 1)' 2>/dev/null; then
    PY="$p"; break
  fi
done

if [ -z "$PY" ]; then
  echo "Python 3.11+ not found — installing…"
  case "$PM" in
    apt)    install_pkg python3 python3-venv python3-pip ;;
    dnf)    install_pkg python3.11 python3.11-pip ;;
    pacman) install_pkg python ;;
    apk)    install_pkg python3 ;;
    *)      echo "✗ Unsupported package manager. Install Python 3.11+ manually and re-run."; exit 1 ;;
  esac
  for cand in python3.13 python3.12 python3.11 python3; do
    p="$(command -v "$cand" 2>/dev/null)" || continue
    if "$p" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3,11) else 1)' 2>/dev/null; then
      PY="$p"; break
    fi
  done
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