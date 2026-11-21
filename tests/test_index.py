"""Tests for the search index: BM25, vector scoring and corpus coverage."""

from __future__ import annotations

import pytest

from app.rag.index import SearchIndex


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
]


@pytest.fixture()
def index() -> SearchIndex:
    return SearchIndex().build(CORPUS, version=1)


class TestBuild:
    def test_builds_all_chunks(self, index):
        assert len(index.chunks) == 4
        assert not index.is_empty

    def test_average_length_is_positive(self, index):
        assert index.average_length > 0

    def test_empty_corpus(self):
        empty = SearchIndex().build([], version=0)
        assert empty.is_empty
        assert empty.average_length == 0.0
        assert empty.bm25_scores(["anything"]) == {}

    def test_chunks_with_no_indexable_tokens_are_dropped(self):
        """A chunk of pure punctuation can never be retrieved; keeping it
        would only skew the average length."""
        index = SearchIndex().build([_chunk(1, "!!! ??? ..."), _chunk(2, "real content here")], 1)
        assert len(index.chunks) == 1

    def test_postings_map_terms_to_chunks(self, index):
        assert "refund" in index.postings
        assert 0 in index.postings["refund"]

    def test_version_is_recorded(self, index):
        assert index.version == 1


class TestBm25:
    def test_matching_chunk_scores_above_zero(self, index):
        scores = index.bm25_scores(["refund"])
        assert scores.get(0, 0) > 0

    def test_non_matching_chunks_are_absent(self, index):
        scores = index.bm25_scores(["refund"])
        assert 1 not in scores
        assert 2 not in scores

    def test_rare_term_outweighs_common_term(self, index):
        """BM25's IDF should make a distinctive term count for more."""
        rare = index.bm25_scores(["refund"]).get(0, 0)
        common = index.bm25_scores(["days"]).get(0, 0)
        assert rare > common

    def test_unknown_term_scores_nothing(self, index):
        assert index.bm25_scores(["zzzznotaword"]) == {}

    def test_empty_query(self, index):
        assert index.bm25_scores([]) == {}

    def test_idf_is_positive(self, index):
        assert index.idf("refund") > 0

    def test_idf_of_absent_term_is_highest(self, index):
        """A term in no document has df=0, giving the maximum IDF — which is
        exactly what makes it strong evidence the corpus cannot answer."""
        assert index.idf("zzzznotaword") > index.idf("refund")


class TestVectorScores:
    def test_relevant_chunk_scores_highest(self, index):
        scores = index.vector_scores("how long is the refund window")
        assert max(scores, key=scores.get) == 0

    def test_scores_are_bounded(self, index):
        for score in index.vector_scores("refund window delivery").values():
            assert 0.0 <= score <= 1.0 + 1e-9

    def test_empty_query(self, index):
        assert index.vector_scores("") == {}

    def test_stopword_only_query(self, index):
        assert index.vector_scores("the and of") == {}


class TestCorpusCoverage:
    def test_full_coverage_when_all_terms_present(self, index):
        coverage, missing = index.corpus_coverage(["refund", "window"])
        assert coverage == pytest.approx(1.0)
        assert missing == []

    def test_zero_coverage_when_no_terms_present(self, index):
        coverage, missing = index.corpus_coverage(["cryptocurrency", "blockchain"])
        assert coverage == pytest.approx(0.0)
        assert set(missing) == {"cryptocurrency", "blockchain"}

    def test_partial_coverage_is_between(self, index):
        coverage, missing = index.corpus_coverage(["refund", "cryptocurrency"])
        assert 0.0 < coverage < 1.0
        assert missing == ["cryptocurrency"]

    def test_missing_terms_are_reported(self, index):
        _, missing = index.corpus_coverage(["refund", "zzzznotaword"])
        assert "zzzznotaword" in missing

    def test_empty_query_is_zero(self, index):
        coverage, missing = index.corpus_coverage([])
        assert coverage == 0.0
        assert missing == []

    def test_duplicate_terms_do_not_inflate_coverage(self, index):
        """Repeating a missing term must not change the score."""
        once, _ = index.corpus_coverage(["refund", "cryptocurrency"])
        twice, _ = index.corpus_coverage(["refund", "cryptocurrency", "cryptocurrency"])
        assert once == pytest.approx(twice)

    def test_matching_a_rare_term_earns_more_coverage(self, index):
        """Coverage is IDF-weighted, so a question whose matched terms are
        distinctive scores higher than one whose matched terms are common.

        This is what makes the abstention signal meaningful: "refund" is
        rare and carries the question, while "days" is common and does not.
        """
        rare_present, _ = index.corpus_coverage(["refund", "zzzznotaword"])
        common_present, _ = index.corpus_coverage(["days", "zzzznotaword"])
        assert rare_present > common_present

    def test_absent_terms_are_capped_not_infinite(self, index):
        """An unseen term must not dominate the score.

        Without a cap, a term with zero occurrences gets the theoretical
        maximum IDF and can single-handedly force an abstention on a question
        the corpus can answer.
        """
        # One absent term among three present ones should still clear 0.5.
        coverage, missing = index.corpus_coverage(["refund", "window", "delivery", "zzzznotaword"])
        assert missing == ["zzzznotaword"]
        assert coverage >= 0.5

    def test_all_terms_absent_is_zero_coverage(self, index):
        coverage, missing = index.corpus_coverage(["zzzznotaword", "qqqqnotaword"])
        assert coverage == 0.0
        assert len(missing) == 2
