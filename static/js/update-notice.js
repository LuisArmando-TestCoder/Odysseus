// static/js/update-notice.js
// Small, self-contained notice for Odysseus' auto-update (docs/auto-update.md).
// Data: GET /api/update/status. Shows a pill in the bottom-left only when
// there's something to say:
//   * the code on disk is newer than the running server -> "Restart to finish"
//   * Odysseus just updated itself -> "Updated · what's new" (dismissible)
//   * an update failed and was rolled back (admins)
//   * an update is available but not auto-installed (admins: dev clone / off)
// Admin actions: restart now, install now. Silent on any error.

const STATUS_URL = '/api/update/status';
const POLL_MS = 10 * 60 * 1000;
const FIRST_DELAY_MS = 6000;
const SEEN_KEY = 'odysseus-update-seen';
const RECENT_MS = 3 * 24 * 3600 * 1000;

let root = null;
let lastStatus = null;
let restarting = false;

function lsGet(k) { try { return localStorage.getItem(k); } catch { return null; } }
function lsSet(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } }

async function getJSON(url, opts) {
  const r = await fetch(url, { credentials: 'same-origin', ...(opts || {}) });
  if (!r.ok) {
    const err = new Error('HTTP ' + r.status);
    err.status = r.status;
    try { err.body = await r.json(); } catch { /* not json */ }
    throw err;
  }
  return r.json();
}

// Exported for tests: which notice (if any) a status should produce.
export function noticeFor(st, seenSha, now = Date.now()) {
  if (!st) return null;
  if (st.restart_needed) {
    return {
      kind: 'restart',
      text: 'Update installed — restart Odysseus to finish',
      action: st.is_admin && st.supervised ? 'Restart now' : null,
    };
  }
  const lastEvt = (st.history || []).slice(-1)[0];
  if (st.is_admin && st.rolled_back_from && lastEvt && lastEvt.event === 'rollback'
      && now - lastEvt.t * 1000 < RECENT_MS && seenSha !== 'rb:' + st.rolled_back_from) {
    return { kind: 'rollback', text: 'An update didn’t start correctly, so Odysseus went back to the previous version', seen: 'rb:' + st.rolled_back_from };
  }
  const changes = Array.isArray(st.changes) ? st.changes : [];
  if (st.updated_at && now - st.updated_at * 1000 < RECENT_MS && st.head && seenSha !== st.head && changes.length) {
    return {
      kind: 'updated',
      text: `Odysseus updated itself · ${changes.length} change${changes.length === 1 ? '' : 's'}`,
      details: changes.map((c) => c.subject),
      seen: st.head,
    };
  }
  if (st.is_admin && st.available && st.available.count > 0 && (st.mode === 'dev' || st.enabled === false)) {
    return {
      kind: 'available',
      text: `${st.available.count} update${st.available.count === 1 ? '' : 's'} available on ${st.branch || 'main'}`,
      details: (st.available.commits || []).map((c) => c.subject),
      action: 'Install',
    };
  }
  return null;
}

function css() {
  return `
  .ody-upd{position:fixed;left:16px;bottom:16px;z-index:9000;max-width:min(420px,calc(100vw - 32px));
    font:13px/1.4 var(--font-sans,system-ui,-apple-system,"Segoe UI",sans-serif);
    color:var(--text-primary,var(--fg,#e8eef5));
    background:color-mix(in srgb,var(--bg-secondary,var(--panel,#0f1720)) 92%,transparent);
    border:1px solid var(--accent-primary,var(--accent,#4fd8ff));border-radius:12px;
    box-shadow:0 0 0 1px rgba(0,0,0,.2),0 10px 30px rgba(0,0,0,.35),0 0 18px color-mix(in srgb,var(--accent-primary,#4fd8ff) 25%,transparent);
    backdrop-filter:blur(8px);padding:10px 12px;display:flex;flex-direction:column;gap:8px}
  .ody-upd-row{display:flex;align-items:center;gap:10px}
  .ody-upd-dot{flex:none;width:8px;height:8px;border-radius:50%;background:var(--accent-primary,#4fd8ff);
    box-shadow:0 0 8px var(--accent-primary,#4fd8ff)}
  .ody-upd[data-kind=rollback] .ody-upd-dot{background:#f5a524;box-shadow:0 0 8px #f5a524}
  .ody-upd-text{flex:1;min-width:0}
  .ody-upd button{font:inherit;cursor:pointer;border-radius:8px;padding:4px 10px;
    border:1px solid var(--accent-primary,#4fd8ff);background:transparent;color:inherit}
  .ody-upd button.primary{background:var(--accent-primary,#4fd8ff);color:#04121a;font-weight:600}
  .ody-upd button.x{border:none;padding:2px 6px;opacity:.7}
  .ody-upd button:focus-visible{outline:2px solid var(--accent-primary,#4fd8ff);outline-offset:2px}
  .ody-upd ul{margin:0;padding:0 0 0 18px;max-height:180px;overflow:auto;opacity:.85}
  .ody-upd ul[hidden]{display:none}`;
}

