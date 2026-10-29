// static/js/hubDashboard.js
// Agent Dashboard — one screen for everything Odysseus is doing and has made:
//   Overview  running turns (chat / autopilot / goal rounds), plans, the goal,
//             sub-agents, background research — each with Open / Stop.
//   Outputs   recently changed documents + live artifacts (open / copy link).
//   Board     findings & decisions shared between chats (+ post one).
//   Search    full-transcript search across every chat (simple syntax).
//   Usage     cost, tokens and calls by source / model / tool / MCP server /
//             chat, plus each chat's context footprint.
// Opened by the sidebar "Dashboard" button, /dashboard, /search, /usage, /board.

const API = '';
let _root = null, _tab = 'overview', _timer = 0, _lastData = null;

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const ago = (ts) => {
  if (!ts) return '';
  const t = typeof ts === 'number' ? ts * (ts < 2e10 ? 1000 : 1) : Date.parse(String(ts).replace(' ', 'T') + (String(ts).includes('Z') || String(ts).includes('+') ? '' : 'Z'));
  const s = Math.max(0, Math.round((Date.now() - t) / 1000));
  if (s < 60) return s + 's ago';
  if (s < 3600) return Math.round(s / 60) + 'm ago';
  if (s < 86400) return (s / 3600).toFixed(1) + 'h ago';
  return Math.round(s / 86400) + 'd ago';
};
const usd = (v) => v == null ? '—' : (v < 0.01 && v > 0 ? '<$0.01' : '$' + Number(v).toFixed(2));
const num = (n) => n == null ? '—' : n >= 1e6 ? (n / 1e6).toFixed(1) + 'M' : n >= 1e3 ? (n / 1e3).toFixed(1) + 'k' : String(n);

function openChat(sid) {
  if (!sid) return;
  close();
  try { window.sessionModule?.selectSession?.(sid); } catch (_) { location.hash = '#' + sid; }
}

async function j(url, opts) {
  const r = await fetch(API + url, { credentials: 'same-origin', ...(opts || {}) });
  if (!r.ok) throw new Error((await r.text()).slice(0, 200) || ('HTTP ' + r.status));
  return r.json();
}

function shell() {
  const el = document.createElement('div');
  el.className = 'hubdash-overlay';
  el.innerHTML = `
    <div class="hubdash" role="dialog" aria-modal="true" aria-label="Agent dashboard">
      <header class="hd-top">
        <span class="hd-title">Dashboard</span>
        <nav class="hd-tabs" role="tablist">
          ${['overview', 'outputs', 'board', 'search', 'usage'].map(t =>
            `<button type="button" role="tab" data-tab="${t}">${t[0].toUpperCase() + t.slice(1)}</button>`).join('')}
        </nav>
        <span class="hd-live" title="Refreshes every 3 seconds"></span>
        <button type="button" class="hd-close" aria-label="Close">✕</button>
      </header>
      <section class="hd-body"></section>
    </div>`;
  el.addEventListener('click', (e) => { if (e.target === el) close(); });
  el.querySelector('.hd-close').addEventListener('click', close);
  el.querySelectorAll('.hd-tabs button').forEach(b => b.addEventListener('click', () => show(b.dataset.tab)));
  window.addEventListener('keydown', _esc, true);
  document.body.appendChild(el);
  return el;
}
function _esc(e) { if (e.key === 'Escape' && _root) { e.stopPropagation(); close(); } }

export function close() {
  clearInterval(_timer); _timer = 0;
  window.removeEventListener('keydown', _esc, true);
  if (_root) _root.remove();
  _root = null;
}

export function open(tab = 'overview', arg = '') {
  if (!_root) _root = shell();
  show(tab, arg);
}

