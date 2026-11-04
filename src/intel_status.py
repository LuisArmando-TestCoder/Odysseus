"""
intel_status.py — one small, owner-scoped "how is my intel doing?" summary.

Backs ``GET /api/intel/status``, which the main SPA polls to put the Intel
Board (``/cases``) one click away with a badge: open cases, open steps,
cases going cold, the case a goal is driving, and watches that changed
recently or keep failing, selectors that became shared between cases in the
last day, and the state of a goal bound to a case. ``attention`` is the
number the badge shows — things that want the user (cold cases + recent
watch changes + failing watches + new cross-case links + a case goal that
is blocked); everything else is context for the tooltip.

Stdlib only; the case store, watch store, goal API and clock are injectable
for tests. Never raises: a broken store degrades to zeros plus a
``degraded`` list naming what could not be read.
"""

from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional

DAY = 86400.0
RECENT_CHANGE_HOURS = 24.0
FAILING_ERROR_COUNT = 3
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")


def _clean(text: Any, limit: int = 80) -> str:
    s = " ".join(_CTRL_RE.sub(" ", str(text or "")).split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def intel_status(owner: Optional[str], *, store=None, watch_store=None, goal_api=None,
                 now: Optional[float] = None, stale_days: Optional[float] = None,
                 recent_hours: float = RECENT_CHANGE_HOURS) -> Dict[str, Any]:
    """Summary dict for the badge. Keys:

    ``open_cases, open_steps, cold_cases, cold, driven, goal, new_links,
    links, watches_active, watches_changed, watches_failing, attention,
    label, degraded``.

    ``goal`` is ``{"phase", "case_id", "title", "reason"}`` when the owner's
    goal is bound to one of their open cases and is active, paused or
    blocked (``reason`` only when blocked); a blocked goal counts toward
    ``attention`` unless its case is already counted as going cold.
    ``links`` lists up to 3 new cross-case links as ``{"value", "cases"}``.
    """
    now = time.time() if now is None else float(now)
    out: Dict[str, Any] = {
        "open_cases": 0, "open_steps": 0, "cold_cases": 0, "cold": [],
        "driven": None, "goal": None, "new_links": 0, "links": [],
        "watches_active": 0, "watches_changed": 0,
        "watches_failing": 0, "attention": 0, "label": "", "degraded": [],
    }
    if not owner:
        out["label"] = "Intel Board"
        return out

    # ── cases ────────────────────────────────────────────────────────────
    try:
        if store is None:
            from src.casefiles import get_case_store
            store = get_case_store()
        cases = store.list_cases(owner, status="open")
        out["open_cases"] = len(cases)
        out["open_steps"] = sum(int(c.get("open_steps") or 0) for c in cases)
        titles = {c["id"]: c.get("title", "") for c in cases}
    except Exception:
        out["degraded"].append("cases")
        titles = {}

    cold_ids: set = set()
    if titles:
        try:
            from src import case_nudges
            kw: Dict[str, Any] = {"store": store, "goal_api": goal_api, "now": now, "limit": 50}
            if stale_days is not None:
                kw["stale_days"] = stale_days
            cold = case_nudges.stale_cases(owner, **kw)
            out["cold_cases"] = len(cold)
            cold_ids = {c["id"] for c in cold}
            out["cold"] = [{"id": c["id"], "title": _clean(c.get("title")),
                            "idle_days": c.get("idle_days"), "action": c.get("action")}
                           for c in cold[:5]]
        except Exception:
            out["degraded"].append("cold_cases")
        try:
            from src import case_nudges
            driven_id = case_nudges._driven_case_id(owner, goal_api)
            if driven_id and driven_id in titles:
                out["driven"] = {"id": driven_id, "title": _clean(titles[driven_id])}
        except Exception:
            pass
        out["goal"] = _case_goal(owner, goal_api, titles)
        try:
            links = store.recent_overlaps(owner, since=now - recent_hours * 3600.0, limit=50)
            out["new_links"] = len(links)
            out["links"] = [{"value": _clean(h.get("value"), 60),
                             "cases": [_clean(c.get("title"), 40) for c in (h.get("cases") or [])][:3]}
                            for h in links[:3]]
        except Exception:
            out["degraded"].append("cross_case_links")

    # ── watchlist ────────────────────────────────────────────────────────
    try:
        if watch_store is None:
            from src.watchlist import get_watch_store
            watch_store = get_watch_store()
        cutoff = now - recent_hours * 3600.0
        for w in watch_store.list(owner):
            if not w.get("active", True):
                continue
            out["watches_active"] += 1
            if float(w.get("last_changed") or 0) >= cutoff:
                out["watches_changed"] += 1
            if int(w.get("error_count") or 0) >= FAILING_ERROR_COUNT:
                out["watches_failing"] += 1
    except Exception:
        out["degraded"].append("watchlist")

    blocked = 0
    g = out["goal"]
    if g and g["phase"] == "blocked" and g["case_id"] not in cold_ids:
        blocked = 1
    out["attention"] = (out["cold_cases"] + out["watches_changed"] + out["watches_failing"]
                        + out["new_links"] + blocked)
    out["label"] = _label(out)
    return out


def _case_goal(owner: str, goal_api, titles: Dict[str, str]) -> Optional[Dict[str, Any]]:
    """State of the owner's goal when it is bound to an open case. Never raises."""
    try:
        if goal_api is None:
            from src import goal as goal_api  # type: ignore[no-redef]
        goal = goal_api.get_goal(owner)
    except Exception:
        return None
    if not isinstance(goal, dict):
        return None
    phase = goal.get("phase")
    case_id = goal.get("case_id")
    if phase not in ("active", "paused", "blocked") or not case_id or case_id not in titles:
        return None
    reason = ""
    if phase == "blocked":
        br = goal.get("blockedReason")
        reason = _clean(br.get("message") if isinstance(br, dict) else br, 120)
    return {"phase": phase, "case_id": case_id, "title": _clean(titles[case_id]),
            "reason": reason}


def _label(s: Dict[str, Any]) -> str:
    """Tooltip text, e.g. 'Intel Board — 3 open cases (5 open steps) · 1 going cold'."""
    if not s["open_cases"] and not s["watches_active"]:
        return "Intel Board — no open cases yet"
    parts: List[str] = []
    if s["open_cases"]:
        p = _plural(s["open_cases"], "open case")
        if s["open_steps"]:
            p += f" ({_plural(s['open_steps'], 'open step')})"
        parts.append(p)
    if s["cold_cases"]:
        parts.append(f"{s['cold_cases']} going cold")
    g = s.get("goal")
    if g and g["phase"] == "blocked":
        p = f"goal blocked on “{g['title']}”"
        parts.append(p + (f": {g['reason']}" if g["reason"] else ""))
    elif g and g["phase"] == "paused":
        parts.append(f"goal paused on “{g['title']}”")
    elif s["driven"]:
        parts.append(f"goal driving “{s['driven']['title']}”")
    if s.get("new_links"):
        n = s["new_links"]
        parts.append(f"{n} new cross-case link" + ("" if n == 1 else "s"))
    if s["watches_active"]:
        n = s["watches_active"]
        parts.append(f"{n} watch" + ("" if n == 1 else "es"))
    if s["watches_changed"]:
        parts.append(f"{s['watches_changed']} changed today")
    if s["watches_failing"]:
        parts.append(f"{s['watches_failing']} failing")
    if s["degraded"]:
        parts.append("some intel unavailable")
    return "Intel Board — " + " · ".join(parts)
