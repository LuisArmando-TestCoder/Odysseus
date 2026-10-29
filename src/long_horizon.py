"""
long_horizon.py — detect long-horizon requests and start goals on their own.

When a user message describes work that clearly needs many rounds — "research
everything about X", "investigate case by case", "keep monitoring and report",
a numbered multi-step plan, "do it autonomously until it's done" — the chat
should not wait for the user to open a goal by hand. ``decide`` scores the
message, looks at the owner's current goal state, and returns one of:

  started   no goal was live → a goal is created (armed) in THIS chat; the
            first turn does step one and the goal driver keeps going in
            visible rounds that stream into the chat.
  steer     a goal is already running in this chat → the message steers it
            (the next rounds see it), no new goal.
  suggest   a goal is live in ANOTHER chat (one goal per owner) or the mode is
            "suggest" → the turn is told to propose a goal instead.
  none      not long-horizon, incognito, or the feature is off.

Settings: ``auto_goal_mode`` = "auto" (default) | "suggest" | "off";
``auto_goal_threshold`` (default 4.0).

The scorer is deterministic, stdlib-only and bilingual (English / Spanish).
"""

from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional

DEFAULT_THRESHOLD = 4.0
HOLD_SECONDS = 20

_AUTONOMY = [
    r"autonom\w*", r"aut[oó]nom\w*", r"relentless\w*", r"independently", r"on (your|its) own",
    r"keep (going|working|at it)", r"don'?t stop", r"until (it'?s |it is |you'?re |everything is )?(done|finished|complete)",
    r"without (asking|stopping|waiting)", r"continuous\w*", r"constant\w*", r"around the clock", r"24/7",
    r"sin parar", r"hasta (que|terminar|acabar)", r"por tu cuenta", r"por s[ií] solo", r"constantemente",
    r"continuamente", r"sin detenerte", r"sin preguntar", r"long[- ]horizon", r"in the background",
]
_ONGOING = [
    r"every (day|week|hour|morning|night|month)", r"daily", r"weekly", r"hourly", r"monitor\w*",
    r"keep (an eye|track|tabs)", r"track\w*", r"over time", r"ongoing", r"follow[- ]up\w*",
    r"cada (d[ií]a|semana|hora|mes)", r"diariamente", r"monitore\w*", r"da(r|le)? seguimiento", r"vigila\w*",
]
_SCOPE = [
    r"entire", r"whole", r"all (of|the)", r"every\w*", r"comprehensive\w*", r"exhaustive\w*",
    r"end[- ]to[- ]end", r"thorough\w*", r"deep[- ]?dive", r"in[- ]depth", r"case by case",
    r"step by step", r"investigat\w*", r"research\w*", r"audit\w*", r"build\w*", r"implement\w*",
    r"migrat\w*", r"refactor\w*", r"overhaul\w*", r"map (out )?(all|every|the)", r"dossier",
    r"todo(s|as)?", r"complet[oa]s?", r"investig\w*", r"a fondo", r"exhaustiv\w*", r"caso por caso",
    r"paso a paso", r"construy\w*", r"recopila\w*", r"analiza\w*",
]
_SEQUENCE = [r"\bthen\b", r"after that", r"\bfinally\b", r"\bnext\b", r"\bluego\b", r"despu[eé]s",
             r"\bfinalmente\b", r"y (luego|despu[eé]s)"]
_QUICK = [r"\bquick(ly)?\b", r"\bbrief(ly)?\b", r"just tell me", r"one (sentence|line|word)", r"\btl;?dr\b",
          r"\br[aá]pido\b", r"en una (frase|l[ií]nea)", r"\bsolo dime\b", r"\bnada m[aá]s\b"]
_QUESTION_START = re.compile(
    r"^\s*(what|who|when|where|why|how|which|is|are|do|does|can|could|should|qu[eé]|qui[eé]n|"
    r"cu[aá]ndo|d[oó]nde|por qu[eé]|c[oó]mo|cu[aá]l|es|son)\b", re.I)


def _count(patterns: List[str], text: str) -> List[str]:
    hits = []
    for p in patterns:
        m = re.search(r"\b" + p if not p.startswith(r"\b") else p, text, re.I)
        if m:
            hits.append(m.group(0).lower())
    return hits


def score_message(message: str) -> Dict[str, Any]:
    """Score how long-horizon a message is. Returns {score, reasons}."""
    text = (message or "").strip()
    reasons: List[str] = []
    score = 0.0
    if not text:
        return {"score": 0.0, "reasons": []}

    auto = _count(_AUTONOMY, text)
    if auto:
        score += 3
        reasons.append("asks for autonomous/continuous work: " + ", ".join(auto[:3]))
    ongoing = _count(_ONGOING, text)
    if ongoing:
        score += 2 + (1 if len(ongoing) >= 2 else 0)
        reasons.append("ongoing/periodic: " + ", ".join(ongoing[:3]))
    scope = _count(_SCOPE, text)
    if scope:
        score += min(4, len(scope))
        reasons.append("broad scope: " + ", ".join(scope[:4]))
    items = re.findall(r"(?m)^\s*(?:\d+[.)]|[-*•])\s+\S", text)
    if len(items) >= 3:
        score += 2
        reasons.append(f"{len(items)}-item plan/list")
    seq = _count(_SEQUENCE, text)
    if len(seq) >= 2:
        score += 1
        reasons.append("sequenced steps")
    sentences = [s for s in re.split(r"[.!?\n]+", text) if len(s.split()) >= 3]
    if len(sentences) >= 4:
        score += 1
        reasons.append(f"{len(sentences)} instructions")
    if len(text) > 400:
        score += 1
        reasons.append("long request")
    if len(text) > 1200:
        score += 1

    quick = _count(_QUICK, text)
    if quick:
        score -= 2
        reasons.append("asks for a quick answer")
    if len(text) < 200 and text.rstrip().endswith("?") and _QUESTION_START.match(text):
        score -= 3
        reasons.append("short question")
    if len(text) < 40:
        score -= 3
    return {"score": round(score, 2), "reasons": reasons}