function show(tab, arg) {
  _tab = tab;
  _root.querySelectorAll('.hd-tabs button').forEach(b => b.setAttribute('aria-selected', String(b.dataset.tab === tab)));
  clearInterval(_timer); _timer = 0;
  const body = _root.querySelector('.hd-body');
  body.innerHTML = '<p class="hd-empty">Loading…</p>';
  if (tab === 'overview' || tab === 'outputs') {
    const load = async () => {
      try { _lastData = await j('/api/hub/dashboard'); if (_tab === tab) (tab === 'overview' ? renderOverview : renderOutputs)(body, _lastData); }
      catch (e) { body.innerHTML = `<p class="hd-empty">Could not load: ${esc(e.message)}</p>`; }
    };
    load();
    _timer = setInterval(() => { if (!document.hidden) load(); }, 3000);
  } else if (tab === 'board') renderBoard(body);
  else if (tab === 'search') renderSearch(body, arg);
  else if (tab === 'usage') renderUsage(body);
}

// ── Overview ────────────────────────────────────────────────────────────────
function renderOverview(body, d) {
  const running = (d.runs || []).filter(r => r.status === 'running');
  const kindLabel = { chat: 'Chat turn', autopilot: 'Autopilot', goal: 'Goal round', queued: 'Queued turn' };
  const runsHtml = running.length ? running.map(r => `
      <li class="hd-row">
        <span class="hd-pulse"></span>
        <span class="hd-main"><b>${esc(r.name)}</b><small>${esc(kindLabel[r.kind] || r.kind)}${r.label ? ' · ' + esc(r.label) : ''} · started ${ago(r.started_at)}${r.queued ? ' · ' + r.queued + ' queued' : ''}${r.autopilot && r.autopilot.auto_turns ? ' · auto turn ' + r.autopilot.auto_turns + '/' + r.autopilot.max_turns : ''}</small></span>
        <button type="button" data-open="${esc(r.session_id)}">Open</button>
        <button type="button" class="hd-danger" data-stop="${esc(r.session_id)}">Stop</button>
      </li>`).join('') : '<li class="hd-empty">Nothing running right now.</li>';
  const plans = (d.plans || []).map(p => {
    const total = p.done.length + p.remaining.length;
    const pct = total ? Math.round(p.done.length / total * 100) : 0;
    return `<li class="hd-row hd-plan">
        <span class="hd-main"><b>${esc(p.name)}</b><small>${p.done.length}/${total} done${p.remaining.length ? ' · now: ' + esc(p.current || p.remaining[0]) : ' · finished'} · ${ago(p.updated_at)}</small>
          <i class="hd-bar"><i style="width:${pct}%"></i></i></span>
        <button type="button" data-open="${esc(p.session_id)}">Open</button>
      </li>`;
  }).join('') || '<li class="hd-empty">No plans yet — the agent posts one for any multi-step task.</li>';
  const g = d.goal;
  const goalHtml = g ? `<li class="hd-row"><span class="hd-main"><b>${esc(g.objective)}</b><small>${esc(g.phase)} · round ${g.roundsStarted || 0}/${g.maxGoalRounds || '∞'} · in ${esc(g.session_name || '')}</small></span>
      ${g.session_id ? `<button type="button" data-open="${esc(g.session_id)}">Open</button>` : ''}</li>` : '<li class="hd-empty">No goal. Start one with /goal &lt;objective&gt;.</li>';
  const subs = (d.subagents || []).map(s => `<li class="hd-row"><span class="hd-main"><b>${esc(s.task || s.id)}</b><small>${esc(s.status)} · ${esc(s.mode || '')} · from ${esc(s.parent_name || '')}</small></span>
      ${s.session_id ? `<button type="button" data-open="${esc(s.session_id)}">Open</button>` : ''}</li>`).join('') || '<li class="hd-empty">No sub-agents.</li>';
  const res = (d.research || []).map(r => `<li class="hd-row"><span class="hd-pulse"></span><span class="hd-main"><b>${esc(r.query)}</b><small>${esc(r.status)} · ${ago(r.started_at)}</small></span>
      <button type="button" data-open="${esc(r.session_id)}">Open</button></li>`).join('') || '<li class="hd-empty">No background research.</li>';
  const u = d.usage && d.usage.totals;
  body.innerHTML = `
    <div class="hd-kpis">
      <div><b>${running.length}</b><span>running</span></div>
      <div><b>${(d.plans || []).filter(p => p.remaining.length).length}</b><span>open plans</span></div>
      <div><b>${(d.subagents || []).filter(s => s.status === 'running').length}</b><span>sub-agents active</span></div>
      <div><b>${(d.research || []).length}</b><span>research threads</span></div>
      <div><b>${u ? usd(u.usd) : '—'}</b><span>spent · 7 days</span></div>
    </div>
    <div class="hd-grid">
      <section><h3>Running now</h3><ul>${runsHtml}</ul></section>
      <section><h3>Plans</h3><ul>${plans}</ul></section>
      <section><h3>Goal</h3><ul>${goalHtml}</ul></section>
      <section><h3>Sub-agents</h3><ul>${subs}</ul></section>
      <section><h3>Background research</h3><ul>${res}</ul></section>
    </div>`;
  wire(body);
}

