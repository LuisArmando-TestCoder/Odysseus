# src/hub.py
"""Knowledge hub — the cross-session layer of Odysseus.

* Transcript search      search(owner, query)  — every chat's full history,
                         simple syntax (words, "phrases", -exclude, in:, role:,
                         tool:, after:, before:, url / PR mapping).
* @-mentions             mention_context(owner, text) — `@[Name](session:ID)`
                         or `@session:ID` pulls that chat's recap into context.
* Cross-session board    share(), inbox_context() — findings / decisions one
                         chat posts are delivered to other chats automatically.
* Rewind snapshots       snapshot(), list_snapshots(), restore() — taken before
                         any destructive history change (compact, truncate,
                         delete, edit), so a clean earlier state can come back.
* Usage analytics        usage(owner) — cost / tokens / calls by source, tool,
                         MCP server, skill, sub-agent and chat; context footprint.
* Live artifacts         publish_live(), live_get() — documents served as live
                         web pages at /live/<token> that update as they change.

Everything is owner-scoped. Storage lives under data/hub/ (JSON) next to the
existing SQLite history; nothing here is ever sent to a model unless a turn
asks for it (mentions, inbox, tools).
"""

import json
import logging
import os
import re
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)
_lock = threading.RLock()


def _data_dir() -> str:
    from src.constants import DATA_DIR
    d = os.path.join(DATA_DIR, "hub")
    os.makedirs(d, exist_ok=True)
    return d


def _db_path() -> str:
    from src.constants import DATA_DIR
    return os.path.join(DATA_DIR, "app.db")


def _db():
    c = sqlite3.connect(_db_path(), timeout=10)
    c.row_factory = sqlite3.Row
    return c


def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", str(s or "_"))


def _read_json(path: str, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write_json(path: str, obj) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)


def _text_of(content) -> str:
    if isinstance(content, str):
        try:
            if content[:1] == "[":
                v = json.loads(content)
                if isinstance(v, list):
                    return " ".join(str(p.get("text", "")) for p in v if isinstance(p, dict))
        except ValueError:
            pass
        return content
    if isinstance(content, list):
        return " ".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
    return str(content or "")


def owned_sessions(owner: Optional[str]) -> Dict[str, Dict[str, Any]]:
    """id → {name, updated, archived, messages} for the owner's chats."""
    with _db() as c:
        if owner:
            rows = c.execute("SELECT id,name,owner,archived,last_message_at,updated_at,message_count,"
                             "total_input_tokens,total_output_tokens,model,folder FROM sessions "
                             "WHERE owner=? OR owner IS NULL OR owner=''", (owner,)).fetchall()
        else:
            rows = c.execute("SELECT id,name,owner,archived,last_message_at,updated_at,message_count,"
                             "total_input_tokens,total_output_tokens,model,folder FROM sessions").fetchall()
    return {r["id"]: dict(r) for r in rows}


# ── project scoping ─────────────────────────────────────────────────────────
# Cross-chat context (agent search, recaps, @-mentions, the board's automatic
# delivery) only flows between chats in the SAME PROJECT. Folders nest
# ("Investigations/Costa Rica/Politicians"); the project is the TOP-LEVEL
# folder, so every chat anywhere inside it shares context. A chat outside any
# folder is isolated. Setting `cross_session_context`:
#   "project" (default) · "all" / true (every chat) · "off" / false (none).

def scope_mode() -> str:
    try:
        from src.settings import get_setting
        v = get_setting("cross_session_context", "project")
    except Exception:
        v = "project"
    v = str(v if v is not None else "project").strip().lower()
    if v in ("false", "0", "off", "none", "no"):
        return "off"
    if v in ("true", "1", "all", "on", "yes"):
        return "all"
    return "project"


def project_of(owner: Optional[str], session_id: Optional[str]) -> Optional[str]:
    if not session_id:
        return None
    s = owned_sessions(owner).get(session_id)
    return _top((s or {}).get("folder"))


def _top(folder) -> Optional[str]:
    if not isinstance(folder, str):
        return None
    parts = [p.strip() for p in folder.split("/") if p.strip()]
    return parts[0] if parts else None


