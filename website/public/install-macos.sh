#!/bin/bash
# Odysseus — macOS installer (the downloadable "executable").
#
# Run it from Terminal:
#   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/LuisArmando-TestCoder/Odysseus/main/installer/bootstrap/install-macos.sh)"
# or download it and run:  bash install-macos.sh
#
# It:
#   1. installs the Xcode Command Line Tools (git) if missing, and waits for them
#   2. ensures Python 3.11+ (Homebrew python@3.12 if missing)
#   3. clones Odysseus into ~/odysseus (override with ODYSSEUS_HOME=/path)
#   4. runs the installer: credentials, environment, hourly auto-update from
#      the main branch, shortcuts, optional start-at-login, first launch
#
# Safe to re-run: an existing install is updated and repaired in place.
# Extra arguments go to installer/install.py (e.g. --non-interactive).
#
# Everything is inside main() so a partially downloaded script never runs.
set -e

main() {
  local REPO_URL="${ODYSSEUS_REPO_URL:-https://github.com/LuisArmando-TestCoder/Odysseus.git}"
  local INSTALL_DIR="${ODYSSEUS_HOME:-$HOME/odysseus}"
  local PY="" cand p

  echo "▶ Odysseus — macOS installer"
  echo "  install folder: $INSTALL_DIR"
  echo

  # ── 1. Command Line Tools (git) ────────────────────────────────────────
  # /usr/bin/git exists even without the CLT (it's a stub that opens an
  # install prompt), so check the tools themselves.
  if ! xcode-select -p >/dev/null 2>&1 || ! /usr/bin/git --version >/dev/null 2>&1; then
    echo "Installing the Xcode Command Line Tools (a system dialog opens — click Install)…"
    xcode-select --install >/dev/null 2>&1 || true
    local waited=0
    until xcode-select -p >/dev/null 2>&1 && /usr/bin/git --version >/dev/null 2>&1; do
      sleep 10; waited=$((waited + 10))
      [ $((waited % 60)) -eq 0 ] && echo "  …still waiting for the Command Line Tools ($((waited / 60)) min)"
      if [ "$waited" -ge 3600 ]; then
        echo "✗ The Command Line Tools didn't finish installing. Re-run this installer when they're done."
        exit 1
      fi
    done
  fi
  echo "  ✓ git: $(git --version)"

  # ── 2. Python 3.11+ ────────────────────────────────────────────────────
  for cand in /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.11 \
              /usr/local/bin/python3.13 /usr/local/bin/python3.12 /usr/local/bin/python3.11 \
              python3.13 python3.12 python3.11 python3; do
    p="$(command -v "$cand" 2>/dev/null)" || continue
    if "$p" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3,11) else 1)' 2>/dev/null; then
      PY="$p"; break
    fi
  done

  if [ -z "$PY" ]; then
    echo "Python 3.11+ not found — installing it with Homebrew."
    if ! command -v brew >/dev/null 2>&1 && [ ! -x /opt/homebrew/bin/brew ] && [ ! -x /usr/local/bin/brew ]; then
      echo "Installing Homebrew (it may ask for your password)…"
      NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" </dev/tty
    fi
    # Homebrew on Apple Silicon installs to /opt/homebrew; add it to PATH for this run.
    if [ -x /opt/homebrew/bin/brew ]; then
      eval "$(/opt/homebrew/bin/brew shellenv)"
    elif [ -x /usr/local/bin/brew ]; then
      eval "$(/usr/local/bin/brew shellenv)"
    fi
    brew install python@3.12
    PY="$(command -v /opt/homebrew/bin/python3.12 || command -v /usr/local/bin/python3.12 || command -v python3.12 || true)"
  fi

  if [ -z "$PY" ] || [ ! -x "$PY" ]; then
    echo "✗ Could not get Python 3.11+. Install it from https://www.python.org/downloads/ and re-run."
    exit 1
  fi
  echo "  ✓ Python: $("$PY" --version 2>&1) at $PY"

  # ── 3. Clone (or reuse) ────────────────────────────────────────────────
  if [ -d "$INSTALL_DIR/.git" ]; then
    echo "  ✓ Odysseus is already installed here — it will be updated and repaired."
  elif [ -e "$INSTALL_DIR" ] && [ -n "$(ls -A "$INSTALL_DIR" 2>/dev/null)" ]; then
    echo "✗ $INSTALL_DIR exists and isn't an Odysseus install. Move it, or set ODYSSEUS_HOME=/other/path."
    exit 1
  else
    echo "  Cloning $REPO_URL …"
    git clone "$REPO_URL" "$INSTALL_DIR"
  fi

  # ── 4. Installer (reads answers from the terminal even when piped) ────
  cd "$INSTALL_DIR"
  echo
  echo "▶ Running the Odysseus installer…"
  if [ -t 0 ]; then
    "$PY" installer/install.py "$@"
  elif [ -r /dev/tty ]; then
    "$PY" installer/install.py "$@" </dev/tty
  else
    "$PY" installer/install.py --non-interactive "$@"
  fi
}

main "$@"
