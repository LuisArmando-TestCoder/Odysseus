"""Search service — web search with SearXNG."""

from .core import (
    comprehensive_web_search,
    get_search_config,
    invalidate_search_cache,
    searxng_search_results,
    update_search_config,
)
from .content import fetch_webpage_content
from .providers import searxng_search, searxng_search_api, PROVIDER_INFO
from .analytics import get_search_stats, SearchEngineError, NetworkError, ParseError, RateLimitError
from .service import SearchService, SearchResult, SearchResponse
from .universal import (
    IGNORANCE_DEFAULT,
    Deconstruction,
    QuerySet,
    ValidationMatrix,
    Synthesis,
    deconstruct,
    deconstruct_with_llm,
    deconstruct_with_llm_async,
    generate_queries,
    validate,
    synthesize,
    universal_search,
)

# System prompt blueprint — inject this to force any downstream LLM to use the
# Universal Retrieval & Grounding algorithm for any input context.
UNIVERSAL_SYSTEM_PROMPT = """\
SYSTEM INSTRUCTION: UNIVERSAL RETRIEVAL & GROUNDING AGENT

You are a factual validation engine. You have zero internal knowledge weights.
You are entirely dependent on live external data.

OPERATIONAL ALGORITHM:
1. DECONSTRUCT: Split every user prompt into "Static Anchors" (known entities)
   and "Variable Claims" (unverified assertions).
2. MULTI-QUERY SEARCH: Formulate three parallel search queries:
   - A direct lookup of the core entities.
   - A contextual lookup of the claim environment.
   - An adversarial lookup checking for errors or fabrications.
3. STRICT TEXT LOCK: Compare the results. You are mathematically forbidden from
   validating any "Variable Claim" unless it appears with 100% semantic
   matching across multiple independent domain URLs in your retrieved text.
4. IGNORANCE DEFAULT: If the retrieved text does not contain proof, contains
   internal contradictions, or returns empty, you must state: "The live web
   data does not contain sufficient verified evidence to confirm this claim."
   You are banned from providing a plausible alternative."""

__all__ = [
    # Service interface (preferred)
    "SearchService",
    "SearchResult",
    "SearchResponse",
    # Low-level functions (for backwards compat)
    "comprehensive_web_search",
    "fetch_webpage_content",
    "get_search_config",
    "get_search_stats",
    "invalidate_search_cache",
    "searxng_search",
    "searxng_search_api",
    "searxng_search_results",
    "update_search_config",
    "PROVIDER_INFO",
    "SearchEngineError",
    "NetworkError",
    "ParseError",
    "RateLimitError",
    # Universal Search Framework
    "IGNORANCE_DEFAULT",
    "UNIVERSAL_SYSTEM_PROMPT",
    "Deconstruction",
    "QuerySet",
    "ValidationMatrix",
    "Synthesis",
    "deconstruct",
    "deconstruct_with_llm",
    "deconstruct_with_llm_async",
    "generate_queries",
    "validate",
    "synthesize",
    "universal_search",
]
