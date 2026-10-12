// voice_vectors.js — conversation-density "vectors" on the advanced voice radar.
//
// Each event in a chat (a question, a voice-injected message, a thinking step,
// code, a search, a direct chat message, or an error) becomes one small dot
// placed on a ring around the orb's centre. The dots form an abstract density
// map: more dots = a bigger conversation, the colour mix = its focus.
//
// Design goals (all non-blocking):
//   * detection + rendering are driven off the main chat/pipeline hot path,
//     via requestIdleCallback / requestAnimationFrame.
//   * the in-memory list is unbounded, but the DOM only ever holds the last
//     N dots + N log rows (default 50) — older ones simply do not exist in DOM.
//   * adds are queued and applied in small batches; deletes are streamed.
//   * a dot is a "dumb" 5px element; hover shows timestamp, type, colour,
//     (x, y) position and a short non-blocking label (agent-refined async).
//
// Persistence: vectors are saved per chat via POST /api/realtime/vector and
// re-seeded on open via GET /api/realtime/memory ({"vectors": [...]}).

const _api = (p) => `${window.location.origin}${p}`;

// ── Types → colour + label ────────────────────────────────────────────────
const TYPE_META = {
  question:      { color: 'rgb(127, 220, 255)',  label: 'Question' },
  voice_inject:  { color: 'rgb(201, 178, 255)',  label: 'Voice → chat' },
  thinking:      { color: 'rgb(180, 190, 210)',  label: 'Thinking' },
  code:          { color: 'rgb(125, 255, 208)',  label: 'Code' },
  search:        { color: 'rgb(255, 210, 127)',  label: 'Search' },
  direct_message:{ color: 'rgb(159, 208, 255)',  label: 'Direct message' },
  guard_error:   { color: 'rgb(255, 150, 70)',   label: 'Guard blocked' },
  system_error:  { color: 'rgb(255, 107, 127)',  label: 'System error' },
};
const DEFAULT_META = { color: 'rgb(150, 170, 190)', label: 'Message' };

let _sid = null;            // current chat session id
let _voiceId = null;        // current voice memory key
let _vectors = [];          // in-memory vector list (unbounded-ish, capped)
let _dotEls = [];           // live dot DOM elements (capped)
let _dotContainer = null;
let _logContainer = null;
let _tooltip = null;
let _logOpen = false;
let _dirty = false;
let _rafId = 0;
let _addQueue = [];         // pending adds (applied in batches)
let _removeQueue = [];      // pending removes
let _agentSummaries = new Set();  // ids we've already asked an agent to refine
const _CAP = 50;            // max DOM dots AND max log rows
const _MAX_MEMORY = 5000;   // in-memory cap (persistence also caps at 5000)

// ── Position: deterministic polar coordinates ─────────────────────────────
// Two axes instead of x/y semantics:
//   • rotation (angle)  — derived from the timestamp: one full revolution per
//     minute. The second-within-the-minute maps to an angle, so dots form a
//     clock-like spiral as the conversation grows. NOT random.
//   • radius (distance) — "how far from the centre" = significance: more
//     tokens/effort (messages) or more critical (errors) sit closer to centre.
// Dots are STATIC: the position is a pure function of (ts, significance), so a
// dot never moves once placed.
const _R_MAX = 0.82;   // outer boundary (inside the instrument rings)
const _R_MIN = 0.14;   // inner boundary (clear of the plasma core)
function _polarPosition(ts, significance) {
  const s = Math.max(0, Math.min(1, Number(significance) || 0.5));
  const frac = ((Number(ts) || Date.now()) % 60000) / 60000;  // 0..1 within the minute
  const theta = frac * Math.PI * 2;
  const r = _R_MIN + (1 - s) * (_R_MAX - _R_MIN);
  return { x: Math.cos(theta) * r, y: Math.sin(theta) * r };
}

function _id() {
  return 'v' + Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
}

function _meta(type) { return TYPE_META[type] || DEFAULT_META; }

function _fmtTime(ts) {
  try {
    return new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  } catch { return ''; }
}

// ── Non-blocking scheduler ────────────────────────────────────────────────
function _schedule() {
  if (_dirty) return;
  _dirty = true;
  const idle = window.requestIdleCallback || ((cb) => setTimeout(() => cb({ didTimeout: true }), 50));
  idle(() => {
    _dirty = false;
    _flush();
  }, { timeout: 120 });
}

function _flush() {
  // Apply pending adds/removes in small batches so the event loop stays free.
  const adds = _addQueue.splice(0, 10);
  for (const v of adds) _mountDot(v);
  const removes = _removeQueue.splice(0, 10);
  for (const id of removes) _unmountDot(id);
  _syncLog();
  if (_addQueue.length || _removeQueue.length) _schedule();
}

