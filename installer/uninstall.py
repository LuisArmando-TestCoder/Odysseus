#!/usr/bin/env python3
"""Odysseus — uninstall what the installer set up.

  python installer/uninstall.py            stop Odysseus, remove the update
                                           schedule, start-at-login and the
                                           shortcuts. Your code, .env and
                                           data/ stay where they are.
  python installer/uninstall.py --keep-shortcuts
  python installer/uninstall.py --yes      don't ask for confirmation

To remove everything afterwards, delete the install folder yourself (it holds
your data in data/ and your settings in .env — back them up first).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import launch  # noqa: E402
import schedulers  # noqa: E402
import shortcuts  # noqa: E402
import updater  # noqa: E402


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Uninstall Odysseus' OS integration")
    p.add_argument("--keep-shortcuts", action="store_true")
    p.add_argument("--yes", "-y", action="store_true")
    a = p.parse_args(argv)
    repo = updater.REPO_DIR

    if not a.yes:
        try:
            ans = input(f"Remove Odysseus' schedule, autostart and shortcuts for {repo}? [y/N] ").strip().lower()
        except EOFError:
            ans = ""
        if not ans.startswith(("y", "s")):
            print("Nothing changed.")
            return 1

    launch.cmd_stop()
    for label, (ok, detail) in (("update schedule", schedulers.unregister_update(repo)),
                                ("start at login", schedulers.unregister_autostart(repo))):
        print(f"  {'✓' if ok else '⚠'} {label}: {detail}")
    if not a.keep_shortcuts:
        for path in shortcuts.remove_shortcuts(repo):
            print(f"  ✓ removed {path}")
    marker = updater.load_marker()
    if marker:
        marker.update({"schedule": "none", "autostart": False, "uninstalled": True})
        updater.save_marker(marker)
    print(f"\nDone. Your code, settings (.env) and data (data/) are still in {repo}.")
    print("Delete that folder to remove Odysseus completely.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
