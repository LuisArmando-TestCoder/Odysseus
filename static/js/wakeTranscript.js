// wakeTranscript.js — the ambient transcript strip for the wake listener.
//
// Everything the always-on listener hears (while Advanced Voice Mode is off)
// streams into a slim glass strip beside the Voice launcher: live interim
// words, then the settled sentence, with the wake phrase lit when it lands.
// It surfaces on speech and fades out after a few quiet seconds — never a
// panel that sits in the way. Click it for the recent history.
//
// Bounded: the strip shows the tail of the last few sentences; the history
// keeps MAX_LINES lines, in memory only (never stored or sent anywhere).

const MAX_LINES = 40;
const TAIL_CHARS = 220;
const SEP = '\u00a0\u00b7\u00a0';   // non-breaking: flex items drop plain edge spaces
const LINGER_MS = 6500;
const WAKE_HL = /\b(advance[ds]?\s+voice\s*modes?|modo\s+(?:de\s+)?voz\s+avanzad[oa])\b/i;

const ICON_CHEV = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 15l6-6 6 6"/></svg>';

function _fmt(ts) {
  const d = new Date(ts);
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
}

// Append `text` to `parent`, wrapping the wake phrase in <mark>.
function _paint(parent, text) {
  const m = WAKE_HL.exec(text);
  if (!m) { parent.append(text); return false; }
  parent.append(text.slice(0, m.index));
  const mk = document.createElement('mark');
  mk.textContent = m[0];
  parent.append(mk, text.slice(m.index + m[0].length));
  return true;
}

