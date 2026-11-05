// voiceWake.js — always-listening wake word ("Alfred") for Advanced Voice Mode.
//
// A cheap browser speech-to-text loop (Web Speech API, continuous + interim
// results) listens to a constant stream of words. The moment the assistant's
// name ("Alfred") appears ANYWHERE in that stream it fires onWake() once and shuts
// itself down, handing the mic to GPT-Live. When Advanced Voice Mode is
// deactivated, the caller resumes it and it listens again.
//
//   idle chat ──► [wake: listening] ──"…Alfred…"──► onWake()
//                       ▲                                           │
//                       └──── resume() ◄── voice mode ends ◄── pause()
//
// The stream is BOUNDED: only the last WAKE_WINDOW characters of recognised
// text are kept (enough to catch the phrase split across result chunks), so an
// hours-long listen never grows memory. No imports — the test suite inlines it.

export const WAKE_WINDOW = 160;
const RESUME_GRACE_MS = 2500;     // ignore late results right after resume (\"gracias, Alfred\" as voice mode closes)
const BACKOFF_MIN_MS = 250;
const BACKOFF_MAX_MS = 8000;

// The wake word is the assistant's NAME ("Alfred" by default; follows the
// voice_assistant_name setting via setWakeName). Said anywhere in the stream,
// in any language, it opens voice mode. STT spells a name many ways, so:
//   • exact word                         alfred
//   • one edit away, same first sound   alfredo, alfret, elfred, alfreds
//   • split in two                      "al fred", "el fred", "all fred"
//   • Mandarin transliteration          阿尔弗雷德 / 阿爾弗雷德 / 艾尔弗雷德
const CJK_NAMES = {
  alfred: ['阿尔弗雷德', '阿爾弗雷德', '艾尔弗雷德', '艾爾弗雷德', '阿弗雷德'],
};
let WAKE_NAME = 'alfred';

export function setWakeName(name) {
  const n = normalizeWake(name).replace(/ /g, '');
  if (n.length >= 3) WAKE_NAME = n;
  return WAKE_NAME;
}
export function getWakeName() { return WAKE_NAME; }

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

const _vowel = (c) => /[aeiou]/.test(c);
function _isName(w, name = WAKE_NAME) {
  if (w === name) return true;
  if (name.length < 5 || w.length < name.length - 1) return false;   // short names: exact only
  const sameStart = w[0] === name[0] || (_vowel(w[0]) && _vowel(name[0]));
  return sameStart && w[1] === name[1] && editDistance(w, name) <= 1;
}

function _matchNorm(norm) {
  const name = WAKE_NAME;
  const glued = norm.replace(/([\u3400-\u9fff\uf900-\ufaff]) (?=[\u3400-\u9fff\uf900-\ufaff])/g, '$1');
  for (const c of CJK_NAMES[name] || []) if (glued.includes(c)) return true;
  const w = norm.split(' ');
  for (let i = 0; i < w.length; i++) {
    if (_isName(w[i], name)) return true;
    // "al fred": a short first piece glued to the rest of the name.
    if (i + 1 < w.length && w[i].length <= 3 && _isName(w[i] + w[i + 1], name)) return true;
  }
  return false;
}

export function normalizeWake(text) {
  return String(text || '')
    .toLowerCase()
    .normalize('NFD').replace(/[\u0300-\u036f]/g, '')
    // Keep CJK (Mandarin wake phrase); everything else non-alphanumeric → space.
    .replace(/[^a-z0-9\u3400-\u9fff\uf900-\ufaff]+/g, ' ')
    // STT sometimes spaces Chinese characters apart: glue CJK runs back together.
    .replace(/([\u3400-\u9fff\uf900-\ufaff]) (?=[\u3400-\u9fff\uf900-\ufaff])/g, '$1')
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
  const emitHeard = (t, fin) => { try { opts.onHeard?.(t, fin); } catch (e) { /* ignore */ } };
  const emitActivity = (ph) => { try { opts.onActivity?.(ph); } catch (e) { /* ignore */ } };

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
      r.onresult = r.onerror = r.onend = r.onspeechstart = r.onspeechend = r.ontranscribing = null;
      try { r.abort(); } catch (e) { try { r.stop(); } catch (e2) { /* ignore */ } }
    }
  }

  function fire() {
    kill();
    win.reset();
    held = true;
    diag.wakes++;
    log('wake word "' + WAKE_NAME + '" heard → activating Advanced Voice Mode');
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
      // Recogniser language: English unless the caller picks one (voice.js
      // follows the browser for pt / fr / it / zh, or the user's override).
      r.lang = opts.lang || 'en-US';
      diag.lang = r.lang;
    } catch (e) { setState('unsupported'); return; }

    r.onresult = (ev) => {
      if (my !== gen) return;
      backoff = BACKOFF_MIN_MS;            // healthy stream
      errStreak = 0; svcErrors = 0;
      if (state !== 'listening') setState('listening');
      const grace = now() - armedAt < RESUME_GRACE_MS;
      let interim = '';
      for (let i = ev.resultIndex; i < ev.results.length; i++) {
        const res = ev.results[i];
        const t = res[0]?.transcript || '';
        if (res.isFinal) {
          noteHeard(t.trim());
          if (opts.verbose !== false) log('heard:', JSON.stringify(t.trim()));
          emitHeard(t, true);
          if (!grace && win.commit(t)) { fire(); return; }
        }
        else interim += ' ' + t;
      }
      if (interim) emitHeard(interim, false);
      emitActivity(interim ? 'speech' : 'idle');
      if (!grace && interim && win.peek(interim)) fire();
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
    r.onspeechstart = () => { if (my === gen) emitActivity('speech'); };
    r.onspeechend = () => { if (my === gen) emitActivity('idle'); };
    r.ontranscribing = () => { if (my === gen) emitActivity('transcribing'); };
    r.onend = () => {
      if (my !== gen) return;
      emitActivity('idle');
      rec = null;
      scheduleRestart();                    // Chrome ends sessions on its own; keep it constant
    };
    try {
      r.start();
      rec = r;
      armedAt = now();
      if (state !== 'error') setState('listening');
      if (diag.restarts === 0 && diag.wakes === 0) log('listening for "' + WAKE_NAME + '" (lang ' + r.lang + ')');
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

export default { createWakeListener, hasWake, normalizeWake, createWakeWindow, editDistance, setWakeName, getWakeName, WAKE_WINDOW };
