# src/legal/__init__.py
"""Legal corpus adapters — Cluster L (SINALEVI Costa Rica + Corte IDH).

Query-first by design: both sites are navigable for targeted queries, so the
adapters fetch + parse on demand and the bulk crawler is opt-in only (the
community-scraper rot the research flagged is avoided by falling back to a
site-restricted web search whenever a page structure changes).
"""

from src.legal.corpus import (  # noqa: F401
    corpus_root,
    index_rows,
    record,
    save_document,
    search_corpus,
)
from src.legal.sinalevi import LawHit, search_sinalevi  # noqa: F401
from src.legal.corteidh import CaseHit, Instrument, corteidh_catalog, corteidh_case, corteidh_search  # noqa: F401
