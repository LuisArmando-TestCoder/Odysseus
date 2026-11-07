// static/js/deadlineLink.js
// Puts the Deadlines page (/deadlines — what Deadline Watch is following up
// on, with snooze / mark done) one click away from the main app: a Tools
// sidebar entry right under "Intel Board" and an icon-rail button, both with
// a badge counting overdue items still being followed up. The tooltip names
// what's due in 24h, how many are snoozed, and the next item.
// Data: GET /api/deadlines/status.
//
// The entries are created here (next to the Intel Board ones) so they share
// that entry's look and its Appearance visibility toggle. Plain click
// navigates; Ctrl/Cmd/Shift/middle click opens a new tab. If the status call
// fails the badge hides — the link always works.

const STATUS_URL = '/api/deadlines/status';
const POLL_MS = 5 * 60 * 1000;
const PAGE_URL = '/deadlines';
const ICON = '<path d="M12 22a9 9 0 1 0 0-18 9 9 0 0 0 0 18z"/><path d="M12 9v4l2.5 1.5"/><path d="M5 3 2 6"/><path d="m22 6-3-3"/>';

export function badgeText(status) {
  const n = Number(status && status.attention) || 0;
  if (n <= 0) return '';
  return n > 99 ? '99+' : String(n);
}

export function badgeLabel(status) {
  return (status && typeof status.label === 'string' && status.label) || 'Deadlines — snooze or mark done';
}

function openPage(newTab) {
  if (newTab) window.open(PAGE_URL, '_blank', 'noopener');
  else window.location.assign(PAGE_URL);
}

function wire(el) {
  if (!el || el.dataset.deadlineWired) return;
  el.dataset.deadlineWired = '1';
  el.addEventListener('click', (e) => {
    e.preventDefault();
    openPage(e.ctrlKey || e.metaKey || e.shiftKey);
  });
  el.addEventListener('auxclick', (e) => {
    if (e.button === 1) { e.preventDefault(); openPage(true); }
  });
  el.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openPage(e.ctrlKey || e.metaKey); }
  });
}

// Build the sidebar row + rail button once, beside the Intel Board ones.
function ensureEntries() {
  let side = document.getElementById('tool-deadlines-btn');
  const anchor = document.getElementById('tool-cases-btn');
  if (!side && anchor && anchor.parentNode) {
    side = document.createElement('div');
    side.className = 'list-item';
    side.id = 'tool-deadlines-btn';
    side.setAttribute('role', 'link');
    side.tabIndex = 0;
    side.title = 'Deadlines — snooze or mark done';
    side.innerHTML =
      '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ' +
      'stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0;opacity:0.5;" aria-hidden="true">' + ICON + '</svg>' +
      '<span class="grow">Deadlines</span>' +
      '<span id="deadline-badge" class="intel-badge deadline-badge" style="display:none" aria-live="polite"></span>';
    anchor.parentNode.insertBefore(side, anchor.nextSibling);
  }
  let rail = document.getElementById('rail-deadlines');
  const railAnchor = document.getElementById('rail-cases');
  if (!rail && railAnchor && railAnchor.parentNode) {
    rail = document.createElement('button');
    rail.className = 'icon-rail-btn';
    rail.id = 'rail-deadlines';
    rail.type = 'button';
    rail.title = 'Deadlines';
    rail.setAttribute('aria-label', 'Deadlines');
    rail.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
      'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + ICON + '</svg>';
    railAnchor.parentNode.insertBefore(rail, railAnchor.nextSibling);
  }
  syncVisibility();
  wire(side);
  wire(rail);
}

// Follow the Intel Board entry's Appearance toggle (hidden there → hidden here).
function syncVisibility() {
  const pairs = [['tool-cases-btn', 'tool-deadlines-btn'], ['rail-cases', 'rail-deadlines']];
  for (const [a, b] of pairs) {
    const src = document.getElementById(a);
    const dst = document.getElementById(b);
    if (!src || !dst) continue;
    const hidden = src.style.display === 'none' || src.hidden;
    dst.style.display = hidden ? 'none' : '';
  }
}

function render(status) {
  const text = badgeText(status);
  const label = badgeLabel(status);
  const side = document.getElementById('tool-deadlines-btn');
  const badge = document.getElementById('deadline-badge');
  const rail = document.getElementById('rail-deadlines');
  if (side) side.title = label;
  if (badge) {
    badge.textContent = text;
    badge.style.display = text ? '' : 'none';
    badge.setAttribute('aria-label', text ? `${text} overdue` : '');
  }
  if (rail) {
    rail.title = label;
    let rb = rail.querySelector('.rail-deadline-badge');
    if (text) {
      if (!rb) {
        rb = document.createElement('span');
        rb.className = 'rail-notes-badge rail-deadline-badge';
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
export async function refreshDeadlineStatus() {
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
  ensureEntries();
  // Appearance toggles flip the Intel Board entry's inline style; mirror it.
  const src = document.getElementById('tool-cases-btn');
  const rsrc = document.getElementById('rail-cases');
  if (window.MutationObserver) {
    const mo = new MutationObserver(syncVisibility);
    if (src) mo.observe(src, { attributes: true, attributeFilter: ['style', 'hidden'] });
    if (rsrc) mo.observe(rsrc, { attributes: true, attributeFilter: ['style', 'hidden'] });
  }
  refreshDeadlineStatus();
  setInterval(() => { if (!document.hidden) refreshDeadlineStatus(); }, POLL_MS);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshDeadlineStatus(); });
  window.refreshDeadlineStatus = refreshDeadlineStatus;
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
else init();
