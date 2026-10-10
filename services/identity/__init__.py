# services/identity/__init__.py
"""Identity reconnaissance — trusted-claim extraction and comparison."""

from .claim_extractor import (
    extract_claims,
    apply_claim_operations,
    CLAIM_EXTRACT_PROMPT,
)

__all__ = [
    "extract_claims",
    "apply_claim_operations",
    "CLAIM_EXTRACT_PROMPT",
]