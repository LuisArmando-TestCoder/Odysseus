"""Taxonomy service — the self-organizing chat folder tree.

Design (as agreed with the user):

- **Breadcrumbs are the single source of truth.** Each chat stores a JSON
  array of tags from broad → specific (e.g. `["Research", "AI Safety",
  "Alignment"]`). Chats are *tagged*, never *moved* — so there are no race
  conditions and no data loss.

- **The tree is a persisted cache, not the truth.** It is saved on every
  change so the user never sees a cascade of reorderings on restart. On
  startup it is rendered instantly from cache, then reconciled in the
  background against the breadcrumbs — only real changes are applied.

- **Two teams of agents** (shared helpers, no duplicated code):
    * Team A (reactive): place a newly-created chat into the tree.
    * Team B (threshold): when a level exceeds `TAXONOMY_THRESHOLD` chats,
      segment them recursively into hyper-specific tags.

- **Error handling:** retry once; if it still fails, stop immediately and log
  to the server log: what was attempted, what failed, and the data that came
  out.

The tree is stored as a JSON file at `DATA_DIR/taxonomy.json`, keyed by owner.
"""

import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# A level with more than this many chats triggers Team B (segmentation).
TAXONOMY_THRESHOLD = 20

# How many times an agent action is retried before giving up.
_MAX_RETRIES = 1  # one retry, then stop


def _data_dir() -> str:
    from src.constants import DATA_DIR
    return DATA_DIR