// ── Outputs ─────────────────────────────────────────────────────────────────
function renderOutputs(body, d) {
  const liveByDoc = new Map((d.live || []).map(l => [l.doc_id, l]));
  const docs = (d.outputs || []).map(o => {
    const l = liveByDoc.get(o.id);
    return `<li class="hd-row"><span class="hd-main"><b>${esc(o.title || 'Untitled')}</b><small>${esc(o.language || '')} · v${o.version_count || 1} · ${ago(o.updated_at)}${o.session_name ? ' · ' + esc(o.session_name) : ''}</small></span>
      <button type="button" data-doc="${esc(o.id)}">Open</button>
      ${l ? `<a class="hd-btn" href="${esc(l.url)}" target="_blank" rel="noopener">Live ↗</a>` : `<button type="button" data-publish="${esc(o.id)}">Publish live</button>`}</li>`;
  }).join('') || '<li class="hd-empty">No documents yet.</li>';
  const live = (d.live || []).map(l => `<li class="hd-row"><span class="hd-pulse"></span><span class="hd-main"><b>${esc(l.title)}</b><small>v${l.version} · updated ${ago(l.updated_at)}${l.allow_mcp ? ' · live MCP data' : ''}</small></span>
      <a class="hd-btn" href="${esc(l.url)}" target="_blank" rel="noopener">Open ↗</a>
      <button type="button" data-copy="${esc(location.origin + l.url)}">Copy link</button>
      <button type="button" class="hd-danger" data-unpublish="${esc(l.token)}">Unpublish</button></li>`).join('') || '<li class="hd-empty">No live pages. Reports the agent writes are published automatically.</li>';
  body.innerHTML = `<div class="hd-grid hd-grid-2"><section><h3>Recent documents</h3><ul>${docs}</ul></section><section><h3>Live pages</h3><ul>${live}</ul></section></div>`;
  wire(body);
}

// ── Board ───────────────────────────────────────────────────────────────────
async function renderBoard(body) {
  body.innerHTML = `
    <form class="hd-post"><textarea rows="2" placeholder="Share a finding or decision with every chat (they receive it automatically)…"></textarea>
      <select aria-label="Kind"><option value="finding">Finding</option><option value="decision">Decision</option><option value="note">Note</option></select>
      <button type="submit">Share</button></form>
    <ul class="hd-board"><li class="hd-empty">Loading…</li></ul>`;
  const form = body.querySelector('form');
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const text = form.querySelector('textarea').value.trim();
    if (!text) return;
    const from = window.sessionModule?.getCurrentSessionId?.() || null;
    try {
      await j('/api/hub/board', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text, kind: form.querySelector('select').value, from_session: from }) });
      form.querySelector('textarea').value = '';
      load();
    } catch (err) { alert('Could not share: ' + err.message); }
  });
  const load = async () => {
    const d = await j('/api/hub/board');
    const ul = body.querySelector('.hd-board');
    ul.innerHTML = (d.items || []).map(it => `<li class="hd-card"><div class="hd-card-h"><span class="hd-kind ${esc(it.kind)}">${esc(it.kind)}</span><b>${esc(it.title)}</b><small>${ago(it.ts)}${it.from_name ? ' · from ' + esc(it.from_name) : ''}${it.to_name ? ' → ' + esc(it.to_name) : ''}</small>
        ${it.from_session ? `<button type="button" data-open="${esc(it.from_session)}">Open source</button>` : ''}</div><p>${esc(it.text)}</p></li>`).join('')
      || '<li class="hd-empty">Nothing shared yet. Chats post key findings here automatically.</li>';
    wire(ul);
  };
  load();
}

