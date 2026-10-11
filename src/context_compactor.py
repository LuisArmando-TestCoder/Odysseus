"""
context_compactor.py

Auto-compacts conversation history when approaching context window limits.
Summarizes older messages via the same LLM, preserving key context.
"""

import logging
import re
from typing import Any, Dict, List, Optional

from src.model_context import get_context_length, estimate_tokens
from src.llm_core import llm_call_async
from src.endpoint_resolver import resolve_endpoint
from core.models import ChatMessage

logger = logging.getLogger(__name__)

COMPACT_THRESHOLD = 0.85  # Trigger compaction at 85% of context window
SUMMARY_MAX_TOKENS = 1024
SMALL_CONTEXT_LIMIT = 8192  # Models with context <= this get aggressive trimming

# Cursor-style self-summarization prompt — produces structured, dense summaries
SELF_SUMMARY_SYSTEM_PROMPT = """You are summarizing a conversation to preserve context after compaction. Produce a structured summary that lets the conversation continue seamlessly.

Use this format:

## Conversation Summary
**Turns summarized:** {count}  |  **Compactions so far:** {n}

### User Goal
One sentence describing what the user is trying to accomplish.

### What Was Done
- Bullet points of completed actions, decisions made, and key outputs
- Include specific file paths, function names, variable names, URLs, and config values
- Note any errors encountered and how they were resolved

### Current State
What is the system/code/task state right now? What was the last thing discussed?

### Pending / Next Steps
- What remains to be done
- Any open questions or blockers

### Key Context
- Important constraints, preferences, or decisions that must not be forgotten
- Specific values: model names, ports, paths, credentials references, versions

Keep the summary under 1000 tokens. Be dense — every token should carry information. Do not include pleasantries or meta-commentary."""

# ── Hierarchical (recursive) compression ──────────────────────────────────────
# Batches of messages are summarized into dense "factual points" (level 1).
# Once enough level-N summaries accumulate, they are merged losslessly into a
# level-(N+1) "patch". Context keeps only the SPINE — the newest summary at
# every level plus the recent raw messages — so any-length conversation can be
# compressed without losing information.
SUMMARY_MERGE_SIZE = 3   # merge this many same-level summaries into one higher-level summary

FACTUAL_POINTS_PROMPT = """You are compressing a chunk of conversation into lossless factual points. Distill ONLY durable facts, decisions, and state — no pleasantries, no meta-commentary.

Output a flat list of dense bullet points. Each bullet must be self-contained and carry concrete information (names, ids, paths, URLs, numbers, decisions, errors and their fixes, open questions). Preserve every fact — do not drop, merge ambiguously, or editorialize. If two facts conflict, keep both and note the conflict."""

PATCH_PROMPT = """You are merging several lower-level conversation summaries into one higher-level summary, losslessly. Preserve every fact across all inputs — this is a compression step, not a rewrite.

Output a flat list of dense bullet points. Do not drop or blur any fact; if two facts conflict or duplicate, resolve duplicates but keep the conflict explicit. Keep concrete identifiers intact."""

_SUMMARY_LEVEL_RE = re.compile(r"^\[(?:Conversation summary|Summary L(\d+))\]", re.IGNORECASE)


def _summary_level(content: str) -> int:
    """Return the hierarchy level of a summary message (1 = batch, 2+ = patch)."""
    m = _SUMMARY_LEVEL_RE.match(content or "")
    if not m:
        return 0
    if m.group(1) is None:
        return 1  # legacy "[Conversation summary]" == level 1
    return int(m.group(1))


def _summary_body(content: str) -> str:
    """Strip the summary marker, returning the raw summary text."""
    m = _SUMMARY_LEVEL_RE.match(content or "")
    return content[m.end():].strip() if m else (content or "").strip()


async def _merge_levels(levels: Dict[int, List[str]], merge_size: int, summarizer) -> Dict[int, List[str]]:
    """Bottom-up recursive merge: while any level has >= merge_size summaries,
    merge the oldest `merge_size` into one summary at the next level up.

    `summarizer` is an async callable (list[str]) -> str that losslessly merges.
    Returns the merged levels dict (lists still in chronological order).
    """
    while True:
        merged_any = False
        for level in sorted(levels.keys()):
            while len(levels[level]) >= merge_size:
                chunk = levels[level][:merge_size]
                levels[level] = levels[level][merge_size:]
                try:
                    merged = await summarizer(chunk)
                    if merged and merged.strip():
                        levels.setdefault(level + 1, []).append(merged.strip())
                        merged_any = True
                        continue
                except Exception as e:
                    logger.error(f"hierarchical merge L{level} failed: {e}")
                # On failure, keep the chunk as-is at the next level (no data loss).
                levels.setdefault(level + 1, []).extend(chunk)
                merged_any = True
        if not merged_any:
            break
    return levels


