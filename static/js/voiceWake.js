// voiceWake.js — always-listening wake phrase for Advanced Voice Mode.
//
// A cheap browser speech-to-text loop (Web Speech API, continuous + interim
// results) listens to a constant stream of words. The moment "advanced voice
// mode" appears ANYWHERE in that stream it fires onWake() once and shuts
// itself down, handing the mic to GPT-Live. When Advanced Voice Mode is
// deactivated, the caller resumes it and it listens again.
//
//   idle chat ──► [wake: listening] ──"…advanced voice mode…"──► onWake()
//                       ▲                                           │
//                       └──── resume() ◄── voice mode ends ◄── pause()
//
// The stream is BOUNDED: only the last WAKE_WINDOW characters of recognised
// text are kept (enough to catch the phrase split across result chunks), so an
// hours-long listen never grows memory. No imports — the test suite inlines it.

export const WAKE_WINDOW = 160;
const RESUME_GRACE_MS = 1200;     // ignore late results right after resume
const BACKOFF_MIN_MS = 250;
const BACKOFF_MAX_MS = 8000;

// "advanced voice mode" + the ways STT tends to spell it.
const WAKE_RE = /\badvance[ds]?\s+voice\s*modes?\b/;

export function normalizeWake(text) {
  return String(text || '')
    .toLowerCase()
    .normalize('NFD').replace(/[̀-ͯ]/g, '')
    .replace(/[^a-z0-9]+/g, ' ')
    .trim();
}

export function hasWake(text) {
  return WAKE_RE.test(normalizeWake(text));
}

// Rolling, size-capped window over the recognised stream.
export function createWakeWindow(max = WAKE_WINDOW) {
  let buf = '';
  return {
    // Commit final text; returns true if the phrase is now in the window.
    commit(text) {
      buf = (buf + ' ' + normalizeWake(text)).trim();
      if (buf.length > max) buf = buf.slice(buf.length - max);
      return WAKE_RE.test(buf);
    },
    // Check final window + current interim (not committed) without storing it.
    peek(interim) {
      return WAKE_RE.test((buf + ' ' + normalizeWake(interim)).trim());
    },
    reset() { buf = ''; },
    get text() { return buf; },
  };
}

/**
 * createWakeListener({ onWake, onState, Recognition, lang, now, setTimer, clearTimer })
 * States: 'off' | 'listening' | 'paused' | 'unsupported' | 'denied'
 */
export function createWakeListener(opts = {}) {
  const g = typeof window !== 'undefined' ? window : globalThis;
  const Recognition = opts.Recognition !== undefined
    ? opts.Recognition
    : (g.SpeechRecognition || g.webkitSpeechRecognition || null);
  const now = opts.now || (() => Date.now());
  const setTimer = opts.setTimer || ((fn, ms) => setTimeout(fn, ms));
  const clearTimer = opts.clearTimer || ((t) => clearTimeout(t));
  const win = createWakeWindow(opts.window || WAKE_WINDOW);

  let state = 'off';
  let rec = null;
  let enabled = false;     // user wants it on
  let held = false;        // voice mode owns the mic
  let gen = 0;             // invalidates callbacks from a stale recogniser
  let restartT = null;
  let backoff = BACKOFF_MIN_MS;
  let armedAt = 0;

  const setState = (s) => {
    if (state === s) return;
    state = s;
    try { opts.onState?.(s); } catch (e) { /* ignore */ }
  };

  function kill() {
    gen++;
    if (restartT) { clearTimer(restartT); restartT = null; }
    const r = rec; rec = null;
    if (r) {
      r.onresult = r.onerror = r.onend = null;
      try { r.abort(); } catch (e) { try { r.stop(); } catch (e2) { /* ignore */ } }
    }
  }

  function fire() {
    kill();
    win.reset();
    held = true;
    setState('paused');
    try { opts.onWake?.(); } catch (e) { console.error('[wake] onWake failed', e); }
  }

  function scheduleRestart() {
    if (!enabled || held || restartT) return;
    const wait = backoff;
    backoff = Math.min(backoff * 2, BACKOFF_MAX_MS);
    restartT = setTimer(() => { restartT = null; spawn(); }, wait);
  }

  function spawn() {
    if (!enabled || held || rec) return;
    if (!Recognition) { setState('unsupported'); return; }
    const my = ++gen;
    let r;
    try {
      r = new Recognition();
      r.continuous = true;
      r.interimResults = true;
      r.maxAlternatives = 1;
      r.lang = opts.lang || (g.navigator?.language) || 'en-US';
    } catch (e) { setState('unsupported'); return; }

    r.onresult = (ev) => {
      if (my !== gen) return;
      backoff = BACKOFF_MIN_MS;            // healthy stream
      if (now() - armedAt < RESUME_GRACE_MS) return;
      let interim = '';
      for (let i = ev.resultIndex; i < ev.results.length; i++) {
        const res = ev.results[i];
        const t = res[0]?.transcript || '';
        if (res.isFinal) { if (win.commit(t)) { fire(); return; } }
        else interim += ' ' + t;
      }
      if (interim && win.peek(interim)) fire();
    };
    r.onerror = (ev) => {
      if (my !== gen) return;
      const err = ev?.error || '';
      if (err === 'not-allowed' || err === 'service-not-allowed') {
        kill(); enabled = false; setState('denied');
      }
      // no-speech / network / aborted / audio-capture → onend restarts.
    };
    r.onend = () => {
      if (my !== gen) return;
      rec = null;
      scheduleRestart();                    // Chrome ends sessions on its own; keep it constant
    };
    try {
      r.start();
      rec = r;
      armedAt = now();
      setState('listening');
    } catch (e) {
      rec = null;
      scheduleRestart();
    }
  }

  return {
    get state() { return state; },
    get supported() { return !!Recognition; },
    get enabled() { return enabled; },
    setEnabled(on) {
      enabled = !!on;
      if (!enabled) { kill(); setState('off'); return; }
      if (!Recognition) { setState('unsupported'); return; }
      if (held) { setState('paused'); return; }
      backoff = BACKOFF_MIN_MS;
      spawn();
    },
    // Voice mode is taking the mic.
    pause() {
      held = true;
      kill();
      if (enabled) setState('paused');
    },
    // Voice mode ended — listen for the phrase again (fresh, empty window).
    resume() {
      held = false;
      win.reset();
      if (!enabled) return;
      backoff = BACKOFF_MIN_MS;
      spawn();
    },
    // test hook
    _window: win,
  };
}

export default { createWakeListener, hasWake, normalizeWake, createWakeWindow, WAKE_WINDOW };
