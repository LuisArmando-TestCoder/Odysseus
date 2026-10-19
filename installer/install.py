#!/usr/bin/env python3
"""Odysseus — self-running installer + daily self-update registrar.

Single entry point that:
  1. detects the platform
  2. pre-checks git / Python (and Homebrew on macOS)
  3. clones or fast-forwards the repository
  4. runs an interactive credential wizard over installer/credentials.py
  5. writes .env idempotently (0600)
  6. sets up the environment (delegating to existing project scripts)
  7. registers a daily self-update task (launchd / cron / schtasks)
  8. starts the server + opens the browser

Usage:
  python3 install.py                       # interactive
  python3 install.py --non-interactive     # use existing .env, skip wizard
  python3 install.py --repo-url <url>      # override the upstream repo
  python3 install.py --no-pyenv            # skip venv/dependency setup
  python3 install.py --no-schedule         # skip daily task registration
  python3 install.py --no-start            # skip server launch
"""

import argparse
import getpass
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict

# Make the scripts in this directory importable regardless of CWD.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from credentials import CREDENTIALS, GROUPS, REPO_URL  # noqa: E402
from env_writer import read_env, write_env  # noqa: E402
import schedulers  # noqa: E402
import shortcuts  # noqa: E402


# ── Tiny console helpers (ANSI, disabled on Windows legacy consoles) ────────

def _supports_color() -> bool:
    if platform.system() == "Windows":
        return os.environ.get("ANSICON") is not None or "WT_SESSION" in os.environ
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


if _supports_color():
    C = {
        "reset": "\033[0m", "bold": "\033[1m", "dim": "\033[2m",
        "red": "\033[31m", "green": "\033[32m", "yellow": "\033[33m",
        "cyan": "\033[36m",
    }
else:
    C = {k: "" for k in ("reset", "bold", "dim", "red", "green", "yellow", "cyan")}


def _print(msg: str = "") -> None:
    print(msg)


def heading(text: str) -> None:
    _print(f"\n{C['bold']}{C['cyan']}▶ {text}{C['reset']}")


def ok(text: str) -> None:
    _print(f"  {C['green']}✓{C['reset']} {text}")


def warn(text: str) -> None:
    _print(f"  {C['yellow']}⚠{C['reset']} {text}")


def fail(text: str) -> None:
    _print(f"  {C['red']}✗{C['reset']} {text}")


def _has(cmd: str) -> bool:
    return shutil.which(cmd) is not None


# ── Progress persistence (resumable wizard) ─────────────────────────────────

_STATE_PATH = Path(__file__).resolve().parent / ".install-state.json"


def _load_state() -> Dict:
    try:
        if _STATE_PATH.exists():
            return json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_state(state: Dict) -> None:
    _STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


# ── Credential wizard ───────────────────────────────────────────────────────

def run_wizard(env_path: Path) -> Dict[str, str]:
    """Prompt the user for every credential in the registry, then return the
    {KEY: value} map to write. Persists progress so Ctrl+C is resumable."""
    state = _load_state()
    answers = dict(state.get("answers", {}))

    heading("Recopilando credenciales (Ctrl+C para pausar, se reanuda donde quedó)")
    _print(f"{C['dim']}Cada variable muestra enlaces de tutorial. Introduce el valor, "
           f"o pulsa Enter para usar el valor por defecto, o 's' para saltar.{C['reset']}\n")

    current_group = None
    for entry in CREDENTIALS:
        key = entry["key"]
        if key in answers:
            continue  # already collected in a prior run

        group = GROUPS.get(entry["category"], entry["category"])
        if group != current_group:
            current_group = group
            _print(f"\n{C['bold']}── {group}{C['reset']}")

        label = entry["label"]
        required = entry.get("required", False)
        default = entry.get("default", "")
        secret = entry.get("secret", False)
        choices = entry.get("choices")

        _print(f"\n  {C['bold']}{label}{C['reset']}  ({'obligatorio' if required else 'opcional'})")
        if entry.get("help_text"):
            _print(f"    {C['dim']}{entry['help_text']}{C['reset']}")
        for i, url in enumerate(entry.get("tutorial_urls", []), 1):
            _print(f"    {C['dim']}Tutorial [{i}]: {url}{C['reset']}")

        prompt_suffix = ""
        if default:
            prompt_suffix = f" [{default}]"
        if choices:
            prompt_suffix = " [" + "/".join(choices) + "]"

        while True:
            if secret:
                value = getpass.getpass(f"    Valor{prompt_suffix} (oculto): ")
            else:
                value = input(f"    Valor{prompt_suffix}: ").strip()

            if value == "" and default:
                value = default
            elif value == "" and not required:
                value = ""
                break
            elif value == "" and required:
                _print(f"    {C['red']}Este campo es obligatorio.{C['reset']}")
                continue

            if value.lower() == "s" and not required:
                value = ""
                break

            # Validate against choices if provided.
            if choices and value not in choices:
                _print(f"    {C['red']}Valor no permitido. Opciones: {choices}{C['reset']}")
                continue

            # Basic validation for known password field.
            if key == "ODYSSEUS_ADMIN_PASSWORD" and value and len(value) < 8:
                _print(f"    {C['red']}La contraseña debe tener al menos 8 caracteres.{C['reset']}")
                continue

            break

        answers[key] = value
        _save_state({"answers": answers, "env_path": str(env_path)})

    return answers


