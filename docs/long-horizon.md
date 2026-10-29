# Long-horizon goals that start on their own

The chat detects requests that need sustained, multi-round work and runs them
as a goal without being asked. Everything the goal does streams into the chat.

## Detection (`src/long_horizon.py`)

Every user message (not research, not incognito) is scored, English and Spanish:

| Signal | Points |
|---|---|
| asks for autonomous / continuous work ("autonomously", "keep going until done", "sin parar", "hasta que…") | +3 |
| ongoing / periodic ("monitor", "every day", "track", "da seguimiento") | +2 (+1 for two or more) |
| broad scope ("entire", "all", "case by case", "investigate", "research", "build", "a fondo", "completo"…) | +1 each, max +4 |
| a 3+ item numbered/bulleted plan | +2 |
| sequenced steps ("then", "after that", "luego"…) | +1 |
| 4+ instructions; long message (>400 / >1200 chars) | +1 each |
| asks for a quick answer ("quickly", "one sentence", "rápido") | −2 |
| short question; very short message | −3 each |

Threshold `auto_goal_threshold` (default 4). Then, given the owner's goal state:

| Situation | Action |
|---|---|
| a goal already runs in **this** chat | **steer** — the turn is told the message steers the goal; next rounds see it |
| no live goal, score ≥ threshold, `auto_goal_mode=auto` (default) | **started** — goal created in this chat; the turn plans (todo_write), does step one and checkpoints |
| a goal is live in **another** chat (one goal per owner), or mode `suggest` | **suggest** — the turn offers to run it as a goal; nothing is hijacked |
| otherwise / mode `off` | nothing |

An auto-started goal is held for 20 s (`holdUntil`) so the user's own turn
always runs first; after it, the goal driver starts rounds whenever the chat is
idle. Stop pauses the goal; finish_goal ends it.

## Nothing hidden

- **In the thread:** a status note says when a long-horizon task was detected
  and a goal started (`goal_auto` stream event).
- **Self-messages:** each goal round / autopilot turn opens with Odysseus'
  own prompt to itself ("Goal round 3/256 — continuing on my own toward: …"),
  live and after reload (`self_message` in the saved turn's metadata).
- **Thinking stream:** reasoning tokens stream live above the answer
  (`.live-thinking`), then fold into the usual "View thinking process" section.
- **Command stream:** every tool call shows its command, and long-running
  bash/python shows its live output tail.
- **Browser actions:** `run_javascript` now works inside goal rounds and
  autopilot turns (the live renderer answers eval requests).
- **Steering:** each round sees the chat's latest user messages and the tail
  of its previous visible output, not just the checkpoint.

Tests: `tests/test_long_horizon.py`.
