// goalCard.js — renderiza la meta continua (espejo de la goal card de DSH).
const GOAL_PHASES = ['active', 'paused', 'blocked', 'complete'];

let _lastPhase = null;

// ── Status-note SVG icons (stroke: currentColor) replacing emoji ────────
// Feather-style, 14px, aria-hidden. A non-color channel so phase is never
// conveyed by color alone (WCAG 1.4.1).
const _GSVG = {
  target: '<svg class="qsvg" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/></svg>',
  scroll: '<svg class="qsvg" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M8 21h12a2 2 0 0 0 2-2v-2H10v2a2 2 0 1 1-4 0V5a2 2 0 1 0-4 0v3h4"/><path d="M19 17V5a2 2 0 0 0-2-2H4"/></svg>',
  check: '<svg class="qsvg qsvg-ok" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>',
  blocked: '<svg class="qsvg qsvg-err" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><line x1="4.93" y1="4.93" x2="19.07" y2="19.07"/></svg>',
  pause: '<svg class="qsvg" viewBox="0 0 24 24" width="14" height="14" fill="currentColor" stroke="none" aria-hidden="true"><rect x="6" y="4" width="4" height="16" rx="1"/><rect x="14" y="4" width="4" height="16" rx="1"/></svg>',
  play: '<svg class="qsvg" viewBox="0 0 24 24" width="14" height="14" fill="currentColor" stroke="none" aria-hidden="true"><path d="M8 5l11 7-11 7z"/></svg>',
  box: '<svg class="qsvg" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="2"/></svg>',
  boxChecked: '<svg class="qsvg qsvg-ok" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="2"/><polyline points="9 12 11 14 15 10"/></svg>',
};

export function renderGoal(goal, extra = {}) {
  const card = document.getElementById('goal-card');
  if (!card) return;
  _ensureNewGoalButton(!goal || goal.phase === 'complete');
  if (!goal) {
    card.hidden = true;
    renderChatGoal(null, extra);       // may still show a todo_write plan for this chat
    return;
  }
  card.hidden = false;

  document.getElementById('goal-objective').textContent = goal.objective || '';
  const badge = document.getElementById('goal-badge');
  const phase = goal.phase || 'active';
  // Non-color channel: a phase glyph + text label, so state is never
  // conveyed by color alone (WCAG 1.4.1). The glyph is a small inline SVG.
  const _phaseGlyph = {
    active: _GSVG.play,
    paused: _GSVG.pause,
    blocked: _GSVG.blocked,
    complete: _GSVG.check,
  }[phase] || _GSVG.play;
  badge.innerHTML = _phaseGlyph + '<span>' + phase + '</span>';
  badge.className = 'goal-badge ' + phase;

  // Rondas (meta amplia)
  const fill = document.getElementById('goal-progress-fill');
  const pct = goal.maxGoalRounds ? Math.min(100, (goal.roundsStarted / goal.maxGoalRounds) * 100) : 0;
  fill.style.width = pct + '%';
  document.getElementById('goal-rounds').textContent =
    `Ronda ${goal.roundsStarted ?? 0}${goal.maxGoalRounds ? '/' + goal.maxGoalRounds : ''}`
    + (goal.createdAt ? ` · ${_ago(goal.createdAt)}` : '');

  // ── GAP 1: cola de items (tarea continua) ──
  const queue = extra.queue || { remaining: [], done: [] };
  const total = (queue.remaining?.length || 0) + (queue.done?.length || 0);
  const qRow = document.getElementById('goal-queue-row');
  const qFill = document.getElementById('goal-queue-fill');
  const qCount = document.getElementById('goal-queue-count');
  const cur = document.getElementById('goal-current');
  if (total > 0) {
    qRow.hidden = false;
    qFill.style.width = ((queue.done?.length || 0) / total) * 100 + '%';
    qCount.textContent = `${queue.done?.length || 0}/${total}`;
    cur.hidden = !(queue.remaining?.length);
    cur.textContent = (queue.remaining?.length)
      ? `· procesando: ${queue.remaining[0]}`
      : '· cola completa';
  } else {
    qRow.hidden = true;
    cur.hidden = true;
  }

  // ── Expandable todo list (DSH parity: the goal's item list is visible
  //    and expandable, fed live by /api/goal/queue). ──
  let todo = document.getElementById('goal-todo-list');
  if (!todo) {
    todo = document.createElement('details');
    todo.id = 'goal-todo-list';
    todo.className = 'goal-todo';
    const sum = document.createElement('summary');
    sum.textContent = 'Task list';
    todo.appendChild(sum);
    const body = document.createElement('div');
    body.className = 'goal-todo-body';
    todo.appendChild(body);
    if (cur && cur.parentNode) cur.parentNode.insertBefore(todo, cur);
    else card.appendChild(todo);
  }
  const todoBody = todo.querySelector('.goal-todo-body');
  if (todoBody) {
    todoBody.innerHTML = '';
    (queue.remaining || []).forEach(item => {
      const row = document.createElement('div');
      row.className = 'goal-todo-item';
      row.innerHTML = _GSVG.box + '<span>' + item + '</span>';
      todoBody.appendChild(row);
    });
    (queue.done || []).forEach(item => {
      const row = document.createElement('div');
      row.className = 'goal-todo-item done';
      row.innerHTML = _GSVG.boxChecked + '<span>' + item + '</span>';
      todoBody.appendChild(row);
    });
  }
  todo.hidden = total === 0;

  // ── GAP 2: actividad ──
  setGoalActivity(extra.activity || (goal.phase === 'blocked' ? 'blocked' : 'idle'));

  // ── GAP 3: trail (checkpoint) ──
  const trail = document.getElementById('goal-trail-body');
  if (trail && extra.checkpoint !== undefined) {
    trail.textContent = extra.checkpoint || '(aún no hay trail)';
  }

  const blocked = document.getElementById('goal-blocked');
  const reason = goal.blockedReason?.message || '';
  blocked.hidden = !reason;
  blocked.textContent = reason;

  // ── GAP 5: toast en transición de fase ──
  if (_lastPhase && _lastPhase !== goal.phase) {
    if (goal.phase === 'complete') showGoalToast(_GSVG.check + ' Meta completada', 'done');
    else if (goal.phase === 'blocked') showGoalToast(_GSVG.blocked + ' Meta bloqueada: ' + (reason || '—'), 'blocked');
  }
  _lastPhase = goal.phase;
  card.classList.toggle('blocked', goal.phase === 'blocked');
  card.classList.toggle('complete', goal.phase === 'complete');

  // ── In-chat goal panel (DSH parity: the ongoing goal lives IN the chat) ──
  renderChatGoal(goal, extra);
}

