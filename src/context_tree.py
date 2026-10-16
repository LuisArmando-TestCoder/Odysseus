"""
context_tree.py — hierarchical, disk-first context index per chat.

Goal (per user's design):
  When a message floods the model ("too much information"), do NOT dump the whole
  thing into the context. Instead, build a SEARCHABLE TREE for that chat, where
  leaf nodes are chunks spilled to disk, and internal nodes are cheap category
  labels + summaries. At answer time the tree is traversed top-down and only the
  relevant leaves are injected back into the prompt.

Cost discipline (mirrors DeepSeek's own methodology):
  - Phase 1 retrieval is EXACT keyword search (grep over the spilled chunk files)
    → ~0 LLM cost, always works, no embeddings required.
  - Phase 2 is OPTIONAL semantic search (embeddings / vector RAG) for conceptual
    queries that don't share a literal term.
  - The tree's internal summaries are produced ONCE at build time by the cheap
    "utility" model (a single batched call per level, not per chunk).

Storage layout (disk-first, reuses Odyssey's data dir):
    DATA_DIR/context_trees/<owner>/<chat_id>/
        tree.json          # { name, summary, children, leaf_ids }
        chunks/<hash>.txt  # one leaf per file (grep target)
        meta.json          # version, chunk count, timestamps

Everything degrades gracefully:
  - No utility endpoint  -> flat tree (only leaves, no summaries; still grep-able).
  - No RAG/embeddings    -> phase 2 disabled; phase 1 still works.
  - No data dir yet      -> lazily created.
"""

import hashlib
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# How many chunks a node may hold before we recurse one more level.
DEFAULT_FANOUT = 16
# Chunking: target chars per leaf (sentence-boundary aware).
CHUNK_SIZE = 1200
CHUNK_OVERLAP = 150
# Cap on total leaves per chat to bound build cost / disk.
MAX_LEAVES = 2000
# Cap on characters we pass to the utility model for a single tree build.
_MAX_SUMMARIZE_INPUT_CHARS = 60000


def _data_dir() -> str:
    from src.constants import DATA_DIR
    return DATA_DIR


