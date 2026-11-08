// ═══════════════════════════════════════════════════════════════════════════
//  Odysseus · Chat performance — keeps long threads at full frame rate.
//
//  Same pattern as WhatsApp Web / ChatGPT / Claude Code's viewport renderer
//  (only what's near the screen costs anything; everything else stays in
//  memory and comes back instantly; scroll position is anchored), but done
//  WITHOUT removing bubbles from the DOM and without touching a single style:
//
//   1. Progressive hydration — a long history opens with only its newest
//      messages built; older ones are built newest→oldest in idle slices and
//      prepended with the reading position pinned. Scrolling toward the top,
//      Ctrl/⌘+F, or any action that indexes the thread finishes it at once,
//      so nothing is ever missing (lossless).
//   2. Honest placeholders — off-screen bubbles are skipped by the browser
//      (content-visibility:auto, messages-hyper.css). Each gets a per-message
//      height estimate, calibrated against bubbles that did render, so the
//      scrollbar is truthful and scrolling up doesn't jump. After a bubble
//      renders once the browser remembers its real size.
//   3. Settled bubbles — the entrance animation (fill: both) kept every one of
//      them animated and on its own compositor layer forever. Once it has
//      played, the bubble is marked settled and the (identical) end state is
//      the plain style again — zero visual change, zero per-frame cost.
//
//  API: window.odysseusChatPerf = { flush(), hydrating(), stats() }
// ═══════════════════════════════════════════════════════════════════════════

const EAGER = 40;          // messages built synchronously on open
const SLICE = 16;          // messages per idle slice afterwards
const MIN_FOR_LAZY = 80;   // shorter threads are built in one go
const NEAR_TOP_PX = 2400;  // scrolling this close to the top finishes hydration

let _job = null;           // { box, anchor, pending: [...], build, token }
let _token = 0;
let _selfTop = -1;   // scrollTop we set ourselves (pinning) — not a reader scroll

const _idle = window.requestIdleCallback
  ? (fn) => window.requestIdleCallback(fn, { timeout: 800 })
  : (fn) => setTimeout(() => fn({ timeRemaining: () => 8, didTimeout: true }), 16);

// Background hydration yields to the reader: while they're actively scrolling
// (and not near the top, where the content is needed) slices wait.
let _lastScroll = 0;
const SCROLL_QUIET_MS = 180;

// ── 3 · settle entrance animations ───────────────────────────────────────────
function _settle(msg) {
  if (!msg || msg.classList.contains('msg-settled')) return;
  // Only once every finite animation on the bubble itself has finished
  // (cinematic + whimsy entrances can overlap).
  try {
    const live = msg.getAnimations({ subtree: false }).some(a =>
      a.playState === 'running' && a.effect && a.effect.getTiming().iterations !== Infinity);
    if (live) return;
  } catch (_) { /* old engines: settle anyway */ }
  msg.classList.add('msg-settled');
}
document.addEventListener('animationend', (e) => {
  const t = e.target;
  if (t && t.classList && t.classList.contains('msg') && t.parentElement && t.parentElement.id === 'chat-history') _settle(t);
}, true);
document.addEventListener('animationcancel', (e) => {
  const t = e.target;
  if (t && t.classList && t.classList.contains('msg')) _settle(t);
}, true);

// ── 2 · height estimates for bubbles that haven't rendered yet ───────────────
const _cal = { w: 0, user: 1, ai: 1, n: 0 };
const LINE = 24, CODE_LINE = 20, ROW = 38, CHROME = 76, CPL_PX = 7.4;