def peers(owner: Optional[str], session_id: Optional[str]) -> Optional[set]:
    """Chat ids this chat may draw context from (excluding itself).
    None = no restriction (mode "all"); empty set = isolated."""
    mode = scope_mode()
    if mode == "all":
        return None
    if mode == "off" or not session_id:
        return set()
    proj = project_of(owner, session_id)
    if not proj:
        return set()
    return {sid for sid, s in owned_sessions(owner).items()
            if sid != session_id and _top(s.get("folder")) == proj}


# ── transcript search ───────────────────────────────────────────────────────

_TOKEN_RE = re.compile(r'(-?)(\w+):("[^"]*"|\S+)|(-?)"([^"]+)"|(-?)(\S+)')
_URL_RE = re.compile(r"https?://\S+")


def parse_query(q: str) -> Dict[str, Any]:
    """words / "phrases" / -exclude / in:<chat> / role:user|ai / tool:<name> /
    after:YYYY-MM-DD / before:YYYY-MM-DD. A URL (e.g. a pull-request link) is
    matched literally, so pasting a PR URL finds the chats that touched it."""
    out = {"terms": [], "exclude": [], "in": None, "role": None, "tool": None,
           "after": None, "before": None, "urls": []}
    q = (q or "").strip()
    for u in _URL_RE.findall(q):
        out["urls"].append(u.rstrip(").,>"))
    q = _URL_RE.sub(" ", q)
    for m in _TOKEN_RE.finditer(q):
        if m.group(2):
            neg, key, val = m.group(1), m.group(2).lower(), m.group(3).strip('"')
            if key in ("in", "session", "chat"):
                out["in"] = val
            elif key == "role":
                out["role"] = {"ai": "assistant", "bot": "assistant", "me": "user"}.get(val.lower(), val.lower())
            elif key == "tool":
                out["tool"] = val
            elif key in ("after", "since"):
                out["after"] = val
            elif key in ("before", "until"):
                out["before"] = val
            else:
                (out["exclude"] if neg else out["terms"]).append(f"{key}:{val}")
        elif m.group(5):
            (out["exclude"] if m.group(4) else out["terms"]).append(m.group(5))
        elif m.group(7):
            (out["exclude"] if m.group(6) else out["terms"]).append(m.group(7))
    return out


def _pr_variants(url: str) -> List[str]:
    """A GitHub/GitLab PR URL also matches 'owner/repo#123' and '#123'."""
    v = [url]
    m = re.match(r"https?://(?:www\.)?(github|gitlab)\.com/([^/]+)/([^/]+)/(?:-/)?(?:pull|merge_requests)/(\d+)", url)
    if m:
        v += [f"{m.group(2)}/{m.group(3)}#{m.group(4)}", f"{m.group(3)}/pull/{m.group(4)}",
              f"{m.group(3)}#{m.group(4)}"]
    return v


