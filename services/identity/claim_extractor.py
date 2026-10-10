# services/identity/claim_extractor.py
"""Claim-extraction agent — parse user input into trusted claims and compare
against the claims already present.

The user is the most trusted source of identity reconnaissance. When they say
"my name is X" (in chat or voice), that claim is parsed here and folded into
the trusted-claims store. The extractor compares the new input against the
existing claims so a correction ("actually my name is Z") edits the prior
claim instead of creating a duplicate.

Two paths:
  1. ``extract_claims`` — deterministic regex/keyword extraction (fast, no
     model call). Handles the common first-person phrasings.
  2. ``extract_claims_llm`` — LLM-backed extraction for free-form input that
     the regex path misses. Returns the same operation shape.

Both return a list of operations:
    {"op": "add"|"edit"|"none", "key": ..., "value": ..., "category": ...}
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional

from src.trusted_claims import normalize_key

logger = logging.getLogger(__name__)

# Categories for each claim key.
_KEY_CATEGORY = {
    "name": "identity",
    "location": "identity",
    "occupation": "identity",
    "email": "contact",
    "phone": "contact",
    "birthday": "identity",
    "preference": "preference",
    "goal": "goal",
    "project": "project",
}

# Phrases that introduce a claim value. The value is everything after the
# phrase, trimmed of trailing punctuation and sentence fragments.
_CLAIM_PHRASES = {
    "name": [
        r"my name is\s+", r"i am called\s+", r"i'm called\s+", r"call me\s+",
        r"my name's\s+", r"i go by\s+",
    ],
    "location": [
        r"i live in\s+", r"i am from\s+", r"i'm from\s+", r"i live at\s+",
        r"i reside in\s+", r"i am based in\s+", r"i'm based in\s+",
    ],
    "occupation": [
        r"i work as\s+", r"i am a\s+", r"i'm a\s+", r"my job is\s+",
        r"i work at\s+", r"i work for\s+",
    ],
    "email": [
        r"my email is\s+", r"my email address is\s+", r"email me at\s+", r"reach me at\s+",
    ],
    "phone": [
        r"my phone number is\s+", r"my number is\s+", r"call me at\s+", r"text me at\s+",
    ],
    "birthday": [
        r"my birthday is\s+", r"i was born on\s+", r"my birthdate is\s+",
    ],
    "preference": [
        r"i prefer\s+", r"i like\s+", r"i love\s+", r"i hate\s+", r"i dislike\s+",
        r"i don't like\s+", r"i do not like\s+",
    ],
    "goal": [
        r"i want to\s+", r"i plan to\s+", r"i hope to\s+", r"my goal is\s+", r"i would like to\s+",
    ],
    "project": [
        r"i am working on\s+", r"i'm working on\s+", r"my project is\s+",
        r"i am building\s+", r"i'm building\s+",
    ],
}

# Stop at these when trimming a claim value (sentence boundaries / conjunctions
# that usually start a new clause).
_VALUE_STOP = re.compile(
    r"(\s+(?:and|but|because|so|also|however|though|although|while|whereas|,|\.|!|\?)\s+.*)$",
    re.IGNORECASE,
)


def _clean_value(raw: str) -> str:
    """Trim a raw claim value to a concise, self-contained string."""
    v = (raw or "").strip()
    if not v:
        return ""
    # Cut at a sentence boundary or conjunction that starts a new clause.
    v = _VALUE_STOP.sub("", v, count=1)
    v = v.strip(" .,!?;:\"'`“”‘’")
    # Drop a stray leading article ("I work as a software engineer" →
    # "software engineer", not "a software engineer").
    v = re.sub(r"^(?:a|an)\s+", "", v, flags=re.IGNORECASE)
    # Cap length to keep claims tight.
    if len(v) > 200:
        v = v[:200].rsplit(" ", 1)[0]
    return v


# Leading filler words that precede a claim without changing its meaning
# ("actually my name is X", "oh, I live in Y", "btw call me Z"). Stripped
# before key detection so corrections are still recognized.
_LEADING_FILLER = re.compile(
    r"^(?:actually|oh|well|so|btw|by the way|no|wait|hmm|um|uh|hey|ok|okay|right|look|listen|also|and|but)[,.\s]+",
    re.IGNORECASE,
)


def extract_claims(text: str) -> List[Dict[str, Any]]:
    """Deterministic claim extraction from a user message.

    Returns a list of operations:
        {"op": "add", "key": ..., "value": ..., "category": ...}
    """
    if not text or not text.strip():
        return []

    ops: List[Dict[str, Any]] = []
    seen_keys = set()

    # Split into sentences so a single message can carry multiple claims.
    sentences = re.split(r"(?<=[.!?])\s+|\n+", text.strip())
    for sentence in sentences:
        s = sentence.strip()
        if not s:
            continue
        # Strip leading filler ("actually", "oh", "btw") so corrections and
        # casual phrasings are still recognized as claims.
        s = _LEADING_FILLER.sub("", s, count=1).strip()
        if not s:
            continue
        key = normalize_key(s)
        if not key or key in seen_keys:
            continue
        for phrase in _CLAIM_PHRASES.get(key, []):
            m = re.search(phrase, s, re.IGNORECASE)
            if m:
                value = _clean_value(s[m.end():])
                if value:
                    seen_keys.add(key)
                    ops.append({
                        "op": "add",
                        "key": key,
                        "value": value,
                        "category": _KEY_CATEGORY.get(key, "fact"),
                    })
                break

    return ops


def apply_claim_operations(
    ops: List[Dict[str, Any]],
    manager,
    *,
    source: str = "chat",
    session_id: Optional[str] = None,
    owner: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Apply extracted operations to the trusted-claims store.

    ``manager`` is a TrustedClaimsManager. Returns the list of applied
    results ({"action", "claim"}).
    """
    results = []
    for op in ops or []:
        if op.get("op") not in ("add", "edit"):
            continue
        key = op.get("key")
        value = op.get("value")
        if not key or not value:
            continue
        res = manager.upsert(
            key=key,
            value=value,
            category=op.get("category", "identity"),
            source=source,
            session_id=session_id,
            owner=owner,
            confidence=1.0,
        )
        if res.get("action") in ("add", "edit"):
            results.append(res)
    return results