def _summary_spine(levels: Dict[int, List[str]]) -> List[Dict[str, str]]:
    """Build the compressed-context spine: the NEWEST summary at every level,
    ordered highest level first so the most-compressed overview leads."""
    spine = []
    for level in sorted(levels.keys(), reverse=True):
        for body in levels[level][-1:]:  # newest at this level
            label = f"[Conversation summary]" if level == 1 else f"[Summary L{level}]"
            spine.append({"role": "system", "content": f"{label}\n{body}"})
    return spine


def _sanitize_tool_messages(msgs: List[Dict]) -> List[Dict]:
    """Drop orphaned `tool` messages and dangling assistant `tool_calls`.

    OpenAI's API requires every `role:"tool"` message to immediately
    follow an assistant message that carries `tool_calls` (or another
    tool message in the same batch). Front-trimming the history can cut
    the assistant `tool_calls` parent while keeping its tool responses,
    which triggers: "messages with role 'tool' must be a response to a
    preceding message with 'tool_calls'". This pass repairs that:
      - drops `tool` messages with no valid preceding tool_calls
      - drops assistant `tool_calls` messages whose tool responses were
        all trimmed away (some providers reject unanswered tool_calls)
    """
    # Pass 1: drop orphan tool messages.
    cleaned: List[Dict] = []
    in_batch = False  # are we right after an assistant tool_calls (or mid-batch)?
    for m in msgs:
        role = m.get("role")
        if role == "tool":
            if in_batch:
                cleaned.append(m)
            # else: orphan — drop
            continue
        if role == "assistant" and m.get("tool_calls"):
            in_batch = True
        else:
            in_batch = False
        cleaned.append(m)

    # Pass 2: drop assistant tool_calls messages that have NO following
    # tool response (dangling) — walk backwards so we know what follows.
    out: List[Dict] = []
    for i, m in enumerate(cleaned):
        if m.get("role") == "assistant" and m.get("tool_calls"):
            nxt = cleaned[i + 1] if i + 1 < len(cleaned) else None
            if not (nxt and nxt.get("role") == "tool"):
                # Dangling tool_calls — keep the message but strip the
                # tool_calls so it's a plain assistant turn (preserves any
                # text content the model produced alongside the calls).
                m = {k: v for k, v in m.items() if k != "tool_calls"}
                if not (m.get("content") or "").strip():
                    continue  # nothing left worth keeping
        out.append(m)
    return out


def _message_text_token_estimate(text: str) -> int:
    return int(len(text) * 0.3) + 4


def _truncate_text_to_token_budget(text: str, token_budget: int) -> str:
    """Trim a too-large current user message instead of dropping it entirely."""
    if token_budget <= 32:
        return "[Current user message omitted: it exceeded the model context window.]"

    # Match src.model_context.estimate_tokens' rough chars * 0.3 estimate.
    max_chars = max(200, int((token_budget - 16) / 0.3))
    if len(text) <= max_chars:
        return text

    notice = (
        "\n\n[Notice: the pasted message was too large for this model's context "
        "window, so Odysseus kept the beginning and end.]"
    )
    keep_chars = max(200, max_chars - len(notice))
    head_len = max(100, int(keep_chars * 0.7))
    tail_len = max(80, keep_chars - head_len)
    return text[:head_len].rstrip() + notice + "\n\n" + text[-tail_len:].lstrip()


def _truncate_message_to_token_budget(msg: Dict[str, Any], token_budget: int) -> Dict[str, Any]:
    """Return a copy of msg whose text content fits inside token_budget."""
    out = dict(msg)
    content = out.get("content", "")
    if isinstance(content, str):
        out["content"] = _truncate_text_to_token_budget(content, token_budget)
        return out

    if isinstance(content, list):
        remaining = token_budget
        new_content = []
        for item in content:
            if not isinstance(item, dict) or item.get("type") != "text":
                new_content.append(item)
                continue
            text = item.get("text", "")
            truncated = _truncate_text_to_token_budget(text, remaining)
            cloned = dict(item)
            cloned["text"] = truncated
            new_content.append(cloned)
            remaining -= _message_text_token_estimate(truncated)
        out["content"] = new_content
    return out


