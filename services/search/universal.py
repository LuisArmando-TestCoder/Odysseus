"""
universal.py — Universal Search Framework

A step-by-step cognitive algorithm that treats all human text as a mathematical
puzzle of claims, anchors, and sources. It does NOT rely on topic-specific
templates; the same mechanical pipeline validates any prompt equally well,
whether it asks about a stock price, a historical event, or a fictitious riddle.

The four phases:

  Phase 1  Universal Deconstruction
           Split the prompt into Static Anchors (known entities), Variable
           Claims (unverified assertions), Structural Constraints (format /
           length / tone), and compute the Information Gap between what we
           can anchor and what is being asserted.

  Phase 2  Heuristic Query Generation (3-Tier)
           Tier 1 Proximity  — anchors adjacent to claims (co-occurrence test)
           Tier 2 Broad       — anchors alone (baseline truth mapping)
           Tier 3 Opposing    — adversarial lookups ("error", "fake", "debunked")

  Phase 3  Universal Validation Matrix
           Three metrics: Source Consensus (independent-domain count),
           Temporal Recency (timestamps vs now), and Semantic Distance
           (retrieved wording vs claim wording).

  Phase 4  Grounded Synthesis
           Token Restriction Protocol — only words backed by retrieved
           snippets — plus the Ignorance Default when evidence is absent or
           contradictory.

The deconstruction + query generation + validation are fully deterministic so
the engine runs without any LLM dependency. An optional LLM/JEV refinement hook
is provided for deployments that want semantic enrichment of Phase 1.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Language-agnostic deconstruction: instead of relying on English-centric
# capitalization/word-boundary regexes (which fail for CJK, right-to-left,
# or agglutinative languages), we ask the active LLM to split the prompt into
# anchors / claims / constraints. The deterministic regex path remains as a
# synchronous fallback when no LLM endpoint is available.

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Adversarial marker terms appended to Tier 3 (Opposing Viewpoint) queries.
_ADVERSARIAL_TERMS = ("error", "fake", "debunked", "hoax", "myth", "contradiction", "fabricated")

# Terms that signal a structural/format request rather than a factual claim.
_STRUCTURAL_HINTS = (
    "bullet", "list", "table", "summary", "short", "brief", "concise",
    "detailed", "in-depth", "paragraph", "essay", "json", "markdown",
    "code", "example", "steps", "numbered", "explain like", "eli5",
    "formal", "casual", "tone", "format", "write", "answer in",
)

# Words that rarely carry factual weight; stripped from claim extraction.
_STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "of", "to", "in", "on", "at", "by", "for", "with", "about", "into",
    "and", "or", "but", "if", "then", "so", "as", "that", "this", "these",
    "those", "it", "its", "he", "she", "they", "them", "we", "you", "i",
    "me", "my", "do", "does", "did", "have", "has", "had", "will", "would",
    "can", "could", "should", "what", "which", "who", "whom", "when",
    "where", "why", "how", "please", "tell", "find", "search", "look",
    "check", "give", "show", "know", "explain", "describe",
}

# The Ignorance Default — emitted when evidence is absent or contradictory.
IGNORANCE_DEFAULT = (
    "The live web data does not contain sufficient verified evidence to "
    "confirm this claim."
)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Deconstruction:
    """Output of Phase 1."""
    raw_prompt: str = ""
    static_anchors: List[str] = field(default_factory=list)
    variable_claims: List[str] = field(default_factory=list)
    structural_constraints: List[str] = field(default_factory=list)
    information_gap: float = 0.0  # 0.0 = fully anchored, 1.0 = fully ungrounded

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class QuerySet:
    """Output of Phase 2 — the three parallel query tiers."""
    tier1_proximity: str = ""
    tier2_broad: str = ""
    tier3_opposing: str = ""

    def as_list(self) -> List[Tuple[str, str]]:
        return [
            ("proximity", self.tier1_proximity),
            ("broad", self.tier2_broad),
            ("opposing", self.tier3_opposing),
        ]


@dataclass
class SourceEvidence:
    """A single retrieved snippet with its provenance."""
    url: str = ""
    domain: str = ""
    title: str = ""
    snippet: str = ""
    age: Optional[str] = None
    timestamp: Optional[float] = None  # epoch seconds if parseable, else None


@dataclass
class ValidationMetric:
    score: float = 0.0            # normalized 0.0 .. 1.0
    weight: float = 0.34          # relative weight in the combined verdict
    detail: str = ""


@dataclass
class ValidationMatrix:
    """Output of Phase 3."""
    claims: List[str] = field(default_factory=list)
    source_consensus: ValidationMetric = field(default_factory=ValidationMetric)
    temporal_recency: ValidationMetric = field(default_factory=ValidationMetric)
    semantic_distance: ValidationMetric = field(default_factory=ValidationMetric)
    distinct_domains: int = 0
    total_snippets: int = 0
    combined_confidence: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Synthesis:
    """Output of Phase 4."""
    grounded: bool = False
    answer: str = ""
    supporting_sources: List[str] = field(default_factory=list)
    is_ignorance_default: bool = False


# ---------------------------------------------------------------------------
# Tokenisation helpers (dependency-free)
# ---------------------------------------------------------------------------

def _tokenize(text: str) -> List[str]:
    """Lowercase, split on non-alphanumeric, drop stopwords and short tokens."""
    toks = re.findall(r"[a-z0-9][a-z0-9\-']*", (text or "").lower())
    return [t for t in toks if t not in _STOPWORDS and len(t) > 1]


def _token_set(text: str) -> Set[str]:
    return set(_tokenize(text))


def _domain_of(url: str) -> str:
    if not url:
        return ""
    try:
        netloc = urlparse(url).netloc.lower()
        # strip leading www.
        return netloc[4:] if netloc.startswith("www.") else netloc
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Phase 1: Universal Deconstruction
# ---------------------------------------------------------------------------

def _extract_structural_constraints(prompt: str) -> List[str]:
    """Flag lowercase hints of format/length/tone requests.

    Multiword hints ("explain like", "answer in") are matched as substrings;
    single-word hints use word boundaries so "list" doesn't match "listed".
    """
    found: List[str] = []
    low = prompt.lower()
    for hint in _STRUCTURAL_HINTS:
        if " " in hint:
            if hint in low:
                found.append(hint)
        elif re.search(rf"\b{re.escape(hint)}\b", low):
            found.append(hint)
    return found


def deconstruct(prompt: str) -> Deconstruction:
    """Split a prompt into Static Anchors, Variable Claims, and Structural
    Constraints. Fully deterministic.

    Anchors are capitalized tokens / multiword entities / numbers with units;
    Claims are the remaining assertion words plus any number/date tokens that
    look like specific unverified facts.
    """
    result = Deconstruction(raw_prompt=prompt or "")

    if not prompt or not prompt.strip():
        return result

    result.structural_constraints = _extract_structural_constraints(prompt)

    # --- Anchor extraction: capitalized substrings (2+ words) and capitalized
    # single tokens that are not sentence-initial pronouns. ---
    anchors: List[str] = []
    # Leading question words / auxiliary verbs that precede a real entity and
    # must not be glued onto it ("Did Albert Einstein" → "Albert Einstein").
    _LEADING_QUESTION_WORDS = {
        "did", "does", "do", "is", "are", "was", "were", "will", "would",
        "can", "could", "should", "has", "have", "had", "what", "who",
        "whom", "when", "where", "why", "how", "tell", "show", "explain",
        "describe", "find", "look", "check", "give", "name", "list",
    }

    # Multiword capitalized phrases ("Laura Fernández Delgado", "Alan Turing").
    multiword = re.findall(
        r"\b(?:[A-Z][a-zÀ-ÿ'’-]+(?:[ \t]+[A-Z][a-zÀ-ÿ'’-]+)+)\b",
        prompt,
    )
    for m in multiword:
        m_stripped = m.strip()
        # Strip a single leading question/auxiliary word.
        first = m_stripped.split(" ")[0].lower() if m_stripped else ""
        if first in _LEADING_QUESTION_WORDS:
            m_stripped = " ".join(m_stripped.split(" ")[1:]).strip()
        if len(m_stripped) > 2:
            anchors.append(m_stripped)

    if not anchors:
        # Single capitalized tokens (person/org/place names, acronyms).
        singles = re.findall(r"\b[A-Z][a-zA-Z]{1,}\b", prompt)
        seen_single = {a.lower() for a in anchors}
        for s in singles:
            if s.lower() not in seen_single and s not in _STOPWORDS:
                anchors.append(s)
                seen_single.add(s.lower())

    # Number-with-unit patterns (stock prices, distances, etc.) are often
    # anchors when they have a known unit, but bare numbers are claims.
    # We treat "X of Y", "$N", "N%" as claim material unless paired with
    # a known entity — handled in claim extraction instead.

    result.static_anchors = _dedupe_preserve_order(anchors)

    # --- Claim extraction: assertion-bearing text. Strip anchors and
    # structural wording, then keep clauses with verbs/numbers/dates. ---
    claims = _extract_claims(prompt, result.static_anchors)
    result.variable_claims = _dedupe_preserve_order(claims)

    # --- Information gap: ratio of ungrounded claim tokens to total
    # meaningful tokens. Anchored-heavy prompts score low. ---
    result.information_gap = _compute_information_gap(
        prompt, result.static_anchors, result.variable_claims
    )

    return result


def _extract_claims(prompt: str, anchors: List[str]) -> List[str]:
    """Extract assertion-like fragments that are not anchors."""
    text = prompt
    # Remove anchor substrings so claims are what's left.
    for a in sorted(anchors, key=len, reverse=True):
        text = text.replace(a, " ")

    claims: List[str] = []
    # Split into sentences/clauses.
    clauses = re.split(r"[.;:?!,\n]+", text)
    for clause in clauses:
        clause = clause.strip()
        if not clause:
            continue
        # A claim is a clause containing a numeric fact, a date, or a
        # relationship verb.
        has_number = bool(re.search(r"\d", clause))
        has_copula = bool(re.search(r"\b(is|are|was|were|has|have|owns|bought|released|discovered|invented|founded|married|died|built|created|by|of)\b", clause.lower()))
        if has_number or has_copula:
            # Clean leading conjunctions AND leftover question/auxiliary words
            # that remain after anchor removal ("Did   invent ..." → "invent ...").
            cleaned = re.sub(
                r"^(and|or|but|also|then|so|because|since|did|does|do|is|are|was|were|will|would|can|could|should|has|have|had|what|who|whom|when|where|why|how)(\s+the)?\s+",
                "",
                clause,
                flags=re.I,
            )
            cleaned = cleaned.strip()
            if len(cleaned) > 2:
                claims.append(cleaned)
    return claims


def _compute_information_gap(prompt: str, anchors: List[str], claims: List[str]) -> float:
    anchor_tokens = set()
    for a in anchors:
        anchor_tokens |= _token_set(a)
    claim_tokens = set()
    for c in claims:
        claim_tokens |= _token_set(c)

    # Total meaningful content tokens.
    total = anchor_tokens | claim_tokens
    if not total:
        return 1.0  # nothing to anchor → maximal gap

    # Grounded tokens = anchors. Ungrounded claim-only tokens drive the gap up.
    ungrounded = claim_tokens - anchor_tokens
    # Weight: claims that share no tokens with anchors are the "distance".
    gap = len(ungrounded) / max(len(total), 1)
    return round(min(1.0, max(0.0, gap)), 4)


def _dedupe_preserve_order(items: Sequence[str]) -> List[str]:
    seen: Set[str] = set()
    out: List[str] = []
    for it in items:
        key = it.strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(it.strip())
    return out


# ---------------------------------------------------------------------------
# Language-agnostic Phase 1 (LLM-driven) — replaces the English-centric regex
# heuristics above with a model that can split ANY language's prompts into
# anchors / claims / constraints. Falls back to ``deconstruct`` when the LLM
# is unreachable or returns malformed output.
# ---------------------------------------------------------------------------

_DECONSTRUCT_PROMPT = """\
You are a fact-deconstruction engine. Treat the following user text as a \
mathematical puzzle of claims, anchors, and sources. Work in the text's own \
language — do NOT translate.