// ── Search ──────────────────────────────────────────────────────────────────
function renderSearch(body, q0 = '') {
  body.innerHTML = `
    <form class="hd-search"><input type="search" placeholder='Search every chat… e.g. "auth bug" in:hn role:ai tool:bash after:2026-10-01 · or paste a PR URL' aria-label="Search all chats">
      <button type="submit">Search</button></form>
    <p class="hd-hint">Syntax: words · "exact phrase" · -exclude · in:&lt;chat&gt; · role:user|ai · tool:&lt;name&gt; · after:/before:YYYY-MM-DD · a URL finds chats that mention it (PR links also match owner/repo#123).</p>
    <div class="hd-results"></div>`;
  const input = body.querySelector('input');
  const out = body.querySelector('.hd-results');
  const run = async () => {
    const q = input.value.trim();
    if (!q) { out.innerHTML = ''; return; }
    out.innerHTML = '<p class="hd-empty">Searching…</p>';
    try {
      const d = await j('/api/hub/search?q=' + encodeURIComponent(q) + '&limit=60');
      if (!d.results.length) { out.innerHTML = '<p class="hd-empty">No matches.</p>'; return; }
      const terms = [...(d.query.terms || []), ...(d.query.urls || [])].filter(Boolean);
      const hl = (t) => { let h = esc(t); terms.forEach(x => { const re = new RegExp(esc(x).replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi'); h = h.replace(re, m => `<mark>${m}</mark>`); }); return h; };
      out.innerHTML = `<div class="hd-chips">${d.sessions.map(s => `<button type="button" data-open="${esc(s.session_id)}">${esc(s.name)} <small>${s.hits}</small></button>`).join('')}</div>
        <ul>${d.results.map(r => `<li class="hd-hit" data-open="${esc(r.session_id)}"><small>${esc(r.session_name)} · ${r.role === 'assistant' ? 'AI' : esc(r.role)} · ${esc(String(r.timestamp).slice(0, 16))}</small><p>${hl(r.snippet)}</p></li>`).join('')}</ul>`;
      wire(out);
    } catch (e) { out.innerHTML = `<p class="hd-empty">${esc(e.message)}</p>`; }
  };
  body.querySelector('form').addEventListener('submit', (e) => { e.preventDefault(); run(); });
  input.value = q0 || '';
  input.focus();
  if (q0) run();
}

// ── Usage ───────────────────────────────────────────────────────────────────
async function renderUsage(body, days = 30) {
  body.innerHTML = '<p class="hd-empty">Loading…</p>';
  let d;
  try { d = await j('/api/hub/usage?days=' + days); } catch (e) { body.innerHTML = `<p class="hd-empty">${esc(e.message)}</p>`; return; }
  const t = d.totals;
  const table = (rows, cols) => `<table class="hd-table"><thead><tr>${cols.map(c => `<th>${c[0]}</th>`).join('')}</tr></thead><tbody>${rows.map(r => `<tr>${cols.map(c => `<td>${c[1](r)}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
  const maxDay = Math.max(1e-9, ...d.by_day.map(x => x.usd));
  const bars = d.by_day.map(x => `<i title="${esc(x.day)} · ${usd(x.usd)} · ${x.calls} calls" style="height:${Math.max(2, x.usd / maxDay * 100)}%"></i>`).join('');
  const src = Object.entries(d.by_source).map(([k, v]) => ({ k, ...v }));
  const mdl = Object.entries(d.by_model).map(([k, v]) => ({ k, ...v })).slice(0, 10);
  const tools = Object.entries(d.by_tool).map(([k, v]) => ({ k, ...v })).slice(0, 25);
  const buckets = Object.entries(d.by_bucket).map(([k, v]) => ({ k, ...v })).sort((a, b) => b.calls - a.calls);
  const mcp = Object.entries(d.by_mcp_server).map(([k, v]) => ({ k, ...v }));
  body.innerHTML = `
    <div class="hd-usage-top">
      <div class="hd-kpis">
        <div><b>${usd(t.usd)}</b><span>${d.days}-day spend</span></div>
        <div><b>${num(t.calls)}</b><span>model calls</span></div>
        <div><b>${num(t.in)}</b><span>input tokens</span></div>
        <div><b>${num(t.out)}</b><span>output tokens</span></div>
        <div><b>${t.unpriced_calls}</b><span>unpriced calls</span></div>
      </div>
      <label class="hd-days">Range <select>${[7, 30, 90, 365].map(n => `<option value="${n}" ${n === d.days ? 'selected' : ''}>${n} days</option>`).join('')}</select></label>
    </div>
    <div class="hd-spark" aria-label="Daily spend">${bars || '<span class="hd-empty">No spend recorded.</span>'}</div>
    <div class="hd-grid hd-grid-2">
      <section><h3>By source</h3>${table(src, [['Source', r => esc(r.k)], ['Cost', r => usd(r.usd)], ['Calls', r => num(r.calls)], ['In', r => num(r.in)], ['Out', r => num(r.out)]])}</section>
      <section><h3>By model</h3>${table(mdl, [['Model', r => esc(r.k.split('/').pop())], ['Cost', r => usd(r.usd)], ['Calls', r => num(r.calls)], ['In', r => num(r.in)]])}</section>
      <section><h3>Tools by kind</h3>${table(buckets, [['Kind', r => esc(r.k)], ['Calls', r => num(r.calls)], ['≈ context tokens', r => num(r.approx_tokens)]])}
        ${mcp.length ? '<h3>MCP servers</h3>' + table(mcp, [['Server', r => esc(r.k)], ['Calls', r => num(r.calls)], ['≈ tokens', r => num(Math.round(r.output_chars / 4))]]) : ''}</section>
      <section><h3>Tools (invocations)</h3>${table(tools, [['Tool', r => esc(r.k)], ['Kind', r => esc(r.bucket)], ['Calls', r => num(r.calls)], ['Errors', r => r.errors ? `<span class="hd-err">${r.errors}</span>` : '0'], ['≈ tokens', r => num(r.approx_tokens)]])}</section>
      <section><h3>Most expensive chats</h3>${table(d.by_session.slice(0, 12), [['Chat', r => `<a href="#" data-open="${esc(r.session_id)}">${esc(r.name)}</a>`], ['Cost', r => usd(r.usd)], ['Calls', r => num(r.calls)], ['In', r => num(r.in)]])}</section>
      <section><h3>Context footprint</h3>${table(d.context_footprint.slice(0, 12), [['Chat', r => `<a href="#" data-open="${esc(r.session_id)}">${esc(r.name)}</a>`], ['Messages', r => num(r.messages)], ['Input tokens (lifetime)', r => num(r.input_tokens)]])}</section>
    </div>`;
  body.querySelector('.hd-days select').addEventListener('change', (e) => renderUsage(body, +e.target.value));
  wire(body);
}

// ── shared wiring ───────────────────────────────────────────────────────────
function wire(root) {
  root.querySelectorAll('[data-open]').forEach(b => b.addEventListener('click', (e) => { e.preventDefault(); openChat(b.dataset.open); }));
  root.querySelectorAll('[data-stop]').forEach(b => b.addEventListener('click', async () => {
    b.disabled = true;
    try { await fetch(`/api/chat/stop/${encodeURIComponent(b.dataset.stop)}`, { method: 'POST', credentials: 'same-origin' }); } catch (_) {}
  }));
  root.querySelectorAll('[data-doc]').forEach(b => b.addEventListener('click', () => {
    close();
    window.documentModule?.loadDocument?.(b.dataset.doc);
  }));
  root.querySelectorAll('[data-publish]').forEach(b => b.addEventListener('click', async () => {
    b.disabled = true;
    try {
      const r = await j('/api/hub/live', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ doc_id: b.dataset.publish, allow_mcp: true }) });
      window.open(r.url, '_blank', 'noopener');
    } catch (e) { alert(e.message); }
  }));
  root.querySelectorAll('[data-copy]').forEach(b => b.addEventListener('click', async () => {
    try { await navigator.clipboard.writeText(b.dataset.copy); b.textContent = 'Copied'; } catch (_) {}
  }));
  root.querySelectorAll('[data-unpublish]').forEach(b => b.addEventListener('click', async () => {
    if (!confirm('Unpublish this live page? The link stops working.')) return;
    await fetch('/api/hub/live/' + encodeURIComponent(b.dataset.unpublish), { method: 'DELETE', credentials: 'same-origin' });
    show('outputs');
  }));
}

// ── @-mentions in the composer ──────────────────────────────────────────────
// Type "@" + a few letters → pick a chat → `@[Name](session:ID)` is inserted;
// the server pulls that chat's recap into this turn's context.
export function initMentions() {
  const ta = document.getElementById('message');
  if (!ta || ta._mentionsWired) return;
  ta._mentionsWired = true;
  let pop = null, items = [], idx = 0, start = -1, reqId = 0;
  const closePop = () => { if (pop) pop.remove(); pop = null; start = -1; };
  const place = () => {
    const r = ta.getBoundingClientRect();
    pop.style.left = Math.max(8, r.left) + 'px';
    pop.style.bottom = (window.innerHeight - r.top + 6) + 'px';
    pop.style.width = Math.min(420, r.width) + 'px';
  };
  const paint = () => {
    if (!pop) return;
    pop.innerHTML = items.length ? items.map((s, i) => `<li role="option" aria-selected="${i === idx}" data-i="${i}"><b>${esc(s.name)}</b><small>${s.messages} msgs · ${ago(s.updated)}</small></li>`).join('')
      : '<li class="hd-empty">No matching chats in this project — only chats in the same project folder can share context.</li>';
    pop.querySelectorAll('li[data-i]').forEach(li => li.addEventListener('mousedown', (e) => { e.preventDefault(); pick(+li.dataset.i); }));
  };
  const pick = (i) => {
    const s = items[i];
    if (!s || start < 0) return closePop();
    const before = ta.value.slice(0, start), after = ta.value.slice(ta.selectionStart);
    const token = `@[${s.name.replace(/[\[\]]/g, '')}](session:${s.id}) `;
    ta.value = before + token + after;
    const pos = before.length + token.length;
    ta.setSelectionRange(pos, pos);
    ta.dispatchEvent(new Event('input', { bubbles: true }));
    closePop();
    ta.focus();
  };
  ta.addEventListener('input', async () => {
    const pos = ta.selectionStart;
    const upto = ta.value.slice(0, pos);
    const m = /(^|\s)@([^\s@\[]{0,30})$/.exec(upto);
    if (!m) return closePop();
    start = pos - m[2].length - 1;
    const my = ++reqId;
    let d;
    const cur = window.sessionModule?.getCurrentSessionId?.();
    try { d = await j('/api/hub/sessions?limit=8&q=' + encodeURIComponent(m[2]) + (cur ? '&from_session=' + encodeURIComponent(cur) : '')); } catch (_) { return; }
    if (my !== reqId) return;
    items = (d.sessions || []).filter(s => s.id !== cur);
    idx = 0;
    if (!pop) { pop = document.createElement('ul'); pop.className = 'mention-pop'; pop.setAttribute('role', 'listbox'); document.body.appendChild(pop); }
    place(); paint();
  });
  ta.addEventListener('keydown', (e) => {
    if (!pop) return;
    if (e.key === 'ArrowDown') { e.preventDefault(); idx = Math.min(items.length - 1, idx + 1); paint(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); idx = Math.max(0, idx - 1); paint(); }
    else if (e.key === 'Enter' || e.key === 'Tab') { if (items.length) { e.preventDefault(); e.stopImmediatePropagation(); pick(idx); } }
    else if (e.key === 'Escape') { e.preventDefault(); closePop(); }
  }, true);
  ta.addEventListener('blur', () => setTimeout(closePop, 150));
}

// ── Rewind (snapshots taken before compaction / truncation / edits) ─────────
export async function openRewind() {
  const sid = window.sessionModule?.getCurrentSessionId?.();
  if (!sid) return;
  if (!_root) _root = shell();
  _root.querySelectorAll('.hd-tabs button').forEach(b => b.setAttribute('aria-selected', 'false'));
  clearInterval(_timer); _timer = 0;
  const body = _root.querySelector('.hd-body');
  body.innerHTML = '<p class="hd-empty">Loading…</p>';
  const d = await j('/api/hub/snapshots/' + encodeURIComponent(sid));
  body.innerHTML = `<h3>Rewind this chat</h3>
    <p class="hd-hint">A snapshot is saved automatically before every compaction, truncation, message delete or edit. Restoring one replaces the chat's current history (the current state is snapshotted first, so this is undoable).</p>
    <button type="button" class="hd-snap-now">Save a snapshot now</button>
    <ul>${(d.snapshots || []).map(s => `<li class="hd-row"><span class="hd-main"><b>${esc(s.reason || 'snapshot')}</b><small>${new Date(s.ts * 1000).toLocaleString()} · ${s.count} messages${s.last_user ? ' · last ask: “' + esc(s.last_user) + '”' : ''}</small></span>
      <button type="button" data-restore="${esc(s.id)}">Restore</button></li>`).join('') || '<li class="hd-empty">No snapshots yet.</li>'}</ul>`;
  body.querySelector('.hd-snap-now').addEventListener('click', async () => { await j('/api/hub/snapshots/' + encodeURIComponent(sid), { method: 'POST' }); openRewind(); });
  body.querySelectorAll('[data-restore]').forEach(b => b.addEventListener('click', async () => {
    if (!confirm('Restore this snapshot? The current history is snapshotted first.')) return;
    try {
      await j(`/api/hub/snapshots/${encodeURIComponent(sid)}/${encodeURIComponent(b.dataset.restore)}/restore`, { method: 'POST' });
      close();
      window.sessionModule?.selectSession?.(sid);
    } catch (e) { alert(e.message); }
  }));
}

export function initHub() {
  initMentions();
  window.hubDashboard = { open, close, openRewind };
  // Sidebar entry (right under "Set a goal", which goalCard builds lazily).
  const mount = (tries = 0) => {
    if (document.getElementById('hub-dash-btn')) return;
    const anchor = document.getElementById('goal-new');
    if (!anchor && tries < 20) { setTimeout(() => mount(tries + 1), 500); return; }
    const btn = document.createElement('button');
    btn.id = 'hub-dash-btn';
    btn.type = 'button';
    btn.className = 'hub-dash-btn';
    btn.title = 'Agent dashboard — everything running, plans, outputs, board, search, usage (/dashboard)';
    btn.innerHTML = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="3" width="7" height="9"/><rect x="14" y="3" width="7" height="5"/><rect x="14" y="12" width="7" height="9"/><rect x="3" y="16" width="7" height="5"/></svg><span>Dashboard</span><span class="hub-dash-count" hidden></span>';
    btn.addEventListener('click', () => open('overview'));
    if (anchor && anchor.parentNode) anchor.after(btn);
    else (document.getElementById('sidebar') || document.body).prepend(btn);
  };
  mount();
  // Running-work counter on the button (fed by the run-dot poll).
  window.addEventListener('odysseus:runs', (ev) => {
    const n = (ev.detail || []).filter(r => r.status === 'running').length;
    const c = document.querySelector('#hub-dash-btn .hub-dash-count');
    if (c) { c.hidden = !n; c.textContent = String(n); }
  });
}

export default { open, close, openRewind, initHub, initMentions };
