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

// "advanced voice mode" + the ways STT tends to spell it, and the Spanish
// "modo de voz avanzado". Exact forms first; a fuzzy per-word check catches
// accented / misheard versions ("advance boys mode", "advanced voice mood").
const WAKE_RE = /\badvance[ds]?\s+voice\s*modes?\b|\bmodo\s+(de\s+)?voz\s+avanzad[oa]\b/;
const VOICE_ALIASES = new Set(['voice', 'voices', 'voic', 'vois', 'voise', 'boys', 'boice', 'boise', 'choice', 'voz']);
const MODE_ALIASES = new Set(['mode', 'modes', 'mod', 'mood', 'mold', 'modo', 'mote', 'moe', 'node']);

export function editDistance(a, b) {
  if (a === b) return 0;
  const m = a.length, n = b.length;
  if (!m || !n) return m || n;
  let prev = Array.from({ length: n + 1 }, (_, j) => j);
  for (let i = 1; i <= m; i++) {
    const cur = [i];
    for (let j = 1; j <= n; j++) {
      cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
    }
    prev = cur;
  }
  return prev[n];
}

const _isAdvanced = (w) => w.length >= 6 && /^[ae]/.test(w) && editDistance(w, 'advanced') <= 3;
const _isVoice = (w) => VOICE_ALIASES.has(w) || (w.length >= 4 && editDistance(w, 'voice') <= 1);
const _isMode = (w) => MODE_ALIASES.has(w) || (w.length >= 3 && editDistance(w, 'mode') <= 1);

function _matchNorm(norm) {
  if (WAKE_RE.test(norm)) return true;
  const w = norm.replace(/voicemode/g, 'voice mode').split(' ');
  for (let i = 0; i + 2 < w.length; i++) {
    if (_isAdvanced(w[i]) && _isVoice(w[i + 1]) && _isMode(w[i + 2])) return true;
  }
  return false;
}

export function normalizeWake(text) {
  return String(text || '')
    .toLowerCase()
    .normalize('NFD').replace(/[̀-ͯ]/g, '')
    .replace(/[^a-z0-9]+/g, ' ')
    .trim();
}

export function hasWake(text) {
  return _matchNorm(normalizeWake(text));
}

// Rolling, size-capped window over the recognised stream.
export function createWakeWindow(max = WAKE_WINDOW) {
  let buf = '';
  return {
    // Commit final text; returns true if the phrase is now in the window.
    commit(text) {
      buf = (buf + ' ' + normalizeWake(text)).trim();
      if (buf.length > max) buf = buf.slice(buf.length - max);
      return _matchNorm(buf);
    },
    // Check final window + current interim (not committed) without storing it.
    peek(interim) {
      return _matchNorm((buf + ' ' + normalizeWake(interim)).trim());
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
  let Recognition = opts.Recognition !== undefined
    ? opts.Recognition
    : (g.SpeechRecognition || g.webkitSpeechRecognition || null);
  // Server-backed recogniser to switch to when the browser's speech service
  // is unreachable (Chrome error "network") or absent (Brave/Chromium).
  let Fallback = opts.Fallback || null;
  let engine = Recognition ? (opts.engine || 'browser') : 'none';
  if (!Recognition && Fallback) { Recognition = Fallback; Fallback = null; engine = 'server'; }
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
  let errStreak = 0;
  const log = opts.log || ((...a) => { try { console.info('[wake]', ...a); } catch (e) { /* ignore */ } });
  const diag = { lang: '', heard: [], lastError: '', errors: 0, restarts: 0, wakes: 0 };
  let svcErrors = 0;

  function switchToFallback(why) {
    if (!Fallback) return false;
    log('browser speech service unavailable (' + why + ') — switching to server STT');
    Recognition = Fallback; Fallback = null; engine = 'server';
    errStreak = 0; svcErrors = 0; backoff = BACKOFF_MIN_MS;
    kill();
    if (state === 'error') setState('listening');
    spawn();
    return true;
  }
  const noteHeard = (t) => { diag.heard.push(t); if (diag.heard.length > 8) diag.heard.shift(); };

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
    diag.wakes++;
    log('wake phrase detected → activating Advanced Voice Mode');
    setState('paused');
    try { opts.onWake?.(); } catch (e) { console.error('[wake] onWake failed', e); }
  }

  function scheduleRestart() {
    if (!enabled || held || restartT) return;
    const wait = errStreak >= 3 ? Math.max(backoff, 3000) : backoff;
    diag.restarts++;
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
      // The wake phrase is English, so recognise English regardless of the
      // browser's UI language (a Spanish recogniser never spells it right).
      r.lang = opts.lang || 'en-US';
      diag.lang = r.lang;
    } catch (e) { setState('unsupported'); return; }

    r.onresult = (ev) => {
      if (my !== gen) return;
      backoff = BACKOFF_MIN_MS;            // healthy stream
      errStreak = 0; svcErrors = 0;
      if (state !== 'listening') setState('listening');
      if (now() - armedAt < RESUME_GRACE_MS) return;
      let interim = '';
      for (let i = ev.resultIndex; i < ev.results.length; i++) {
        const res = ev.results[i];
        const t = res[0]?.transcript || '';
        if (res.isFinal) {
          noteHeard(t.trim());
          if (opts.verbose !== false) log('heard:', JSON.stringify(t.trim()));
          if (win.commit(t)) { fire(); return; }
        }
        else interim += ' ' + t;
      }
      if (interim && win.peek(interim)) fire();
    };
    r.onerror = (ev) => {
      if (my !== gen) return;
      const err = ev?.error || '';
      diag.lastError = err; diag.errors++;
      if (err === 'no-speech' || err === 'aborted') return;   // normal silence / our own abort
      log('recognition error:', err, ev?.message || '');
      if ((err === 'network' || err === 'service-not-allowed' || err === 'language-not-supported') && Fallback) {
        if (++svcErrors >= 2 || err !== 'network') { switchToFallback(err); return; }
      }
      if (err === 'not-allowed' || err === 'service-not-allowed') {
        kill(); enabled = false; setState('denied');
        return;
      }
      // network / audio-capture / language-not-supported: keep retrying, but
      // show it — after a few in a row the indicator says it isn't hearing.
      if (++errStreak >= 3) setState('error');
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
      if (state !== 'error') setState('listening');
      if (diag.restarts === 0 && diag.wakes === 0) log('listening for "advanced voice mode" (lang ' + r.lang + ')');
    } catch (e) {
      rec = null;
      scheduleRestart();
    }
  }

  return {
    useServer() { return switchToFallback('requested'); },
    get state() { return state; },
    get supported() { return !!Recognition; },
    get enabled() { return enabled; },
    get diag() { return { ...diag, heard: [...diag.heard], buffer: win.text, state, engine }; },
    get engine() { return engine; },
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

export default { createWakeListener, hasWake, normalizeWake, createWakeWindow, editDistance, WAKE_WINDOW };
