// goalCard.js — renderiza la meta continua (espejo de la goal card de DSH).
const GOAL_PHASES = ['active', 'paused', 'blocked', 'complete'];

export function renderGoal(goal) {
  const card = document.getElementById('goal-card');
  if (!card) return;
  if (!goal) { card.hidden = true; return; }
  card.hidden = false;

  document.getElementById('goal-objective').textContent = goal.objective || '';
  const badge = document.getElementById('goal-badge');
  badge.textContent = goal.phase || 'active';
  badge.className = 'goal-badge ' + (goal.phase || 'active');

  const fill = document.getElementById('goal-progress-fill');
  const pct = goal.maxGoalRounds ? Math.min(100, (goal.roundsStarted / goal.maxGoalRounds) * 100) : 0;
  fill.style.width = pct + '%';
  document.getElementById('goal-rounds').textContent =
    `Ronda ${goal.roundsStarted ?? 0}${goal.maxGoalRounds ? '/' + goal.maxGoalRounds : ''}`;

  const blocked = document.getElementById('goal-blocked');
  const reason = goal.blockedReason?.message || '';
  blocked.hidden = !reason;
  blocked.textContent = reason;
  card.classList.toggle('complete', goal.phase === 'complete');
}

function bind(btnId, action) {
  const el = document.getElementById(btnId);
  el?.addEventListener('click', async () => {
    await fetch('/api/goal/' + action, { method: 'POST' });
    refreshGoal();
  });
}

export async function refreshGoal() {
  try {
    const r = await fetch('/api/goal');
    if (!r.ok) { renderGoal(null); return; }
    const data = await r.json();
    renderGoal(data.goal || null);
  } catch { /* sin meta */ }
}

export function initGoalCard() {
  bind('goal-pause', 'pause');
  bind('goal-resume', 'resume');
  bind('goal-complete', 'complete');
  refreshGoal();
  // Re-render on the agent's goal events (wire to your SSE here).
}