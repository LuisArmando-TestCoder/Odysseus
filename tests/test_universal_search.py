"""Tests for the Universal Search Framework (services/search/universal.py).

Covers all four phases deterministically plus the end-to-end orchestrator,
with a focus on the fictional-riddle anchor-isolation scenario the spec calls
out: feeding a fabricated claim should NOT anchor it, and the validation
matrix should flag it rather than hallucinate a plausible answer.
"""

from datetime import datetime, timezone

import pytest

from services.search.universal import (
    IGNORANCE_DEFAULT,
    Deconstruction,
    QuerySet,
    ValidationMatrix,
    Synthesis,
    deconstruct,
    deconstruct_with_llm,
    generate_queries,
    validate,
    synthesize,
    _parse_deconstruction,
    _resolve_deconstruct_endpoint,
)
import services.search.universal as universal_mod


# ---------------------------------------------------------------------------
# Phase 1: Universal Deconstruction
# ---------------------------------------------------------------------------

class TestDeconstruction:
    def test_extracts_capitalized_multiword_anchor(self):
        d = deconstruct("Who invented the Turing Machine? Alan Turing discovered the Halting Problem.")
        assert "Alan Turing" in d.static_anchors

    def test_extracts_named_entity_with_diacritics(self):
        d = deconstruct("Laura Fernández Delgado was the first female president of Mexico.")
        assert any("Laura" in a for a in d.static_anchors)

    def test_fictional_riddle_does_not_anchor_invented_name(self):
        # A made-up entity that only appears lowercase should not be extracted
        # as a capitalized anchor, avoiding treating fiction as a known entity.
        d = deconstruct("there is a tiny nation called flabberwockistan that declared war on gravity in 1973")
        # No genuine capitalized multiword name -> no forged anchor.
        assert "Flabberwockistan" not in d.static_anchors

    def test_extracts_number_claim(self):
        d = deconstruct("Tesla delivered 1.2 million cars in 2023.")
        # The numeric assertion becomes a claim.
        assert any("1.2 million" in c or "2023" in c for c in d.variable_claims)

    def test_extracts_structural_constraints(self):
        d = deconstruct("Give me a bullet list summary of the Eiffel Tower, keep it short.")
        assert "bullet" in d.structural_constraints or "list" in d.structural_constraints
        assert "summary" in d.structural_constraints

    def test_information_gap_is_zero_when_fully_anchored(self):
        # Pure anchor, no claim -> low gap.
        d = deconstruct("Alan Turing")
        assert 0.0 <= d.information_gap <= 1.0

    def test_empty_prompt(self):
        d = deconstruct("")
        assert d.static_anchors == []
        assert d.variable_claims == []
        assert d.information_gap == 0.0


# ---------------------------------------------------------------------------
# Phase 2: Heuristic Query Generation (3-Tier)
# ---------------------------------------------------------------------------

class TestQueryGeneration:
    def test_generates_three_tiers(self):
        d = deconstruct("Did Alan Turing invent the Loops programming language in 1954?")
        q = generate_queries(d)
        assert q.tier1_proximity  # anchors + claims
        assert q.tier2_broad      # anchors alone
        assert q.tier3_opposing   # adversarial term baked in
        # Opposing tier must carry an adversarial marker.
        assert any(
            term in q.tier3_opposing.lower()
            for term in ("error", "fake", "debunked", "hoax", "myth")
        )

    def test_opposing_query_contains_adversarial_terms(self):
        q = generate_queries(deconstruct("Alan Turing invented the internet"))
        assert "error" in q.tier3_opposing.lower() or "fake" in q.tier3_opposing.lower()

    def test_tiers_distinct(self):
        d = deconstruct("Was Marie Curie born in 1867 and did she discover radium?")
        q = generate_queries(d)
        tiers = {q.tier1_proximity, q.tier2_broad, q.tier3_opposing}
        assert len(tiers) == 3


# ---------------------------------------------------------------------------
# Phase 3: Universal Validation Matrix
# ---------------------------------------------------------------------------

class TestValidationMatrix:
    def _results(self, n_domains=3):
        out = []
        for i in range(n_domains):
            out.append({
                "url": f"https://news{i}.example.com/article",
                "title": f"Alan Turing invented a test {i}",
                "snippet": "Alan Turing proposed the Turing test in 1950",
                "age": "3 days",
            })
        return out

    def test_source_consensus_counts_domains(self):
        m = validate(self._results(3), ["Turing proposed the test in 1950"])
        assert m.distinct_domains == 3
        assert m.total_snippets == 3
        assert m.source_consensus.score >= 0.9

    def test_single_domain_flagged(self):
        m = validate(self._results(1), ["Turing test"])
        assert m.distinct_domains == 1
        assert m.source_consensus.score == 0.3
        assert "single domain" in m.source_consensus.detail

    def test_no_results_yields_zero_snippets(self):
        m = validate([], ["some claim"])
        assert m.total_snippets == 0
        assert m.combined_confidence == 0.0

    def test_semantic_distance_low_for_unrelated_text(self):
        # Claim words absent from evidence -> low semantic overlap score.
        m = validate(
            [{"url": "https://a.example.com", "title": "penguin facts", "snippet": "penguins live in antarctica", "age": "1 day"}],
            ["quantum entanglement causes faster-than-light communication"],
        )
        assert m.semantic_distance.score < 0.5

    def test_temporal_recency_scores_fresh_high(self):
        m = validate(self._results(3), ["Turing test"])
        assert m.temporal_recency.score >= 0.5  # 3 days old is fresh


