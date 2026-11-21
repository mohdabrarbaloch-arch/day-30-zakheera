"""Tests for hybrid retrieval and Reciprocal Rank Fusion."""

from __future__ import annotations

import pytest

from app.rag.index import SearchIndex
from app.rag.retriever import RRF_K, retrieve


def _chunk(chunk_id: int, text: str, document_id: int = 1, ordinal: int = 0) -> dict:
    return {
        "chunk_id": chunk_id,
        "document_id": document_id,
        "document_title": f"Doc {document_id}",
        "filename": f"doc{document_id}.md",
        "ordinal": ordinal,
        "text": text,
        "start_char": 0,
        "end_char": len(text),
    }


CORPUS = [
    _chunk(1, "The refund window is thirty days from the date of delivery.", 1, 0),
    _chunk(2, "Annual leave accrues at two days for each completed month of service.", 2, 0),
    _chunk(3, "Any suspected security incident must be reported within one hour.", 3, 0),
    _chunk(4, "Hotel accommodation is capped at 25000 per night in Karachi.", 4, 0),
    _chunk(5, "Expenses must be submitted within thirty days of being incurred.", 5, 0),
]


@pytest.fixture()
def index() -> SearchIndex:
    return SearchIndex().build(CORPUS, version=1)


class TestRetrieve:
    def test_returns_results_for_a_matching_question(self, index):
        results = retrieve(index, "how long is the refund window")
        assert results
        assert results[0].chunk.chunk_id == 1

    def test_respects_top_k(self, index):
        assert len(retrieve(index, "days", top_k=2)) <= 2

    def test_empty_index_returns_nothing(self):
        assert retrieve(SearchIndex().build([], 0), "anything") == []

    def test_unrelated_question_returns_nothing(self, index):
        assert retrieve(index, "zzzznotaword qqqq") == []

    def test_results_are_ordered_by_fused_score(self, index):
        results = retrieve(index, "refund window delivery days")
        scores = [r.fused_score for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_both_rankers_contribute(self, index):
        """A chunk found by both rankers should outrank one found by only one."""
        results = retrieve(index, "refund window delivery")
        top = results[0]
        assert top.bm25_rank is not None
        assert top.vector_rank is not None

    def test_ranks_are_one_based(self, index):
        for result in retrieve(index, "refund window"):
            if result.bm25_rank is not None:
                assert result.bm25_rank >= 1
            if result.vector_rank is not None:
                assert result.vector_rank >= 1

    def test_fused_score_matches_rrf_formula(self, index):
        """The fused score must be exactly the RRF sum, or the number shown
        in the UI would not mean what it claims to."""
        for result in retrieve(index, "refund window delivery"):
            expected = 0.0
            if result.bm25_rank is not None:
                expected += 1.0 / (RRF_K + result.bm25_rank)
            if result.vector_rank is not None:
                expected += 1.0 / (RRF_K + result.vector_rank)
            assert result.fused_score == pytest.approx(expected, abs=1e-9)

    def test_deterministic_across_calls(self, index):
        """The same question must give the same order every time."""
        first = [r.chunk.chunk_id for r in retrieve(index, "days within thirty")]
        second = [r.chunk.chunk_id for r in retrieve(index, "days within thirty")]
        assert first == second

    def test_candidate_pool_widens_recall(self, index):
        narrow = retrieve(index, "days", top_k=1, candidate_pool=1)
        wide = retrieve(index, "days", top_k=1, candidate_pool=50)
        assert len(narrow) <= len(wide)

    def test_scores_are_populated(self, index):
        result = retrieve(index, "refund window")[0]
        assert result.bm25_score > 0
        assert result.vector_score > 0
        assert result.fused_score > 0