function ensureRoot() {
  if (root) return root;
  const style = document.createElement('style');
  style.textContent = css();
  document.head.appendChild(style);
  root = document.createElement('div');
  root.className = 'ody-upd';
  root.setAttribute('role', 'status');
  root.setAttribute('aria-live', 'polite');
  document.body.appendChild(root);
  return root;
}

function hide() { if (root) root.hidden = true; }

function render(n) {
  if (!n) { hide(); return; }
  const el = ensureRoot();
  el.hidden = false;
  el.dataset.kind = n.kind;
  el.textContent = '';

  const row = document.createElement('div');
  row.className = 'ody-upd-row';
  const dot = document.createElement('span');
  dot.className = 'ody-upd-dot';
  const text = document.createElement('span');
  text.className = 'ody-upd-text';
  text.textContent = n.text;
  row.append(dot, text);

  let list = null;
  if (n.details && n.details.length) {
    list = document.createElement('ul');
    list.hidden = true;
    for (const d of n.details.slice(0, 30)) {
      const li = document.createElement('li');
      li.textContent = d;
      list.appendChild(li);
    }
    const more = document.createElement('button');
    more.type = 'button';
    more.textContent = 'What’s new';
    more.setAttribute('aria-expanded', 'false');
    more.onclick = () => {
      list.hidden = !list.hidden;
      more.setAttribute('aria-expanded', String(!list.hidden));
    };
    row.appendChild(more);
  }
  if (n.action) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'primary';
    b.textContent = n.action;
    b.onclick = () => (n.kind === 'restart' ? restartNow(b) : installNow(b));
    row.appendChild(b);
  }
  if (n.seen || n.kind === 'available') {
    const x = document.createElement('button');
    x.type = 'button';
    x.className = 'x';
    x.setAttribute('aria-label', 'Dismiss');
    x.textContent = '✕';
    x.onclick = () => { if (n.seen) lsSet(SEEN_KEY, n.seen); hide(); };
    row.appendChild(x);
  }
  el.appendChild(row);
  if (list) el.appendChild(list);
}

async function restartNow(btn) {
  btn.disabled = true;
  btn.textContent = 'Restarting…';
  try {
    await getJSON('/api/update/restart', { method: 'POST' });
  } catch (e) {
    btn.disabled = false;
    btn.textContent = 'Restart now';
    const msg = (e.body && e.body.detail) || 'Restart failed';
    render({ kind: 'restart', text: msg });
    return;
  }
  restarting = true;
  const before = lastStatus && lastStatus.boot_sha;
  const deadline = Date.now() + 5 * 60 * 1000;
  // Wait for a new server process (different boot sha), then reload.
  const tick = async () => {
    if (Date.now() > deadline) { restarting = false; render({ kind: 'restart', text: 'Still restarting — reload the page in a moment' }); return; }
    try {
      const st = await getJSON(STATUS_URL);
      if (st.boot_sha && st.boot_sha !== before && !st.restart_needed) { location.reload(); return; }
    } catch { /* server down while restarting */ }
    setTimeout(tick, 2500);
  };
  setTimeout(tick, 3000);
}

async function installNow(btn) {
  btn.disabled = true;
  btn.textContent = 'Installing…';
  try {
    await getJSON('/api/update/apply', { method: 'POST' });
    render({ kind: 'available', text: 'Installing the update — Odysseus restarts when it’s ready' });
  } catch {
    btn.disabled = false;
    btn.textContent = 'Install';
  }
}

async function poll() {
  if (restarting) return;
  try {
    lastStatus = await getJSON(STATUS_URL);
    render(noticeFor(lastStatus, lsGet(SEEN_KEY)));
  } catch (e) {
    if (e.status === 401 || e.status === 403) return; // signed out: stay quiet
  }
}

if (typeof window !== 'undefined' && typeof document !== 'undefined') {
  const start = () => {
    setTimeout(poll, FIRST_DELAY_MS);
    setInterval(poll, POLL_MS);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) poll(); });
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
}