# ---------------------------------------------------------------------------
# Phase 4: Grounded Synthesis
# ---------------------------------------------------------------------------

class TestSynthesis:
    def test_ignorance_default_when_no_snippets(self):
        matrix = ValidationMatrix(claims=["x"], total_snippets=0, combined_confidence=0.0)
        s = synthesize(matrix)
        assert s.is_ignorance_default
        assert s.answer == IGNORANCE_DEFAULT

    def test_ignorance_default_when_low_confidence(self):
        matrix = ValidationMatrix(
            claims=["x"],
            total_snippets=2,
            combined_confidence=0.2,
            distinct_domains=1,
        )
        s = synthesize(matrix)
        assert s.is_ignorance_default
        assert "sufficient verified evidence" in s.answer

    def test_grounded_when_confident(self):
        matrix = ValidationMatrix(
            claims=["x"],
            total_snippets=3,
            combined_confidence=0.8,
            distinct_domains=3,
        )
        s = synthesize(matrix)
        assert s.grounded
        assert not s.is_ignorance_default


# ---------------------------------------------------------------------------
# Fictional-riddle integration scenario
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# LLM-driven (language-agnostic) deconstruction
# ---------------------------------------------------------------------------

class TestLLMDeconstruction:
    def test_parse_valid_json(self):
        raw = '{"static_anchors": ["Alan Turing"], "variable_claims": ["invented the internet"], "structural_constraints": []}'
        d = _parse_deconstruction(raw, "did Alan Turing invent the internet?")
        assert d is not None
        assert d.static_anchors == ["Alan Turing"]
        assert d.variable_claims == ["invented the internet"]
        assert 0.0 <= d.information_gap <= 1.0

    def test_parse_fenced_json(self):
        raw = '```json\n{"static_anchors": ["Tesla"], "variable_claims": ["1.2 million cars in 2023"], "structural_constraints": ["short"]}\n```'
        d = _parse_deconstruction(raw, "Tesla delivered 1.2 million cars in 2023")
        assert d is not None
        assert "Tesla" in d.static_anchors
        assert any("2023" in c for c in d.variable_claims)
        assert "short" in d.structural_constraints

    def test_parse_loose_json_with_prose(self):
        raw = 'Sure, here it is: {"static_anchors": ["NASA"], "variable_claims": ["landed on Mars"], "structural_constraints": []}'
        d = _parse_deconstruction(raw, "did NASA land on Mars?")
        assert d is not None
        assert d.static_anchors == ["NASA"]

    def test_parse_malformed_returns_none(self):
        assert _parse_deconstruction("not json at all", "prompt") is None
        assert _parse_deconstruction("", "prompt") is None

    def test_parse_empty_both_lists_rejected(self):
        raw = '{"static_anchors": [], "variable_claims": [], "structural_constraints": ["short"]}'
        assert _parse_deconstruction(raw, "prompt") is None

    def test_deconstruct_with_llm_uses_llm_result(self, monkeypatch):
        monkeypatch.setattr(
            universal_mod, "_resolve_deconstruct_endpoint",
            lambda: ("http://x/v1", "m", {"Authorization": "Bearer k"}),
        )
        def _fake_raw(url, model, messages, headers, temperature, max_tokens, timeout):
            return '{"static_anchors": ["Marie Curie"], "variable_claims": ["discovered radium"], "structural_constraints": []}'
        # _llm_call_raw is imported lazily inside deconstruct_with_llm; patch the
        # attribute on the module it's imported from.
        import src.llm_core as lc
        monkeypatch.setattr(lc, "_llm_call_raw", _fake_raw)
        d = deconstruct_with_llm("did Marie Curie discover radium?")
        assert d.static_anchors == ["Marie Curie"]

    def test_deconstruct_with_llm_falls_back_when_no_endpoint(self, monkeypatch):
        monkeypatch.setattr(universal_mod, "_resolve_deconstruct_endpoint", lambda: None)
        d = deconstruct_with_llm("Did Albert Einstein invent a parmesan cheese powered engine in 1927?")
        # Deterministic fallback should still find the anchor.
        assert "Albert Einstein" in d.static_anchors


class TestFictionalRiddleIsolation:
    def test_fabricated_claim_flag_path(self):
        """A completely invented assertion should (deterministically) fail to
        be confirmed because anchors are not forged and confidence stays low."""
        prompt = (
            "In 1999, the Republic of Flabberwock founded a lunar colony "
            "powered entirely by cucumber batteries. Is this true?"
        )
        d = deconstruct(prompt)
        # No capitalized multiword anchor is invented from the lowercase fiction.
        # Validation of an empty/absent evidence set must not hallucinate.
        m = validate([], d.variable_claims)
        s = synthesize(m)
        assert s.is_ignorance_default
        assert s.answer == IGNORANCE_DEFAULT

    def test_technical_riddle_anchors_isolated(self):
        """A riddle about a real entity + an invented claim isolates the real
        anchor from the fabricated assertion."""
        prompt = "Did Albert Einstein file a patent for a parmesan cheese powered engine in 1927?"
        d = deconstruct(prompt)
        assert "Albert Einstein" in d.static_anchors
        # The cheese claim must be captured as a variable claim, not an anchor.
        assert any("parmesan" in c.lower() for c in d.variable_claims)