CLAIM_EXTRACT_PROMPT = """You extract identity claims the USER stated about themselves in the first person.
The user is the authoritative source of their own identity, so these claims are TRUSTED.

Given the user's message and the claims already on file, return a JSON array of operations.
Each operation is one of:
  {"op": "add", "key": "<key>", "value": "<value>", "category": "<category>"}
  {"op": "edit", "key": "<key>", "value": "<new value>", "category": "<category>"}
  {"op": "none"}

Keys: name, location, occupation, email, phone, birthday, preference, goal, project.
Categories: identity, contact, preference, goal, project, fact.

Rules:
- Only extract claims the USER stated about THEMSELVES (first person). Ignore
  claims about other people, hypotheticals, and questions.
- If the user corrects or contradicts an existing claim with the same key,
  emit "edit" with the new value. Otherwise emit "add".
- If the message contains no new claim, return [{"op": "none"}].
- Keep each value concise and self-contained (a short phrase, not a paragraph).
- Do NOT invent values. If the value is ambiguous or missing, skip it.

Return ONLY valid JSON, no markdown fences."""


async def extract_claims_llm(
    text: str,
    existing_claims: List[Dict[str, Any]],
    *,
    url: str,
    model: str,
    headers: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    """LLM-backed claim extraction for free-form input.

    Falls back to the deterministic extractor on any failure.
    """
    from src.llm_core import llm_call_async

    existing = [
        {"key": c.get("key"), "value": c.get("value")}
        for c in existing_claims
        if c.get("key") and c.get("value")
    ]
    user_payload = json.dumps({
        "message": text,
        "existing_claims": existing,
    }, ensure_ascii=False)

    try:
        raw = await llm_call_async(
            url, model,
            [
                {"role": "system", "content": CLAIM_EXTRACT_PROMPT},
                {"role": "user", "content": user_payload},
            ],
            temperature=0.1, max_tokens=600, headers=headers, timeout=30,
        )
    except Exception as e:
        logger.info(f"claim extractor LLM failed, using regex fallback: {e}")
        return extract_claims(text)

    m = re.search(r"\[.*\]", raw or "", re.DOTALL)
    if not m:
        return extract_claims(text)
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return extract_claims(text)

    if not isinstance(data, list):
        return extract_claims(text)

    ops = []
    for item in data:
        if not isinstance(item, dict):
            continue
        op = item.get("op")
        if op not in ("add", "edit"):
            continue
        key = item.get("key")
        value = (item.get("value") or "").strip()
        if not key or not value:
            continue
        ops.append({
            "op": op,
            "key": key,
            "value": value,
            "category": item.get("category") or _KEY_CATEGORY.get(key, "fact"),
        })
    return ops or extract_claims(text)