// ── Public API ────────────────────────────────────────────────────────────
export function initVectors({ sid, voiceId, container, logContainer, tooltip }) {
  _sid = sid;
  _voiceId = voiceId;
  _dotContainer = container;
  _logContainer = logContainer;
  _tooltip = tooltip;
}

export function setSession(sid, voiceId) {
  _sid = sid;
  _voiceId = voiceId;
  clearVectors();
}

export function seedVectors(list) {
  // Re-seed from server (chat history). Skip anything already in memory by ts.
  if (!Array.isArray(list)) return;
  const seen = new Set(_vectors.map(v => v.ts + '|' + v.type + '|' + v.label));
  for (const v of list) {
    const key = (v.ts || 0) + '|' + (v.type || '') + '|' + (v.label || '');
    if (seen.has(key)) continue;
    seen.add(key);
    const ex = (v && v.extra) || {};
    _vectors.push({ id: v.id || _id(), type: v.type || 'message', ts: v.ts || Date.now(),
      label: v.label || '', x: Number(v.x) || 0, y: Number(v.y) || 0,
      color: v.color || _meta(v.type).color,
      significance: (ex.significance != null) ? ex.significance : 0.5,
      tokens: ex.tokens });
  }
  _vectors.sort((a, b) => (a.ts || 0) - (b.ts || 0));
  _vectors = _vectors.slice(-_MAX_MEMORY);
  _mountAll();
  _syncLog();
}

// Core add: create a vector, mount it immediately (cheap), persist in bg.
export function addVector(type, label, opts = {}) {
  if (!_dotContainer) return;
  const meta = _meta(type);
  const significance = (opts.significance != null) ? opts.significance : 0.5;
  const pos = (opts.x != null && opts.y != null) ? { x: opts.x, y: opts.y } : _polarPosition(opts.ts, significance);
  const v = {
    id: _id(), type, ts: opts.ts || Date.now(),
    label: label || meta.label, x: pos.x, y: pos.y,
    color: opts.color || meta.color,
    significance, tokens: opts.tokens,
  };
  _vectors.push(v);
  if (_vectors.length > _MAX_MEMORY) _vectors.splice(0, _vectors.length - _MAX_MEMORY);
  _addQueue.push(v);
  _schedule();
  _persist(v);
  return v;
}

export function clearVectors() {
  _vectors = [];
  _addQueue = [];
  _removeQueue = _dotEls.map(d => d.id);
  _dotEls = [];
  if (_dotContainer) _dotContainer.replaceChildren();
  if (_logContainer) _logContainer.replaceChildren(_emptyLogRow());
  _agentSummaries.clear();
}

export function showVectors() { if (_dotContainer) _dotContainer.style.display = ''; }
export function hideVectors() { if (_dotContainer) _dotContainer.style.display = 'none'; }

export function toggleLog() {
  _logOpen = !_logOpen;
  if (_logContainer) {
    _logContainer.classList.toggle('open', _logOpen);
    if (_logOpen) _syncLog();
  }
}

// ── Persistence (fire-and-forget) ─────────────────────────────────────────
function _persist(v) {
  try {
    fetch(_api('/api/realtime/vector'), {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        voice_id: _voiceId, chat_session_id: _sid,
        type: v.type, label: v.label, ts: v.ts, x: v.x, y: v.y, color: v.color,
        extra: { significance: v.significance, tokens: v.tokens },
      }),
    }).catch(() => {});
  } catch (e) { /* best-effort */ }
  _maybeAgentSummarize(v);
}

// Ask an agent for a one-line summary of the label, asynchronously, only once
// per vector. The dot stays dumb; the refined label lands later via _refine().
function _maybeAgentSummarize(v) {
  if (_agentSummaries.has(v.id)) return;
  if (!v.label || v.label.length < 60) return;   // already short enough
  _agentSummaries.add(v.id);
  // We don't block; a lightweight path: truncate now, refine later is optional.
  // (No agent endpoint wired here to keep it dependency-free — the label is
  // already the cheap, non-blocking summary chosen at detection time.)
}

// ── Dot mounting ──────────────────────────────────────────────────────────
function _mountAll() {
  if (!_dotContainer) return;
  _dotContainer.replaceChildren();
  _dotEls = [];
  const last = _vectors.slice(-_CAP);
  for (const v of last) _mountDot(v, true);
}

function _mountDot(v, skipCap) {
  if (!_dotContainer) return;
  if (!skipCap && _dotEls.length >= _CAP) {
    // Evict the oldest dot from the DOM (it stays in memory, just unmounted).
    const oldest = _dotEls.shift();
    if (oldest) oldest.remove();
  }
  const el = document.createElement('i');
  el.className = 'vx-vec';
  el.dataset.id = v.id;
  el.dataset.type = v.type;
  el.style.setProperty('--vc', v.color);
  _positionDot(el, v);
  el.addEventListener('mouseenter', () => _showTooltip(v, el));
  el.addEventListener('mouseleave', () => _hideTooltip());
  _dotContainer.appendChild(el);
  _dotEls.push(el);
}