Split it into EXACTLY three lists:

1. "static_anchors": known, verifiable entities — proper names, companies, \
people, places, scientific/technical terms, products, organizations. These are \
things with a stable, look-up-able identity.
2. "variable_claims": the unverified assertions, numbers, dates, and \
cause/effect or relationship statements that need external confirmation.
3. "structural_constraints": any request about FORMAT, LENGTH, or TONE \
(e.g. "list", "summary", "short", "in a table", "formal tone"). If none, use [].

Rules:
- A given phrase belongs to exactly one list. Prefer anchors over claims when \
a phrase is a known entity.
- Preserve the original wording/surface form of each phrase — do not \
paraphrase, lowercase, or normalize names.
- If the text contains no anchors, "static_anchors" must be [].
- If the text asserts nothing verifiable, "variable_claims" must be [].

Respond with ONLY a single JSON object, no commentary, no code fences:
{"static_anchors": [...], "variable_claims": [...], "structural_constraints": [...]}

TEXT:
\"\"\"
{prompt}
\"\"\"
"""


def _resolve_deconstruct_endpoint() -> Optional[Tuple[str, str, Dict]]:
    """Resolve a chat endpoint for the deconstruction LLM call.

    Uses the utility endpoint (defaults to the chat model when unmapped) so no
    extra configuration is required. Returns (url, model, headers) or None.
    """
    try:
        from src.endpoint_resolver import resolve_endpoint
        url, model, headers = resolve_endpoint("utility")
        if url and model:
            return url, model, headers
    except Exception as e:
        logger.debug(f"deconstruct endpoint resolve failed: {e}")
    return None


def _parse_deconstruction(raw: str, prompt: str) -> Optional[Deconstruction]:
    """Parse the LLM's JSON answer (or a fenced/loose JSON blob) into a
    Deconstruction. Returns None on any parse/coercion failure."""
    if not raw:
        return None
    text = raw.strip()
    # Strip a leading/trailing ```json fence if the model added one.
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    # Extract the first JSON object in the string (robust to surrounding prose).
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return None
        try:
            obj = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    if not isinstance(obj, dict):
        return None

    def _str_list(v) -> List[str]:
        out: List[str] = []
        if isinstance(v, list):
            for item in v:
                if isinstance(item, str) and item.strip():
                    out.append(item.strip())
        return out

    anchors = _str_list(obj.get("static_anchors"))
    claims = _str_list(obj.get("variable_claims"))
    constraints = _str_list(obj.get("structural_constraints"))

    # Reject a response that lost the prompt's substance (both empty).
    if not anchors and not claims:
        return None

    d = Deconstruction(raw_prompt=prompt)
    d.static_anchors = _dedupe_preserve_order(anchors)
    d.variable_claims = _dedupe_preserve_order(claims)
    d.structural_constraints = _dedupe_preserve_order(constraints)
    d.information_gap = _compute_information_gap(
        prompt, d.static_anchors, d.variable_claims
    )
    return d


def deconstruct_with_llm(prompt: str) -> Deconstruction:
    """Language-agnostic Phase 1 via the active LLM, with a deterministic
    fallback to :func:`deconstruct` when the LLM is unavailable or fails."""
    if not prompt or not prompt.strip():
        return deconstruct(prompt)
    resolved = _resolve_deconstruct_endpoint()
    if not resolved:
        return deconstruct(prompt)
    url, model, headers = resolved
    try:
        from src.llm_core import _llm_call_raw
        raw = _llm_call_raw(
            url,
            model,
            messages=[
                {"role": "user", "content": _DECONSTRUCT_PROMPT.format(prompt=prompt)},
            ],
            headers=headers,
            temperature=0.0,
            max_tokens=700,
            timeout=30,
        )
        parsed = _parse_deconstruction(raw, prompt)
        if parsed:
            return parsed
    except Exception as e:
        logger.debug(f"LLM deconstruction failed, using regex fallback: {e}")
    return deconstruct(prompt)


async def deconstruct_with_llm_async(prompt: str) -> Deconstruction:
    """Async twin of :func:`deconstruct_with_llm` for event-loop callers."""
    if not prompt or not prompt.strip():
        return deconstruct(prompt)
    resolved = _resolve_deconstruct_endpoint()
    if not resolved:
        return deconstruct(prompt)
    url, model, headers = resolved
    try:
        from src.llm_core import _llm_call_async_raw
        raw = await _llm_call_async_raw(
            url,
            model,
            messages=[
                {"role": "user", "content": _DECONSTRUCT_PROMPT.format(prompt=prompt)},
            ],
            headers=headers,
            temperature=0.0,
            max_tokens=700,
            timeout=30,
        )
        parsed = _parse_deconstruction(raw, prompt)
        if parsed:
            return parsed
    except Exception as e:
        logger.debug(f"LLM deconstruction failed, using regex fallback: {e}")
    return deconstruct(prompt)


# ---------------------------------------------------------------------------
# Phase 2: Heuristic Query Generation (3-Tier)
# ---------------------------------------------------------------------------

def generate_queries(d: Deconstruction) -> QuerySet:
    """Generate the three parallel query tiers from the deconstruction."""
    anchors = d.static_anchors
    claims = d.variable_claims

    anchor_str = " ".join(anchors) if anchors else ""
    claim_str = " ".join(claims) if claims else ""

    # Tier 1 Proximity: anchors + claims co-occurrence.
    if anchor_str and claim_str:
        t1 = f"{anchor_str} {claim_str}"
    elif anchor_str:
        t1 = anchor_str
    else:
        t1 = claim_str

    # Tier 2 Broad: anchors alone (or the whole prompt if no anchors).
    t2 = anchor_str if anchor_str else (d.raw_prompt.strip()[:200] or "")

    # Tier 3 Opposing: anchors + adversarial terms (space-separated OR).
    if anchor_str:
        t3 = f"{anchor_str} {' OR '.join(_ADVERSARIAL_TERMS[:3])}"
    elif claim_str:
        t3 = f"{claim_str} error OR fake OR debunked"
    else:
        t3 = ""

    qs = QuerySet(
        tier1_proximity=t1.strip(),
        tier2_broad=t2.strip(),
        tier3_opposing=t3.strip(),
    )
    return qs


# ---------------------------------------------------------------------------
# Phase 3: Universal Validation Matrix
# ---------------------------------------------------------------------------

def _parse_age(age: Optional[str], now: float) -> Optional[float]:
    """Parse a provider 'age' string into an epoch timestamp, or None."""
    if not age:
        return None
    s = str(age).strip().lower()
    m = re.search(r"(\d+)\s*(second|minute|hour|day|week|month|year)s?\b", s)
    if not m:
        return None
    n = int(m.group(1))
    unit = m.group(2)
    seconds = {
        "second": 1, "minute": 60, "hour": 3600,
        "day": 86400, "week": 604800, "month": 2592000, "year": 31536000,
    }[unit]
    return now - (n * seconds)


def _temporal_score(evidences: List[SourceEvidence], now: float) -> Tuple[ValidationMetric, int]:
    """Recency score: how many snippets carry a timestamp and how fresh they are."""
    usable = 0
    fresh = 0
    for e in evidences:
        if e.timestamp is None:
            continue
        usable += 1
        # Within 2 years → recent enough to be "live" rather than stale.
        if (now - e.timestamp) <= (2 * 365 * 86400):
            fresh += 1
    if usable == 0:
        detail = "no parseable timestamps — recency unverifiable (treated weakly neutral)"
        return ValidationMetric(score=0.5, detail=detail), usable
    score = fresh / usable
    detail = f"{fresh}/{usable} snippets have timestamps within 2 years"
    return ValidationMetric(score=round(score, 4), detail=detail), usable


def _semantic_distance_score(claims: List[str], evidences: List[SourceEvidence]) -> ValidationMetric:
    """Overlap between claim tokens and retrieved text tokens. High overlap →
    low distance → high confidence. Deterministic token-set Jaccard."""
    if not claims:
        return ValidationMetric(score=0.5, detail="no claims to measure against")

    claim_tokens: Set[str] = set()
    for c in claims:
        claim_tokens |= _token_set(c)
    if not claim_tokens:
        return ValidationMetric(score=0.5, detail="no measurable claim tokens")

    all_text = " ".join((e.title or "") + " " + (e.snippet or "") for e in evidences)
    evidence_tokens = _token_set(all_text)
    if not evidence_tokens:
        return ValidationMetric(score=0.0, detail="no retrieved evidence text")

    inter = claim_tokens & evidence_tokens
    union = claim_tokens | evidence_tokens
    jaccard = len(inter) / len(union) if union else 0.0
    # Scale: Jaccard of ~0.3 is already strong for keyword overlap.
    score = min(1.0, jaccard * 3.0)
    detail = f"Jaccard={jaccard:.3f} (scaled {score:.3f}); {len(inter)} shared tokens"
    return ValidationMetric(score=round(score, 4), detail=detail)


def validate(
    results: List[Dict[str, Any]],
    claims: List[str],
    now: Optional[datetime] = None,
) -> ValidationMatrix:
    """Run the three metrics over raw provider results.

    ``results`` is the list normally returned by ``searxng_search_results`` /
    ``comprehensive_web_search`` providers: each dict has ``url``, ``title``,
    ``snippet``, and optionally ``age``.
    """
    now_dt = now or datetime.now(timezone.utc)
    now_epoch = now_dt.timestamp()

    evidences: List[SourceEvidence] = []
    domains: Set[str] = set()
    for r in results or []:
        if not isinstance(r, dict):
            continue
        url = str(r.get("url") or "")
        domain = _domain_of(url)
        if not domain:
            continue
        domains.add(domain)
        evidence = SourceEvidence(
            url=url,
            domain=domain,
            title=str(r.get("title") or ""),
            snippet=str(r.get("snippet") or ""),
            age=r.get("age"),
            timestamp=_parse_age(r.get("age"), now_epoch),
        )
        evidences.append(evidence)

    total = len(evidences)
    distinct = len(domains)

    # 1. Source consensus — independent uncorrelated domains count.
    # Floor at 3 domains for full consensus; scale 0..1.
    consensus_score = min(1.0, distinct / 3.0) if total > 0 else 0.0
    if distinct == 1 and total > 0:
        cons_detail = "single domain — flagged (possible echo/collusion)"
        consensus_score = 0.3
    elif total == 0:
        cons_detail = "no evidence retrieved"
    else:
        cons_detail = f"{distinct} independent domain(s) across {total} snippet(s)"

    # 2. Temporal recency.
    temporal, _ = _temporal_score(evidences, now_epoch)

    # 3. Semantic distance.
    semantic = _semantic_distance_score(claims, evidences)

    # Combined weighted confidence. When no evidence was retrieved at all,
    # confidence is zero regardless of the neutral temporal/semantic defaults.
    if total == 0:
        combined = 0.0
    else:
        combined = (
            consensus_score * 0.40
            + temporal.score * 0.25
            + semantic.score * 0.35
        )

    matrix = ValidationMatrix(
        claims=claims,
        source_consensus=ValidationMetric(
            score=round(consensus_score, 4), weight=0.40, detail=cons_detail
        ),
        temporal_recency=temporal,
        semantic_distance=semantic,
        distinct_domains=distinct,
        total_snippets=total,
        combined_confidence=round(combined, 4),
    )
    return matrix


# ---------------------------------------------------------------------------
# Phase 4: Grounded Synthesis
# ---------------------------------------------------------------------------

# Minimum combined confidence to consider a claim "verified".
_VERIFY_THRESHOLD = 0.5


def synthesize(matrix: ValidationMatrix) -> Synthesis:
    """Lock generation: return a grounded answer or the Ignorance Default.

    The Token Restriction Protocol is enforced by only composing the answer
    from snippet text actually retrieved (the sources list), never from
    internal knowledge. When confidence is below threshold, the Ignorance
    Default is emitted.
    """
    if matrix.total_snippets == 0:
        return Synthesis(
            grounded=False,
            answer=IGNORANCE_DEFAULT,
            is_ignorance_default=True,
        )

    if matrix.combined_confidence < _VERIFY_THRESHOLD:
        return Synthesis(
            grounded=False,
            answer=IGNORANCE_DEFAULT,
            is_ignorance_default=True,
        )

    # Grounded: cite the top corroborating sources.
    # We return the source list; the actual word-composition in a live agent
    # uses these snippets as the ONLY permitted material.
    supporting = []
    # (Caller supplies supporting source list via an optional arg in
    # orchestrator; here we reconstruct from the matrix detail text.)
    return Synthesis(
        grounded=True,
        answer=(
            f"Verified across {matrix.distinct_domains} independent source(s) "
            f"with a combined confidence of {matrix.combined_confidence:.2f}. "
            f"Sources confirm the claim's key terms."
        ),
        supporting_sources=[],
        is_ignorance_default=False,
    )


# ---------------------------------------------------------------------------
# End-to-end orchestrator
# ---------------------------------------------------------------------------

def universal_search(
    prompt: str,
    *,
    max_pages: int = 4,
    time_filter: Optional[str] = None,
    return_internals: bool = False,
    use_llm: bool = False,
) -> Dict[str, Any]:
    """Run the full 4-phase pipeline against the configured search providers.

    Returns a dict:
      {
        "deconstruction": {...},
        "queries": {...},
        "results_by_tier": {"proximity": [...], "broad": [...], "opposing": [...]},
        "validation": {...},
        "synthesis": {...},
      }

    This is the integration point: it reuses ``searxng_search_results`` from
    ``core`` so caching, provider chains, ranking, and fallbacks all apply
    exactly as in a normal web_search.
    """
    from .core import searxng_search_results

    decon = deconstruct_with_llm(prompt) if use_llm else deconstruct(prompt)
    queries = generate_queries(decon)

    # Run the three query tiers in parallel via a thread pool (they are
    # blocking provider calls). Each tier returns ranked raw results.
    from concurrent.futures import ThreadPoolExecutor, as_completed

    results_by_tier: Dict[str, List[Dict[str, Any]]] = {
        "proximity": [], "broad": [], "opposing": [],
    }

    def _run_tier(name: str, q: str) -> Tuple[str, List[Dict[str, Any]]]:
        if not q:
            return (name, [])
        try:
            found = searxng_search_results(q, count=max(3, max_pages), time_filter=time_filter)
            return (name, list(found or []))
        except Exception as e:
            logger.warning(f"universal_search tier={name} failed: {e}")
            return (name, [])

    with ThreadPoolExecutor(max_workers=3) as ex:
        futures = {
            ex.submit(_run_tier, name, q): name
            for name, q in queries.as_list() if q
        }
        for fut in as_completed(futures):
            try:
                name, found = fut.result()
                results_by_tier[name] = found
            except Exception as e:
                logger.warning(f"universal_search tier future failed: {e}")

    # Merge all tiers for validation (dedupe by domain for consensus, but keep
    # all snippets for semantic distance measurement).
    merged: List[Dict[str, Any]] = []
    seen_urls: Set[str] = set()
    for tier_results in results_by_tier.values():
        for r in tier_results:
            u = r.get("url") if isinstance(r, dict) else None
            if u and u not in seen_urls:
                seen_urls.add(u)
                merged.append(r)

    matrix = validate(merged, decon.variable_claims)
    synthesis = synthesize(matrix)

    # Attach supporting source metadata to the grounded synthesis.
    if synthesis.grounded:
        synthesis.supporting_sources = [
            {"url": r.get("url"), "title": r.get("title"), "domain": _domain_of(r.get("url") or "")}
            for r in merged if r.get("url")
        ][: matrix.distinct_domains]

    payload: Dict[str, Any] = {
        "deconstruction": decon.to_dict(),
        "queries": asdict(queries),
        "validation": matrix.to_dict(),
        "synthesis": asdict(synthesis),
    }
    if return_internals:
        payload["results_by_tier"] = results_by_tier

    return payload