def trim_for_context(messages: List[Dict], context_length: int, reserve_tokens: int = 512) -> List[Dict]:
    """Trim system messages to fit within context_length.

    For small-context models, progressively strips:
    1. RAG/memory system messages (keep preset system prompt)
    2. Older conversation turns
    Reserves space for the response.

    The compaction summary spine ("[Conversation summary]" / "[Summary L{n}]")
    is the SAVED context of a previously-compacted conversation. It is never
    dropped — re-injecting it is what lets a "tired" conversation resume
    seamlessly when the user revisits it and types again.
    """
    budget = context_length - reserve_tokens
    used = estimate_tokens(messages)
    if used <= budget:
        return messages

    logger.info(f"Trimming messages: {used} tokens > {budget} budget (ctx={context_length})")

    # Separate summary spine, system messages, protected messages, and convo.
    system_msgs = []
    summary_msgs = []   # compaction summary spine — the saved context
    protected_msgs = []
    convo_msgs = []
    for msg in messages:
        if msg.get("_protected"):
            protected_msgs.append(msg)
        elif msg.get("role") == "system":
            if _summary_level(msg.get("content", "")) > 0:
                summary_msgs.append(msg)
            else:
                system_msgs.append(msg)
        else:
            convo_msgs.append(msg)

    # Protected + summary spine count toward budget but are never dropped.
    fixed_tokens = estimate_tokens(protected_msgs) + estimate_tokens(summary_msgs)
    budget -= fixed_tokens

    # Priority: keep first system msg (preset prompt), drop others (memory, RAG, memo)
    essential_system = system_msgs[:1] if system_msgs else []
    extra_system = system_msgs[1:]

    # The fixed prefix (preset + summary spine + protected) is always kept.
    fixed = essential_system + summary_msgs + protected_msgs

    # Try dropping extra system messages one by one (from the end)
    trimmed = fixed + convo_msgs
    if estimate_tokens(trimmed) <= budget:
        # Dropping extras was enough — try adding back some
        result = list(fixed)
        for msg in extra_system:
            candidate = result + [msg] + convo_msgs
            if estimate_tokens(candidate) <= budget:
                result.append(msg)
            else:
                break
        return _sanitize_tool_messages(result + convo_msgs)

    # Still too big — truncate the first system message (but keep more than 500 chars)
    if essential_system:
        sys_text = essential_system[0].get("content", "")
        if len(sys_text) > 2000:
            essential_system[0] = {"role": "system", "content": sys_text[:2000] + "\n[System prompt truncated for context limits]"}
            fixed = essential_system + summary_msgs + protected_msgs
            trimmed = fixed + convo_msgs
            if estimate_tokens(trimmed) <= budget:
                return _sanitize_tool_messages(fixed + convo_msgs)

    # Still too big — drop older conversation turns BUT always keep the current
    # user turn. If a pasted message alone exceeds the model context, truncate
    # that message with a visible notice instead of dropping it; otherwise the
    # model appears to "ignore" large pastes because it never receives them.
    # Hermes-style: recent context matters more than old context.
    PROTECT_RECENT = 10
    current_msg = convo_msgs[-1:] if convo_msgs else []
    prior_convo = convo_msgs[:-1] if convo_msgs else []
    if len(prior_convo) >= PROTECT_RECENT:
        old_msgs = prior_convo[:-(PROTECT_RECENT - 1)]
        recent_msgs = prior_convo[-(PROTECT_RECENT - 1):] + current_msg
        while old_msgs and estimate_tokens(fixed + old_msgs + recent_msgs) > budget:
            old_msgs.pop(0)
        convo_msgs = old_msgs + recent_msgs
    else:
        convo_msgs = prior_convo + current_msg
        while prior_convo and estimate_tokens(fixed + prior_convo + current_msg) > budget:
            prior_convo.pop(0)
        convo_msgs = prior_convo + current_msg

    # If the current message itself is too large, shrink only that message.
    if current_msg and estimate_tokens(fixed + convo_msgs) > budget:
        prefix = fixed + convo_msgs[:-1]
        available_for_current = max(64, budget - estimate_tokens(prefix))
        convo_msgs[-1] = _truncate_message_to_token_budget(convo_msgs[-1], available_for_current)

    result = _sanitize_tool_messages(fixed + convo_msgs)
    logger.info(f"Trimmed to {estimate_tokens(result)} tokens ({len(result)} messages)")
    return result


