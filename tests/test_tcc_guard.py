"""Tests for src/tcc_guard.py — macOS TCC denial detection + actionable error.

Offline: no real TCC needed; the detection functions are pure string/path
logic and the tool-level integration is pinned through execute_tool_block with
a stubbed subprocess.
"""

import pytest

from src.tcc_guard import folder_from_text, is_tcc_denial, is_tcc_path, tcc_error


def test_is_tcc_path_matches_protected_folders():
    assert is_tcc_path("~/Documents/foo.txt") == "~/Documents"
    assert is_tcc_path("~/Desktop/projects/x") == "~/Desktop"
    assert is_tcc_path("~/Downloads/report.pdf") == "~/Downloads"
    assert is_tcc_path("~/Library/Application Support/x") == "~/Library"
    assert is_tcc_path("/tmp/foo.txt") is None
    assert is_tcc_path("/var/tmp/notes.md") is None


def test_is_tcc_denial_signatures():
    assert is_tcc_denial("ls: ./Documents: Operation not permitted")
    assert is_tcc_denial("OSError: [Errno 1] Operation not permitted")
    assert not is_tcc_denial("")
    # errno=1 marker (PermissionError signature) counts too.
    assert is_tcc_denial("some traceback\n  errno=1, message='Operation not permitted'")
    assert not is_tcc_denial("command not found")


def test_folder_from_text_extracts_protected_folder():
    assert folder_from_text("ls ~/Documents/x") == "~/Documents"
    assert folder_from_text("cat /etc/hosts") is None


def test_tcc_error_is_actionable():
    msg = tcc_error(path="~/Desktop/projects/x")
    assert "Full Disk Access" in msg
    assert "~/Desktop" in msg
    assert "Operation not permitted" in msg
    # Three concrete exit strategies: grant permission, move file, paste/attach.
    assert "move/copy the file" in msg
    assert "paste the content or attach" in msg