# ── Repository bootstrap ────────────────────────────────────────────────────

def _pull_or_clone(repo_dir: Path, repo_url: str) -> bool:
    heading(f"Repositorio en {repo_dir}")
    if (repo_dir / ".git").exists():
        _print("  Repositorio existente — actualizando…")
        r = subprocess.run(
            ["git", "-C", str(repo_dir), "pull", "--ff-only"],
            capture_output=True,
            text=True,
        )
        if r.returncode == 0:
            ok("Repositorio actualizado")
            return True
        warn(f"git pull --ff-only falló: {r.stderr.strip()}")
        # Safe fallback: stash and retry.
        subprocess.run(["git", "-C", str(repo_dir), "stash", "push", "-m", "installer"], capture_output=True)
        r2 = subprocess.run(
            ["git", "-C", str(repo_dir), "pull", "--ff-only"],
            capture_output=True,
            text=True,
        )
        if r2.returncode == 0:
            ok("Repositorio actualizado (cambios locales guardados en stash)")
            return True
        fail(f"No se pudo actualizar: {r2.stderr.strip()}")
        return False

    _print(f"  Clonando {repo_url} …")
    r = subprocess.run(
        ["git", "clone", repo_url, str(repo_dir)],
        capture_output=True,
        text=True,
    )
    if r.returncode == 0:
        ok("Repositorio clonado")
        return True
    fail(f"No se pudo clonar: {r.stderr.strip()}")
    return False


# ── Environment setup (delegate to existing project scripts) ────────────────

