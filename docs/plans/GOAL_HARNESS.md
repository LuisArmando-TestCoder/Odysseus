# GOAL HARNESS SPEC — Odysseus (espejo de DSH)

Backend que empareja la goal card del `UI_SPEC.md`. Mismo modelo que DSH:
un `Goal` es un **objeto versionado**, no texto del chat.

---

## 1. STATE MODEL

```json
{
  "id": "goal-<uuid>",
  "session_id": "<session>",
  "objective": "Dotar a Odysseus de ...",
  "revision": 2,
  "phase": "active",              // active | paused | blocked | complete
  "roundsStarted": 6,
  "maxGoalRounds": 256,
  "blockedReason": { "code": "model-reported", "message": "..." } | null,
  "activation": "armed",          // armed | disarmed
  "createdAt": 1710000000,
  "updatedAt": 1710000000
}
```

Campos no cosméticos:
- **`revision`** — contador monotónico para **concurrencia optimista**: `update_goal`
  exige la revisión exacta; una revisión vieja → conflicto (evita carreras entre rondas).
- **`phase`** — máquina de estados finita.
- **`roundsStarted`** — progreso + tope de auto-continuación.
- **`activation`** — `armed`/`disarmed`: tras resume/fork se desarma hasta que un
  humano pide continuar.
- **`blockedReason`** — objeto `{code, message}`, no string suelto.

---

## 2. FUNCTIONS

### `create_goal(session_id, objective, max_goal_rounds=None) -> Goal`
Crea y arma. Se usa cuando el pedido humano es un objetivo de larga duración,
**aunque no diga "goal"**; no para trabajo de un solo turno.

### `get_goal(session_id) -> Goal | None`
Lee el goal actual (incluye `id` y `revision` que `update_goal` exige).

### `update_goal(session_id, revision, action, ...) -> Goal`
Transiciona la fase. **Exige `revision` exacta.**

| action | Efecto | Quién puede |
|---|---|---|
| `edit` | cambia objective/max_goal_rounds; reabre si estaba complete/blocked; re-arma | humano (top-level) |
| `pause` | phase → paused, desarma | humano (top-level) |
| `resume` | phase → active, limpia blockedReason, re-arma | humano (top-level) |
| `complete` | phase → complete, desarma | humano o auto-continuación |
| `blocked` | phase → blocked, fija blockedReason, desarma | auto-continuación |

---

## 3. FRAMING (reglas de ciclo de vida, verbatim de DSH)

1. **`edit`/`pause`/`resume` requieren pedido humano directo.** La auto-continuación
   no puede pausar ni redefinir el objetivo sola.
2. **`complete`/`blocked` sí se permiten durante auto-continuación.**
3. **`complete` solo cuando el objetivo está realmente logrado** (con evidencia),
   no cuando "ya no sé qué más hacer".
4. **`blocked` solo tras 3 rondas consecutivas con la misma condición** bloqueante,
   y con `blocked_reason` concreto. *Dificultad, incertidumbre o trabajo útil
   restante NO es "blocked".*
5. **Tras resume/fork, un goal activo se desarma** (`activation: disarmed`);
   cuando un humano pide "continuar/resumir" en cualquier idioma, se re-arma con
   `update_goal(..., action="resume")`.
6. **Tope:** al alcanzar `maxGoalRounds` sin completar → `blocked` con
   `"maxGoalRounds reached"`.

---

## 4. INFRAESTRUCTURA

### 4.1 Persistencia — `data/goals/<session>.md.json` (escritura atómica)

Reusar `core/atomic_io.atomic_write_json` (ya existe) — es el mismo `dsh-atomic-write`.

### 4.2 `src/goal.py` (código)

```python
"""Persistent, versioned goal state — mirror of DSH's goal system."""
import json, os, re, time, uuid
from typing import Any, Dict, Optional

PHASES = ("active", "paused", "blocked", "complete")


class GoalConflict(Exception):
    """update_goal called with a stale revision."""


def _path(session_id: str) -> str:
    from src.constants import DATA_DIR
    d = os.path.join(DATA_DIR, "goals")
    os.makedirs(d, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", str(session_id or "default"))
    return os.path.join(d, f"{safe}.json")


def _read(session_id: str) -> Optional[Dict[str, Any]]:
    p = _path(session_id)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _write(session_id: str, goal: Dict[str, Any]) -> None:
    from core.atomic_io import atomic_write_json
    atomic_write_json(_path(session_id), goal)


def create_goal(session_id: str, objective: str,
                max_goal_rounds: Optional[int] = None) -> Dict[str, Any]:
    goal = {
        "id": f"goal-{uuid.uuid4()}",
        "session_id": session_id,
        "objective": (objective or "").strip(),
        "revision": 1,
        "phase": "active",
        "roundsStarted": 0,
        "maxGoalRounds": max_goal_rounds or 256,
        "blockedReason": None,
        "activation": "armed",
        "createdAt": time.time(),
        "updatedAt": time.time(),
    }
    _write(session_id, goal)
    return goal


def get_goal(session_id: str) -> Optional[Dict[str, Any]]:
    return _read(session_id)


def update_goal(session_id: str, revision: int, action: str,
                objective: Optional[str] = None,
                max_goal_rounds: Optional[int] = None,
                blocked_reason: Optional[str] = None) -> Dict[str, Any]:
    goal = _read(session_id)
    if not goal:
        raise KeyError("no goal for this session")
    if goal["revision"] != revision:
        raise GoalConflict(f"stale revision: have {goal['revision']}, got {revision}")

    if action == "edit":
        if objective is not None:
            goal["objective"] = objective.strip()
        if max_goal_rounds is not None:
            goal["maxGoalRounds"] = max_goal_rounds
        if goal["phase"] in ("complete", "blocked"):
            goal["phase"] = "active"
            goal["blockedReason"] = None
        goal["activation"] = "armed"
    elif action == "pause":
        goal["phase"] = "paused"
        goal["activation"] = "disarmed"
    elif action == "resume":
        if goal["phase"] in ("paused", "blocked"):
            goal["phase"] = "active"
            goal["blockedReason"] = None
        goal["activation"] = "armed"
    elif action == "complete":
        goal["phase"] = "complete"
        goal["activation"] = "disarmed"
    elif action == "blocked":
        goal["phase"] = "blocked"
        goal["blockedReason"] = {"code": "model-reported", "message": blocked_reason or ""}
        goal["activation"] = "disarmed"
    else:
        raise ValueError(f"unknown action {action!r}")

    goal["revision"] += 1
    goal["updatedAt"] = time.time()
    _write(session_id, goal)
    return goal
```