def objective_from(message: str, limit: int = 600) -> str:
    text = re.sub(r"\s+", " ", (message or "").strip())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _setting(key: str, default):
    try:
        from src.settings import get_setting
        v = get_setting(key, default)
        return default if v is None else v
    except Exception:
        return default


def decide(message: str, owner: Optional[str], session_id: Optional[str], *,
           incognito: bool = False, goal_api=None, mode: Optional[str] = None,
           threshold: Optional[float] = None, create: bool = True) -> Dict[str, Any]:
    """Decide what to do with a user message (see module doc). ``goal_api`` is
    injectable for tests: an object with get_goal(owner) and
    create_goal(owner, objective, session_id=...)."""
    mode = (mode or str(_setting("auto_goal_mode", "auto"))).lower()
    try:
        threshold = float(threshold if threshold is not None else _setting("auto_goal_threshold", DEFAULT_THRESHOLD))
    except (TypeError, ValueError):
        threshold = DEFAULT_THRESHOLD
    out: Dict[str, Any] = {"action": "none", "score": 0.0, "reasons": [], "goal": None}
    if mode == "off" or incognito or not owner or not session_id:
        return out
    if goal_api is None:
        from src import goal as goal_api  # noqa: N811
    try:
        current = goal_api.get_goal(owner)
    except Exception:
        current = None
    live = current if current and current.get("phase") in ("active", "paused") else None

    # A goal already runs in this chat: every message steers it.
    if live and live.get("session_id") == session_id:
        out.update(action="steer", goal=live)
        return out

    s = score_message(message)
    out.update(score=s["score"], reasons=s["reasons"])
    if s["score"] < threshold:
        return out
    objective = objective_from(message)
    out["objective"] = objective
    if live:
        out.update(action="suggest", goal=live, why="a goal is already live in another chat")
        return out
    if mode != "auto" or not create:
        out.update(action="suggest", why="auto_goal_mode is suggest")
        return out
    try:
        g = goal_api.create_goal(owner, objective, session_id=session_id)
        g["autoStarted"] = True
        # The user's own turn runs first: rounds are held briefly so the goal
        # driver's idle check can't race the turn before it registers.
        g["holdUntil"] = time.time() + HOLD_SECONDS
        writer = getattr(goal_api, "_write", None)
        if writer:
            writer(owner, g)
        out.update(action="started", goal=g)
    except Exception as e:  # never break the chat turn
        out.update(action="suggest", why=f"could not create goal: {e}")
    return out


def turn_context(decision: Dict[str, Any]) -> str:
    """System text for the CURRENT turn, so the agent knows what the harness
    just decided and acts accordingly. "" when nothing to say."""
    a = decision.get("action")
    g = decision.get("goal") or {}
    if a == "started":
        return (
            "=== LONG-HORIZON GOAL STARTED AUTOMATICALLY ===\n"
            f"The harness detected that this request needs sustained, multi-round work "
            f"({'; '.join(decision.get('reasons', [])[:3])}) and started a goal for it in this chat:\n"
            f"objective: {g.get('objective', '')}\n"
            "Tell the user in one line that you are taking it on as a long-running goal and that progress "
            "will keep streaming here (Stop pauses it). Then: lay out the plan with todo_write, do the FIRST "
            "concrete step now, and record progress with checkpoint. After this turn the harness keeps "
            "starting rounds on its own while the chat is idle — visible in this chat — until you call "
            "finish_goal(status='done') or finish_goal(status='blocked', reason=...)."
        )
    if a == "steer":
        return (
            "=== ACTIVE GOAL IN THIS CHAT ===\n"
            f"objective: {g.get('objective', '')} (phase: {g.get('phase')}, rounds: {g.get('roundsStarted', 0)})\n"
            "The user's new message steers this goal: answer it, fold any new instruction into the plan "
            "(todo_write) and checkpoint; following rounds will pick it up. If the user asks to stop or "
            "change course entirely, do so (finish_goal with the right status)."
        )
    if a == "suggest":
        why = decision.get("why", "")
        return (
            "=== LONG-HORIZON REQUEST DETECTED ===\n"
            f"This looks like sustained multi-round work but no goal was started ({why}). Do what you can "
            "now, and offer in one line to run it as a goal"
            + (f" (the live goal elsewhere is: {g.get('objective', '')[:200]})" if g else "") + "."
        )
    return ""