function _rawEstimate(msg) {
  const body = msg.querySelector(':scope > .body') || msg.querySelector('.body');
  if (!body) return 0;
  const w = Math.max(240, (_cal.w || (document.getElementById('chat-history') || {}).clientWidth || 760) - 48);
  const cpl = Math.max(24, Math.floor(w / CPL_PX));
  let h = CHROME;
  for (const el of body.children) {
    const tag = el.tagName;
    if (tag === 'PRE') {
      h += (el.textContent.match(/\n/g) || []).length * CODE_LINE + 56;
    } else if (tag === 'TABLE' || el.classList.contains('md-table-scroll')) {
      h += el.querySelectorAll('tr').length * ROW + 24;
    } else if (el.classList.contains('thinking-section') || el.classList.contains('tool-block') || tag === 'DETAILS') {
      h += 52;
    } else if (tag === 'UL' || tag === 'OL') {
      for (const li of el.children) h += Math.max(1, Math.ceil(li.textContent.length / (cpl - 4))) * LINE + 4;
      h += 16;
    } else if (tag === 'IMG' || el.querySelector('img, canvas, video, svg.mermaid')) {
      h += 320;
    } else if (tag === 'HR') {
      h += 24;
    } else {
      const len = el.textContent.length;
      h += len ? Math.max(1, Math.ceil(len / cpl)) * LINE + 14 : 8;
    }
  }
  if (!body.children.length) h += Math.max(1, Math.ceil(body.textContent.length / cpl)) * LINE;
  return h;
}

function _estimate(msg) {
  const raw = _rawEstimate(msg);
  if (!raw) return 0;
  const k = msg.classList.contains('msg-user') ? _cal.user : _cal.ai;
  return Math.round(raw * k);
}

function _applyEstimate(msg) {
  if (!msg || msg.style.containIntrinsicSize) return;
  const h = _estimate(msg);
  if (h > 0) msg.style.containIntrinsicSize = `auto ${h}px`;
}

// Calibrate the estimator against bubbles that actually rendered (the eager
// tail is on screen after the first frame). Robust median of real/raw.
function _calibrate(box) {
  const rendered = [];
  const msgs = box.querySelectorAll(':scope > .msg');
  for (let i = msgs.length - 1; i >= 0 && rendered.length < 24; i--) {
    const m = msgs[i];
    const r = m.getBoundingClientRect();
    if (r.bottom < -200 || r.top > innerHeight + 200 || r.height < 20) continue;
    rendered.push(m);
  }
  if (!rendered.length) return;
  _cal.w = box.clientWidth;
  const ratios = { user: [], ai: [] };
  for (const m of rendered) {
    const raw = _rawEstimate(m);
    if (!raw) continue;
    (m.classList.contains('msg-user') ? ratios.user : ratios.ai).push(m.getBoundingClientRect().height / raw);
  }
  const med = (a) => { if (!a.length) return 0; a.sort((x, y) => x - y); return a[a.length >> 1]; };
  const u = med(ratios.user), a = med(ratios.ai);
  if (u) _cal.user = Math.min(3, Math.max(0.4, u));
  if (a) _cal.ai = Math.min(3, Math.max(0.4, a));
  _cal.n++;
}

// ── 1 · progressive hydration ────────────────────────────────────────────────
/**
 * Build `messages` into `box`. `build(list)` must return a DocumentFragment
 * (or element list) for a contiguous slice, in order, and `post(nodes)` runs
 * per-node finishing work (highlighting, mermaid). Returns nothing; the tail
 * is in the DOM when this returns.
 */
export function hydrate(box, messages, build, post) {
  cancel();
  if (!box || !messages.length) return;
  if (messages.length < MIN_FOR_LAZY) {
    _commit(box, build(messages), null, post, false);
    return;
  }
  const tail = messages.slice(-EAGER);
  const head = messages.slice(0, -EAGER);
  const first = _commit(box, build(tail), null, post, false);
  if (!first) return;
  const token = ++_token;
  _job = { box, anchor: first, pending: head, build, post, token };
  box.setAttribute('aria-busy', 'true');
  // Calibrate once the tail has painted, then start the idle slices.
  requestAnimationFrame(() => requestAnimationFrame(() => {
    if (!_job || _job.token !== token) return;
    _calibrate(box);
    for (const m of box.querySelectorAll(':scope > .msg')) _applyEstimate(m);
    _idle(_step);
  }));
}

