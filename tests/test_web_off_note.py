from pathlib import Path

def test_web_off_note_present():
    s = (Path(__file__).resolve().parent.parent / "src" / "agent_loop.py").read_text()
    assert "WEB ACCESS IS SWITCHED OFF" in s
    assert "Never say you lack internet" in s