// The goal panel docked at the top of the conversation. Compact by default
// (objective · phase · round/step progress · elapsed); click to expand the
// task list (current step lit), the latest trail lines and the controls.
// Shown only in the chat whose session the goal runs in.
let _lastGoal = null, _lastExtra = {};
const _openKey = (gid) => 'odysseus-goal-panel-open-' + (gid || '');
export function renderChatGoal(goal, extra = {}) {
  _lastGoal = goal; _lastExtra = extra;
  const host = document.getElementById('goal-banner');
  if (!host) return;
  const sid = window.sessionModule?.getCurrentSessionId?.() || null;
  const card = document.getElementById('goal-card');
  const q0 = extra.queue || { remaining: [], done: [] };
  const queueHere = !q0.session_id || !sid || q0.session_id === sid;
  const goalHere = goal && !(goal.session_id && sid && goal.session_id !== sid);
  const hasPlan = queueHere && ((q0.remaining || []).length + (q0.done || []).length) > 0;
  if (!goalHere && !hasPlan) {
    host.hidden = true; host.innerHTML = '';
    if (card) card.hidden = !goal;            // goal of another chat → keep it reachable in the sidebar
    return;
  }
  if (card) card.hidden = !!goalHere || !goal;
  // Plan mode: no goal in this chat, but the agent wrote a todo_write checklist.
  const planMode = !goalHere;
  if (planMode) {
    goal = { id: 'plan-' + (sid || ''), phase: (q0.remaining || []).length ? 'active' : 'complete',
             objective: (q0.remaining || []).length ? (q0.current || q0.remaining[0]) : 'All steps done',
             createdAt: q0.updated_at };
  }
  if (!queueHere) extra = { ...extra, queue: { remaining: [], done: [] } };
  const phase = goal.phase || 'active';
  const queue = extra.queue || { remaining: [], done: [] };
  const done = queue.done || [], remaining = queue.remaining || [];
  const total = done.length + remaining.length;
  const rounds = goal.roundsStarted ?? 0, maxR = goal.maxGoalRounds || 0;
  let open = false;
  try { open = localStorage.getItem(_openKey(goal.id)) === '1'; } catch (_) {}
  host.hidden = false;
  host.className = 'goal-banner goal-dock ' + phase + (open ? ' open' : '') + (planMode ? ' plan' : '');
  host.setAttribute('role', 'region');
  host.setAttribute('aria-label', planMode ? 'Plan' : 'Ongoing goal');
  host.onclick = null;
  const pct = (n, d) => d ? Math.min(100, Math.round((n / d) * 100)) : 0;
  const stepTxt = total ? `step ${Math.min(done.length + 1, total)}/${total}` : 'no task list yet';
  host.innerHTML = `
    <button type="button" class="gd-head" aria-expanded="${open}">
      <span class="gd-ico">${_GSVG.target}</span>
      <span class="gd-label">${planMode ? 'Plan' : 'Goal'}</span>
      <span class="gd-phase ${phase}">${{ active: _GSVG.play, paused: _GSVG.pause, blocked: _GSVG.blocked, complete: _GSVG.check }[phase] || ''}<span>${planMode ? (phase === 'complete' ? 'done' : 'working') : phase}</span></span>
      <span class="gd-obj"></span>
      <span class="gd-meta"></span>
      <span class="gd-chev" aria-hidden="true"><svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg></span>
    </button>
    <div class="gd-bars" aria-hidden="true">
      <i class="gd-bar-r" style="width:${pct(rounds, maxR)}%"></i><i class="gd-bar-s" style="width:${pct(done.length, total)}%"></i>
    </div>
    <div class="gd-body">
      <ol class="gd-steps"></ol>
      <div class="gd-trail-wrap"><span class="gd-sub">Latest progress</span><pre class="gd-trail"></pre></div>
      <p class="gd-blocked" hidden></p>
      <div class="gd-acts">
        <button type="button" data-act="pause">${_GSVG.pause}<span>Pause</span></button>
        <button type="button" data-act="resume">${_GSVG.play}<span>Resume</span></button>
        <button type="button" data-act="complete">${_GSVG.check}<span>Mark complete</span></button>
      </div>
    </div>`;
  host.querySelector('.gd-obj').textContent = goal.objective || '';
  host.querySelector('.gd-obj').title = goal.objective || '';
  host.querySelector('.gd-meta').textContent = planMode
    ? `${done.length}/${total} done` + (goal.createdAt ? ` · updated ${_ago(goal.createdAt)} ago` : '')
    : `round ${rounds}${maxR ? '/' + maxR : ''} · ${stepTxt}` + (goal.createdAt ? ` · ${_ago(goal.createdAt)}` : '');
  const ol = host.querySelector('.gd-steps');
  if (!total) {
    const li = document.createElement('li'); li.className = 'gd-empty';
    li.textContent = 'The agent has not written a task list yet — it appears here as soon as it plans the steps.';
    ol.appendChild(li);
  }
  done.forEach(t => { const li = document.createElement('li'); li.className = 'done'; li.innerHTML = _GSVG.boxChecked; li.append(document.createTextNode(' ' + t)); ol.appendChild(li); });
  const curIdx = Math.max(0, queue.current ? remaining.indexOf(queue.current) : 0);
  remaining.forEach((t, i) => { const li = document.createElement('li'); li.className = i === curIdx ? 'current' : ''; li.innerHTML = _GSVG.box; li.append(document.createTextNode(' ' + t)); ol.appendChild(li); });
  const trail = String(extra.checkpoint || '').trim().split('\n').filter(Boolean).slice(-4).join('\n');
  const tw = host.querySelector('.gd-trail-wrap');
  if (trail && !planMode) host.querySelector('.gd-trail').textContent = trail; else tw.hidden = true;
  if (planMode) { host.querySelector('.gd-acts').style.display = 'none'; host.querySelector('.gd-bar-r').style.width = '0'; }
  const reason = goal.blockedReason?.message || '';
  const bl = host.querySelector('.gd-blocked'); bl.hidden = !reason; bl.textContent = reason;
  host.querySelector('[data-act="pause"]').hidden = phase !== 'active';
  host.querySelector('[data-act="resume"]').hidden = phase !== 'paused';
  host.querySelector('[data-act="complete"]').hidden = phase === 'complete';
  const head = host.querySelector('.gd-head');
  head.addEventListener('click', () => {
    const o = host.classList.toggle('open');
    head.setAttribute('aria-expanded', String(o));
    try { localStorage.setItem(_openKey(goal.id), o ? '1' : '0'); } catch (_) {}
  });
  host.querySelectorAll('.gd-acts button').forEach(b => b.addEventListener('click', async (e) => {
    e.stopPropagation();
    await fetch('/api/goal/' + b.dataset.act, { method: 'POST', credentials: 'same-origin' });
    refreshGoal();
  }));
}

