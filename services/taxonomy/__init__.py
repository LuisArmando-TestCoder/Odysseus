"""Self-organizing chat taxonomy.

A tree of tags (broad → specific) that models the chat list. Chats are
*tagged* with a breadcrumb (a JSON array of tags), never moved — so there are
no race conditions. The breadcrumb is the single source of truth; the folder
tree is a persisted cache rebuilt/reconciled from breadcrumbs.

Two teams of information-organizer agents (shared, no duplicated code):
  - Team A (reactive): place a newly-created chat into the tree immediately.
  - Team B (threshold): when a level exceeds ~20 chats, segment them
    recursively into hyper-specific tags.

Error handling: retry once, then stop and log (attempted action, failure,
output data) to the server log.
"""

from .service import (
    TAXONOMY_THRESHOLD,
    get_tree,
    save_tree,
    rebuild_tree,
    reconcile_tree,
    place_chat,
    segment_level,
    parse_breadcrumb,
    serialize_breadcrumb,
)

__all__ = [
    "TAXONOMY_THRESHOLD",
    "get_tree",
    "save_tree",
    "rebuild_tree",
    "reconcile_tree",
    "place_chat",
    "segment_level",
    "parse_breadcrumb",
    "serialize_breadcrumb",
]