def _tree_path(owner: str) -> str:
    d = os.path.join(_data_dir(), "taxonomy")
    os.makedirs(d, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", owner or "_")
    return os.path.join(d, f"{safe}.json")


# ── Breadcrumb helpers ──────────────────────────────────────────────────────

def parse_breadcrumb(raw: Optional[str]) -> List[str]:
    """Parse a stored breadcrumb (JSON array) into a list of tags."""
    if not raw:
        return []
    try:
        v = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if isinstance(v, list):
        return [str(t).strip() for t in v if str(t).strip()]
    return []


def serialize_breadcrumb(tags: List[str]) -> str:
    return json.dumps([t for t in tags if t], ensure_ascii=False)


# ── Tree cache ──────────────────────────────────────────────────────────────

def get_tree(owner: str) -> Dict[str, Any]:
    """Load the persisted tree cache for an owner. Returns an empty tree if
    none exists yet."""
    try:
        with open(_tree_path(owner), "r", encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict) and "children" in d:
            return d
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return {"name": None, "children": {}}


def save_tree(owner: str, tree: Dict[str, Any]) -> None:
    """Persist the tree cache. Best-effort — never raises."""
    try:
        from core.atomic_io import atomic_write_json
        atomic_write_json(_tree_path(owner), tree)
    except Exception as e:
        logger.warning(f"taxonomy: failed to save tree for {owner!r}: {e}")


# ── Tree building from breadcrumbs ──────────────────────────────────────────

def _insert_breadcrumb(node: Dict[str, Any], tags: List[str], session_id: str) -> None:
    """Insert a session id at the leaf of a breadcrumb path in the tree."""
    cur = node
    for tag in tags:
        cur = cur.setdefault("children", {}).setdefault(tag, {"name": tag, "children": {}})
    cur.setdefault("sessions", []).append(session_id)


def rebuild_tree(owner: str, breadcrumbs: Dict[str, List[str]]) -> Dict[str, Any]:
    """Rebuild the tree from a map of session_id → breadcrumb. Pure — returns
    the new tree without touching the cache."""
    tree: Dict[str, Any] = {"name": None, "children": {}}
    for sid, tags in breadcrumbs.items():
        if tags:
            _insert_breadcrumb(tree, tags, sid)
    return tree


def _count_sessions(node: Dict[str, Any]) -> int:
    """Count sessions in a subtree (including this node's own)."""
    n = len(node.get("sessions", []))
    for child in node.get("children", {}).values():
        n += _count_sessions(child)
    return n


def _collect_breadcrumbs(node: Dict[str, Any], prefix: List[str], out: Dict[str, List[str]]) -> None:
    """Walk the tree and collect every session's breadcrumb."""
    for sid in node.get("sessions", []):
        out[sid] = list(prefix)
    for name, child in node.get("children", {}).items():
        _collect_breadcrumbs(child, prefix + [name], out)


def reconcile_tree(owner: str, breadcrumbs: Dict[str, List[str]]) -> Dict[str, Any]:
    """Reconcile the cached tree against the breadcrumbs (source of truth).

    Returns the reconciled tree and persists it. Only real differences cause a
    change — so the user never sees a stream of reorderings on restart.
    """
    cached = get_tree(owner)
    cached_breadcrumbs: Dict[str, List[str]] = {}
    _collect_breadcrumbs(cached, [], cached_breadcrumbs)

    # A change is needed if any session's breadcrumb differs from the cache.
    changed = False
    if set(cached_breadcrumbs.keys()) != set(breadcrumbs.keys()):
        changed = True
    else:
        for sid, tags in breadcrumbs.items():
            if cached_breadcrumbs.get(sid) != tags:
                changed = True
                break

    if not changed:
        return cached

    tree = rebuild_tree(owner, breadcrumbs)
    save_tree(owner, tree)
    return tree


# ── Agent team (shared helpers) ─────────────────────────────────────────────

def _resolve_endpoint():
    """Pick a model endpoint for taxonomy agents (utility → task → default)."""
    from src.endpoint_resolver import resolve_endpoint
    url, model, headers = resolve_endpoint("utility")
    if url and model:
        return url, model, headers
    try:
        from src.task_endpoint import resolve_task_endpoint
        url, model, headers = resolve_task_endpoint()
        if url and model:
            return url, model, headers
    except Exception:
        pass
    url, model, headers = resolve_endpoint("default")
    if url and model:
        return url, model, headers
    return None, None, None


def _llm(owner: str, system: str, user: str, max_tokens: int = 1200) -> str:
    """Run a taxonomy agent LLM call. Raises on failure."""
    from src.llm_core import llm_call
    url, model, headers = _resolve_endpoint()
    if not url or not model:
        raise RuntimeError("No model endpoint available for taxonomy")
    return llm_call(
        url, model,
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.2, max_tokens=max_tokens, headers=headers, timeout=90,
    )


def _parse_json(raw: str) -> Dict[str, Any]:
    """Lenient JSON extraction (handles fences, thinking blocks, trailing commas)."""
    text = (raw or "").strip()
    text = re.sub(r"<think(?:ing)?>[\s\S]*?</think(?:ing)?>", "", text, flags=re.I).strip()
    fence = re.search(r"```(?:json)?\s*\n?([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()
    for cand in (text, re.sub(r",(\s*[}\]])", r"\1", text)):
        try:
            return json.loads(cand)
        except json.JSONDecodeError:
            continue
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass
    return {}


def _run_with_retry(owner: str, action: str, fn, *args) -> Any:
    """Run an agent action with one retry. On final failure, log the attempted
    action, the failure, and the data that came out, then re-raise."""
    last_err: Optional[Exception] = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            return fn(*args)
        except Exception as e:
            last_err = e
            logger.warning(
                f"taxonomy: {action} failed (attempt {attempt + 1}/{_MAX_RETRIES + 1}) "
                f"for owner={owner!r}: {e}"
            )
    # Final failure — log the full context and stop.
    logger.error(
        f"taxonomy: {action} FAILED after {_MAX_RETRIES + 1} attempts for owner={owner!r}. "
        f"Attempted: {action}. Failure: {last_err}. "
        f"Data: {json.dumps({'args': [str(a)[:500] for a in args]}, ensure_ascii=False)}"
    )
    raise last_err


# ── Tagger: propose tags for a chat ─────────────────────────────────────────

_TAGGER_PROMPT = """You are a chat organizer. Given a chat's title and a short excerpt of its
content, propose a breadcrumb of tags from broad (epistemological/ontological) to specific.
Rules:
- Start broad, narrow to hyper-specific (2-5 tags).
- Use short, reusable tag names (1-3 words each).
- Reuse existing tags when they fit (see the existing tree).
- Output ONLY JSON: {"tags": ["Broad", "Narrower", "Specific"]}
"""


def _tag_chat(owner: str, title: str, excerpt: str, existing_tags: List[str]) -> List[str]:
    """Propose a breadcrumb of tags for a chat."""
    raw = _llm(owner, _TAGGER_PROMPT,
               f"Title: {title}\nExcerpt: {excerpt[:2000]}\n\n"
               f"Existing tags (reuse when they fit): {', '.join(existing_tags) or '(none)'}")
    data = _parse_json(raw)
    tags = data.get("tags") or []
    return [str(t).strip() for t in tags if str(t).strip()][:6]


# ── Reviser: dedupe a proposed tag against the existing tree ────────────────

_REVISER_PROMPT = """You are a tag reviser. A new tag was proposed. If it is redundant with an
existing tag (same meaning, different wording), return the existing tag so the new chat reuses
it. Otherwise return the proposed tag unchanged.
Output ONLY JSON: {"tag": "<the tag to use>"}
"""


def _revise_tag(owner: str, proposed: str, existing_tags: List[str]) -> str:
    """Return the canonical tag to use (existing if redundant, else proposed)."""
    if not existing_tags:
        return proposed
    raw = _llm(owner, _REVISER_PROMPT,
               f"Proposed tag: {proposed}\nExisting tags: {', '.join(existing_tags)}")
    data = _parse_json(raw)
    tag = (data.get("tag") or proposed).strip()
    return tag or proposed


# ── Placer: walk the tree and insert a chat at the deepest match ────────────

def _all_tags(node: Dict[str, Any]) -> List[str]:
    """Flatten every tag in the tree (for the reviser's existing-tag list)."""
    out: List[str] = []
    for name, child in node.get("children", {}).items():
        out.append(name)
        out.extend(_all_tags(child))
    return out


def place_chat(owner: str, session_id: str, title: str, excerpt: str) -> List[str]:
    """Team A — place a chat into the tree. Returns the assigned breadcrumb.

    If no existing tag fits, a new category is proposed (via the reviser) and
    the chat is placed there. If the agent fails after retries, the chat is
    left untagged (orphaned) and the error is logged — it will be picked up by
    the next threshold trigger.
    """
    tree = get_tree(owner)
    existing = _all_tags(tree)

    def _do() -> List[str]:
        tags = _tag_chat(owner, title, excerpt, existing)
        if not tags:
            return []
        # Revise each tag against the existing tree (dedupe).
        revised: List[str] = []
        for t in tags:
            revised.append(_revise_tag(owner, t, existing + revised))
        return revised

    try:
        breadcrumb = _run_with_retry(owner, "place_chat", _do)
    except Exception:
        return []  # orphaned; logged by _run_with_retry

    if not breadcrumb:
        return []

    # Persist the breadcrumb on the session (source of truth).
    _set_session_tags(session_id, breadcrumb)

    # Update the tree cache and save.
    tree = get_tree(owner)
    _insert_breadcrumb(tree, breadcrumb, session_id)
    save_tree(owner, tree)
    return breadcrumb


# ── Segmenter: Team B — split an over-threshold level ───────────────────────

_SEGMENT_PROMPT = """You are a chat segmenter. A folder level has too many chats. Group its chats
into sub-categories (more specific tags) so each sub-category is smaller.
Rules:
- Use the chat titles to decide groupings.
- Create 2-6 sub-categories with short names.
- Assign every chat to exactly one sub-category.
- Output ONLY JSON: {"groups": {"SubCategory": ["chat_id1", "chat_id2"], ...}}
"""


def segment_level(owner: str, path: List[str], sessions: List[Dict[str, str]]) -> Dict[str, List[str]]:
    """Team B — segment an over-threshold level into sub-categories.

    `sessions` is a list of {id, title}. Returns {sub_tag: [session_ids]}.
    On failure (after retries), returns {} and logs — the level stays as-is.
    """
    if len(sessions) <= TAXONOMY_THRESHOLD:
        return {}

    def _do() -> Dict[str, List[str]]:
        names = "\n".join(f'  "{s["id"]}": "{s["title"]}"' for s in sessions)
        raw = _llm(owner, _SEGMENT_PROMPT,
                   f"Folder path: {' / '.join(path) or '(root)'}\nChats:\n{names}")
        data = _parse_json(raw)
        groups = data.get("groups") or {}
        # Map id prefixes back to full ids.
        id_map = {s["id"][:8]: s["id"] for s in sessions}
        result: Dict[str, List[str]] = {}
        for sub, ids in groups.items():
            resolved = []
            for sid in ids:
                full = id_map.get(str(sid)[:8]) or (sid if sid in {s["id"] for s in sessions} else None)
                if full:
                    resolved.append(full)
            if resolved:
                result[str(sub).strip()] = resolved
        return result

    try:
        return _run_with_retry(owner, "segment_level", _do)
    except Exception:
        return {}


# ── Session tag persistence ─────────────────────────────────────────────────

def _set_session_tags(session_id: str, tags: List[str]) -> None:
    """Persist a session's breadcrumb (source of truth). Best-effort."""
    try:
        from core.database import SessionLocal, Session as DbSession
        db = SessionLocal()
        try:
            row = db.query(DbSession).filter(DbSession.id == session_id).first()
            if row:
                row.tags = serialize_breadcrumb(tags)
                db.commit()
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"taxonomy: failed to persist tags for {session_id}: {e}")


def get_session_breadcrumbs(owner: str) -> Dict[str, List[str]]:
    """Read every session's breadcrumb for an owner (source of truth)."""
    try:
        from core.database import SessionLocal, Session as DbSession
        db = SessionLocal()
        try:
            rows = db.query(DbSession.id, DbSession.tags).filter(
                DbSession.archived == False, DbSession.owner == owner
            ).all()
            return {rid: parse_breadcrumb(rtags) for rid, rtags in rows if parse_breadcrumb(rtags)}
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"taxonomy: failed to read breadcrumbs for {owner!r}: {e}")
        return {}