function _ago(ts) {
  const s = Math.max(0, Math.round(Date.now() / 1000 - Number(ts)));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.round(s / 60)}m`;
  if (s < 86400) return `${(s / 3600).toFixed(1)}h`;
  return `${Math.round(s / 86400)}d`;
}

// ── Goal creation (UI entry point; the agent can also call create_goal) ──
export async function createGoal(objective, sessionId) {
  objective = String(objective || '').trim();
  if (!objective) return { ok: false, error: 'objective is required' };
  const sid = sessionId || window.sessionModule?.getCurrentSessionId?.() || undefined;
  try {
    const r = await fetch('/api/goal', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ objective, session_id: sid }),
    });
    if (!r.ok) {
      let msg = `HTTP ${r.status}`;
      try { const j = await r.json(); msg = j.detail || msg; } catch (_) {}
      if (r.status === 409) msg = 'A goal is already active — complete or pause it first.';
      return { ok: false, error: msg };
    }
    await refreshGoal();
    showGoalToast(_GSVG.target + ' Goal started: ' + _esc(objective.slice(0, 80)), 'done');
    return { ok: true };
  } catch (e) {
    return { ok: false, error: String(e && e.message || e) };
  }
}

const _esc = (s) => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

function _ensureNewGoalButton(show) {
  const card = document.getElementById('goal-card');
  if (!card) return;
  let box = document.getElementById('goal-new');
  if (!box) {
    box = document.createElement('div');
    box.id = 'goal-new';
    box.className = 'goal-new';
    box.innerHTML = '<button type="button" class="goal-new-btn">' + _GSVG.target +
      '<span>Set a goal</span><kbd>/goal</kbd></button>' +
      '<form class="goal-new-form" hidden><textarea rows="2" maxlength="600" ' +
      'placeholder="What should Odysseus keep working toward? (Enter to start · Esc to cancel)"></textarea>' +
      '<p class="goal-new-err" hidden></p></form>';
    card.parentNode.insertBefore(box, card.nextSibling);
    const btn = box.querySelector('.goal-new-btn');
    const form = box.querySelector('.goal-new-form');
    const ta = form.querySelector('textarea');
    const err = form.querySelector('.goal-new-err');
    btn.addEventListener('click', () => { btn.hidden = true; form.hidden = false; ta.focus(); });
    const cancel = () => { form.hidden = true; btn.hidden = false; ta.value = ''; err.hidden = true; };
    ta.addEventListener('keydown', async (e) => {
      if (e.key === 'Escape') { e.preventDefault(); cancel(); }
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        const res = await createGoal(ta.value);
        if (res.ok) cancel();
        else { err.textContent = res.error; err.hidden = false; }
      }
    });
  }
  box.hidden = !show;
}

export function setGoalActivity(state) {
  const el = document.getElementById('goal-activity');
  if (el) el.dataset.state = state || 'idle';
}

function showGoalToast(msg, kind) {
  const t = document.createElement('div');
  t.className = 'goal-toast ' + kind;
  t.innerHTML = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 3200);
}

// Resolve the checkpoint endpoint response whether it returns JSON
// ({"checkpoint": "..."}) or plain text.
async function fetchCheckpoint() {
  try {
    const r = await fetch('/api/goal/checkpoint');
    if (!r.ok) return '';
    const ct = r.headers.get('content-type') || '';
    if (ct.includes('application/json')) {
      const j = await r.json();
      return (j && j.checkpoint) || '';
    }
    return await r.text();
  } catch { return ''; }
}

export async function refreshGoal() {
  try {
    const [g, q, c] = await Promise.all([
      fetch('/api/goal').then(r => r.ok ? r.json() : null),
      (() => { const sid = window.sessionModule?.getCurrentSessionId?.();
        return fetch('/api/goal/queue' + (sid ? '?session_id=' + encodeURIComponent(sid) : ''))
          .then(r => r.ok ? r.json() : null).catch(() => null); })(),
      fetchCheckpoint(),
    ]);
    renderGoal(g?.goal || null, { queue: q || { remaining: [], done: [] }, checkpoint: c });
  } catch { renderGoal(null); }
}

function bind(btnId, action) {
  const el = document.getElementById(btnId);
  el?.addEventListener('click', async () => {
    await fetch('/api/goal/' + action, { method: 'POST' });
    refreshGoal();
  });
}

export function initGoalCard() {
  bind('goal-pause', 'pause');
  bind('goal-resume', 'resume');
  bind('goal-complete', 'complete');
  refreshGoal();
  // Keep "Ronda n · 12m" and the task list fresh even between SSE events.
  setInterval(() => { if (!document.hidden) refreshGoal(); }, 30000);
  // Switching chats shows/hides the in-chat goal panel for that chat.
  window.addEventListener('odysseus:session-changed', () => { renderChatGoal(_lastGoal, { checkpoint: _lastExtra.checkpoint }); refreshGoal(); });
  window.goalCard = { createGoal, refreshGoal };
  // Re-render on the agent's goal events (wire to your SSE here).
  // window.addEventListener('goal-update', () => refreshGoal());
}