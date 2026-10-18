"""Lightweight routing hints for chat requests that need tools.

These patterns are intentionally conservative. They only promote plain chat
to agent mode when the user asks the assistant to take an action, not when the
user asks how a feature works.
"""

from __future__ import annotations

import re
from typing import Iterable, Pattern


_ACTION_QUESTION = r"\b(?:can|could|would|will)\s+you\s+"
_PLEASE = r"^\s*(?:please\s+)?"

_CALENDAR_ACTION = r"(?:add|create|schedule|book|put|set\s+up|make)"
_CALENDAR_THING = r"(?:calendar|calendar\s+(?:entry|item)|event|meeting|appointment|entry|call)"

_PANEL = (
    r"(?:calendar|notes?|inbox|email|mail|documents?|docs|library|gallery|"
    r"settings|cookbook|sessions?|chats?|skills|memories|memory|brain)"
)

_TOOL_INTENT_PATTERNS: tuple[Pattern[str], ...] = tuple(
    re.compile(pattern, re.I)
    for pattern in (
        # Calendar/event creation. Covers "Can you add an entry to my
        # calendar?" and imperatives like "add lunch to my calendar".
        rf"{_ACTION_QUESTION}{_CALENDAR_ACTION}\b.{{0,120}}\b{_CALENDAR_THING}\b",
        rf"{_PLEASE}{_CALENDAR_ACTION}\b.{{0,120}}\b(?:to|on|in|into|for)\s+(?:my\s+|the\s+|this\s+)?calendar\b",
        rf"{_PLEASE}{_CALENDAR_ACTION}\s+(?:a\s+|an\s+)?(?:calendar\s+)?(?:event|meeting|appointment|entry|item|call)\b",
        r"\bput\s+.+\bon\s+(?:my\s+)?calendar\b",

        # Notes, todos, checklists, and reminders.
        r"\bremind\s+me\b",
        rf"{_ACTION_QUESTION}(?:add|create|make|take|jot|write\s+down|set)\b.{{0,120}}\b(?:note|todo|task|checklist|reminder)\b",
        rf"{_PLEASE}(?:add|create|make)\s+(?:a\s+|an\s+)?(?:todo|task|reminder|note|checklist)\b",
        rf"{_PLEASE}(?:take|jot|write\s+down)\s+(?:a\s+|an\s+)?note\b",
        rf"{_PLEASE}(?:add|jot|write\s+down)\b.{{0,120}}\b(?:to|in|into)\s+(?:my\s+|the\s+)?(?:todo(?:\s+list)?|task\s+list|notes?|checklist)\b",
        rf"{_PLEASE}set\s+(?:a\s+)?reminder\b",
        rf"{_ACTION_QUESTION}set\s+(?:a\s+)?reminder\b",

        # Email actions.
        rf"{_ACTION_QUESTION}(?:send|write|reply|email|message|archive|delete|mark)\b.{{0,120}}\b(?:emails?|mail|messages?|inbox|unread|read)\b",
        rf"{_PLEASE}(?:send|write|reply)\b.{{0,120}}\b(?:emails?|mail|messages?)\b",
        rf"{_PLEASE}(?:archive|delete|mark)\b.{{0,120}}\b(?:emails?|mail|messages?|inbox)\b",
        r"\b(?:send|write|reply)\s+(?:an?\s+)?(?:email|message|mail)\b",
        r"\bemail\s+\w+\b",
        r"\bcheck\s+(?:my\s+)?(?:email|inbox|mail)\b",
        r"\bunread\s+(?:email|mail)s?\b",

        # UI/control-plane actions that should open panels or flip toggles.
        rf"{_PLEASE}(?:open|show|bring\s+up)\s+(?:me\s+)?(?:my\s+|the\s+)?{_PANEL}\b",
        r"\b(?:disable|enable|turn\s+(?:on|off))\s+(?:the\s+)?(?:shell|search|web|browser|documents?|memory|skills|images?|calendar|email|mail|research|incognito)\b",

        # Deep research jobs, not quick conceptual mentions of research.
        rf"{_PLEASE}(?:research|deep\s+dive|look\s+into|investigate)\s+.+",
        rf"{_ACTION_QUESTION}(?:research|do\s+research|deep\s+dive|look\s+into|investigate)\s+.+",

        # Shell / remote-host intent.
        r"\bssh\s+(?:in)?to\b",
        r"\bssh\s+\w+",
        r"\b(run|execute)\s+.{1,40}\bon\s+\w+",
        r"\b(can|could|please|would)\s+you\s+(run|execute|exec)\b",
        r"\b(deploy|build|install|restart|reboot|kill|tail|grep|cat|ls|cd|cp|mv|rm)\b\s+\S+",
        r"\b(check|see)\s+(if|whether|what)\s+.{1,40}\b(running|process|service|port|file|exists?)\b",

        # Browser-side execution intent — "open X in my browser", "run JS",
        # "new tab", "window.open", "navigate to", "click the button", etc.
        # These must promote to agent mode so the run_javascript tool is
        # available; otherwise the model narrates "I can't run JavaScript".
        r"\b(?:open|launch|go\s+to|visit|navigate\s+to)\b.{0,80}\b(?:in\s+)?(?:my\s+|the\s+)?browser\b",
        r"\b(?:open|launch)\b.{0,60}\b(?:new\s+)?tab\b",
        r"\b(?:open|launch|visit|go\s+to)\b.{0,60}\b(?:youtube|google|gmail|github|twitter|x\.com|reddit|netflix|spotify|maps|drive|docs)\b",
        r"\brun(?:_|\s+)?javascript\b",
        r"\brun\s+js\b",
        r"\bwindow\.open\b",
        r"\b(?:in|on)\s+my\s+browser\b",
        r"\b(?:click|press|scroll|hover)\s+(?:on\s+)?(?:the\s+)?(?:button|link|tab|page)\b",
        r"\b(?:read|get|check)\s+(?:the\s+)?(?:current\s+)?(?:page|url|title|tab)\b",
    )
)


