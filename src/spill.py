# src/spill.py
"""SpillStore — oversized tool output goes to disk, never to the model whole.

Layer 0 / T1.3 — mirror of DSH's `packages/spill` (SpillStore): the tool
result stays complete on disk under an owner-scoped path; the model receives
a prefix + locator + retrieval hint. A spill failure NEVER turns a successful
tool call into an error — callers degrade back to plain truncation.
"""

import logging
import os
import time
from dataclasses import dataclass
from typing import Dict, Optional

from core.atomic_io import atomic_write_json

logger = logging.getLogger(__name__)

SPILL_PREFIX_CHARS = 2000


@dataclass
class SpillRef:
    locator: str
    bytes: int
    retrieval_hint: str

    def as_dict(self) -> Dict[str, object]:
        return {"locator": self.locator, "bytes": self.bytes, "retrieval_hint": self.retrieval_hint}


class SpillStore:
    """Session-scoped spill files under data/spills/<owner>/<session>/."""

    def _root(self, owner: Optional[str], session_id: Optional[str]) -> str:
        from src.constants import DATA_DIR
        safe_owner = _safe(owner or "shared")
        safe_sid = _safe(session_id or "unknown")
        root = os.path.join(DATA_DIR, "spills", safe_owner, safe_sid)
        os.makedirs(root, exist_ok=True)
        return root

    def save_text(self, *, owner: Optional[str], session_id: Optional[str],
                  tool: str, text: str) -> Optional[SpillRef]:
        """Persist `text` verbatim; returns the locator ref, or None on any
        storage failure (the caller decides how to degrade)."""
        try:
            root = self._root(owner, session_id)
            ts = int(time.time() * 1000)
            name = f"{ts}_{_safe(tool)}.txt"
            path = os.path.join(root, name)
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            nbytes = os.path.getsize(path)
            ref = SpillRef(
                locator=path,
                bytes=nbytes,
                retrieval_hint=f"read_file '{path}' (use offset/limit) or docfind --file '{path}' --term <keyword>",
            )
            logger.info("[spill] saved %d bytes for %s → %s", nbytes, tool, path)
            return ref
        except Exception as e:
            logger.warning("[spill] save failed for %s: %s", tool, e)
            return None


def _safe(name: str) -> str:
    import re
    return re.sub(r"[^A-Za-z0-9._-]", "_", str(name or "x"))


# Process-wide singleton (mirrors the app's other singletons).
_store: Optional[SpillStore] = None


def get_spill_store() -> SpillStore:
    global _store
    if _store is None:
        _store = SpillStore()
    return _store


def spill_or_truncate(text: str, *, tool: str, session_id: Optional[str] = None,
                      owner: Optional[str] = None, limit: int = 10_000,
                      prefix_chars: int = SPILL_PREFIX_CHARS) -> str:
    """T1.6 policy: oversized text is spilled to disk with a locator; the model
    gets a prefix + locator + retrieval hint. On spill failure, degrade to the
    old plain truncation (a spill failure must never fail the tool call)."""
    if not isinstance(text, str) or len(text) <= limit:
        return text
    ref = get_spill_store().save_text(
        owner=owner, session_id=session_id, tool=tool, text=text,
    )
    if ref is None:
        return text[:limit] + f"\n... (truncated, {len(text)} chars total)"
    return (
        text[:prefix_chars]
        + f"\n\n[FULL OUTPUT SPILLED TO: {ref.locator} — {len(text)} chars]\n"
        + f"(Use read_file with offset/limit or docfind to inspect it. {ref.retrieval_hint})"
    )
