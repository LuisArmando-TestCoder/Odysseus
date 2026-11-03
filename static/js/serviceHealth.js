// static/js/serviceHealth.js
// Sidebar "Services" entry with a status dot fed by the scheduled Service
// Health task (GET /api/health/services — saved state, no probes). Click it to
// expand a small panel: what is down or flaky, the error, the fix, when it
// was last checked, and a "Check now" button (POST /api/health/services/check
// — live probes, not saved, so notifications still come from the task).
//
// If the status call fails the dot goes grey ("unknown"); nothing else breaks.

const STATUS_URL = '/api/health/services';
const CHECK_URL = '/api/health/services/check';
const POLL_MS = 5 * 60 * 1000;

const DOT_TITLES = {
  ok: 'All services OK',
  down: 'A service is down',
  flaky: 'A service failed its last check',
  stale: 'Service checks have not run recently',
  unknown: 'Service health not checked yet',
};

export function dotState(status) {
  const s = status && status.status;
  return Object.prototype.hasOwnProperty.call(DOT_TITLES, s) ? s : 'unknown';
}

export function ago(ts, nowSec) {
  if (ts == null) return 'never';
  const now = nowSec == null ? Date.now() / 1000 : nowSec;
  const s = Math.max(0, now - Number(ts));
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) { const h = Math.round(s / 3600); return `${h} hour${h === 1 ? '' : 's'} ago`; }
  const d = Math.round(s / 86400); return `${d} day${d === 1 ? '' : 's'} ago`;
}

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;   // never innerHTML: details come from servers
  return n;
}

let _last = null;
let _open = false;

function renderPanel(panel, status, note) {
  panel.replaceChildren();
  const st = dotState(status);
  const head = el('div', 'svc-health-head', (status && status.label) || DOT_TITLES[st]);
  panel.appendChild(head);
  const rows = (status && Array.isArray(status.services)) ? status.services : [];
  const bad = rows.filter((r) => r && r.status !== 'ok');
  if (bad.length) {
    const ul = el('ul', 'svc-health-list');
    bad.forEach((r) => {
      const li = el('li', `svc-health-row svc-${r.status === 'down' ? 'down' : 'flaky'}`);
      const title = el('div', 'svc-health-name', r.label || 'Service');
      if (r.for) title.appendChild(el('span', 'svc-health-for', ` · ${r.status === 'down' ? 'down' : 'failing'} ${r.for}`));
      li.appendChild(title);
      if (r.detail) li.appendChild(el('div', 'svc-health-detail', r.detail));
      if (r.fix) li.appendChild(el('div', 'svc-health-fix', `Fix: ${r.fix}`));
      ul.appendChild(li);
    });
    panel.appendChild(ul);
  } else if (st === 'unknown') {
    panel.appendChild(el('div', 'svc-health-empty',
      'The Service Health task has not run yet. Run a check now, or enable it in Tasks.'));
  } else if (rows.length) {
    panel.appendChild(el('div', 'svc-health-empty',
      rows.map((r) => r.label).join(' · ')));
  }
  const foot = el('div', 'svc-health-foot');
  const when = status && status.live ? 'Live check' : `Last checked ${ago(status && status.checked_at)}`;
  foot.appendChild(el('span', 'svc-health-when', note || when));
  const btn = el('button', 'svc-health-check', 'Check now');
  btn.type = 'button';
  btn.addEventListener('click', (e) => { e.stopPropagation(); checkNow(btn); });
  foot.appendChild(btn);
  panel.appendChild(foot);
}

function render(status, note) {
  const st = dotState(status);
  const item = document.getElementById('tool-services-btn');
  const dot = document.getElementById('svc-health-dot');
  const panel = document.getElementById('svc-health-panel');
  const label = (status && status.label) || DOT_TITLES[st];
  if (item) { item.title = label; item.dataset.svcState = st; }
  if (dot) {
    dot.className = `svc-health-dot svc-${st}`;
    dot.setAttribute('aria-label', label);
  }
  if (panel) {
    panel.hidden = !_open;
    if (_open) renderPanel(panel, status, note);
  }
}

let _inflight = false;
export async function refreshServiceHealth() {
  if (_inflight) return _last;
  _inflight = true;
  try {
    const r = await fetch(STATUS_URL, { credentials: 'same-origin' });
    if (!r.ok) throw new Error(String(r.status));
    _last = await r.json();
  } catch (_) {
    _last = { status: 'unknown', label: 'Service health unavailable', services: [] };
  } finally {
    _inflight = false;
  }
  render(_last);
  return _last;
}

async function checkNow(btn) {
  if (btn) { btn.disabled = true; btn.textContent = 'Checking…'; }
  let note = '';
  try {
    const r = await fetch(CHECK_URL, { method: 'POST', credentials: 'same-origin' });
    if (r.ok) {
      _last = await r.json();
    } else {
      let msg = `Check failed (${r.status})`;
      try { const j = await r.json(); if (j && typeof j.detail === 'string') msg = j.detail; } catch (_) { /* keep */ }
      note = msg;
    }
  } catch (_) {
    note = 'Check failed — is the server reachable?';
  }
  render(_last, note);
}

function toggle() {
  _open = !_open;
  const item = document.getElementById('tool-services-btn');
  if (item) item.setAttribute('aria-expanded', String(_open));
  render(_last);
}

function init() {
  const item = document.getElementById('tool-services-btn');
  if (!item || item.dataset.svcWired) return;
  item.dataset.svcWired = '1';
  item.addEventListener('click', (e) => {
    // An in-place toggle, not a tool window: keep the mobile drawer open
    // (sidebar-layout.js closes it on clicks bubbling from [id^="tool-"]).
    e.stopPropagation();
    toggle();
  });
  item.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); }
  });
  refreshServiceHealth();
  setInterval(() => { if (!document.hidden) refreshServiceHealth(); }, POLL_MS);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshServiceHealth(); });
  window.refreshServiceHealth = refreshServiceHealth;
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
else init();