async def maybe_compact(
    session,
    endpoint_url: str,
    model: str,
    messages: List[Dict],
    headers: Optional[Dict] = None,
) -> tuple:
    """Check context usage and compact if above threshold.

    Returns (messages, context_length, was_compacted).
    """
    context_length = get_context_length(endpoint_url, model)
    used = estimate_tokens(messages)
    pct = (used / context_length) * 100 if context_length else 0

    if pct < COMPACT_THRESHOLD * 100:
        return messages, context_length, False

    logger.info(
        f"Context at {pct:.1f}% ({used}/{context_length} tokens) — compacting"
    )

    # Split into system preface, prior summaries (by level), and conversation.
    system_msgs: List[Dict] = []
    convo_msgs: List[Dict] = []
    summary_levels: Dict[int, List[str]] = {}
    for msg in messages:
        if msg.get("role") != "system":
            convo_msgs.append(msg)
            continue
        lvl = _summary_level(msg.get("content", ""))
        if lvl > 0:
            summary_levels.setdefault(lvl, []).append(_summary_body(msg.get("content", "")))
        else:
            system_msgs.append(msg)

    if len(convo_msgs) < 4:
        return messages, context_length, False

    # Split conversation: summarize older half, keep recent half
    split_point = len(convo_msgs) // 2
    older = convo_msgs[:split_point]
    recent = convo_msgs[split_point:]

    # Use utility model if configured, otherwise fall back to session model
    util_url, util_model, util_headers = resolve_endpoint("utility")
    compact_url = util_url or endpoint_url
    compact_model = util_model or model
    compact_headers = util_headers if util_url else headers

    async def _call(prompt: str, text: str) -> str:
        sm = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": text},
        ]
        return await llm_call_async(
            compact_url, compact_model, sm,
            temperature=0.2, max_tokens=SUMMARY_MAX_TOKENS,
            headers=compact_headers, timeout=30,
        )

    # Summarize the older chunk into a new level-1 batch of factual points.
    convo_text = "\n".join(
        f"{msg['role'].upper()}: {msg.get('content', '')[:2000]}"
        for msg in older
    )
    try:
        batch = await _call(FACTUAL_POINTS_PROMPT, convo_text)
    except Exception as e:
        logger.error(f"Compaction summary failed: {e}")
        return system_msgs + _summary_spine(summary_levels) + recent, context_length, False
    if not (batch or "").strip():
        return system_msgs + _summary_spine(summary_levels) + recent, context_length, False

    summary_levels.setdefault(1, []).append(batch.strip())

    # Recursively merge lower-level summaries into higher-level patches.
    async def _merger(texts: List[str]) -> str:
        return await _call(PATCH_PROMPT, "\n\n---\n\n".join(texts))
    summary_levels = await _merge_levels(summary_levels, SUMMARY_MERGE_SIZE, _merger)

    spine = _summary_spine(summary_levels)
    compacted = system_msgs + spine + recent

    # Persist the new hierarchy to the in-memory session history.
    _update_session_history(session, split_point, summary_levels, system_msg_count=len(system_msgs))

    new_used = estimate_tokens(compacted)
    logger.info(
        f"Compacted: {used} -> {new_used} tokens "
        f"({len(older)} messages summarized into {len(spine)} summary spine entries, {len(recent)} kept)"
    )

    return compacted, context_length, True


def _update_session_history(session, split_point: int, summary_levels: Dict[int, List[str]],
                            system_msg_count: int = 0):
    """Update the in-memory session history after hierarchical compaction.

    `split_point` is the index in `convo_msgs` (system-stripped). The
    in-memory `session.history` includes leading system messages, so the
    actual recent-history slice starts at `system_msg_count + split_point`.
    The compressed summary spine (newest summary at every level) is prepended,
    and the leading system messages are preserved.
    """
    if not session or not hasattr(session, "history"):
        return

    effective_split = system_msg_count + split_point
    if effective_split >= len(session.history):
        return

    system_prefix = list(session.history[:system_msg_count])
    recent_history = session.history[effective_split:]
    summary_msgs = [
        ChatMessage(
            role="system",
            content=s["content"],
            metadata={"compacted": True, "summarized_count": split_point},
        )
        for s in _summary_spine(summary_levels)
    ]
    new_history = system_prefix + summary_msgs + recent_history
    try:
        from core import models as _core_models
        manager = getattr(_core_models, "_session_manager", None)
    except Exception:
        manager = None
    if manager and getattr(session, "id", None):
        if manager.replace_messages(session.id, new_history):
            return
    session.history = new_history