export function createWakeTranscript({ fabSelector = '#voice-mode-fab' } = {}) {
  const el = document.createElement('div');
  el.className = 'wt';
  el.dataset.live = '0';
  el.dataset.phase = 'idle';
  el.innerHTML = `
    <section class="wt-panel" id="wt-panel" hidden aria-label="Ambient transcript history">
      <header class="wt-head">
        <span class="wt-k">Ambient transcript</span>
        <span class="wt-engine" data-wt="engine"></span>
        <button type="button" class="wt-btn" data-wt="clear">Clear</button>
      </header>
      <ol class="wt-list" role="log" aria-live="off"></ol>
      <p class="wt-empty">Nothing heard yet.</p>
      <footer class="wt-foot">Heard while voice mode is off · kept in this tab only · say <b>“advanced voice mode”</b> to engage</footer>
    </section>
    <button type="button" class="wt-bar" aria-expanded="false" aria-controls="wt-panel" title="What the wake listener is hearing — click for history">
      <span class="wt-eq" aria-hidden="true"><i></i><i></i><i></i><i></i><i></i></span>
      <span class="wt-k wt-tag" data-wt="tag">Hearing</span>
      <span class="wt-line"><span class="wt-final"></span><span class="wt-interim"></span></span>
      <span class="wt-chev">${ICON_CHEV}</span>
    </button>`;
  document.body.appendChild(el);

  const $ = (s) => el.querySelector(s);
  const bar = $('.wt-bar'), panel = $('.wt-panel'), list = $('.wt-list');
  const lineFinal = $('.wt-final'), lineInterim = $('.wt-interim'), tag = $('[data-wt="tag"]');
  const lines = [];
  let tail = '';
  let hideT = 0, wakeT = 0;
  let state = 'off';

  // ── placement: hug the launcher, wherever the theme put it ──────────────
  function place() {
    const fab = document.querySelector(fabSelector);
    const r = fab?.getBoundingClientRect();
    if (!r || !r.width) { el.dataset.anchor = 'none'; return; }
    const vw = window.innerWidth, vh = window.innerHeight;
    if (r.top < vh * 0.4) {
      // Launcher docked in a top bar (phones): drop the strip just below it.
      el.dataset.anchor = 'top';
      el.style.setProperty('--wt-top', Math.round(r.bottom + 8) + 'px');
      el.style.setProperty('--wt-left', '8px');
      el.style.setProperty('--wt-right', '8px');
    } else {
      el.dataset.anchor = 'side';
      el.style.setProperty('--wt-right', Math.round(vw - r.left + 10) + 'px');
      el.style.setProperty('--wt-bottom', Math.round(vh - r.bottom) + 'px');
      el.style.setProperty('--wt-h', Math.round(r.height) + 'px');
    }
  }
  const onResize = () => { if (el.dataset.live === '1' || !panel.hidden) place(); };
  window.addEventListener('resize', onResize);

  function surface() {
    if (state !== 'listening' && state !== 'error') return;
    place();
    el.dataset.live = '1';
    clearTimeout(hideT);
    hideT = setTimeout(() => {
      if (panel.hidden && el.dataset.phase === 'idle') el.dataset.live = '0';
      else surface();
    }, LINGER_MS);
  }

  function renderTail() {
    lineFinal.replaceChildren();
    _paint(lineFinal, tail);
  }

  function addLine(text, wake = false) {
    const item = { t: Date.now(), text, wake };
    lines.push(item);
    if (lines.length > MAX_LINES) { lines.shift(); list.firstElementChild?.remove(); }
    const li = document.createElement('li');
    if (wake) li.className = 'is-wake';
    const tm = document.createElement('time'); tm.textContent = _fmt(item.t);
    const tx = document.createElement('span');
    if (_paint(tx, text)) li.className = 'is-wake';
    li.append(tm, tx);
    list.appendChild(li);
    el.classList.toggle('has-lines', true);
    if (!panel.hidden) list.scrollTop = list.scrollHeight;
  }

  function setOpen(open) {
    panel.hidden = !open;
    bar.setAttribute('aria-expanded', String(open));
    el.classList.toggle('is-open', open);
    if (open) { place(); list.scrollTop = list.scrollHeight; el.dataset.live = '1'; clearTimeout(hideT); }
    else surface();
  }

  bar.addEventListener('click', () => setOpen(panel.hidden));
  $('[data-wt="clear"]').addEventListener('click', () => {
    lines.length = 0; list.replaceChildren(); tail = ''; renderTail();
    el.classList.remove('has-lines');
  });
  el.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !panel.hidden) { e.stopPropagation(); setOpen(false); bar.focus(); }
  });
  document.addEventListener('pointerdown', (e) => {
    if (!panel.hidden && !el.contains(e.target)) setOpen(false);
  }, true);

  return {
    el,
    // A recognised chunk. Interim chunks replace each other; finals settle.
    heard(text, final) {
      const t = String(text || '').replace(/\s+/g, ' ').trim();
      if (!t) return;
      if (final) {
        lineInterim.textContent = '';
        tail = (tail ? tail + SEP : '') + t;
        if (tail.length > TAIL_CHARS) tail = '…' + tail.slice(tail.length - TAIL_CHARS);
        renderTail();
        addLine(t);
      } else {
        lineInterim.textContent = (tail ? SEP : '') + t;
      }
      el.dataset.phase = final ? 'idle' : 'speech';
      surface();
    },
    // 'speech' (mic hears a voice) · 'transcribing' (clip at the server) · 'idle'
    activity(phase) {
      el.dataset.phase = phase || 'idle';
      tag.textContent = phase === 'transcribing' ? 'Transcribing' : 'Hearing';
      if (phase !== 'idle') surface();
    },
    // The wake phrase landed: light it, then step aside for the HUD.
    wake() {
      el.classList.add('is-wake');
      tag.textContent = 'Engaging';
      if (lines.length) lines[lines.length - 1].wake = true;
      list.lastElementChild?.classList.add('is-wake');
      place();
      el.dataset.live = '1';
      clearTimeout(hideT); clearTimeout(wakeT);
      wakeT = setTimeout(() => {
        el.classList.remove('is-wake');
        tag.textContent = 'Hearing';
        tail = ''; renderTail(); lineInterim.textContent = '';
        el.dataset.live = '0';
      }, 1400);
    },
    setState(s, engine) {
      state = s;
      el.dataset.state = s;
      $('[data-wt="engine"]').textContent = engine === 'server' ? 'server STT' : engine === 'browser' ? 'browser STT' : '';
      if (s === 'error') {
        tag.textContent = 'Not hearing';
        lineInterim.textContent = '';
        lineFinal.textContent = 'speech service unreachable — retrying';
        surface();
        return;
      }
      if (tag.textContent === 'Not hearing') { tag.textContent = 'Hearing'; renderTail(); }
      if (s !== 'listening') {
        if (!el.classList.contains('is-wake')) el.dataset.live = '0';
        el.dataset.phase = 'idle';
        if (!panel.hidden) setOpen(false);
      }
    },
    get lines() { return lines.map(l => ({ ...l })); },
    destroy() { window.removeEventListener('resize', onResize); el.remove(); },
  };
}

export default { createWakeTranscript };
