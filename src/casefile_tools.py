"""
casefile_tools.py — agent tool handlers for Case Files and the Watchlist.

Two tools, each multiplexed on an ``op`` field so the model only has to learn
two names:

  case_file     {"op": "open"|"list"|"get"|"update"|"add_entity"|"add_finding"|
                        "link"|"alias"|"add_step"|"done_step"|"pivots"|"search"|
                        "export"|"drive"|"release"|"delete", ...}
  watch_target  {"op": "add"|"list"|"remove"|"pause"|"resume"|"check", ...}

Handlers return the standard tool dict ({"output": str, "exit_code": int} or
{"error": str, "exit_code": 1}).
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Dict, Optional


def _args(content: str) -> Dict[str, Any]:
    content = (content or "").strip()
    if not content:
        return {}
    if content.startswith("```"):
        content = content.strip("`")
        content = content.split("\n", 1)[1] if "\n" in content else ""
    try:
        val = json.loads(content)
    except json.JSONDecodeError:
        raise ValueError("Arguments must be a JSON object")
    if not isinstance(val, dict):
        raise ValueError("Arguments must be a JSON object")
    return val


def _err(msg: str) -> Dict[str, Any]:
    return {"error": msg, "exit_code": 1}


def _ok(text: str, **extra) -> Dict[str, Any]:
    return {"output": text, "exit_code": 0, **extra}


def _case_summary(case: Dict[str, Any], max_items: int = 15) -> str:
    ents = list(case["entities"].values())
    lines = [f"Case {case['id']} — {case['title']} [{case['status']}]"]
    if case.get("objective"):
        lines.append(f"Objective: {case['objective']}")
    lines.append(f"{len(ents)} entities · {len(case['findings'])} findings · "
                 f"{len(case['links'])} links · "
                 f"{sum(1 for s in case['next_steps'] if not s['done'])} open steps")
    if ents:
        lines.append("Entities:")
        for e in sorted(ents, key=lambda e: -e.get("confidence", 0))[:max_items]:
            lines.append(f"  - {e['id']} [{e['type']}] {e['value']} (conf {e.get('confidence', 0):.2f}, "
                         f"{len(e.get('sources') or [])} src)")
    if case["findings"]:
        lines.append("Latest findings:")
        for f in case["findings"][-max_items:]:
            lines.append(f"  - {f['text'][:200]}" + (f" <{f['source_url']}>" if f.get("source_url") else ""))
    open_steps = [s for s in case["next_steps"] if not s["done"]]
    if open_steps:
        lines.append("Open steps:")
        for s in open_steps[:max_items]:
            lines.append(f"  - {s['id']}: {s['text']}")
    return "\n".join(lines)


def run_case_file(args: Dict[str, Any], owner: Optional[str], store=None,
                  session_id: Optional[str] = None, goal_api=None) -> Dict[str, Any]:
    from src.casefiles import get_case_store
    st = store or get_case_store()
    op = str(args.get("op") or args.get("action") or "").lower().strip()
    case_ref = str(args.get("case") or args.get("case_id") or "").strip()
    try:
        if op in ("open", "create", "new"):
            c = st.create_case(owner, args.get("title", ""), args.get("objective", ""), args.get("tags"))
            pivots = st.suggest_pivots(owner, c["id"])
            msg = f"Opened case {c['id']} — {c['title']}."
            if pivots:
                msg += "\nSuggested first moves:\n" + "\n".join(f"  - {p}" for p in pivots)
            return _ok(msg, case_id=c["id"])
        if op == "list":
            cases = st.list_cases(owner, status=args.get("status", "open") or None)
            if not cases:
                return _ok("No cases. Open one with op=open.")
            return _ok("\n".join(f"- {c['id']} · {c['title']} [{c['status']}] — {c['entities']} entities, "
                                 f"{c['findings']} findings, {c['open_steps']} open steps" for c in cases))
        if op == "search":
            hits = st.search(owner, args.get("query", ""))
            if not hits:
                return _ok("No matches across cases.")
            return _ok("\n".join(f"- [{h['kind']}] {h['text']} — {h['case']} ({h['case_id']})" for h in hits))
        if not case_ref:
            return _err("`case` (id, id prefix or title) is required for op=" + (op or "?"))
        if op in ("get", "show", "view"):
            return _ok(_case_summary(st.get_case(owner, case_ref)))
        if op == "update":
            c = st.update_case(owner, case_ref, title=args.get("title"), objective=args.get("objective"),
                               status=args.get("status"), tags=args.get("tags"))
            return _ok(f"Updated {c['id']} [{c['status']}].")
        if op in ("add_entity", "entity"):
            e = st.add_entity(owner, case_ref, str(args.get("value", "")), args.get("type"),
                              notes=args.get("notes", ""), source=args.get("source", ""),
                              confidence=args.get("confidence"), label=args.get("label", ""))
            verb = "Added" if e["created"] else "Corroborated existing"
            return _ok(f"{verb} {e['type']} {e['value']} ({e['id']}, conf {e['confidence']:.2f}).")
        if op in ("add_finding", "finding", "note"):
            f = st.add_finding(owner, case_ref, args.get("text", ""), source_url=args.get("source_url") or args.get("source", ""),
                               confidence=float(args.get("confidence", 0.6)), kind=args.get("kind", "fact"),
                               entities=args.get("entities"), auto_extract=bool(args.get("auto_extract", True)))
            if f.get("duplicate"):
                return _ok(f"Finding already recorded ({f['id']}).")
            msg = f"Recorded finding {f['id']}."
            if f["new_entities"]:
                msg += " New selectors: " + ", ".join(f"{e['type']}:{e['value']}" for e in f["new_entities"])
            return _ok(msg)
        if op == "link":
            l = st.link(owner, case_ref, str(args.get("a", "")), str(args.get("b", "")),
                        args.get("relation", "related_to"), args.get("evidence", ""))
            return _ok(f"Linked ({l['id']}): {args.get('a')} —{l['relation']}→ {args.get('b')}.")
        if op == "alias":
            e = st.add_alias(owner, case_ref, str(args.get("entity", "")), str(args.get("alias", "")))
            return _ok(f"{e['value']} now also known as: {', '.join(e['aliases'])}.")
        if op in ("add_step", "step"):
            s = st.add_step(owner, case_ref, args.get("text", ""))
            return _ok(f"Queued step {s['id']}: {s['text']}")
        if op in ("done_step", "complete_step", "done"):
            s = st.complete_step(owner, case_ref, str(args.get("step", "")), args.get("result", ""))
            return _ok(f"Completed: {s['text']}")
        if op in ("pivots", "suggest", "next"):
            pivots = st.suggest_pivots(owner, case_ref, limit=int(args.get("limit", 12)))
            if args.get("queue"):
                for p in pivots:
                    st.add_step(owner, case_ref, p)
            return _ok("\n".join(f"- {p}" for p in pivots) or "No new pivots — every known selector has queued steps.")
        if op in ("export", "dossier", "report"):
            md = st.export_markdown(owner, case_ref)
            return _ok(md)
        if op in ("drive", "bind_goal", "pursue"):
            from src.case_goal import drive_case
            try:
                maxr = int(args.get("max_goal_rounds") or 0) or None
            except (TypeError, ValueError):
                maxr = None
            r = drive_case(owner, case_ref, session_id=session_id, max_goal_rounds=maxr,
                           store=st, goal_api=goal_api)
            c, g = r["case"], r["goal"]
            n_open = sum(1 for s in c["next_steps"] if not s["done"])
            if r["created"]:
                msg = f"Started a goal that drives case {c['id']} ({c['title']}): rounds will work its open steps."
            else:
                msg = f"The current goal now drives case {c['id']} ({c['title']})."
                if r["rebound_from"]:
                    msg += f" (Previously bound to {r['rebound_from']}.)"
            if not n_open:
                msg += " It has no open steps yet — queue some (op=pivots queue=true) so rounds have work."
            if g.get("phase") == "paused":
                msg += " The goal is paused; resume it from the goal card to start rounds."
            return _ok(msg, goal=g, goal_created=r["created"])
        if op in ("release", "unbind_goal", "stop_driving"):
            from src.case_goal import release_case
            cid = st.resolve_case_id(owner, case_ref)
            g = release_case(owner, cid, goal_api=goal_api)
            return _ok("Goal released from the case." if g else "The goal was not driving this case.")
        if op == "delete":
            st.delete_case(owner, case_ref)
            return _ok("Case deleted.")
        return _err(f"Unknown op {op!r}. Use open, list, get, update, add_entity, add_finding, link, alias, "
                    "add_step, done_step, pivots, search, export, drive, release, delete.")
    except (KeyError, ValueError) as e:
        return _err(str(e).strip("'\""))


def run_watch_target(args: Dict[str, Any], owner: Optional[str], store=None, fetcher=None) -> Dict[str, Any]:
    from src.watchlist import get_watch_store, format_events
    st = store or get_watch_store()
    op = str(args.get("op") or args.get("action") or "list").lower().strip()
    ref = str(args.get("id") or args.get("url") or args.get("watch") or "").strip()
    try:
        if op in ("add", "watch", "create"):
            case_id = str(args.get("case") or args.get("case_id") or "").strip()
            if case_id:
                from src.casefiles import get_case_store
                case_id = get_case_store(os.path.dirname(st.root)).resolve_case_id(owner, case_id)
            w = st.add(owner, args.get("url", ""), label=args.get("label", ""),
                       interval_minutes=int(args.get("interval_minutes", 360)),
                       case_id=case_id, keywords=args.get("keywords"),
                       kind=str(args.get("kind") or ""))
            msg = f"Watching {w['url']} every {w['interval_minutes']} min ({w['id']})."
            if w.get("kind") == "feed":
                msg += " Feed mode: alerts list new items."
            if w.get("keywords"):
                msg += f" Alerts only on: {', '.join(w['keywords'])}."
            if w.get("case_id"):
                msg += f" Changes file into case {w['case_id']}."
            return _ok(msg)
        if op == "list":
            ws = st.list(owner)
            if not ws:
                return _ok("Watchlist is empty.")
            return _ok("\n".join(
                f"- {w['id']} · {w['label']} — {w['url']} every {w['interval_minutes']}m"
                f"{' [feed]' if w.get('is_feed') else ''}"
                f"{'' if w['active'] else ' (paused)'} · {w['change_count']} change(s)"
                + (f" · last error: {w['last_error']}" if w.get("last_error") else "") for w in ws))
        if not ref:
            return _err("`id` or `url` of the watch is required")
        if op in ("remove", "delete", "unwatch"):
            st.remove(owner, ref)
            return _ok("Watch removed.")
        if op in ("pause", "resume"):
            w = st.set_active(owner, ref, op == "resume")
            return _ok(f"{w['label']} {'resumed' if w['active'] else 'paused'}.")
        if op == "check":
            ev = st.check_one(owner, ref, fetcher=fetcher)
            if ev:
                return _ok(format_events([ev]))
            ws = {w["id"]: w for w in st.list(owner)}
            w = next((x for x in ws.values() if x["id"] == ref or x["url"] == ref or x["label"] == ref), None)
            if w and w.get("last_error"):
                return _err(f"Fetch failed: {w['last_error']}")
            return _ok("No change since last check (or baseline captured).")
        return _err(f"Unknown op {op!r}. Use add, list, remove, pause, resume, check.")
    except (KeyError, ValueError) as e:
        return _err(str(e).strip("'\""))


async def do_case_file(content: str, owner: Optional[str] = None,
                       session_id: Optional[str] = None) -> Dict[str, Any]:
    try:
        args = _args(content)
    except ValueError as e:
        return _err(str(e))
    result = await asyncio.to_thread(run_case_file, args, owner, None, session_id)
    if result.get("goal_created") and result.get("goal"):
        # Idle-gated: the first round starts once this turn finishes.
        try:
            from src import goal_driver
            goal_driver.maybe_start_goal_round(owner=owner, goal=result["goal"])
        except Exception:
            pass
    return result


async def do_watch_target(content: str, owner: Optional[str] = None) -> Dict[str, Any]:
    try:
        args = _args(content)
    except ValueError as e:
        return _err(str(e))
    return await asyncio.to_thread(run_watch_target, args, owner)
