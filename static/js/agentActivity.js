// static/js/agentActivity.js
// DSH parity — make an agent turn readable at a glance:
//   • Deep-dive header per turn: "Deep dive · 1m 12s · 4 rounds · 9 actions"
//     with per-category chips; ticks live while the turn runs; click to
//     collapse the work log down to the final answer.
//   • Action categories: consecutive tool calls of the same kind are folded
//     into one expandable group ("Searched the web · 3"), and each group
//     carries the thought that led to it (the round's text right above).
// Pure DOM post-processing over what chat.js / chatRenderer.js already render
// (.msg-ai bubbles + .agent-thread > .agent-thread-node), so it works for both
// the live stream and history reloads without touching the render paths.

const CATS = {
  search: { label: 'Searched', noun: ['search', 'searches'], icon: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>' },
  read:   { label: 'Read',     noun: ['source', 'sources'],  icon: '<path d="M4 5a2 2 0 0 1 2-2h12v18H6a2 2 0 0 1-2-2Z"/><path d="M8 7h6M8 11h6"/>' },
  edit:   { label: 'Edited',   noun: ['file', 'files'],      icon: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/>' },
  run:    { label: 'Ran',      noun: ['command', 'commands'], icon: '<polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/>' },
  plan:   { label: 'Planned',  noun: ['step', 'steps'],      icon: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/>' },
  recon:  { label: 'Recon',    noun: ['probe', 'probes'],    icon: '<circle cx="12" cy="12" r="9"/><path d="M12 12 19 5"/><circle cx="12" cy="12" r="4"/>' },
  other:  { label: 'Used',     noun: ['tool', 'tools'],      icon: '<path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.6 2.6-2.8-2.8Z"/>' },
};

const TOOL_CAT = {
  recon: 'recon', factcheck: 'recon', sherlock_osint: 'recon', osint_framework: 'recon', case_file: 'recon', watch_target: 'recon',
  web_search: 'search', reddit_search: 'search', scrapebadger: 'search', trigger_research: 'search',
  registros_cr: 'search', sinalevi_search: 'search', corteidh_search: 'search', search_chats: 'search',
  read_file: 'read', web_fetch: 'read', youtube_transcript: 'read', docfind: 'read', corteidh_case: 'read',
  legal_fetch_document: 'read', read_email: 'read', list_emails: 'read', context_tree: 'read',
  write_file: 'edit', create_document: 'edit', edit_document: 'edit', update_document: 'edit',
  document_append: 'edit', suggest_document: 'edit',
  bash: 'run', python: 'run', run_python: 'run', run_javascript: 'run', api_call: 'run', app_api: 'run',
  create_goal: 'plan', todo_write: 'plan', checkpoint: 'plan', finish_goal: 'plan', update_goal: 'plan',
  get_goal: 'plan', ask_user_question: 'plan', subagent: 'plan',
};

export function categoryOf(tool) {
  const t = String(tool || '').trim().toLowerCase();
  if (TOOL_CAT[t]) return TOOL_CAT[t];
  if (/search|lookup|find/.test(t)) return 'search';
  if (/read|fetch|get_|list_/.test(t)) return 'read';
  if (/write|edit|create|update|append/.test(t)) return 'edit';
  return 'other';
}

const _svg = (paths, size = 13) =>
  `<svg viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths}</svg>`;
const CHEV = '<polyline points="9 18 15 12 9 6"/>';

export function fmtDuration(sec) {
  sec = Math.max(0, Number(sec) || 0);
  if (sec < 60) return `${sec < 10 ? sec.toFixed(1) : Math.round(sec)}s`;
  const m = Math.floor(sec / 60), s = Math.round(sec % 60);
  if (m < 60) return `${m}m ${String(s).padStart(2, '0')}s`;
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}m`;
}

function _toolOf(node) {
  return (node.dataset && node.dataset.tool) || (node.querySelector('.agent-thread-tool')?.textContent || '').trim();
}

/** The thought that led to a thread: the round text in the bubble right above it. */
function _thoughtBefore(thread) {
  let el = thread.previousElementSibling;
  while (el && !(el.classList.contains('msg-ai') || el.classList.contains('agent-thread'))) el = el.previousElementSibling;
  if (!el || !el.classList.contains('msg-ai')) return '';
  const body = el.querySelector('.body');
  const t = (body ? body.innerText : '').replace(/\s+/g, ' ').trim();
  return t.length > 320 ? t.slice(0, 317) + '…' : t;
}

/** Fold consecutive same-category tool nodes inside one .agent-thread. Idempotent. */
export function groupThread(thread, { collapsed = true } = {}) {
  if (!thread || thread.dataset.actGrouped === '1') return;
  const nodes = [...thread.children].filter(n => n.classList && n.classList.contains('agent-thread-node'));
  if (!nodes.length) return;
  const thought = _thoughtBefore(thread);
  let run = [];
  let first = true;
  const flush = () => {
    if (!run.length) return;
    const cat = categoryOf(_toolOf(run[0]));
    const meta = CATS[cat];
    const failed = run.filter(n => n.classList.contains('error')).length;
    const g = document.createElement('div');
    g.className = 'act-group' + (collapsed ? ' collapsed' : '') + (failed ? ' has-error' : '');
    g.dataset.cat = cat;
    const head = document.createElement('button');
    head.type = 'button';
    head.className = 'act-head';
    const n = run.length;
    const tools = [...new Set(run.map(_toolOf))].join(', ');
    head.innerHTML = `<span class="act-ico">${_svg(meta.icon)}</span>`
      + `<span class="act-label">${meta.label} ${n} ${meta.noun[n === 1 ? 0 : 1]}</span>`
      + `<span class="act-tools"></span>`
      + (failed ? `<span class="act-fail">${failed} failed</span>` : '')
      + `<span class="act-chev">${_svg(CHEV, 12)}</span>`;
    head.querySelector('.act-tools').textContent = tools;
    head.setAttribute('aria-expanded', String(!collapsed));
    head.addEventListener('click', (e) => {
      e.stopPropagation();
      const c = g.classList.toggle('collapsed');
      head.setAttribute('aria-expanded', String(!c));
    });
    const body = document.createElement('div');
    body.className = 'act-body';
    if (first && thought) {
      const th = document.createElement('div');
      th.className = 'act-thought';
      th.textContent = thought;
      body.appendChild(th);
    }
    first = false;
    run[0].before(g);
    run.forEach(nd => body.appendChild(nd));
    g.append(head, body);
    run = [];
  };
  for (const nd of nodes) {
    if (run.length && categoryOf(_toolOf(run[0])) !== categoryOf(_toolOf(nd))) flush();
    run.push(nd);
  }
  flush();
  thread.dataset.actGrouped = '1';
}

/** Count actions per category across the given turn elements. */
function _counts(els) {
  const counts = {};
  let total = 0;
  for (const el of els) {
    if (!el.querySelectorAll) continue;
    const nodes = el.classList?.contains('agent-thread-node') ? [el] : el.querySelectorAll('.agent-thread-node');
    nodes.forEach(n => { const c = categoryOf(_toolOf(n)); counts[c] = (counts[c] || 0) + 1; total++; });
  }
  return { counts, total };
}

function _paintHeader(hdr, { seconds, rounds, live }) {
  const els = hdr._turnEls ? hdr._turnEls() : [];
  const { counts, total } = _counts(els);
  const chips = Object.keys(CATS).filter(k => counts[k]).map(k =>
    `<span class="dd-chip" data-cat="${k}">${_svg(CATS[k].icon, 11)}${counts[k]}</span>`).join('');
  hdr.querySelector('.dd-time').textContent = (seconds || live) ? fmtDuration(seconds) : '';
  hdr.querySelector('.dd-meta').textContent =
    `${rounds} round${rounds === 1 ? '' : 's'} · ${total} action${total === 1 ? '' : 's'}`;
  hdr.querySelector('.dd-chips').innerHTML = chips;
  hdr.classList.toggle('live', !!live);
}

function _makeHeader(getEls) {
  const hdr = document.createElement('button');
  hdr.type = 'button';
  hdr.className = 'deep-dive';
  hdr.innerHTML = `<span class="dd-pulse"></span><span class="dd-title">Deep dive</span>`
    + `<span class="dd-time"></span><span class="dd-meta"></span><span class="dd-chips"></span>`
    + `<span class="dd-toggle">${_svg(CHEV, 12)}</span>`;
  hdr._turnEls = getEls;
  hdr.title = 'Collapse / expand the work log';
  hdr.addEventListener('click', () => {
    const collapsed = hdr.classList.toggle('dd-collapsed');
    const els = hdr._turnEls();
    const finalBubble = [...els].reverse().find(e => e.classList?.contains('msg-ai'));
    for (const el of els) {
      if (el === finalBubble || el.classList?.contains('edit-summary') || el.classList?.contains('deliverable-card')) continue;
      el.classList.toggle('dd-hidden', collapsed);
    }
  });
  return hdr;
}

/**
 * Decorate a finished agent turn: group its tool threads and put the
 * deep-dive header before its first element.
 * @param {Element[]} els   the turn's elements in DOM order (bubbles, threads, separators)
 * @param {{seconds:number, rounds:number}} info
 */
export function decorateTurn(els, info = {}) {
  els = (els || []).filter(Boolean);
  if (!els.length) return null;
  const threads = els.filter(e => e.classList?.contains('agent-thread'));
  if (!threads.length) return null;                  // plain answer — nothing to summarise
  threads.forEach(t => groupThread(t, { collapsed: true }));
  let hdr = els[0].previousElementSibling;
  if (!(hdr && hdr.classList.contains('deep-dive'))) {
    const set = new Set(els);
    hdr = _makeHeader(() => [...set]);
    els[0].before(hdr);
  }
  _paintHeader(hdr, { seconds: info.seconds || 0, rounds: info.rounds || threads.length, live: false });
  return hdr;
}

// ── Live: a ticking header while the turn streams ─────────────────────────
export function startLive(firstEl, getEls) {
  if (!firstEl || !firstEl.parentNode) return null;
  const t0 = performance.now();
  const hdr = _makeHeader(getEls);
  firstEl.before(hdr);
  let rounds = 1;
  let done = false;
  let note = '';
  const _applyNote = () => {
    let n = hdr.querySelector('.dd-note');
    if (!note) { if (n) n.remove(); return; }
    if (!n) { n = document.createElement('span'); n.className = 'dd-note'; hdr.appendChild(n); }
    n.textContent = note;
  };
  const tick = () => { _paintHeader(hdr, { seconds: (performance.now() - t0) / 1000, rounds, live: true }); _applyNote(); };
  tick();
  const iv = setInterval(tick, 1000);
  return {
    header: hdr,
    setRounds(n) { rounds = Math.max(rounds, n || 1); tick(); },
    setNote(t) { note = String(t || ''); _applyNote(); },
    finish(info = {}) {
      clearInterval(iv);
      if (done) return;
      done = true;
      note = ''; _applyNote();
      const els = getEls();
      hdr._turnEls = () => els;           // freeze the turn: later turns never join it
      if (!els.some(e => e.classList?.contains('agent-thread'))) { hdr.remove(); return; }
      els.filter(e => e.classList?.contains('agent-thread')).forEach(t => groupThread(t, { collapsed: true }));
      _paintHeader(hdr, { seconds: info.seconds ?? (performance.now() - t0) / 1000, rounds: info.rounds || rounds, live: false });
    },
    cancel() { clearInterval(iv); hdr.remove(); },
  };
}

export default { categoryOf, groupThread, decorateTurn, startLive, fmtDuration };
