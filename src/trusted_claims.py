# src/trusted_claims.py
"""Trusted-claims store — the highest-trust tier of identity reconnaissance.

Where memory.json holds *untrusted* auto-extracted facts (wrapped in
``untrusted_context_message`` so the model treats them as data, not
instructions), this store holds claims the USER stated directly in the
first person ("my name is X", "I live in Y", "call me Z"). Those are the
most trusted source of identity reconnaissance: the user is the authority
on their own identity, so these claims are injected as *trusted* system
information the model may rely on.

A claim is a key/value pair with provenance:

    {
      "id": "<uuid>",
      "key": "name",            # normalized claim type
      "value": "Ada Lovelace",  # the asserted value
      "category": "identity",   # identity | preference | fact | contact | project | goal
      "source": "chat",         # chat | voice | manual
      "session_id": "<sid>",    # chat the claim was made in (may be None)
      "owner": "<user>",
      "timestamp": 1700000000,
      "confidence": 1.0,        # 0..1 — direct first-person claims are 1.0
      "verified": true,         # user-stated => verified by definition
    }

The claim-extraction agent (services/identity/claim_extractor.py) parses
user input, compares it against the claims already present, and returns
add/edit operations — so a later "actually my name is Z" edits the earlier
"my name is X" instead of piling up a duplicate.
"""

import json
import logging
import os
import time
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Canonical claim keys, with the phrases that map to them. Order matters:
# more specific phrases first so "call me" wins over a bare "name".
_CLAIM_KEY_PATTERNS: List[tuple] = [
    ("name", ["my name is", "i am called", "i'm called", "call me", "my name's", "i go by"]),
    ("location", ["i live in", "i am from", "i'm from", "i live at", "i reside in", "i am based in", "i'm based in"]),
    ("occupation", ["i work as", "i am a", "i'm a", "my job is", "i work at", "i work for"]),
    ("email", ["my email is", "my email address is", "email me at", "reach me at"]),
    ("phone", ["my phone number is", "my number is", "call me at", "text me at"]),
    ("birthday", ["my birthday is", "i was born on", "my birthdate is"]),
    ("preference", ["i prefer", "i like", "i love", "i hate", "i dislike", "i don't like", "i do not like"]),
    ("goal", ["i want to", "i plan to", "i hope to", "my goal is", "i would like to"]),
    ("project", ["i am working on", "i'm working on", "my project is", "i am building", "i'm building"]),
]

# Keys that are identity/PII — surfaced first and treated as authoritative.
_IDENTITY_KEYS = {"name", "location", "occupation", "email", "phone", "birthday"}


def _claims_file() -> str:
    from src.constants import DATA_DIR
    return os.path.join(DATA_DIR, "trusted_claims.json")


def normalize_key(text: str) -> Optional[str]:
    """Map a raw phrase to a canonical claim key, or None if it's not a claim."""
    t = (text or "").strip().lower()
    if not t:
        return None
    for key, phrases in _CLAIM_KEY_PATTERNS:
        for p in phrases:
            if t.startswith(p):
                return key
    return None