def message_needs_tools(text: str, patterns: Iterable[Pattern[str]] = _TOOL_INTENT_PATTERNS) -> bool:
    """Return True when a plain chat message should be promoted to agent mode."""
    if not text:
        return False
    return any(pattern.search(text) for pattern in patterns)


# ---------------------------------------------------------------------------
# JEV routing: quick answer vs deep research
# ---------------------------------------------------------------------------
# The user wants the chat and the deep-researcher to be "one in a way", with
# smart routing between them. JEV (src.decisions) is the project's calibrated
# decision-maker (used by the voice pipeline and the JS safety guard), so we
# ask it a typed `noul` question: "does this request need deep research, or is
# it answerable from context?" The regex heuristic above stays as a fast,
# offline fallback when JEV is unavailable.

# Narrowed fast-path: only EXPLICIT research asks route straight to deep
# research. Casual phrasing ("look into", "find out about", "study up on",
# "report on") no longer triggers — those go through the JEV gate below.
_RESEARCH_HINT_RE = re.compile(
    r"\b(?:deep\s+research|do\s+(?:some\s+)?research|research\s+(?:this|that|it|the|into|on)\b|"
    r"investigate\b|deep\s+dive\b|write\s+(?:me\s+)?(?:a\s+)?(?:research\s+)?report\b|"
    r"prepare\s+(?:a\s+)?(?:research\s+)?report\b|look\s+up\s+everything\b)",
    re.I,
)

# Pre-gate: a message must look research-shaped before we spend a JEV
# round-trip on it. Chatty/short messages skip JEV entirely.
_RESEARCH_WORTHY_RE = re.compile(
    r"\b(?:who|what|when|where|why|how|which|best|compare|vs\.?|review|analysis|"
    r"analyze|latest|trends|impact|pros|cons|history|market|report)\b", re.I,
)


def _looks_research_worthy(text: str) -> bool:
    words = text.split()
    if len(words) < 4:
        return False
    if re.match(r"^(?:thanks|thank you|ok\b|okay|yes|no|lol|hi|hey|hello)\b", text.strip(), re.I):
        return False
    return "?" in text or bool(_RESEARCH_WORTHY_RE.search(text))


def research_needed_jev(text: str, *, threshold: float = 0.75) -> bool:
    """Decide whether a user message warrants deep research (vs a quick answer).

    Uses JEV's calibrated `noul` answer when available; falls back to the
    regex heuristic when JEV is disabled / unreachable / no key. Never raises —
    routing must degrade gracefully, not break the chat.
    """
    text = (text or "").strip()
    if not text:
        return False

    # Fast path: an explicit research verb is a strong signal — skip the
    # network round-trip and route to research immediately.
    if _RESEARCH_HINT_RE.search(text):
        return True

    # Pre-gate: don't spend a JEV round-trip on chatty/short messages.
    if not _looks_research_worthy(text):
        return False

    try:
        from src.decisions import jev_decide_sync, DecisionsUnavailable
        answers = jev_decide_sync(
            state=text,
            questions={
                "research_needed": {
                    "type": "noul",
                    "instructions": (
                        "Did the user EXPLICITLY ask for research, a report, or an "
                        "investigation (e.g. 'do deep research on X', 'write me a "
                        "research report on Y', 'investigate Z')? Answer 'yes' only "
                        "when the user clearly requested a researched deliverable — "
                        "not merely a question whose answer would benefit from a "
                        "web search, and not chit-chat or simple factual questions."
                    ),
                    "criteria": {
                        "true": "User explicitly asked for research / a report / an investigation",
                        "false": "Not an explicit research request",
                    },
                }
            },
        )
        ans = answers.get("research_needed", {})
        value = ans.get("value")
        if value is None:
            return False
        return float(value) >= threshold
    except (DecisionsUnavailable, Exception):
        # JEV unavailable — fall back to the regex heuristic.
        return _RESEARCH_HINT_RE.search(text) is not None
