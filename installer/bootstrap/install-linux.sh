#!/bin/bash
# Odysseus — Linux installer (the downloadable "executable").
#
# Run it from a terminal:
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/LuisArmando-TestCoder/Odysseus/main/installer/bootstrap/install-linux.sh)"
# or download it and run:  bash install-linux.sh
#
# It:
#   1. ensures git (apt / dnf / pacman / zypper / apk)
#   2. ensures Python 3.11+ with venv support
#   3. clones Odysseus into ~/odysseus (override with ODYSSEUS_HOME=/path)
#   4. runs the installer: credentials, environment, hourly auto-update from
#      the main branch, shortcuts, optional start-at-login, first launch
#
# Safe to re-run: an existing install is updated and repaired in place.
# Extra arguments go to installer/install.py (e.g. --non-interactive).
#
# Everything is inside main() so a partially downloaded script never runs.
set -e

PM=""
detect_pm() {
  if command -v apt-get >/dev/null 2>&1; then PM="apt"
  elif command -v dnf >/dev/null 2>&1; then PM="dnf"
  elif command -v pacman >/dev/null 2>&1; then PM="pacman"
  elif command -v zypper >/dev/null 2>&1; then PM="zypper"
  elif command -v apk >/dev/null 2>&1; then PM="apk"
  fi
}

install_pkg() {
  local SUDO=""
  [ "$(id -u)" -ne 0 ] && SUDO="sudo"
  case "$PM" in
    apt)    $SUDO apt-get update -y && $SUDO apt-get install -y "$@" ;;
    dnf)    $SUDO dnf install -y "$@" ;;
    pacman) $SUDO pacman -S --noconfirm --needed "$@" ;;
    zypper) $SUDO zypper --non-interactive install "$@" ;;
    apk)    $SUDO apk add "$@" ;;
    *)      echo "✗ Unknown package manager. Install $* yourself and re-run."; exit 1 ;;
  esac
}

find_python() {
  local cand p
  for cand in python3.13 python3.12 python3.11 python3; do
    p="$(command -v "$cand" 2>/dev/null)" || continue
    if "$p" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3,11) else 1)' 2>/dev/null; then
      echo "$p"; return 0
    fi
  done
  return 1
}

main() {
  local REPO_URL="${ODYSSEUS_REPO_URL:-https://github.com/LuisArmando-TestCoder/Odysseus.git}"
  local INSTALL_DIR="${ODYSSEUS_HOME:-$HOME/odysseus}"
  local PY=""

  echo "▶ Odysseus — Linux installer"
  echo "  install folder: $INSTALL_DIR"
  echo
  detect_pm

  # ── 1. git ─────────────────────────────────────────────────────────────
  if ! command -v git >/dev/null 2>&1; then
    echo "git not found — installing…"
    install_pkg git
  fi
  echo "  ✓ git: $(git --version)"

  # ── 2. Python 3.11+ (with venv/ensurepip) ──────────────────────────────
  PY="$(find_python || true)"
  if [ -z "$PY" ]; then
    echo "Python 3.11+ not found — installing…"
    case "$PM" in
      apt)    install_pkg python3 python3-venv python3-pip ;;
      dnf)    install_pkg python3.12 || install_pkg python3.11 ;;
      pacman) install_pkg python ;;
      zypper) install_pkg python312 || install_pkg python311 ;;
      apk)    install_pkg python3 py3-pip ;;
      *)      echo "✗ Install Python 3.11+ yourself and re-run."; exit 1 ;;
    esac
    PY="$(find_python || true)"
  fi
  if [ -z "$PY" ]; then
    echo "✗ Could not get Python 3.11+ (your distro may ship an older one)."
    echo "  Install 3.11+ (e.g. via your distro's backports, deadsnakes, or pyenv) and re-run."
    exit 1
  fi
  # Debian/Ubuntu ship venv separately.
  if ! "$PY" -c 'import ensurepip, venv' >/dev/null 2>&1; then
    echo "Adding Python venv support…"
    local ver; ver="$("$PY" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
    case "$PM" in
      apt) install_pkg "python${ver}-venv" || install_pkg python3-venv ;;
      *)   echo "  (if venv creation fails, install your distro's python venv package)" ;;
    esac
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