def _snippet(text: str, needles: List[str], width: int = 220) -> str:
    low = text.lower()
    pos = -1
    for n in needles:
        pos = low.find(n.lower())
        if pos >= 0:
            break
    if pos < 0:
        return text[:width].strip()
    a = max(0, pos - width // 3)
    b = min(len(text), a + width)
    return ("…" if a else "") + text[a:b].strip() + ("…" if b < len(text) else "")


def search(owner: Optional[str], query: str, limit: int = 40,
           allowed: Optional[set] = None) -> Dict[str, Any]:
    """`allowed` restricts the chats searched (project scoping for the agent);
    None = all of the owner's chats (the user's own search UI)."""
    p = parse_query(query)
    sessions = owned_sessions(owner)
    if not sessions:
        return {"query": p, "results": [], "sessions": []}
    ids = list(sessions) if allowed is None else [i for i in sessions if i in allowed]
    if p["in"]:
        k = p["in"].lower()
        ids = [i for i in ids if i.startswith(k) or k in (sessions[i].get("name") or "").lower()]
    if not ids:
        return {"query": p, "results": [], "sessions": []}
    where, args = [], []
    where.append("session_id IN (%s)" % ",".join("?" * len(ids)))
    args += ids
    for t in p["terms"]:
        where.append("content LIKE ? ESCAPE '\\'")
        args.append("%" + t.replace("%", r"\%").replace("_", r"\_") + "%")
    for t in p["exclude"]:
        where.append("content NOT LIKE ? ESCAPE '\\'")
        args.append("%" + t.replace("%", r"\%").replace("_", r"\_") + "%")
    if p["urls"]:
        ors = []
        for u in p["urls"]:
            for v in _pr_variants(u):
                ors.append("content LIKE ? OR metadata LIKE ?")
                args += ["%" + v + "%", "%" + v + "%"]
        where.append("(" + " OR ".join(ors) + ")")
    if p["role"] in ("user", "assistant", "system"):
        where.append("role = ?")
        args.append(p["role"])
    if p["tool"]:
        where.append("metadata LIKE ?")
        args.append('%"tool": "' + p["tool"] + '%')
    if p["after"]:
        where.append("timestamp >= ?")
        args.append(p["after"])
    if p["before"]:
        where.append("timestamp < ?")
        args.append(p["before"])
    if not (p["terms"] or p["urls"] or p["tool"]):
        return {"query": p, "results": [], "sessions": []}
    sql = ("SELECT id, session_id, role, content, timestamp FROM chat_messages WHERE "
           + " AND ".join(where) + " ORDER BY timestamp DESC LIMIT ?")
    args.append(int(limit) * 3)
    with _db() as c:
        rows = c.execute(sql, args).fetchall()
    needles = p["terms"] + [v for u in p["urls"] for v in _pr_variants(u)] + ([p["tool"]] if p["tool"] else [])
    results, per = [], {}
    for r in rows:
        sid = r["session_id"]
        per[sid] = per.get(sid, 0) + 1
        if len(results) >= limit:
            continue
        txt = _text_of(r["content"])
        results.append({
            "session_id": sid, "session_name": (sessions.get(sid) or {}).get("name") or "Untitled",
            "message_id": r["id"], "role": r["role"], "timestamp": r["timestamp"],
            "snippet": _snippet(txt, needles),
        })
    ranked = sorted(per.items(), key=lambda kv: -kv[1])
    return {"query": p, "results": results,
            "sessions": [{"session_id": s, "name": (sessions.get(s) or {}).get("name") or "Untitled",
                          "hits": n} for s, n in ranked[:20]]}


# ── recaps / @-mentions ─────────────────────────────────────────────────────

def recap(owner: Optional[str], session_id: str, max_chars: int = 6000,
          query: Optional[str] = None, allowed: Optional[set] = None) -> Optional[Dict[str, Any]]:
    """Dense recap of another chat: its latest compaction summary if any, plus
    the most recent exchanges (or the passages matching `query`)."""
    sessions = owned_sessions(owner)
    if allowed is not None:
        sessions = {k: v for k, v in sessions.items() if k in allowed}
    sid = session_id if session_id in sessions else next(
        (i for i in sessions if i.startswith(session_id or "\0")), None)
    if not sid:
        return None
    with _db() as c:
        rows = c.execute("SELECT role, content, metadata, timestamp FROM chat_messages WHERE session_id=? "
                         "ORDER BY timestamp ASC", (sid,)).fetchall()
    msgs = [(r["role"], _text_of(r["content"]), r["metadata"] or "") for r in rows]
    summary = ""
    for role, txt, md in msgs:
        if role == "system" and ("compact" in md or txt.startswith("[Conversation summary")):
            summary = txt
    parts = []
    if summary:
        parts.append("Earlier summary: " + summary[:2000])
    if query:
        ql = query.lower()
        hits = [(r, t) for r, t, _ in msgs if ql in t.lower()]
        for role, txt in hits[-8:]:
            parts.append(f"[{role}] {_snippet(txt, [query], 600)}")
    budget = max_chars - sum(len(x) for x in parts)
    tail = []
    for role, txt, _ in reversed(msgs):
        if role not in ("user", "assistant"):
            continue
        t = txt.strip()
        if len(t) > 1200:
            t = t[:1200] + " …"
        line = f"[{role}] {t}"
        if budget - len(line) < 0:
            break
        budget -= len(line)
        tail.append(line)
    parts += list(reversed(tail))
    board = [b for b in _board(owner) if b.get("from_session") == sid][-5:]
    if board:
        parts.append("Shared findings from this chat: " + " | ".join(b["text"][:300] for b in board))
    return {"session_id": sid, "name": sessions[sid].get("name") or "Untitled",
            "messages": len(msgs), "recap": "\n".join(parts)}


_MENTION_RE = re.compile(r"@\[([^\]]{0,120})\]\(session:([A-Za-z0-9-]{6,64})\)|@session:([A-Za-z0-9-]{6,64})")


def mention_context(owner: Optional[str], text: str, session_id: Optional[str] = None) -> Optional[str]:
    allowed = peers(owner, session_id)
    ids = []
    for m in _MENTION_RE.finditer(text or ""):
        sid = m.group(2) or m.group(3)
        if sid and sid not in ids:
            ids.append(sid)
    if not ids:
        return None
    blocks, skipped = [], 0
    for sid in ids[:5]:
        r = recap(owner, sid, max_chars=5000 if len(ids) == 1 else 2500, allowed=allowed)
        if r:
            blocks.append(f"### @{r['name']} (chat {r['session_id'][:8]}, {r['messages']} messages)\n{r['recap']}")
        else:
            skipped += 1
    if skipped:
        blocks.append(f"({skipped} mentioned chat(s) are outside this chat's project, so their content "
                      "was not shared. Tell the user they can move the chats into the same project folder.)")
    if not blocks:
        return None
    return ("=== REFERENCED CHATS (the user @-mentioned these; use them as context) ===\n"
            + "\n\n".join(blocks))


# ── cross-session board ─────────────────────────────────────────────────────

def _board_path(owner) -> str:
    return os.path.join(_data_dir(), f"board_{_safe(owner or 'shared')}.json")


def _board(owner) -> List[Dict[str, Any]]:
    return _read_json(_board_path(owner), [])


def share(owner: Optional[str], from_session: Optional[str], text: str, *,
          to_session: Optional[str] = None, kind: str = "finding",
          title: Optional[str] = None) -> Dict[str, Any]:
    text = (text or "").strip()
    if not text:
        raise ValueError("empty finding")
    item = {"id": secrets.token_hex(6), "ts": time.time(), "kind": kind,
            "title": (title or text.split("\n")[0])[:120], "text": text[:4000],
            "from_session": from_session, "to_session": to_session, "delivered_to": []}
    with _lock:
        b = _board(owner)
        b.append(item)
        _write_json(_board_path(owner), b[-500:])
    return item


def board(owner: Optional[str], limit: int = 100) -> List[Dict[str, Any]]:
    return list(reversed(_board(owner)[-limit:]))


def inbox_context(owner: Optional[str], session_id: str) -> Optional[str]:
    """Findings addressed to this chat (delivered once) + the latest few
    broadcast findings from OTHER chats (titles), as one system block."""
    if not session_id:
        return None
    allowed = peers(owner, session_id)
    if allowed is not None and not allowed:
        return None                        # isolated chat: nothing flows in
    ok_from = (lambda sid: True) if allowed is None else (lambda sid: sid in allowed)
    with _lock:
        b = _board(owner)
        direct, changed = [], False
        for it in b:
            if (it.get("to_session") == session_id and session_id not in it.get("delivered_to", [])
                    and (not it.get("from_session") or ok_from(it.get("from_session")))):
                direct.append(it)
                it.setdefault("delivered_to", []).append(session_id)
                changed = True
        if changed:
            _write_json(_board_path(owner), b)
    recent = [it for it in b if not it.get("to_session") and it.get("from_session") != session_id
              and it.get("from_session") and ok_from(it.get("from_session"))
              and time.time() - it.get("ts", 0) < 7 * 86400][-6:]
    if not direct and not recent:
        return None
    names = owned_sessions(owner)
    lines = ["=== CROSS-SESSION BOARD ==="]
    for it in direct:
        src = (names.get(it.get("from_session")) or {}).get("name") or "another chat"
        lines.append(f"• Message for this chat from “{src}”: {it['text'][:1500]}")
    if recent:
        lines.append("Recent findings shared by other chats (use session_read / sessions_search for detail):")
        for it in recent:
            src = (names.get(it.get("from_session")) or {}).get("name") or "a chat"
            lines.append(f"  – [{it['kind']}] {it['title']} (from “{src}”)")
    return "\n".join(lines)


# ── rewind snapshots ────────────────────────────────────────────────────────

def _snap_dir(session_id: str) -> str:
    d = os.path.join(_data_dir(), "snapshots", _safe(session_id))
    os.makedirs(d, exist_ok=True)
    return d


def snapshot(session_id: str, reason: str) -> Optional[str]:
    """Save the chat's current history (all branches) before a destructive op."""
    try:
        with _db() as c:
            rows = c.execute("SELECT role, content, metadata, timestamp, branch_id FROM chat_messages "
                             "WHERE session_id=? ORDER BY timestamp ASC", (session_id,)).fetchall()
        if not rows:
            return None
        sid = f"{int(time.time() * 1000)}"
        _write_json(os.path.join(_snap_dir(session_id), sid + ".json"), {
            "id": sid, "ts": time.time(), "reason": reason, "count": len(rows),
            "messages": [dict(r) for r in rows],
        })
        snaps = sorted(os.listdir(_snap_dir(session_id)))
        for old in snaps[:-30]:
            try:
                os.remove(os.path.join(_snap_dir(session_id), old))
            except OSError:
                pass
        return sid
    except Exception as e:
        logger.warning("[hub] snapshot failed for %s: %s", session_id, e)
        return None


def list_snapshots(session_id: str) -> List[Dict[str, Any]]:
    out = []
    for name in sorted(os.listdir(_snap_dir(session_id)), reverse=True):
        d = _read_json(os.path.join(_snap_dir(session_id), name), None)
        if not d:
            continue
        msgs = d.get("messages") or []
        last_user = next((_text_of(m["content"])[:120] for m in reversed(msgs) if m.get("role") == "user"), "")
        out.append({"id": d["id"], "ts": d["ts"], "reason": d.get("reason"), "count": d.get("count"),
                    "last_user": last_user})
    return out


def restore(session_manager, session_id: str, snap_id: str) -> int:
    d = _read_json(os.path.join(_snap_dir(session_id), _safe(snap_id) + ".json"), None)
    if not d:
        raise KeyError("snapshot not found")
    snapshot(session_id, f"before restoring {snap_id}")
    from core.models import ChatMessage
    sess = session_manager.get_session(session_id)
    branch = getattr(sess, "active_branch", "root") or "root"
    msgs = []
    for m in d.get("messages") or []:
        if (m.get("branch_id") or "root") != branch:
            continue
        try:
            md = json.loads(m["metadata"]) if m.get("metadata") else None
        except ValueError:
            md = None
        content = m["content"]
        try:
            if isinstance(content, str) and content[:1] == "[":
                v = json.loads(content)
                if isinstance(v, list):
                    content = v
        except ValueError:
            pass
        if isinstance(md, dict):
            md.pop("_db_id", None)
        msgs.append(ChatMessage(m["role"], content, metadata=md))
    session_manager.replace_messages(session_id, msgs)
    try:
        sess.history = msgs
        sess.message_count = len(msgs)
    except Exception:
        pass
    return len(msgs)


# ── usage analytics ─────────────────────────────────────────────────────────

def _tool_bucket(name: str) -> str:
    n = (name or "").lower()
    if n.startswith("mcp__") or n.startswith("mcp_"):
        return "mcp"
    if "subagent" in n or n in ("spawn_agent", "delegate", "send_subagent_message"):
        return "subagent"
    if "skill" in n:
        return "skill"
    if n in ("web_search", "web_fetch", "deep_research", "docfind", "osint", "research"):
        return "research"
    if n.endswith("_document") or n in ("create_document", "document_append"):
        return "documents"
    return "builtin"


def usage(owner: Optional[str], days: int = 30) -> Dict[str, Any]:
    sessions = owned_sessions(owner)
    from src.constants import DATA_DIR
    led_dir = os.path.join(DATA_DIR, "cost_ledger")
    tot = {"usd": 0.0, "calls": 0, "in": 0, "out": 0, "unpriced_calls": 0}
    by_source, by_model, by_session, by_day = {}, {}, {}, {}
    since = time.time() - days * 86400
    try:
        files = os.listdir(led_dir)
    except OSError:
        files = []
    for fn in files:
        d = _read_json(os.path.join(led_dir, fn), None)
        if not isinstance(d, dict):
            continue
        sid = d.get("session_id")
        if owner and d.get("owner") not in (owner, None, "") and sid not in sessions:
            continue
        for e in d.get("entries") or []:
            if e.get("ts", 0) < since:
                continue
            usd = e.get("usd") or 0.0
            tot["usd"] += usd
            tot["calls"] += 1
            tot["in"] += e.get("in") or 0
            tot["out"] += e.get("out") or 0
            if e.get("usd") is None:
                tot["unpriced_calls"] += 1
            for key, bucket in ((e.get("source") or "chat", by_source), (e.get("model") or "?", by_model)):
                x = bucket.setdefault(key, {"usd": 0.0, "calls": 0, "in": 0, "out": 0})
                x["usd"] += usd; x["calls"] += 1; x["in"] += e.get("in") or 0; x["out"] += e.get("out") or 0
            if sid:
                x = by_session.setdefault(sid, {"usd": 0.0, "calls": 0, "in": 0, "out": 0,
                                                "name": (sessions.get(sid) or {}).get("name") or sid[:8]})
                x["usd"] += usd; x["calls"] += 1; x["in"] += e.get("in") or 0; x["out"] += e.get("out") or 0
            day = datetime.fromtimestamp(e.get("ts", 0), timezone.utc).strftime("%Y-%m-%d")
            x = by_day.setdefault(day, {"usd": 0.0, "calls": 0})
            x["usd"] += usd; x["calls"] += 1
    # Tool invocation frequency + output footprint from the transcripts.
    by_tool, by_bucket, by_mcp = {}, {}, {}
    ids = list(sessions)
    if ids:
        cutoff = datetime.fromtimestamp(since, timezone.utc).strftime("%Y-%m-%d")
        with _db() as c:
            rows = c.execute(
                "SELECT session_id, metadata FROM chat_messages WHERE role='assistant' AND metadata LIKE ? "
                "AND timestamp >= ? AND session_id IN (" + ",".join("?" * len(ids)) + ") ORDER BY timestamp DESC LIMIT 4000",
                ["%tool_events%", cutoff] + ids).fetchall()
        for r in rows:
            try:
                md = json.loads(r["metadata"])
            except (ValueError, TypeError):
                continue
            for ev in md.get("tool_events") or []:
                name = ev.get("tool") or "?"
                x = by_tool.setdefault(name, {"calls": 0, "errors": 0, "output_chars": 0, "bucket": _tool_bucket(name)})
                x["calls"] += 1
                if ev.get("exit_code") not in (0, None):
                    x["errors"] += 1
                x["output_chars"] += len(str(ev.get("output") or ""))
                b = by_bucket.setdefault(x["bucket"], {"calls": 0, "output_chars": 0, "approx_tokens": 0})
                b["calls"] += 1
                b["output_chars"] += len(str(ev.get("output") or ""))
                if x["bucket"] == "mcp":
                    server = name.split("__")[1] if name.count("__") >= 2 else name
                    m = by_mcp.setdefault(server, {"calls": 0, "output_chars": 0})
                    m["calls"] += 1
                    m["output_chars"] += len(str(ev.get("output") or ""))
    for b in by_bucket.values():
        b["approx_tokens"] = b["output_chars"] // 4
    for t in by_tool.values():
        t["approx_tokens"] = t["output_chars"] // 4
    # Context footprint per chat (tokens the chat history costs on every turn).
    footprint = []
    for sid, s in sessions.items():
        footprint.append({"session_id": sid, "name": s.get("name") or "Untitled",
                          "messages": s.get("message_count") or 0,
                          "input_tokens": s.get("total_input_tokens") or 0,
                          "output_tokens": s.get("total_output_tokens") or 0})
    footprint.sort(key=lambda x: -(x["input_tokens"] or 0))
    rnd = lambda d: {k: (round(v, 6) if isinstance(v, float) else v) for k, v in d.items()}
    return {
        "days": days, "totals": rnd(tot),
        "by_source": {k: rnd(v) for k, v in sorted(by_source.items(), key=lambda kv: -kv[1]["usd"])},
        "by_model": {k: rnd(v) for k, v in sorted(by_model.items(), key=lambda kv: -kv[1]["usd"])},
        "by_session": sorted(({"session_id": k, **rnd(v)} for k, v in by_session.items()),
                             key=lambda x: -x["usd"])[:25],
        "by_day": [{"day": k, **rnd(v)} for k, v in sorted(by_day.items())],
        "by_tool": dict(sorted(by_tool.items(), key=lambda kv: -kv[1]["calls"])),
        "by_bucket": by_bucket, "by_mcp_server": by_mcp,
        "context_footprint": footprint[:25],
    }


# ── live artifacts ──────────────────────────────────────────────────────────

def _live_path() -> str:
    return os.path.join(_data_dir(), "live.json")


def publish_live(owner: Optional[str], doc_id: str, *, allow_mcp: bool = False) -> Dict[str, Any]:
    with _lock:
        live = _read_json(_live_path(), {})
        for tok, it in live.items():
            if it.get("doc_id") == doc_id and it.get("owner") == owner:
                if allow_mcp and not it.get("allow_mcp"):
                    it["allow_mcp"] = True
                    _write_json(_live_path(), live)
                return {"token": tok, **it}
        tok = secrets.token_urlsafe(18)
        it = {"doc_id": doc_id, "owner": owner, "created": time.time(), "allow_mcp": bool(allow_mcp)}
        live[tok] = it
        _write_json(_live_path(), live)
        return {"token": tok, **it}


def unpublish_live(owner: Optional[str], token: str) -> bool:
    with _lock:
        live = _read_json(_live_path(), {})
        it = live.get(token)
        if not it or (owner and it.get("owner") != owner):
            return False
        live.pop(token)
        _write_json(_live_path(), live)
        return True


def live_list(owner: Optional[str]) -> List[Dict[str, Any]]:
    live = _read_json(_live_path(), {})
    out = []
    with _db() as c:
        for tok, it in live.items():
            if owner and it.get("owner") != owner:
                continue
            r = c.execute("SELECT title, updated_at, version_count FROM documents WHERE id=?", (it["doc_id"],)).fetchone()
            if not r:
                continue
            out.append({"token": tok, "url": f"/live/{tok}", "doc_id": it["doc_id"], "title": r["title"],
                        "updated_at": r["updated_at"], "version": r["version_count"],
                        "allow_mcp": it.get("allow_mcp", False)})
    out.sort(key=lambda x: str(x.get("updated_at") or ""), reverse=True)
    return out


def live_entry(token: str) -> Optional[Dict[str, Any]]:
    if not token or len(token) < 16:
        return None
    return _read_json(_live_path(), {}).get(token)


def live_get(token: str) -> Optional[Dict[str, Any]]:
    it = live_entry(token)
    if not it:
        return None
    with _db() as c:
        r = c.execute("SELECT id, title, language, current_content, version_count, updated_at FROM documents "
                      "WHERE id=?", (it["doc_id"],)).fetchone()
    if not r:
        return None
    return {"title": r["title"], "language": r["language"], "content": r["current_content"] or "",
            "version": r["version_count"], "updated_at": str(r["updated_at"] or ""),
            "allow_mcp": it.get("allow_mcp", False)}


_MCP_BLOCK_RE = re.compile(r"```mcp-live\s*\n(.*?)\n```", re.S)


def live_mcp_blocks(content: str) -> List[Dict[str, Any]]:
    out = []
    for m in _MCP_BLOCK_RE.finditer(content or ""):
        try:
            spec = json.loads(m.group(1))
        except ValueError:
            spec = {"error": "invalid JSON in mcp-live block"}
        out.append(spec if isinstance(spec, dict) else {"error": "mcp-live block must be an object"})
    return out


_MCP_CACHE: Dict[str, Any] = {}


async def live_mcp_call(token: str, index: int, *, as_owner: bool = False) -> Dict[str, Any]:
    """Run the index-th ```mcp-live``` block of a live artifact. Data blocks
    run for any reader when the artifact was published with allow_mcp; blocks
    marked "action": true only run for the signed-in owner."""
    it = live_entry(token)
    doc = live_get(token)
    if not it or not doc:
        return {"error": "not found"}
    blocks = live_mcp_blocks(doc["content"])
    if index < 0 or index >= len(blocks):
        return {"error": "no such block"}
    spec = blocks[index]
    if spec.get("error"):
        return spec
    if not (it.get("allow_mcp") or as_owner):
        return {"error": "live data is disabled for this artifact"}
    if spec.get("action") and not as_owner:
        return {"error": "actions can only be run by the owner (sign in)"}
    tool = str(spec.get("tool") or "")
    args = spec.get("args") or {}
    if not tool.startswith("mcp__"):
        tool = "mcp__" + tool.replace(".", "__", 1)
    ttl = float(spec.get("refresh") or 60)
    key = f"{token}:{index}:{json.dumps(args, sort_keys=True)}"
    hit = _MCP_CACHE.get(key)
    if hit and not spec.get("action") and time.time() - hit[0] < ttl:
        return hit[1]
    from src.agent_tools import get_mcp_manager
    mgr = get_mcp_manager()
    if not mgr:
        return {"error": "MCP is not configured"}
    try:
        res = await mgr.call_tool(tool, args)
    except Exception as e:
        res = {"error": str(e)}
    out = {"tool": tool, "result": res, "at": time.time()}
    _MCP_CACHE[key] = (time.time(), out)
    return out
