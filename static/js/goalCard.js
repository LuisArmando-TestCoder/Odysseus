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
  if (!goal) {
    card.hidden = true;
    const banner = document.getElementById('goal-banner');
    if (banner) banner.hidden = true;
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
    `Ronda ${goal.roundsStarted ?? 0}${goal.maxGoalRounds ? '/' + goal.maxGoalRounds : ''}`;

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

  // ── GAP 7: banner in-chat ──
  const banner = document.getElementById('goal-banner');
  if (banner) {
    banner.hidden = false;
    // Reset any prior phase class so the base (neutral) style is the fallback,
    // then apply the current phase's class for the visible state.
    banner.classList.remove('active', 'paused', 'blocked', 'complete');
    banner.classList.add(phase);
    banner.innerHTML = _GSVG.target + '<span>' + (goal.phase === 'active' ? 'Meta activa' : 'Meta ' + goal.phase) + ': ' + goal.objective + '</span>';
    banner.onclick = () => document.getElementById('goal-card')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }
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
      fetch('/api/goal/queue').then(r => r.ok ? r.json() : null).catch(() => null),
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
  // Re-render on the agent's goal events (wire to your SSE here).
  // window.addEventListener('goal-update', () => refreshGoal());
}