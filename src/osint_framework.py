"""
osint_framework.py — level-by-level navigation of the OSINT Framework tree.

The OSINT Framework (bundled_skills/general/self-osint-privacy-audit/arf.json)
is a large nested tree (folders -> folders -> ... -> url leaves). Loading and
showing the WHOLE tree to the model on every turn would be expensive and
overwhelming. This module is the "middleware" that keeps navigation cheap:

  - The tree is loaded ONCE and cached in-process.
  - ``get_level(path)`` returns ONLY the current level's view — the names and
    types of the children at that node (or the full leaf when a url is reached).
  - ``resolve_node(path)`` returns the full node at a path.

The agent navigates by calling the ``osint_framework`` tool with a ``path``
(list of names from the root). Each call is a cheap, non-blocking read of one
level; the agent descends recursively until it lands on a url leaf, at which
point the full tool details (url, description, input, output, opsec, etc.) are
returned and the agent can use them however it likes.
"""

import json
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# The bundled skill directory that ships the OSINT Framework tree.
_OSINT_TREE_REL = os.path.join(
    "bundled_skills", "general", "self-osint-privacy-audit", "arf.json"
)

# In-process cache: the tree is ~1.4 MB of JSON; parse it once, never again.
_tree_cache: Optional[Dict[str, Any]] = None


def _tree_path() -> str:
    from src.constants import BASE_DIR
    return os.path.join(BASE_DIR, _OSINT_TREE_REL)


def load_tree(force: bool = False) -> Dict[str, Any]:
    """Load (and cache) the OSINT Framework tree. Returns the root node."""
    global _tree_cache
    if _tree_cache is not None and not force:
        return _tree_cache
    path = _tree_path()
    with open(path, "r", encoding="utf-8") as f:
        _tree_cache = json.load(f)
    logger.info("Loaded OSINT Framework tree from %s", path)
    return _tree_cache


def _normalize_path(path) -> List[str]:
    """Accept a list of names, a single name, or a dotted/'>'-joined string."""
    if path is None:
        return []
    if isinstance(path, str):
        s = path.strip()
        if not s:
            return []
        # Support "A > B > C" or "A/B/C" or "A.B.C" shorthand.
        for sep in (" > ", ">", "/", "."):
            if sep in s:
                return [p.strip() for p in s.split(sep) if p.strip()]
        return [s]
    if isinstance(path, (list, tuple)):
        return [str(p).strip() for p in path if str(p).strip()]
    return []


def resolve_node(path) -> Optional[Dict[str, Any]]:
    """Return the node at ``path`` (list of names from the root), or None."""
    tree = load_tree()
    node = tree
    for name in _normalize_path(path):
        children = node.get("children") or []
        match = next((c for c in children if c.get("name") == name), None)
        if match is None:
            return None
        node = match
    return node


def get_level(path) -> Dict[str, Any]:
    """Return the current level's view for the node at ``path``.

    - folder node  -> {"current", "type": "folder", "level", "options": [...]}
      where each option is {"name", "type"} (type is "folder" or "url").
    - url leaf     -> the full node (url, description, input, output, opsec,
      pricing, etc.) so the agent can use it directly.

    This is the cheap, non-blocking "middleware" view: it never serializes the
    whole tree, only the one level the agent is currently looking at.
    """
    node = resolve_node(path)
    if node is None:
        return {
            "error": f"Path not found: {' > '.join(_normalize_path(path))}",
            "exit_code": 1,
        }

    if node.get("type") == "folder":
        children = node.get("children") or []
        options = [
            {"name": c.get("name"), "type": c.get("type")}
            for c in children
        ]
        return {
            "current": node.get("name"),
            "type": "folder",
            "level": len(_normalize_path(path)),
            "options": options,
            "exit_code": 0,
        }

    # Leaf (url) — return the full node so the agent can use it.
    return {**node, "exit_code": 0}


def format_level(path) -> str:
    """Render a level view as a compact, model-friendly text block."""
    view = get_level(path)
    if view.get("error"):
        return view["error"]

    if view.get("type") == "folder":
        lines = [
            f"OSINT Framework — current level: {view['current']} (depth {view['level']})",
            "",
            "Choose one option and descend by appending its name to the path:",
        ]
        for i, opt in enumerate(view.get("options") or [], 1):
            kind = "📁" if opt.get("type") == "folder" else "🔗"
            lines.append(f"  {i}. {kind} {opt['name']}")
        if not view.get("options"):
            lines.append("  (no children)")
        return "\n".join(lines)

    # Leaf
    lines = [f"OSINT Framework — tool: {view.get('name')}"]
    if view.get("url"):
        lines.append(f"URL: {view['url']}")
    for key in ("description", "bestFor", "input", "output", "pricing",
                "opsec", "opsecNote", "status"):
        if view.get(key):
            lines.append(f"{key}: {view[key]}")
    return "\n".join(lines)