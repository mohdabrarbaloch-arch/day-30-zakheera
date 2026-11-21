"""Tests for TF-IDF vectorisation and cosine similarity."""

from __future__ import annotations

import math

import pytest

from app.rag.embedder import TfidfModel, cosine_similarity

CORPUS = [
    "The refund window is thirty days from delivery.",
    "Annual leave accrues at two days per month.",
    "Security incidents must be reported within one hour.",
    "The hotel cap in Karachi is 25000 per night.",
]


class TestTfidfModel:
    def test_fit_builds_vocabulary(self):
        model = TfidfModel().fit(CORPUS)
        assert model.document_count == len(CORPUS)
        assert "refund" in model.vocabulary
        assert "leave" in model.vocabulary

    def test_vocabulary_is_sorted_and_stable(self):
        """Index positions must not depend on dict ordering, or persisted
        vectors would break after a restart."""
        first = TfidfModel().fit(CORPUS).vocabulary
        second = TfidfModel().fit(list(reversed(CORPUS))).vocabulary
        assert first == second

    def test_vectors_are_l2_normalised(self):
        model = TfidfModel().fit(CORPUS)
        for document in CORPUS:
            vector = model.transform(document)
            norm = math.sqrt(sum(v * v for v in vector.values()))
            assert norm == pytest.approx(1.0, abs=1e-9)

    def test_rare_terms_get_higher_idf(self):
        model = TfidfModel().fit(CORPUS)
        # "refund" appears in one document; "day" (from "days") in two.
        assert model.idf("refund") > model.idf("day")

    def test_out_of_vocabulary_terms_are_dropped(self):
        model = TfidfModel().fit(CORPUS)
        vector = model.transform("zzzznotaword")
        assert vector == {}

    def test_empty_text_gives_empty_vector(self):
        model = TfidfModel().fit(CORPUS)
        assert model.transform("") == {}
        assert model.transform("the and of") == {}

    def test_round_trip_serialisation(self):
        model = TfidfModel().fit(CORPUS)
        restored = TfidfModel.from_dict(model.to_dict())
        assert restored.vocabulary == model.vocabulary
        assert restored.document_count == model.document_count
        assert restored.transform(CORPUS[0]) == model.transform(CORPUS[0])


class TestCosineSimilarity:
    def test_identical_vectors_are_one(self):
        model = TfidfModel().fit(CORPUS)
        vector = model.transform(CORPUS[0])
        assert cosine_similarity(vector, vector) == pytest.approx(1.0, abs=1e-9)

    def test_disjoint_vectors_are_zero(self):
        model = TfidfModel().fit(CORPUS)
        left = model.transform("refund window delivery")
        right = model.transform("security incident reported")
        assert cosine_similarity(left, right) == pytest.approx(0.0, abs=1e-9)

    def test_empty_vector_is_zero(self):
        model = TfidfModel().fit(CORPUS)
        assert cosine_similarity({}, model.transform(CORPUS[0])) == 0.0
        assert cosine_similarity({}, {}) == 0.0

    def test_similar_text_scores_higher_than_unrelated(self):
        model = TfidfModel().fit(CORPUS)
        query = model.transform("how long is the refund window")
        related = model.transform(CORPUS[0])
        unrelated = model.transform(CORPUS[2])
        assert cosine_similarity(query, related) > cosine_similarity(query, unrelated)

    def test_symmetry(self):
        model = TfidfModel().fit(CORPUS)
        left = model.transform(CORPUS[0])
        right = model.transform(CORPUS[1])
        assert cosine_similarity(left, right) == pytest.approx(cosine_similarity(right, left))