class TrustedClaimsManager:
    """Persistent, per-owner store of user-stated claims (trusted source)."""

    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, "trusted_claims.json")
        self._ensure_file()

    def _ensure_file(self):
        if not os.path.exists(self.path):
            try:
                with open(self.path, "w", encoding="utf-8") as f:
                    json.dump([], f, ensure_ascii=False, indent=2)
            except OSError as e:
                logger.warning("Could not create trusted_claims.json: %s", e)

    def load_all(self) -> List[Dict]:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
        except (json.JSONDecodeError, OSError) as e:
            logger.error("Error loading trusted_claims.json: %s", e)
        return []

    def load(self, owner: Optional[str] = None) -> List[Dict]:
        entries = self.load_all()
        if owner is None:
            return entries
        return [e for e in entries if e.get("owner") == owner]

    def load_for_session(
        self,
        owner: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> List[Dict]:
        """Load claims scoped to a specific chat (session_id).

        The trusted system context is strictly PER-CHAT, never global: a claim
        stated in chat A ("my name is Alfred") must NOT leak into chat B or any
        other session. There is no unscoped/global tier — a claim without a
        session_id is never surfaced. When no session_id is provided, nothing
        is returned.
        """
        if session_id is None:
            return []
        entries = self.load(owner=owner)
        return [e for e in entries if e.get("session_id") == session_id]

    def save(self, entries: List[Dict]):
        try:
            from core.atomic_io import atomic_write_json
            atomic_write_json(self.path, entries)
        except Exception:
            # Fallback to a plain atomic write if the helper is unavailable.
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(entries, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)

    def _new_claim(
        self,
        key: str,
        value: str,
        category: str = "identity",
        source: str = "chat",
        session_id: Optional[str] = None,
        owner: Optional[str] = None,
        confidence: float = 1.0,
    ) -> Dict:
        entry = {
            "id": str(uuid.uuid4()),
            "key": key,
            "value": value.strip(),
            "category": category,
            "source": source,
            "session_id": session_id,
            "owner": owner,
            "timestamp": int(time.time()),
            "confidence": float(confidence),
            "verified": True,
        }
        return entry

    def find_by_key(
        self,
        key: str,
        owner: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> Optional[Dict]:
        for e in self.load_for_session(owner=owner, session_id=session_id):
            if e.get("key") == key:
                return e
        return None

    def upsert(
        self,
        key: str,
        value: str,
        category: str = "identity",
        source: str = "chat",
        session_id: Optional[str] = None,
        owner: Optional[str] = None,
        confidence: float = 1.0,
    ) -> Dict:
        """Add a new claim, or edit the existing claim with the same key.

        Strictly scoped by (owner, session_id): a claim stated in chat A edits
        chat A's claim, never chat B's. There is no global tier — a claim
        without a session_id is NOT stored (returns "none"), so it can never
        leak across chats. Returns {"action": "add"|"edit"|"none", "claim": {...}}.
        """
        value = (value or "").strip()
        if not value:
            return {"action": "none", "claim": None}
        if session_id is None:
            # No session context — refuse to store an unscoped/global claim.
            return {"action": "none", "claim": None}

        entries = self.load_all()
        existing = None
        for e in entries:
            if (
                e.get("key") == key
                and (owner is None or e.get("owner") == owner)
                and (e.get("session_id") == session_id)
            ):
                existing = e
                break

        if existing:
            if existing.get("value", "").strip().lower() == value.lower():
                return {"action": "none", "claim": existing}
            existing["value"] = value
            existing["category"] = category
            existing["source"] = source
            existing["session_id"] = session_id
            existing["timestamp"] = int(time.time())
            existing["confidence"] = float(confidence)
            existing["verified"] = True
            self.save(entries)
            return {"action": "edit", "claim": existing}

        entry = self._new_claim(key, value, category, source, session_id, owner, confidence)
        entries.append(entry)
        self.save(entries)
        return {"action": "add", "claim": entry}

    def delete(self, claim_id: str, owner: Optional[str] = None) -> bool:
        entries = self.load_all()
        target = next((e for e in entries if e.get("id") == claim_id), None)
        if not target:
            return False
        if owner is not None and target.get("owner") != owner:
            return False
        self.save([e for e in entries if e.get("id") != claim_id])
        return True

    def prompt_block(
        self,
        owner: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> str:
        """Render the trusted claims as a system-prompt block.

        Scoped to a specific chat (session_id) — the trusted system context is
        PER-CHAT, not global. Identity/PII claims first, then general claims.
        This is the text the model receives as *trusted* system information.
        """
        claims = self.load_for_session(owner=owner, session_id=session_id)
        if not claims:
            return ""

        identity = [c for c in claims if c.get("key") in _IDENTITY_KEYS]
        general = [c for c in claims if c.get("key") not in _IDENTITY_KEYS]

        lines = [
            "## Trusted user information (stated directly by the user)",
            "The following facts were stated by the user in the first person and are "
            "authoritative. Rely on them for identity, personalization, and any request "
            "that depends on who the user is. If the user states something that "
            "contradicts an entry below, the user's latest statement wins.",
        ]
        if identity:
            lines.append("\nIdentity / PII:")
            for c in identity:
                lines.append(f"- {c['key']}: {c['value']}")
        if general:
            lines.append("\nOther claims:")
            for c in general:
                lines.append(f"- {c['key']}: {c['value']}")
        return "\n".join(lines)


_registry: Dict[str, TrustedClaimsManager] = {}


def get_trusted_claims_manager(data_dir: Optional[str] = None) -> TrustedClaimsManager:
    """Return a process-wide singleton for the given data dir (default DATA_DIR)."""
    if data_dir is None:
        from src.constants import DATA_DIR
        data_dir = DATA_DIR
    mgr = _registry.get(data_dir)
    if mgr is None:
        mgr = TrustedClaimsManager(data_dir)
        _registry[data_dir] = mgr
    return mgr