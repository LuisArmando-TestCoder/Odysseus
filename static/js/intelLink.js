// static/js/intelLink.js
// Puts the Intel Board (/cases — Case Files + Watchlist) one click away from
// the main app: a Tools-sidebar entry and an icon-rail button, both with a
// badge counting what wants attention (cases going cold, watches that changed
// in the last day, watches that keep failing). Data: GET /api/intel/status.
//
// Plain click navigates in this tab; Ctrl/Cmd/Shift/middle click opens a new
// tab, like a normal link. If the status call fails the badge just hides —
// the link always works.

const STATUS_URL = '/api/intel/status';
const POLL_MS = 5 * 60 * 1000;
const BOARD_URL = '/cases';

export function badgeText(status) {
  const n = Number(status && status.attention) || 0;
  if (n <= 0) return '';
  return n > 99 ? '99+' : String(n);
}

export function badgeLabel(status) {
  return (status && typeof status.label === 'string' && status.label) || 'Intel Board';
}

function openBoard(newTab) {
  if (newTab) window.open(BOARD_URL, '_blank', 'noopener');
  else window.location.assign(BOARD_URL);
}

function wire(el) {
  if (!el || el.dataset.intelWired) return;
  el.dataset.intelWired = '1';
  el.addEventListener('click', (e) => {
    e.preventDefault();
    openBoard(e.ctrlKey || e.metaKey || e.shiftKey);
  });
  el.addEventListener('auxclick', (e) => {
    if (e.button === 1) { e.preventDefault(); openBoard(true); }
  });
  el.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openBoard(e.ctrlKey || e.metaKey); }
  });
}

function render(status) {
  const text = badgeText(status);
  const label = badgeLabel(status);
  const side = document.getElementById('tool-cases-btn');
  const badge = document.getElementById('intel-badge');
  const rail = document.getElementById('rail-cases');
  if (side) side.title = label;
  if (badge) {
    badge.textContent = text;
    badge.style.display = text ? '' : 'none';
    badge.setAttribute('aria-label', text ? `${text} need attention` : '');
  }
  if (rail) {
    rail.title = label;
    let rb = rail.querySelector('.rail-intel-badge');
    if (text) {
      if (!rb) {
        rb = document.createElement('span');
        rb.className = 'rail-notes-badge rail-intel-badge';
        rail.style.position = rail.style.position || 'relative';
        rail.appendChild(rb);
      }
      rb.textContent = text;
    } else if (rb) {
      rb.remove();
    }
  }
}

let _inflight = false;
export async function refreshIntelStatus() {
  if (_inflight) return null;
  _inflight = true;
  try {
    const r = await fetch(STATUS_URL, { credentials: 'same-origin' });
    if (!r.ok) throw new Error(String(r.status));
    const status = await r.json();
    render(status);
    return status;
  } catch (_) {
    render(null);
    return null;
  } finally {
    _inflight = false;
  }
}

function init() {
  wire(document.getElementById('tool-cases-btn'));
  wire(document.getElementById('rail-cases'));
  refreshIntelStatus();
  setInterval(() => { if (!document.hidden) refreshIntelStatus(); }, POLL_MS);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshIntelStatus(); });
  window.refreshIntelStatus = refreshIntelStatus;
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
else init();