function _commit(box, frag, before, post, pin) {
  const nodes = Array.from(frag.childNodes || frag);
  if (!nodes.length) return null;
  // Older messages hydrated off-screen never show an entrance, so they start
  // settled. The visible tail keeps its normal entrance and settles when the
  // animation ends.
  if (pin) for (const n of nodes) if (n.nodeType === 1 && n.classList.contains('msg')) n.classList.add('msg-settled');
  let fromBottom = 0;
  if (pin) fromBottom = box.scrollHeight - box.scrollTop;
  if (before) box.insertBefore(frag, before); else box.appendChild(frag);
  for (const n of nodes) if (n.nodeType === 1 && n.classList.contains('msg')) _applyEstimate(n);
  if (pin) { box.scrollTop = box.scrollHeight - fromBottom; _selfTop = box.scrollTop; }
  if (post) for (const n of nodes) if (n.nodeType === 1) post(n);
  return nodes.find(n => n.nodeType === 1) || nodes[0];
}

function _alive(job) {
  return job && _job === job && job.anchor && job.anchor.parentNode === job.box;
}

function _sliceOnce(job) {
  const take = job.pending.splice(Math.max(0, job.pending.length - SLICE), SLICE);
  if (!take.length) return;
  const first = _commit(job.box, job.build(take), job.anchor, job.post, true);
  if (first) job.anchor = first;
}

function _step(deadline) {
  const job = _job;
  if (!_alive(job)) { cancel(); return; }
  if (performance.now() - _lastScroll < SCROLL_QUIET_MS) { setTimeout(() => _idle(_step), SCROLL_QUIET_MS); return; }
  do {
    _sliceOnce(job);
  } while (job.pending.length && deadline && deadline.timeRemaining && deadline.timeRemaining() > 3);
  if (job.pending.length) _idle(_step);
  else _finish();
}

function _finish() {
  if (_job && _job.box) _job.box.removeAttribute('aria-busy');
  _job = null;
  try { document.dispatchEvent(new CustomEvent('odysseus:chat-hydrated')); } catch (_) {}
}

export function cancel() {
  if (_job && _job.box) _job.box.removeAttribute('aria-busy');
  _job = null;
}

/** Build every remaining older message now (synchronously). */
export function flush() {
  const job = _job;
  if (!_alive(job)) { if (_job) cancel(); return; }
  while (job.pending.length) _sliceOnce(job);
  _finish();
}

export function hydrating() { return !!_alive(_job); }

function _onScroll(e) {
  const box = e.currentTarget;
  if (Math.abs(box.scrollTop - _selfTop) > 1) _lastScroll = performance.now();
  _selfTop = -1;
  if (_job && _job.box === box && box.scrollTop < NEAR_TOP_PX) {
    // Keep ahead of the reader: a couple of slices right away.
    const job = _job;
    if (!_alive(job)) return;
    _sliceOnce(job);
    if (box.scrollTop < 400) _sliceOnce(job);
    if (!job.pending.length) _finish();
  }
}

function _wire() {
  const box = document.getElementById('chat-history');
  if (!box) { setTimeout(_wire, 300); return; }
  box.addEventListener('scroll', _onScroll, { passive: true });
  // Messages added outside the batch path (live turns) get an estimate too,
  // so they never fall back to the 120px placeholder once off-screen.
  new MutationObserver((muts) => {
    for (const m of muts) for (const n of m.addedNodes) {
      if (n.nodeType === 1 && n.classList.contains('msg') && !n.style.containIntrinsicSize) {
        // Live bubbles are on screen when added; the browser records their
        // real size as they render, so only estimate ones that start hidden.
        if (_job) _applyEstimate(n);
      }
    }
    if (_job && !_alive(_job)) cancel();   // session switched / history wiped
  }).observe(box, { childList: true });
}
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', _wire, { once: true });
else _wire();

// Find-in-page must see everything.
document.addEventListener('keydown', (e) => {
  if ((e.metaKey || e.ctrlKey) && !e.altKey && (e.key === 'f' || e.key === 'F' || e.key === 'g')) flush();
}, true);
window.addEventListener('beforeprint', flush);

function stats() {
  const box = document.getElementById('chat-history');
  return {
    hydrating: hydrating(),
    pending: _job ? _job.pending.length : 0,
    inDom: box ? box.querySelectorAll(':scope > .msg').length : 0,
    calibration: { ..._cal },
  };
}

const api = { hydrate, flush, hydrating, cancel, stats, estimate: _estimate };
window.odysseusChatPerf = api;
export default api;
