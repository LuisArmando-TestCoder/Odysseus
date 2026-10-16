// goalCard.js — renderiza la meta continua (espejo de la goal card de DSH).
const GOAL_PHASES = ['active', 'paused', 'blocked', 'complete'];

let _lastPhase = null;

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
  badge.textContent = goal.phase || 'active';
  badge.className = 'goal-badge ' + (goal.phase || 'active');

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
    if (goal.phase === 'complete') showGoalToast('✅ Meta completada', 'done');
    else if (goal.phase === 'blocked') showGoalToast('⛔ Meta bloqueada: ' + (reason || '—'), 'blocked');
  }
  _lastPhase = goal.phase;
  card.classList.toggle('blocked', goal.phase === 'blocked');
  card.classList.toggle('complete', goal.phase === 'complete');

  // ── GAP 7: banner in-chat ──
  const banner = document.getElementById('goal-banner');
  if (banner) {
    banner.hidden = false;
    banner.textContent = `🎯 ${goal.phase === 'active' ? 'Meta activa' : 'Meta ' + goal.phase}: ${goal.objective}`;
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
  t.textContent = msg;
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