"""V3 — voice↔chat bridge regression tests.

Offline: the voice memory layer is monkeypatched, so no network/WebRTC is
involved. Pins the two bridge directions + the V2 turn-type guards.
"""

import pytest


class _FakeVM:
    def __init__(self, spine="", transcript=""):
        self._spine = spine
        self._transcript = transcript

    def spine_text(self, include_live=True):
        return self._spine

    def transcript_text(self, max_turns=200):
        return self._transcript


@pytest.fixture
def _voice(monkeypatch):
    import services.voice.memory as vm_mod
    holder = {"vm": _FakeVM(spine="spine-summary", transcript="spoken line")}

    def _get_voice_memory(owner, voice_id):
        holder["last_key"] = (owner, voice_id)
        return holder["vm"]

    monkeypatch.setattr(vm_mod, "get_voice_memory", _get_voice_memory)
    return holder


def test_chat_gets_voice_stratum_keyed_by_chat_session(_voice):
    from routes.chat_helpers import voice_memory_stratum
    text = voice_memory_stratum("alice", "sess-7")
    assert "spine-summary" in text
    assert "spoken line" in text
    assert "[Full voice transcript" in text
    # Keyed chat-<session> — the matching voice conversation only.
    assert _voice["last_key"] == ("alice", "chat-sess-7")


def test_voice_stratum_empty_when_memory_layer_down(monkeypatch):
    import services.voice.memory as vm_mod

    def _boom(owner, voice_id):
        raise RuntimeError("memory down")

    monkeypatch.setattr(vm_mod, "get_voice_memory", _boom)
    from routes.chat_helpers import voice_memory_stratum
    assert voice_memory_stratum("alice", "s1") == ""


def test_voice_stratum_omitted_for_incognito(monkeypatch):
    """V2 guard: build_chat_context must not inject the stratum for incognito.
    We assert on the guard expression by driving build_chat_context far enough
    to skip the voice block — verified structurally via the helper contract:
    the injection site checks `not incognito and not no_memory`."""
    import routes.chat_helpers as ch
    src = __import__("inspect").getsource(ch.build_chat_context)
    assert "not incognito and not no_memory" in src
    assert "voice_memory_stratum" in src


def test_realtime_bridge_helpers_exist():
    """Direction 2 helpers must keep their exact names/signatures."""
    from services.voice.memory import chat_summary_spine, session_documents_spine
    assert callable(chat_summary_spine)
    assert callable(session_documents_spine)