### 4.3 `routes/goal_routes.py` (código)

```python
def setup_goal_routes(owner_resolver) -> APIRouter:
    router = APIRouter(prefix="/api/goal", tags=["goal"])

    @router.get("")
    async def goal_get(request: Request):
        sid = owner_resolver(request)
        g = get_goal(sid)
        return {"goal": g} if g else {"goal": None}

    @router.post("/{action}")
    async def goal_update(request: Request, action: str):
        sid = owner_resolver(request)
        body = await request.json() if (await request.body()) else {}
        g = get_goal(sid)
        if not g:
            raise HTTPException(404, "no active goal")
        if action in ("edit", "pause", "resume") and not _is_top_level_human(request):
            raise HTTPException(403, "this action requires a direct human request")
        try:
            g = update_goal(sid, int(body.get("revision", g["revision"])), action,
                            objective=body.get("objective"),
                            max_goal_rounds=body.get("max_goal_rounds"),
                            blocked_reason=body.get("blocked_reason"))
        except GoalConflict:
            raise HTTPException(409, "stale revision — reload and retry")
        return {"goal": g}

    return router
```

### 4.4 `src/goal_round_driver.py` — el loop de auto-continuación

```python
async def goal_round_loop(session_id: str, run_agent_round, blocked_conditions: list):
    """Auto-continue rounds until complete/blocked/paused, mirroring DSH's
    goal-round-driver. Persists progress in the checkpoint (already exists)."""
    from src.goal import get_goal, update_goal, GoalConflict

    same_blocked = 0
    last_blocked_reason = None
    while True:
        goal = get_goal(session_id)
        if not goal or goal["phase"] in ("complete", "blocked", "paused"):
            break
        if goal["roundsStarted"] >= goal["maxGoalRounds"]:
            update_goal(session_id, goal["revision"], "blocked",
                        blocked_reason="maxGoalRounds reached")
            break

        # 1) run one agent round (the agent appends to checkpoint via src/checkpoint.py)
        outcome = await run_agent_round(goal)

        # 2) bump progress
        try:
            goal = update_goal(session_id, goal["revision"], "edit")  # no-op edit to bump rev
        except GoalConflict:
            goal = get_goal(session_id)
        goal["roundsStarted"] += 1
        # (persist roundsStarted — the loop owns the counter, not the model)

        # 3) decide continue vs stop
        if outcome.get("done"):
            update_goal(session_id, goal["revision"], "complete")
            break
        if outcome.get("blocked_reason"):
            if outcome["blocked_reason"] == last_blocked_reason:
                same_blocked += 1
            else:
                same_blocked = 1
                last_blocked_reason = outcome["blocked_reason"]
            if same_blocked >= 3:  # DSH rule: blocked only after 3 consecutive rounds
                update_goal(session_id, goal["revision"], "blocked",
                            blocked_reason=last_blocked_reason)
                break
        else:
            same_blocked = 0
```

---

## 5. WIRING al agent loop

Igual que el checkpoint: al iniciar cada ronda, inyectar el goal en el prompt.

En `src/agent_loop.py`, junto a la inyección de checkpoint (línea ~1574):

```python
    # Inject the active goal so the agent stays on-task across rounds.
    try:
        from src.goal import get_goal as _get_goal
        _goal = _get_goal(session_id)
    except Exception:
        _goal = None
    if _goal and _goal["phase"] == "active":
        _goal_msg = (
            "=== ACTIVE GOAL ===\n"
            f"objective: {_goal['objective']}\n"
            f"round {_goal['roundsStarted']}/{_goal['maxGoalRounds']} · phase {_goal['phase']}\n"
            "Work toward this objective; use the checkpoint to resume, not re-derive. "
            "When the objective is truly achieved, stop and report completion."
        )
        messages.insert(0, {"role": "system", "content": _goal_msg})
```

---

## 6. Resumen (dónde / qué)

| Archivo | Qué |
|---|---|
| `src/goal.py` | modelo `Goal` + `create/get/update` con `revision` y `phase` FSM |
| `routes/goal_routes.py` | `GET /api/goal` + `POST /api/goal/{action}` con guard de "humano directo" |
| `src/goal_round_driver.py` | loop de auto-continuación + regla de 3 rondas para `blocked` |
| `src/agent_loop.py` | inyectar `=== ACTIVE GOAL ===` en el prompt (junto al checkpoint) |
| `core/atomic_io.py` | ya existe — reusar para persistencia atómica |
| `src/checkpoint.py` | ya existe — el driver lo usa para persistir progreso entre rondas |

**Framing en una línea:** el goal es un **objeto versionado** (`revision` para
concurrencia, `phase` para el ciclo de vida, `activation` para armed/disarmed)
que **el harness posee y el driver consume** — el modelo solo trabaja en la ronda;
no "recuerda" el estado, lo lee del harness.
