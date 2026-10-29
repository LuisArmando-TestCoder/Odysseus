"""Voice ↔ chat bridge: the live voice session gets the WHOLE chat context
(goal, summaries, every request, latest exchanges) without having to ask."""
import sys
import types

import pytest

import services.voice.memory as vm


def _msg(role, content):
    return {"role": role, "content": content}


@pytest.fixture
def fake_goal(monkeypatch):
    mod = types.ModuleType("src.goal")
    holder = {"goal": None}
    mod.get_goal_for_session = lambda sid, owner=None: holder["goal"]
    monkeypatch.setitem(sys.modules, "src.goal", mod)
    return holder


def _with_history(monkeypatch, hist):
    monkeypatch.setattr(vm, "_chat_history", lambda sid, owner: hist)


def test_digest_has_goal_summary_index_and_recent(monkeypatch, fake_goal):
    fake_goal["goal"] = {"objective": "Map Acme's shell companies", "status": "active", "case_id": "case_0123456789"}
    hist = [_msg("system", "[Conversation summary]\nEarlier we set up the Intel Board.")]
    for i in range(1, 31):
        hist.append(_msg("user", f"request number {i} about topic-{i}"))
        hist.append(_msg("assistant", f"answer {i} " + "detail " * 40))
    _with_history(monkeypatch, hist)
    d = vm.chat_context_digest("s1", "alice", budget=6000)
    assert len(d) <= 6000
    assert "[Chat goal — active] Map Acme's shell companies" in d and "case_0123456789" in d
    assert "Earlier we set up the Intel Board." in d
    assert "Everything the user asked in this chat (30 requests" in d
    # Oldest and newest requests are both visible, even when the middle is elided.
    assert "1. request number 1 about topic-1" in d
    assert "30. request number 30 about topic-30" in d
    # The newest exchange is carried in detail.
    assert "answer 30" in d
    assert "Most recent exchanges" in d


def test_index_elides_middle_when_over_budget(monkeypatch, fake_goal):
    hist = []
    for i in range(1, 201):
        hist.append(_msg("user", f"ask {i} " + "x" * 150))
    _with_history(monkeypatch, hist)
    d = vm.chat_context_digest("s1", "alice", budget=8000)
    assert "more earlier requests" in d
    assert "1. ask 1" in d and "200. ask 200" in d


def test_code_and_tool_blocks_are_compacted(monkeypatch, fake_goal):
    big = "```python\n" + "print('x')\n" * 200 + "```"
    _with_history(monkeypatch, [_msg("user", "run it"), _msg("assistant", "Here:\n" + big + "\nDone.")])
    d = vm.chat_context_digest("s1", "alice")
    assert "[code/tool block" in d and "print('x')" not in d and "Done." in d
    small = "```bash\nls\n```"
    _with_history(monkeypatch, [_msg("assistant", "Ran " + small)])
    assert "ls" in vm.chat_context_digest("s1", "alice")


def test_empty_unknown_or_broken_chat_is_empty(monkeypatch, fake_goal):
    _with_history(monkeypatch, None)
    assert vm.chat_context_digest("s1", "alice") == ""
    _with_history(monkeypatch, [])
    assert vm.chat_context_digest("s1", "alice") == ""
    assert vm.chat_context_digest(None, "alice") == ""

    def boom(sid, owner):
        raise RuntimeError("db down")
    monkeypatch.setattr(vm, "_chat_history", boom)
    assert vm.chat_context_digest("s1", "alice") == ""


def test_newest_turn_always_kept_even_if_huge(monkeypatch, fake_goal):
    _with_history(monkeypatch, [_msg("user", "q"), _msg("assistant", "z" * 50000)])
    d = vm.chat_context_digest("s1", "alice", budget=3000)
    assert "Chat agent: zzz" in d and len(d) <= 3000


def test_chat_history_is_owner_scoped(monkeypatch):
    class Sess:
        owner = "alice"
        history = [_msg("user", "secret")]

    class Mgr:
        def get_session(self, sid):
            return Sess() if sid == "s1" else None

    core_mod = types.ModuleType("core")
    models_mod = types.ModuleType("core.models")
    models_mod._session_manager = Mgr()
    core_mod.models = models_mod
    monkeypatch.setitem(sys.modules, "core", core_mod)
    monkeypatch.setitem(sys.modules, "core.models", models_mod)
    assert vm._chat_history("s1", "alice") == Sess.history
    assert vm._chat_history("s1", "mallory") is None      # never another user's chat
    assert vm._chat_history("nope", "alice") is None
    assert vm._chat_history(None, "alice") is None


def test_realtime_session_uses_full_digest():
    """The live-session instructions are built from chat_context_digest with a
    generous budget, not the old 6-turn / 4000-char spine."""
    import pathlib
    src = pathlib.Path(__file__).resolve().parents[1].joinpath("routes", "realtime_routes.py").read_text()
    assert "chat_context_digest(_chat_sid, _owner, budget=_budget)" in src
    assert "VOICE_CHAT_CONTEXT_CHARS = 12000" in src
    assert "_chat_ctx[:4000]" not in src
