"""Tests for extractive synthesis and citation offsets.

The central guarantee: every citation's ``quote`` must be exactly the text at
``content[start_char:end_char]`` in the source document. If that ever fails,
the product's core claim — "check the citation yourself" — is false.
"""

from __future__ import annotations

import pytest

from app.rag.index import SearchIndex
from app.rag.retriever import retrieve
from app.rag.synthesizer import synthesise

DOCUMENT = """# Leave Policy

Every full-time employee is entitled to twenty-four paid annual leave days per
calendar year. Leave accrues at two days per completed month of service.

A maximum of ten unused annual leave days may be carried into the following
year. Any balance above ten days lapses on 31 December.

Sick leave may not be used to extend annual leave or public holidays.
"""

OTHER = """# Expense Policy

Expenses must be submitted within thirty days of being incurred. Claims older
than thirty days require written justification from the finance director.

Hotel accommodation is capped at 25000 per night in Karachi and Lahore.
"""


def _build() -> SearchIndex:
    return SearchIndex().build(
        [
            {
                "chunk_id": 1,
                "document_id": 1,
                "document_title": "Leave Policy",
                "filename": "leave.md",
                "ordinal": 0,
                "text": DOCUMENT,
                "start_char": 0,
                "end_char": len(DOCUMENT),
            },
            {
                "chunk_id": 2,
                "document_id": 2,
                "document_title": "Expense Policy",
                "filename": "expense.md",
                "ordinal": 0,
                "text": OTHER,
                "start_char": 0,
                "end_char": len(OTHER),
            },
        ],
        version=1,
    )


@pytest.fixture()
def index() -> SearchIndex:
    return _build()


class TestSynthesise:
    def test_produces_an_answer_for_a_matching_question(self, index):
        scored = retrieve(index, "how many annual leave days do I get")
        result = synthesise("how many annual leave days do I get", scored, index)
        assert result.answer
        assert result.citations

    def test_citations_are_numbered_from_one(self, index):
        scored = retrieve(index, "annual leave days")
        result = synthesise("annual leave days", scored, index)
        assert [c.marker for c in result.citations] == list(range(1, len(result.citations) + 1))

    def test_answer_contains_marker_for_every_citation(self, index):
        scored = retrieve(index, "annual leave days")
        result = synthesise("annual leave days", scored, index)
        for citation in result.citations:
            assert f"[{citation.marker}]" in result.answer

    def test_quote_matches_the_source_offsets(self, index):
        """The core guarantee: a citation must point at its own text."""
        scored = retrieve(index, "how many annual leave days do I get")
        result = synthesise("how many annual leave days do I get", scored, index)
        sources = {1: DOCUMENT, 2: OTHER}
        for citation in result.citations:
            source = sources[citation.document_id]
            assert source[citation.start_char : citation.end_char] == citation.quote

    def test_offsets_are_within_bounds(self, index):
        scored = retrieve(index, "annual leave days")
        result = synthesise("annual leave days", scored, index)
        sources = {1: DOCUMENT, 2: OTHER}
        for citation in result.citations:
            source = sources[citation.document_id]
            assert 0 <= citation.start_char < citation.end_char <= len(source)

    def test_quotes_are_verbatim_substrings(self, index):
        scored = retrieve(index, "annual leave days")
        result = synthesise("annual leave days", scored, index)
        for citation in result.citations:
            assert citation.quote in DOCUMENT or citation.quote in OTHER

    def test_no_citations_for_an_unrelated_question(self, index):
        scored = retrieve(index, "zzzznotaword")
        result = synthesise("zzzznotaword", scored, index)
        assert result.citations == []
        assert result.answer == ""

    def test_respects_max_sentences(self, index):
        scored = retrieve(index, "annual leave days expense policy")
        result = synthesise("annual leave days expense policy", scored, index, max_sentences=2)
        assert len(result.citations) <= 2

    def test_does_not_quote_the_same_sentence_twice(self, index):
        scored = retrieve(index, "annual leave days")
        result = synthesise("annual leave days", scored, index)
        quotes = [c.quote for c in result.citations]
        assert len(quotes) == len(set(quotes))

    def test_citations_are_ordered_by_document_then_position(self, index):
        scored = retrieve(index, "annual leave days expense policy")
        result = synthesise("annual leave days expense policy", scored, index)
        keys = [(c.document_id, c.start_char) for c in result.citations]
        assert keys == sorted(keys)

    def test_mode_is_extractive(self, index):
        scored = retrieve(index, "annual leave days")
        assert synthesise("annual leave days", scored, index).mode == "extractive"

    def test_empty_retrieval_gives_empty_synthesis(self, index):
        result = synthesise("anything", [], index)
        assert result.answer == ""
        assert result.citations == []

    def test_does_not_quote_a_mid_sentence_fragment(self, index):
        """A chunk starting mid-sentence (from overlap) must not yield a
        citation that begins with a lowercase fragment."""
        for citation in synthesise(
            "annual leave days", retrieve(index, "annual leave days"), index
        ).citations:
            assert citation.quote[0].isupper() or not citation.quote[0].isascii()
