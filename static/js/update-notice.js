// static/js/update-notice.js
// Odysseus auto-update, browser side (docs/auto-update.md).
//
// 1. Pull updates in when the app opens. Opening / refocusing a tab calls
//    POST /api/update/nudge: if the last check is stale the server starts the
//    normal background update run (throttled server-side; the install mode,
//    CI gate and quiet-moment restart still decide what actually happens).
// 2. Follow the server through restarts. A light heartbeat on /api/health
//    notices when the server goes away (update restart, relaunch, crash) and
//    shows a live "restarting" state instead of a page that silently breaks.
// 3. Come back on the new code by itself. When the server returns running
//    different code than this page was loaded from, the page reloads — but
//    never while you're typing, and any unsent text in the composer is carried
//    across the reload.
// Plus the original notices: "restart to finish", "updated · what's new",
// rolled back, update available (admins). Silent on any error.

const STATUS_URL = '/api/update/status';
const NUDGE_URL = '/api/update/nudge';
const HEALTH_URL = '/api/health';
const POLL_MS = 10 * 60 * 1000;          // full status refresh
const BEAT_MS = 20 * 1000;               // heartbeat while visible
const BEAT_EXPECT_MS = 4 * 1000;         // heartbeat while an update is under way
const BEAT_DOWN_MS = 1500;               // heartbeat while the server is away
const FIRST_DELAY_MS = 4000;
const RELOAD_COUNTDOWN_S = 4;
const TYPING_QUIET_MS = 4000;
const SEEN_KEY = 'odysseus-update-seen';
const DRAFTS_KEY = 'odysseus-update-drafts';
const RECENT_MS = 3 * 24 * 3600 * 1000;

let root = null;
let lastStatus = null;
let restarting = false;
let loadedHead = null;       // code this page was loaded from
let loadedBoot = null;       // server process this page was loaded from
let serverDown = false;
let downSince = 0;
let expectUntil = 0;         // heartbeat faster until then
let beatTimer = null;
let reloadTimer = null;
let reloadDeferred = false;
let lastKeyAt = 0;

function lsGet(k) { try { return localStorage.getItem(k); } catch { return null; } }
function lsSet(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } }
function ssGet(k) { try { return sessionStorage.getItem(k); } catch { return null; } }
function ssSet(k, v) { try { sessionStorage.setItem(k, v); } catch { /* private mode */ } }
function ssDel(k) { try { sessionStorage.removeItem(k); } catch { /* private mode */ } }