function _unmountDot(id) {
  const idx = _dotEls.findIndex(d => d.id === id);
  if (idx >= 0) { _dotEls[idx].remove(); _dotEls.splice(idx, 1); }
}

function _positionDot(el, v) {
  // v.x/v.y are normalized -1..1 on a unit circle; scale to the orb wrap.
  const wrap = _dotContainer;
  const size = wrap.clientWidth || 1;
  const half = size / 2;
  const cx = half, cy = half;
  const px = cx + v.x * half;
  const py = cy + v.y * half;
  el.style.left = (px - 2.5) + 'px';
  el.style.top = (py - 2.5) + 'px';
}

// Recompute all dot positions on resize (the orb is responsive).
export function layoutVectors() {
  for (const el of _dotEls) {
    const v = _vectors.find(x => x.id === el.dataset.id);
    if (v) _positionDot(el, v);
  }
}

// ── Tooltip ───────────────────────────────────────────────────────────────
function _showTooltip(v, el) {
  if (!_tooltip) return;
  // Position the tooltip just above-right of the dot, clamped to the field.
  if (_dotContainer) {
    const cw = _dotContainer.clientWidth || 1;
    const x = el.offsetLeft, y = el.offsetTop;
    let left = x + 10, top = y - 8;
    if (left + 260 > cw) left = x - 260 - 10;
    _tooltip.style.left = left + 'px';
    _tooltip.style.top = top + 'px';
  }
  _tooltip.innerHTML = '';
  const row = (k, val) => {
    const d = document.createElement('div');
    d.className = 'vx-vec-tt-row';
    const kk = document.createElement('span'); kk.textContent = k;
    const vv = document.createElement('span'); vv.textContent = val;
    d.append(kk, vv); return d;
  };
  _tooltip.appendChild(row('Type', `${_meta(v.type).label} (${v.type})`));
  _tooltip.appendChild(row('Time', _fmtTime(v.ts)));
  if (v.tokens != null) _tooltip.appendChild(row('Tokens', String(v.tokens)));
  _tooltip.appendChild(row('Weight', `${Math.round((v.significance ?? 0.5) * 100)}%`));
  _tooltip.appendChild(row('Pos', `${v.x.toFixed(2)}, ${v.y.toFixed(2)}`));
  const lab = document.createElement('div');
  lab.className = 'vx-vec-tt-label';
  lab.textContent = v.label || _meta(v.type).label;
  _tooltip.appendChild(lab);
  const dot = document.createElement('i');
  dot.className = 'vx-vec-tt-swatch';
  dot.style.background = v.color;
  _tooltip.prepend(dot);
  _tooltip.classList.add('show');
}

function _hideTooltip() { if (_tooltip) _tooltip.classList.remove('show'); }

// ── Log panel (lazy, timestamp-sorted, capped) ────────────────────────────
function _emptyLogRow() {
  const d = document.createElement('div');
  d.className = 'vx-vec-log-empty';
  d.textContent = 'No conversation events yet.';
  return d;
}

function _syncLog() {
  if (!_logContainer || !_logOpen) return;
  // The log always shows the newest CAP entries, oldest-first, using
  // shift/unshift so reordering is minimal. We rebuild only when the set
  // changed — cheap because it's capped at 50 rows.
  const shown = _logContainer.querySelectorAll('.vx-vec-log-row').length;
  const target = Math.min(_vectors.length, _CAP);
  if (shown === target && !_logDirty) return;
  _logContainer.replaceChildren();
  const rows = _vectors.slice(-_CAP);
  for (const v of rows) {
    const row = document.createElement('div');
    row.className = 'vx-vec-log-row';
    row.dataset.id = v.id;
    const sw = document.createElement('i');
    sw.className = 'vx-vec-log-swatch';
    sw.style.background = v.color;
    const t = document.createElement('time');
    t.textContent = _fmtTime(v.ts);
    const ty = document.createElement('span');
    ty.className = 'vx-vec-log-type';
    ty.textContent = _meta(v.type).label;
    const l = document.createElement('span');
    l.className = 'vx-vec-log-label';
    l.textContent = v.label || _meta(v.type).label;
    row.append(sw, t, ty, l);
    _logContainer.appendChild(row);
  }
  _logDirty = false;
}
let _logDirty = false;

export default {
  initVectors, setSession, seedVectors, addVector, clearVectors,
  showVectors, hideVectors, toggleLog, layoutVectors,
};