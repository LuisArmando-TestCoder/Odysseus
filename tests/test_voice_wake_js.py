"""Pin the always-listening wake phrase in static/js/voiceWake.js.

While Advanced Voice Mode is off, a browser STT loop listens to a constant
stream; "advanced voice mode" anywhere in it fires onWake() once and the
listener shuts down. When voice mode is deactivated, resume() listens again.
The recognised stream is bounded (rolling WAKE_WINDOW chars).

Driven through `node --input-type=module` with the module inlined (it has no
imports), and a fake SpeechRecognition + manual clock/timers.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_SRC_PATH = _REPO / "static" / "js" / "voiceWake.js"
_HAS_NODE = shutil.which("node") is not None
_SRC = _SRC_PATH.read_text(encoding="utf-8") if _SRC_PATH.exists() else ""

pytestmark = pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")

_HARNESS = r"""
let T = 10000;
const timers = [];
const instances = [];
class FakeRec {
  constructor() { this.started = false; this.aborted = false; instances.push(this); }
  start() { this.started = true; }
  stop() { this.aborted = true; }
  abort() { this.aborted = true; }
  say(text, isFinal = true) {
    const res = [{ transcript: text }]; res.isFinal = isFinal;
    this.onresult?.({ resultIndex: 0, results: [res] });
  }
}
const live = () => instances.filter(r => r.started && !r.aborted).at(-1);
const tick = (ms) => { T += ms; for (const t of timers.splice(0)) { if (t.at <= T) t.fn(); else timers.push(t); } };
let wakes = 0; const states = [];
const make = (extra = {}) => createWakeListener({
  Recognition: FakeRec, now: () => T,
  setTimer: (fn, ms) => { const t = { fn, at: T + ms }; timers.push(t); return t; },
  clearTimer: (t) => { const i = timers.indexOf(t); if (i >= 0) timers.splice(i, 1); },
  onWake: () => wakes++, onState: (s) => states.push(s), log: () => {}, ...extra,
});
"""


def _run(body: str):
    js = _SRC + "\n" + _HARNESS + "\n" + body
    proc = subprocess.run(
        ["node", "--input-type=module"], input=js, capture_output=True,
        text=True, encoding="utf-8", cwd=str(_REPO), timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip())


def test_phrase_detected_anywhere_and_variants():
    out = _run("""
    console.log(JSON.stringify([
      hasWake('ok so turn on advanced voice mode please'),
      hasWake('Advanced Voice Mode!'),
      hasWake('advance voice mode'),
      hasWake('advanced voicemode'),
      hasWake('advanced voice'),
      hasWake('voice mode advanced'),
      hasWake('the disadvanced voice modem'),
    ]));""")
    assert out == [True, True, True, True, False, False, False]


def test_window_is_bounded_and_catches_split_phrase():
    out = _run("""
    const w = createWakeWindow(40);
    const a = w.commit('blah '.repeat(500));
    const len = w.text.length;
    const b = w.commit('turn on advanced');
    const c = w.commit('voice mode');
    console.log(JSON.stringify([a, len <= 40, b, c]));""")
    assert out == [False, True, False, True]


def test_fires_once_then_shuts_down_then_resumes():
    out = _run("""
    const w = make(); w.setEnabled(true); tick(2000);
    const r1 = live(); r1.say('hello there');
    const before = wakes;
    r1.say('hey alfred advanced voice mode now');
    const afterFire = [wakes, w.state, r1.aborted, live() === undefined];
    r1.say('advanced voice mode');          // stale recogniser: ignored
    tick(60000);                            // no restart while voice mode runs
    const stillOff = live() === undefined && wakes === 1;
    w.resume();                             // voice mode deactivated
    const r2 = live(); tick(2000);
    r2.say('advanced voice mode');
    console.log(JSON.stringify([before, ...afterFire, stillOff, !!r2 && r2 !== r1, wakes, w.state]));""")
    assert out == [0, 1, "paused", True, True, True, True, 2, "paused"]


def test_interim_results_trigger_fast():
    out = _run("""
    const w = make(); w.setEnabled(true); tick(2000);
    live().say('advanced voice mo', false);
    const a = wakes;
    live().say('advanced voice mode', false);
    console.log(JSON.stringify([a, wakes]));""")
    assert out == [0, 1]


def test_constant_stream_restarts_after_browser_ends_session():
    out = _run("""
    const w = make(); w.setEnabled(true);
    const r1 = live(); r1.started = false; r1.onend();   // Chrome timed out
    const gap = live() === undefined;
    tick(300);
    const r2 = live();
    console.log(JSON.stringify([gap, !!r2 && r2 !== r1, w.state]));""")
    assert out == [True, True, "listening"]


def test_grace_window_ignores_stale_phrase_right_after_resume():
    out = _run("""
    const w = make(); w.setEnabled(true); tick(2000);
    live().say('advanced voice mode');      // wake #1
    w.resume();
    live().say('advanced voice mode');      // trailing echo within grace
    const a = wakes;
    tick(2000);
    live().say('advanced voice mode');
    console.log(JSON.stringify([a, wakes]));""")
    assert out == [1, 2]


def test_pause_while_voice_active_and_permission_denied():
    out = _run("""
    const w = make(); w.pause(); w.setEnabled(true);
    const paused = [w.state, live() === undefined];
    w.resume(); const r = live();
    r.onerror({ error: 'not-allowed' });
    r.onend?.(); tick(60000);
    console.log(JSON.stringify([...paused, w.state, live() === undefined]));""")
    assert out == ["paused", True, "denied", True]


def test_disable_and_unsupported():
    out = _run("""
    const w = make(); w.setEnabled(true); w.setEnabled(false);
    const off = [w.state, live() === undefined];
    const u = createWakeListener({ Recognition: null }); u.setEnabled(true);
    console.log(JSON.stringify([...off, u.state, u.supported]));""")
    assert out == ["off", True, "unsupported", False]


def test_fuzzy_misheard_and_spanish_forms():
    out = _run("""
    console.log(JSON.stringify([
      hasWake('advance boys mode'),
      hasWake('advanced voice mood'),
      hasWake('advanzed voice mode'),
      hasWake('Advanced Voice Mod.'),
      hasWake('activa el modo de voz avanzado'),
      hasWake('modo voz avanzada'),
      hasWake('advanced users mode'),
      hasWake('add voice mode'),
      hasWake('advanced voice is great'),
    ]));""")
    assert out == [True, True, True, True, True, True, False, False, False]


def test_recogniser_defaults_to_english_and_reports_errors():
    out = _run("""
    const w = make({ log: () => {} }); w.setEnabled(true);
    const lang = live().lang;
    for (let i = 0; i < 3; i++) { const r = live(); r.onerror({ error: 'network' }); r.started = false; r.onend(); tick(10000); }
    const errState = w.state;
    tick(2000); live().say('hello');        // stream healthy again
    console.log(JSON.stringify([lang, errState, w.state, w.diag.lastError, w.diag.heard.at(-1)]));""")
    assert out == ["en-US", "error", "listening", "network", "hello"]


def test_switches_to_server_stt_after_network_errors():
    out = _run("""
    class ServerRec extends FakeRec { constructor() { super(); this.server = true; } }
    const w = make({ Fallback: ServerRec }); w.setEnabled(true);
    const first = [w.engine, !!live().server];
    live().onerror({ error: 'network' }); const r1 = live(); r1.started = false; r1.onend(); tick(1000);
    const afterOne = w.engine;                 // one blip: keep trying the browser
    live().onerror({ error: 'network' });     // second in a row → switch
    const r = live();
    tick(2000); r.say('advanced voice mode');
    console.log(JSON.stringify([...first, afterOne, w.engine, !!r.server, wakes, w.state]));""")
    assert out == ["browser", False, "browser", "server", True, 1, "paused"]


def test_brave_style_service_not_allowed_switches_immediately():
    out = _run("""
    class ServerRec extends FakeRec { constructor() { super(); this.server = true; } }
    const w = make({ Fallback: ServerRec }); w.setEnabled(true);
    live().onerror({ error: 'service-not-allowed' });
    console.log(JSON.stringify([w.engine, !!live()?.server, w.state]));""")
    assert out == ["server", True, "listening"]


def test_no_browser_engine_uses_server_directly():
    out = _run("""
    class ServerRec extends FakeRec { constructor() { super(); this.server = true; } }
    const w = make({ Recognition: null, Fallback: ServerRec }); w.setEnabled(true);
    console.log(JSON.stringify([w.engine, !!live()?.server, w.state]));""")
    assert out == ["server", True, "listening"]