def _safe(part: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", part or "_")


def _tree_dir(owner: str, chat_id: str) -> str:
    d = os.path.join(_data_dir(), "context_trees", _safe(owner), _safe(chat_id))
    os.makedirs(d, exist_ok=True)
    return d


def _chunks_dir(owner: str, chat_id: str) -> str:
    d = os.path.join(_tree_dir(owner, chat_id), "chunks")
    os.makedirs(d, exist_ok=True)
    return d


def _hash_id(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Chunking (sentence-boundary aware; reused pattern from VectorRAG)
# ---------------------------------------------------------------------------
def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> List[str]:
    """Split text into sentence-aligned chunks. Deterministic and dependency-free."""
    if not text or not text.strip():
        return []
    if len(text) <= chunk_size:
        return [text.strip()]

    sentences = re.split(r"(?<=[.!?])\s+|\n{2,}", text)
    sentences = [s.strip() for s in sentences if s.strip()]

    chunks: List[str] = []
    current: List[str] = []
    current_len = 0
    for s in sentences:
        sl = len(s)
        if sl > chunk_size:
            if current:
                chunks.append(" ".join(current))
                current = []
                current_len = 0
            for start in range(0, sl, chunk_size - overlap):
                chunks.append(s[start:start + chunk_size])
            continue
        if current_len + sl + 1 > chunk_size and current:
            chunks.append(" ".join(current))
            # overlap tail
            tail: List[str] = []
            tl = 0
            for s2 in reversed(current):
                if tl + len(s2) > overlap:
                    break
                tail.insert(0, s2)
                tl += len(s2) + 1
            current = tail
            current_len = sum(len(x) for x in current) + max(0, len(current) - 1)
        current.append(s)
        current_len += sl + 1
    if current:
        chunks.append(" ".join(current))
    return [c for c in chunks if c.strip()] or [text.strip()]


# ---------------------------------------------------------------------------
# Utility-model helpers (reuse taxonomy's endpoint resolution + lenient JSON)
# ---------------------------------------------------------------------------
def _resolve_utility_endpoint() -> Optional[Tuple[str, str, Dict]]:
    from src.endpoint_resolver import resolve_endpoint
    try:
        url, model, headers = resolve_endpoint("utility")
        if url and model:
            return url, model, headers
    except Exception:
        pass
    try:
        from src.task_endpoint import resolve_task_endpoint
        url, model, headers = resolve_task_endpoint()
        if url and model:
            return url, model, headers
    except Exception:
        pass
    try:
        url, model, headers = resolve_endpoint("default")
        if url and model:
            return url, model, headers
    except Exception:
        pass
    return None


def _parse_json(raw: str) -> Any:
    text = (raw or "").strip()
    text = re.sub(r"<think(?:ing)?>[\s\S]*?</think(?:ing)?>", "", text, flags=re.I).strip()
    fence = re.search(r"```(?:json)?\s*\n?([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()
    for cand in (text, re.sub(r",(\s*[}\]])", r"\1", text)):
        try:
            return json.loads(cand)
        except (json.JSONDecodeError, TypeError):
            continue
    s, e = text.find("{"), text.rfind("}")
    if s >= 0 and e > s:
        try:
            return json.loads(text[s:e + 1])
        except (json.JSONDecodeError, TypeError):
            pass
    return None


_GROUP_PROMPT = """You are organizing chunks of a single chat into a small hierarchy so the
chat can retrieve them without loading everything.

Given a list of chunks (id -> first words), group them into 2-{fanout} categories
(named by 1-3 word topic). Put every chunk id in exactly one category. For each
category write a ONE-sentence summary in the SAME language as the chunks.

Output ONLY JSON:
{{"groups": {{"Category Name": {{"summary": "...", "ids": ["id1", "id2"]}}}}}}
"""


def _cluster_with_llm(
    owner: str, chunk_map: Dict[str, str], fanout: int
) -> Dict[str, Dict[str, Any]]:
    """One batched utility call: chunk ids -> {category: {summary, ids}}.
    Returns {} on any failure (flat tree fallback)."""
    resolved = _resolve_utility_endpoint()
    if not resolved:
        return {}
    url, model, headers = resolved

    entries = []
    for cid, text in chunk_map.items():
        entries.append(f"{cid}: {text[:80].replace(chr(10), ' ')}")
    payload = "\n".join(entries)[:_MAX_SUMMARIZE_INPUT_CHARS]

    try:
        from src.llm_core import llm_call
        raw = llm_call(
            url, model,
            [{"role": "user", "content": _GROUP_PROMPT.format(fanout=fanout) + "\n\n" + payload}],
            temperature=0.2, max_tokens=1600, headers=headers, timeout=60,
        )
    except Exception as e:
        logger.debug(f"[context_tree] cluster LLM call failed: {e}")
        return {}

    data = _parse_json(raw)
    groups = (data or {}).get("groups") or {}
    out: Dict[str, Dict[str, Any]] = {}
    for name, g in groups.items():
        if not isinstance(g, dict):
            continue
        ids = [str(i) for i in (g.get("ids") or []) if str(i) in chunk_map]
        summary = str(g.get("summary") or name).strip()
        if ids:
            out[str(name).strip()] = {"summary": summary, "ids": ids}
    return out


# ---------------------------------------------------------------------------
# Tree build
# ---------------------------------------------------------------------------
def build_tree(
    owner: str,
    chat_id: str,
    texts: List[str],
    *,
    rag_manager: Any = None,
    fanout: int = DEFAULT_FANOUT,
    source: str = "message",
) -> Dict[str, Any]:
    """Chunk `texts`, spill leaves to disk, build a category tree, optionally
    embed leaves into the vector RAG. Idempotent-ish: appends new chunks,
    replaces the tree.json. Returns the tree dict."""
    chunks_dir = _chunks_dir(owner, chat_id)

    # 1. Chunk + spill to disk (dedup by content hash).
    chunk_map: Dict[str, str] = {}
    for t in texts:
        if not t or not t.strip():
            continue
        for c in chunk_text(t):
            cid = _hash_id(c)
            fpath = os.path.join(chunks_dir, f"{cid}.txt")
            if not os.path.exists(fpath):
                try:
                    with open(fpath, "w", encoding="utf-8") as f:
                        f.write(c)
                except OSError as e:
                    logger.warning(f"[context_tree] spill failed for {cid}: {e}")
                    continue
            chunk_map[cid] = c
            if len(chunk_map) >= MAX_LEAVES:
                break
        if len(chunk_map) >= MAX_LEAVES:
            break

    # 2. Optional vector index of leaves (semantic retrieval phase).
    if rag_manager is not None and getattr(rag_manager, "healthy", False):
        # Index under this chat's per-session source for owner-scoped retrieval.
        src = f"chat_tree:{_safe(chat_id)}"
        try:
            rag_manager.index_source(
                src,
                "\n\n".join(chunk_map.values()),
                owner=owner,
                kind="chat_leaf",
                extra_meta={"chat_id": chat_id, "source": source},
            )
        except Exception as e:
            logger.debug(f"[context_tree] vector index failed: {e}")

    # 3. Build the tree (clustering via utility model; flat fallback).
    tree: Dict[str, Any] = {
        "name": None,
        "summary": "",
        "children": {},
        "leaf_ids": [],
    }
    if not chunk_map:
        save_tree(owner, chat_id, tree)
        return tree

    groups = _cluster_with_llm(owner, chunk_map, fanout)
    if not groups:
        # Flat tree: all chunks as direct leaves under root.
        tree["leaf_ids"] = sorted(chunk_map.keys())
    else:
        assigned: set = set()
        for name, g in groups.items():
            node: Dict[str, Any] = {
                "summary": g["summary"],
                "children": {},
                "leaf_ids": sorted(g["ids"]),
            }
            assigned.update(g["ids"])
            tree["children"][name] = node
        # Any unassigned chunk goes under a catch-all.
        leftover = sorted(set(chunk_map.keys()) - assigned)
        if leftover:
            tree["leaf_ids"] = leftover

    save_tree(owner, chat_id, tree)
    return tree


# ---------------------------------------------------------------------------
# Tree persistence
# ---------------------------------------------------------------------------
def save_tree(owner: str, chat_id: str, tree: Dict[str, Any]) -> None:
    try:
        from core.atomic_io import atomic_write_json
        atomic_write_json(os.path.join(_tree_dir(owner, chat_id), "tree.json"), tree)
    except Exception:
        try:
            with open(os.path.join(_tree_dir(owner, chat_id), "tree.json"), "w", encoding="utf-8") as f:
                json.dump(tree, f, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"[context_tree] save failed: {e}")


def get_tree(owner: str, chat_id: str) -> Dict[str, Any]:
    try:
        with open(os.path.join(_tree_dir(owner, chat_id), "tree.json"), "r", encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            return d
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return {"name": None, "summary": "", "children": {}, "leaf_ids": []}


def delete_tree(owner: str, chat_id: str) -> None:
    import shutil
    d = os.path.join(_data_dir(), "context_trees", _safe(owner), _safe(chat_id))
    shutil.rmtree(d, ignore_errors=True)


def read_leaf(owner: str, chat_id: str, leaf_id: str) -> str:
    """Read a spilled leaf's text. Returns '' if missing."""
    safe = re.sub(r"[^a-f0-9]", "", leaf_id or "")
    if not safe:
        return ""
    try:
        with open(os.path.join(_chunks_dir(owner, chat_id), f"{safe}.txt"), "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------
def _collect_leaf_ids(node: Dict[str, Any]) -> List[str]:
    ids = list(node.get("leaf_ids", []))
    for child in node.get("children", {}).values():
        ids.extend(_collect_leaf_ids(child))
    return ids


def _grep_terms(owner: str, chat_id: str, query: str, limit: int = 5) -> List[str]:
    """Phase 1 — exact keyword search over spilled chunk files. Cheap, no LLM."""
    terms = [t.strip().lower() for t in re.findall(r"[A-Za-z0-9À-ÿ]{3,}", query)]
    if not terms:
        return []
    chunks_dir = _chunks_dir(owner, chat_id)
    scored: Dict[str, int] = {}
    try:
        for fname in os.listdir(chunks_dir):
            if not fname.endswith(".txt"):
                continue
            cid = fname[:-4]
            try:
                with open(os.path.join(chunks_dir, fname), "r", encoding="utf-8") as f:
                    body = f.read()
            except Exception:
                continue
            low = body.lower()
            s = sum(1 for t in terms if t in low)
            if s > 0:
                scored[cid] = s
    except FileNotFoundError:
        return []
    ranked = sorted(scored, key=lambda c: scored[c], reverse=True)[:limit]
    return ranked


def _semantic_leaf_ids(
    owner: str, chat_id: str, query: str, rag_manager: Any, limit: int = 5
) -> List[str]:
    """Phase 2 (optional) — vector similarity over chat leaves via the RAG."""
    if rag_manager is None or not getattr(rag_manager, "healthy", False):
        return []
    src = f"chat_tree:{_safe(chat_id)}"
    try:
        results = rag_manager.search(query, k=limit, owner=owner)
    except Exception as e:
        logger.debug(f"[context_tree] semantic search failed: {e}")
        return []
    out: List[str] = []
    for r in results:
        meta = r.get("metadata") or {}
        # Only return leaves that belong to THIS chat's tree.
        if meta.get("source") == src or meta.get("chat_id") == chat_id:
            cid = _hash_id(r.get("document", ""))
            out.append(cid)
    return out


def retrieve(
    owner: str,
    chat_id: str,
    query: str,
    *,
    k: int = 6,
    rag_manager: Any = None,
    max_chars: int = 6000,
) -> List[Dict[str, str]]:
    """Retrieve the most relevant leaves for a query. Returns
    [{leaf_id, text, path}] under the char budget, without throwing away the
    full documents (they stay on disk)."""
    tree = get_tree(owner, chat_id)
    leaf_ids = _collect_leaf_ids(tree)
    if not leaf_ids:
        leaf_ids = _grep_terms(owner, chat_id, query, limit=k)

    # Phase 1: exact keyword.
    exact = _grep_terms(owner, chat_id, query, limit=k)

    # Phase 2 (optional): semantic additions.
    semantic = _semantic_leaf_ids(owner, chat_id, query, rag_manager, limit=k)

    # Merge: exact first (highest precision), then semantic, then remaining leaves.
    ordered = exact
    ordered += [i for i in semantic if i not in ordered]
    ordered += [i for i in leaf_ids if i not in ordered]

    results: List[Dict[str, str]] = []
    total = 0
    for cid in ordered:
        if total >= max_chars and results:
            break
        text = read_leaf(owner, chat_id, cid)
        if not text:
            continue
        text = text[:max_chars]  # bound a single leaf too
        results.append({"leaf_id": cid, "text": text, "path": _path_for(tree, cid)})
        total += len(text)
    return results


def _path_for(tree: Dict[str, Any], leaf_id: str, prefix: Tuple[str, ...] = ()) -> str:
    if leaf_id in tree.get("leaf_ids", []):
        return "/".join(prefix) or "root"
    for name, child in tree.get("children", {}).items():
        found = _path_for(child, leaf_id, prefix + (name,))
        if found:
            return found
    return "root"


def collect_all(owner: str, chat_id: str, max_chars: int = 12000) -> str:
    """Convenience: concatenate all leaves up to max_chars (for a digest)."""
    tree = get_tree(owner, chat_id)
    ids = _collect_leaf_ids(tree)
    parts, total = [], 0
    for cid in ids:
        txt = read_leaf(owner, chat_id, cid)
        if total + len(txt) > max_chars:
            break
        parts.append(txt)
        total += len(txt)
    return "\n\n".join(parts)