async function getJSON(url, opts) {
  const r = await fetch(url, { credentials: 'same-origin', cache: 'no-store', ...(opts || {}) });
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
  if (st.restart_needed && st.restart_requested && st.supervised) {
    return {
      kind: 'pending',
      text: 'Update downloaded — Odysseus restarts into it at the next quiet moment',
      action: st.is_admin ? 'Restart now' : null,
    };
  }
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
  // A developer clone in "follow" mode that can't fast-forward on its own.
  if (st.is_admin && st.mode === 'follow' && st.available && st.available.count > 0
      && (st.last_result === 'blocked_local_edits' || st.last_result === 'diverged')
      && seenSha !== 'blk:' + (st.available.sha || '') + ':' + st.last_result) {
    return {
      kind: 'blocked',
      text: st.last_result === 'diverged'
        ? `${st.available.count} update${st.available.count === 1 ? '' : 's'} waiting — your branch and origin have both moved`
        : `${st.available.count} update${st.available.count === 1 ? '' : 's'} waiting on your uncommitted edits`,
      details: [st.error, ...(st.available.commits || []).map((c) => c.subject)].filter(Boolean),
      seen: 'blk:' + (st.available.sha || '') + ':' + st.last_result,
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

// Exported for tests: is it safe to reload the page right now?
export function safeToReload(doc, now = Date.now(), lastKey = 0) {
  if (now - lastKey < TYPING_QUIET_MS) return false;
  const a = doc && doc.activeElement;
  if (a && a !== doc.body) {
    const tag = (a.tagName || '').toLowerCase();
    const editable = a.isContentEditable || tag === 'textarea'
      || (tag === 'input' && !/^(button|submit|checkbox|radio|range|color|file|reset|image)$/i.test(a.type || ''));
    if (editable && String(a.value ?? a.textContent ?? '').trim()) return false;
  }
  // A modal or a file picker in use: wait.
  if (doc && doc.querySelector && doc.querySelector('dialog[open]')) return false;
  return true;
}

// ── drafts survive the reload ───────────────────────────────────────────────
function saveDrafts() {
  const drafts = {};
  document.querySelectorAll('textarea[id], [contenteditable="true"][id]').forEach((el) => {
    const v = el.tagName === 'TEXTAREA' ? el.value : el.innerText;
    if (v && v.trim()) drafts[el.id] = { v, html: el.tagName !== 'TEXTAREA' };
  });
  if (Object.keys(drafts).length) ssSet(DRAFTS_KEY, JSON.stringify({ t: Date.now(), drafts }));
}

function restoreDrafts() {
  const raw = ssGet(DRAFTS_KEY);
  if (!raw) return;
  let data;
  try { data = JSON.parse(raw); } catch { ssDel(DRAFTS_KEY); return; }
  if (!data || Date.now() - data.t > 10 * 60 * 1000) { ssDel(DRAFTS_KEY); return; }
  const pending = { ...data.drafts };
  let tries = 0;
  const attempt = () => {
    for (const [id, d] of Object.entries(pending)) {
      const el = document.getElementById(id);
      if (!el) continue;
      const cur = el.tagName === 'TEXTAREA' ? el.value : el.innerText;
      if (!cur || !cur.trim()) {
        if (el.tagName === 'TEXTAREA') el.value = d.v; else el.innerText = d.v;
        el.dispatchEvent(new Event('input', { bubbles: true }));
      }
      delete pending[id];
    }
    if (Object.keys(pending).length && ++tries < 20) setTimeout(attempt, 250);
    else ssDel(DRAFTS_KEY);
  };
  attempt();
}

// ── view ────────────────────────────────────────────────────────────────────
function css() {
  return `
  .ody-upd{position:fixed;left:16px;bottom:16px;z-index:9000;max-width:min(440px,calc(100vw - 32px));
    font:13px/1.4 var(--font-sans,system-ui,-apple-system,"Segoe UI",sans-serif);
    color:var(--text-primary,var(--fg,#e8eef5));
    background:color-mix(in srgb,var(--bg-secondary,var(--panel,#0f1720)) 92%,transparent);
    border:1px solid var(--accent-primary,var(--accent,#4fd8ff));border-radius:12px;
    box-shadow:0 0 0 1px rgba(0,0,0,.2),0 10px 30px rgba(0,0,0,.35),0 0 18px color-mix(in srgb,var(--accent-primary,#4fd8ff) 25%,transparent);
    backdrop-filter:blur(8px);padding:10px 12px;display:flex;flex-direction:column;gap:8px;overflow:hidden;
    animation:ody-upd-in .28s cubic-bezier(.2,.8,.2,1)}
  .ody-upd[hidden]{display:none}
  @keyframes ody-upd-in{from{opacity:0;transform:translateY(8px) scale(.98)}to{opacity:1;transform:none}}
  .ody-upd-row{display:flex;align-items:center;gap:10px}
  .ody-upd-dot{flex:none;width:8px;height:8px;border-radius:50%;background:var(--accent-primary,#4fd8ff);
    box-shadow:0 0 8px var(--accent-primary,#4fd8ff)}
  .ody-upd[data-kind=rollback] .ody-upd-dot,.ody-upd[data-kind=blocked] .ody-upd-dot{background:#f5a524;box-shadow:0 0 8px #f5a524}
  .ody-upd[data-kind=down] .ody-upd-dot,.ody-upd[data-kind=pending] .ody-upd-dot{animation:ody-upd-pulse 1.2s ease-in-out infinite}
  .ody-upd[data-kind=back] .ody-upd-dot{background:#3ddc97;box-shadow:0 0 10px #3ddc97}
  @keyframes ody-upd-pulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.35;transform:scale(.7)}}
  .ody-upd-text{flex:1;min-width:0}
  .ody-upd-actions{display:flex;flex-wrap:wrap;gap:6px;padding-left:18px}
  .ody-upd-sub{display:block;opacity:.65;font-size:12px;font-variant-numeric:tabular-nums}
  .ody-upd-bar{position:absolute;left:0;right:0;bottom:0;height:2px;overflow:hidden}
  .ody-upd-bar::after{content:"";position:absolute;inset:0;width:40%;
    background:linear-gradient(90deg,transparent,var(--accent-primary,#4fd8ff),transparent);
    animation:ody-upd-scan 1.4s linear infinite}
  .ody-upd[data-kind=back] .ody-upd-bar::after{width:100%;animation:ody-upd-fill var(--ody-upd-cd,4s) linear forwards;
    background:linear-gradient(90deg,#3ddc97,var(--accent-primary,#4fd8ff));transform-origin:left}
  @keyframes ody-upd-scan{from{transform:translateX(-100%)}to{transform:translateX(250%)}}
  @keyframes ody-upd-fill{from{transform:scaleX(0)}to{transform:scaleX(1)}}
  .ody-upd button{font:inherit;cursor:pointer;border-radius:8px;padding:4px 10px;
    border:1px solid var(--accent-primary,#4fd8ff);background:transparent;color:inherit;white-space:nowrap}
  .ody-upd button.primary{background:var(--accent-primary,#4fd8ff);color:#04121a;font-weight:600}
  .ody-upd button.x{border:none;padding:2px 6px;opacity:.7}
  .ody-upd button:disabled{opacity:.6;cursor:default}
  .ody-upd button:focus-visible{outline:2px solid var(--accent-primary,#4fd8ff);outline-offset:2px}
  .ody-upd ul{margin:0;padding:0 0 0 18px;max-height:180px;overflow:auto;opacity:.85}
  .ody-upd ul[hidden]{display:none}
  @media (prefers-reduced-motion:reduce){.ody-upd,.ody-upd *,.ody-upd-bar::after{animation:none!important}}`;
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

function button(label, onClick, cls) {
  const b = document.createElement('button');
  b.type = 'button';
  if (cls) b.className = cls;
  b.textContent = label;
  b.onclick = onClick;
  return b;
}

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
  if (n.sub) {
    const sub = document.createElement('span');
    sub.className = 'ody-upd-sub';
    sub.textContent = n.sub;
    text.appendChild(sub);
  }
  row.append(dot, text);

  let list = null;
  let more = null;
  const controls = [];
  if (n.details && n.details.length) {
    list = document.createElement('ul');
    list.hidden = true;
    for (const d of n.details.slice(0, 30)) {
      const li = document.createElement('li');
      li.textContent = d;
      list.appendChild(li);
    }
    more = button(n.kind === 'blocked' ? 'Details' : 'What’s new', () => {
      list.hidden = !list.hidden;
      more.setAttribute('aria-expanded', String(!list.hidden));
    });
    more.setAttribute('aria-expanded', 'false');
  }
  if (n.buttons) for (const b of n.buttons) controls.push(button(b.label, b.onClick, b.cls));
  if (n.action) {
    const b = button(n.action, () => (n.kind === 'restart' || n.kind === 'pending' ? restartNow(b) : installNow(b)), 'primary');
    controls.push(b);
  }
  if (more) controls.push(more);
  // One control fits beside the text; more go on their own row so the
  // message never gets squeezed into a narrow column.
  let actions = null;
  if (controls.length === 1) row.appendChild(controls[0]);
  else if (controls.length > 1) {
    actions = document.createElement('div');
    actions.className = 'ody-upd-actions';
    actions.append(...controls);
  }
  if (n.seen || n.kind === 'available') {
    const x = button('✕', () => { if (n.seen) lsSet(SEEN_KEY, n.seen); hide(); }, 'x');
    x.setAttribute('aria-label', 'Dismiss');
    row.appendChild(x);
  }
  el.appendChild(row);
  if (actions) el.appendChild(actions);
  if (list) el.appendChild(list);
  if (n.bar) {
    const bar = document.createElement('div');
    bar.className = 'ody-upd-bar';
    el.appendChild(bar);
  }
}

function short(sha) { return (sha || '').slice(0, 7); }

// ── restart following ──────────────────────────────────────────────────────
function renderDown() {
  const secs = Math.max(0, Math.round((Date.now() - downSince) / 1000));
  const updating = lastStatus && (lastStatus.restart_requested || lastStatus.updating || lastStatus.restart_needed);
  render({
    kind: 'down',
    text: updating ? 'Restarting into the update…' : 'Reconnecting to Odysseus…',
    sub: secs >= 3 ? `${secs}s · this tab comes back by itself` : 'this tab comes back by itself',
    bar: true,
  });
}

function scheduleBeat(ms) {
  clearTimeout(beatTimer);
  beatTimer = setTimeout(beat, ms);
}

function nextBeatMs() {
  if (serverDown) return BEAT_DOWN_MS;
  if (Date.now() < expectUntil) return BEAT_EXPECT_MS;
  return BEAT_MS;
}

async function beat() {
  if (typeof document !== 'undefined' && document.hidden && !serverDown) return; // resumes on visibility
  let ok = false;
  try {
    const r = await fetch(HEALTH_URL, { cache: 'no-store', credentials: 'same-origin' });
    ok = r.ok;
  } catch { ok = false; }

  if (!ok) {
    if (!serverDown) { serverDown = true; downSince = Date.now(); restarting = true; }
    renderDown();
  } else if (serverDown) {
    serverDown = false;
    await onServerBack();
  } else if (Date.now() < expectUntil) {
    await poll();
  }
  scheduleBeat(nextBeatMs());
}

async function onServerBack() {
  let st = null;
  for (let i = 0; i < 5 && !st; i++) {
    try { st = await getJSON(STATUS_URL); } catch (e) {
      if (e.status === 401 || e.status === 403) { location.reload(); return; } // new session needed
      await new Promise((r) => setTimeout(r, 800));
    }
  }
  restarting = false;
  if (!st) { hide(); return; }
  lastStatus = st;
  // The page's HTML/JS came from the code on disk when it loaded (loadedHead).
  // Reload only when the server now runs different code than that.
  if (loadedHead && st.boot_sha && st.boot_sha !== loadedHead) {
    startReloadCountdown(st);
  } else {
    // Same code, server just bounced: the page keeps working.
    render({ kind: 'back', text: 'Reconnected', sub: 'Odysseus is back', bar: false });
    setTimeout(() => { if (root && root.dataset.kind === 'back' && !reloadTimer) render(noticeFor(st, lsGet(SEEN_KEY))); }, 2500);
  }
}

function startReloadCountdown(st) {
  clearTimeout(reloadTimer);
  const head = short(st.boot_sha || st.head);
  const changes = Array.isArray(st.changes) ? st.changes : [];
  const details = changes.map((c) => c.subject);
  const what = changes.length ? ` · ${changes.length} change${changes.length === 1 ? '' : 's'}` : '';
  const btns = [{ label: 'Reload now', cls: 'primary', onClick: reloadNow }, { label: 'Later', onClick: deferReload }];
  if (reloadDeferred) {
    render({ kind: 'restart', text: `Odysseus is running a newer version (${head})`,
      sub: 'reload when you’re ready · your draft is kept',
      details, buttons: [{ label: 'Reload', cls: 'primary', onClick: reloadNow }] });
    return;
  }
  let left = RELOAD_COUNTDOWN_S;
  let mode = '';
  const sub = () => root && root.querySelector('.ody-upd-sub');
  const tick = () => {
    if (!safeToReload(document, Date.now(), lastKeyAt)) {
      if (mode !== 'wait') {
        mode = 'wait';
        render({ kind: 'back', text: `Updated to ${head}${what}`,
          sub: 'reloads when you stop typing · your draft is kept', details, buttons: btns });
      }
      left = RELOAD_COUNTDOWN_S;
      reloadTimer = setTimeout(tick, 1000);
      return;
    }
    if (mode !== 'count') {
      mode = 'count';
      render({ kind: 'back', text: `Updated to ${head}${what}`, sub: `reloading in ${left}s`,
        details, buttons: btns, bar: true });
      if (root) root.style.setProperty('--ody-upd-cd', left + 's');
    } else if (sub()) {
      sub().textContent = `reloading in ${left}s`;
    }
    if (left <= 0) { reloadNow(); return; }
    left -= 1;
    reloadTimer = setTimeout(tick, 1000);
  };
  tick();
}

function deferReload() {
  clearTimeout(reloadTimer);
  reloadTimer = null;
  reloadDeferred = true;
  if (lastStatus) startReloadCountdown(lastStatus);
}

function reloadNow() {
  clearTimeout(reloadTimer);
  reloadTimer = null;
  saveDrafts();
  location.reload();
}

// ── actions ─────────────────────────────────────────────────────────────────
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
  // The heartbeat takes it from here: down → back on new code → reload.
  restarting = true;
  reloadDeferred = false;
  expectUntil = Date.now() + 5 * 60 * 1000;
  render({ kind: 'down', text: 'Restarting Odysseus…', sub: 'this tab comes back by itself', bar: true });
  scheduleBeat(800);
}

async function installNow(btn) {
  btn.disabled = true;
  btn.textContent = 'Installing…';
  try {
    await getJSON('/api/update/apply', { method: 'POST' });
    expectUntil = Date.now() + 10 * 60 * 1000;
    render({ kind: 'pending', text: 'Installing the update — Odysseus restarts when it’s ready', bar: true });
    scheduleBeat(BEAT_EXPECT_MS);
  } catch {
    btn.disabled = false;
    btn.textContent = 'Install';
  }
}

function absorb(st) {
  lastStatus = st;
  if (loadedHead === null) loadedHead = st.head || '';
  if (loadedBoot === null) loadedBoot = st.boot_sha || '';
  // Something is moving: watch closely for a few minutes.
  if (st.checking || st.updating || st.restart_requested) expectUntil = Math.max(expectUntil, Date.now() + 3 * 60 * 1000);
  // Code on disk moved but this server hasn't restarted, and this page is
  // already behind the server (another tab / launcher restarted it): reload.
  if (!serverDown && loadedHead && st.boot_sha && st.boot_sha !== loadedHead && st.boot_sha !== loadedBoot) {
    startReloadCountdown(st);
    return;
  }
  if (!reloadTimer) render(noticeFor(st, lsGet(SEEN_KEY)));
}

async function poll() {
  if (restarting || serverDown) return;
  try {
    absorb(await getJSON(STATUS_URL));
  } catch (e) {
    if (e.status === 401 || e.status === 403) return; // signed out: stay quiet
  }
}

// Opening or refocusing the app: pull updates in (server-throttled).
async function nudge() {
  if (restarting || serverDown) return;
  try {
    absorb(await getJSON(NUDGE_URL, { method: 'POST' }));
  } catch (e) {
    if (e.status === 404 || e.status === 405) await poll(); // older server
  }
  scheduleBeat(nextBeatMs());
}

if (typeof window !== 'undefined' && typeof document !== 'undefined') {
  const start = () => {
    restoreDrafts();
    document.addEventListener('keydown', () => { lastKeyAt = Date.now(); }, true);
    document.addEventListener('input', () => { lastKeyAt = Date.now(); }, true);
    setTimeout(nudge, FIRST_DELAY_MS);
    setInterval(poll, POLL_MS);
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden) { nudge(); scheduleBeat(300); }
    });
    window.addEventListener('online', () => scheduleBeat(200));
    window.addEventListener('pageshow', (ev) => { if (ev.persisted) nudge(); }); // back/forward cache
    window.addEventListener('beforeunload', () => { if (serverDown || reloadTimer) saveDrafts(); });
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
}