def _setup_environment(repo_dir: Path) -> bool:
    heading("Preparando el entorno Python")

    is_macos = platform.system() == "Darwin"

    # Find a Python 3.11+. (On Apple Silicon we prefer Homebrew's arm64
    # interpreter so compiled extensions load with the correct architecture.)
    py = None
    candidates = []
    if is_macos and platform.machine() == "arm64":
        candidates = [
            "/opt/homebrew/bin/python3.13",
            "/opt/homebrew/bin/python3.12",
            "/opt/homebrew/bin/python3.11",
            "python3",
        ]
    else:
        candidates = ["python3", "python3.13", "python3.12", "python3.11", "python"]

    for cand in candidates:
        p = shutil.which(cand) or (cand if os.path.exists(cand) else None)
        if not p:
            continue
        try:
            r = subprocess.run(
                [p, "-c", "import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 11) else 1)"],
                capture_output=True,
            )
            if r.returncode == 0:
                py = p
                break
        except Exception:
            continue

    if not py:
        fail("No se encontró Python 3.11+. Instálalo y reintenta.")
        _print("    https://www.python.org/downloads/  (o: brew install python@3.11)")
        return False

    ok(f"Python: {py}")

    venv_dir = repo_dir / "venv"
    venv_py = venv_dir / ("Scripts/python.exe" if platform.system() == "Windows" else "bin/python3")

    if not venv_py.exists():
        heading("Creando entorno virtual (venv)…")
        r = subprocess.run([py, "-m", "venv", str(venv_dir)], capture_output=True, text=True)
        if r.returncode != 0:
            fail(f"Fallo al crear venv: {r.stderr.strip()}")
            return False
        ok("venv creado")

    pip = venv_dir / ("Scripts/pip.exe" if platform.system() == "Windows" else "bin/pip")
    heading("Instalando dependencias de Python (puede tardar unos minutos)…")
    r = subprocess.run(
        [str(pip), "install", "-r", str(repo_dir / "requirements.txt")],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        fail(f"Fallo al instalar dependencias: {r.stderr.strip()[-500:]}")
        return False
    ok("Dependencias instaladas")

    heading("Preparando Odysseus (setup idempotente)…")
    env = dict(os.environ)
    env["ODYSSEUS_SKIP_RUN_HINT"] = "1"
    r = subprocess.run(
        [str(venv_py), str(repo_dir / "setup.py")],
        capture_output=True,
        text=True,
        env=env,
    )
    if r.returncode != 0:
        warn("setup.py reportó un error (puede no ser fatal).")
    else:
        ok("Setup completado")

    return True


def _start_server(repo_dir: Path) -> None:
    heading("Arrancando Odysseus…")
    venv_py = repo_dir / ("Scripts/python.exe" if platform.system() == "Windows" else "bin/python3")
    port = read_env(repo_dir / ".env").get("APP_PORT", "7860")
    host = read_env(repo_dir / ".env").get("APP_BIND", "127.0.0.1")
    url_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    url = f"http://{url_host}:{port}"

    _print(f"{C['bold']}  Abre en tu navegador: {url}{C['reset']}")
    _print(f"  (pulsa Ctrl+C para detener el servidor)\n")

    # Launch uvicorn in the foreground so Ctrl+C stops it cleanly.
    try:
        subprocess.run([str(venv_py), "-m", "uvicorn", "app:app", "--host", host, "--port", port])
    except KeyboardInterrupt:
        _print(f"\n{C['yellow']}Servidor detenido.{C['reset']}")


# ── Main ────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Odysseus self-running installer")
    parser.add_argument("--non-interactive", action="store_true", help="use existing .env, skip wizard")
    parser.add_argument("--repo-url", default=None, help="override upstream repo URL")
    parser.add_argument("--no-pyenv", action="store_true", help="skip venv/dependency setup")
    parser.add_argument("--no-schedule", action="store_true", help="skip daily task registration")
    parser.add_argument("--no-start", action="store_true", help="skip server launch")
    parser.add_argument("--no-shortcut", action="store_true", help="skip desktop shortcut creation")
    args = parser.parse_args()

    repo_url = args.repo_url or REPO_URL
    # Install target: the directory containing this installer/ directory.
    repo_dir = Path(__file__).resolve().parent.parent
    env_path = repo_dir / ".env"

    heading("Odysseus — instalador auto-ejecutable")
    _print(f"  Plataforma: {platform.system()} ({platform.machine()})")
    _print(f"  Programador diario: {schedulers.detect_scheduler()}")
    _print(f"  Directorio de instalación: {repo_dir}\n")

    # Pre-checks.
    heading("Verificando dependencias del sistema")
    for cmd in ("git",):
        if _has(cmd):
            ok(f"{cmd} disponible")
        else:
            fail(f"{cmd} no encontrado. Instálalo y reintenta.")
            _print("    https://git-scm.com/downloads")
            return 1

    if not args.no_pyenv:
        # Python check happens inside _setup_environment, but surface it early.
        pyfound = False
        for cand in ("python3", "python3.13", "python3.12", "python3.11", "python"):
            if _has(cand):
                pyfound = True
                break
        if not pyfound and platform.system() != "Windows":
            fail("Python 3.11+ no encontrado.")
            _print("    https://www.python.org/downloads/")
            return 1

    # 1. Repo.
    if not _pull_or_clone(repo_dir, repo_url):
        return 1

    # 2. Credentials wizard (unless --non-interactive or answers already saved).
    updates: Dict[str, str] = {}
    if args.non_interactive:
        heading("Modo no interactivo — usando .env existente (o valores por defecto)")
        existing = read_env(env_path)
        for entry in CREDENTIALS:
            key = entry["key"]
            if key in existing:
                updates[key] = existing[key]
            elif entry.get("default", "") != "":
                updates[key] = entry["default"]
    else:
        updates = run_wizard(env_path)

    # 3. Write .env (preserving what's there).
    if updates:
        heading("Generando .env")
        write_env(env_path, updates, comment_header="Configurado por el instalador de Odysseus")
        ok(f".env escrito en {env_path} (permisos 600)")

    # 4. Environment setup.
    if not args.no_pyenv:
        if not _setup_environment(repo_dir):
            return 1

    # 5. Daily self-update task.
    if not args.no_schedule:
        heading("Registrando auto-actualización diaria")
        success, detail = schedulers.register_daily_update(repo_dir)
        if success:
            ok(detail)
        else:
            warn(detail)
        # Ensure scripts are executable on POSIX.
        if platform.system() != "Windows":
            for script in ("self_update.sh",):
                p = repo_dir / "installer" / script
                if p.exists():
                    p.chmod(0o755)

    # 6. Desktop shortcut (unless disabled).
    if not args.no_shortcut:
        heading("Creando acceso directo en el escritorio")
        ok_short, detail = shortcuts.create_shortcut(repo_dir)
        if ok_short:
            ok(detail)
        else:
            warn(detail)

    # 7. First launch.
    if not args.no_start:
        _start_server(repo_dir)
    else:
        ok("Instalación completada (--no-start). Arranca manualmente cuando quieras.")

    # Clear progress state on a full successful run.
    if _STATE_PATH.exists():
        try:
            _STATE_PATH.unlink()
        except OSError:
            pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())