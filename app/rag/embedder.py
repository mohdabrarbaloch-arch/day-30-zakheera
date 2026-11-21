"""TF-IDF vectorisation.

This is a real, if classical, embedding: term frequency weighted by inverse
document frequency, L2-normalised so that cosine similarity is a plain dot
product. It is not a neural embedding and does not claim to be — but it is
deterministic, needs no model download, runs in microseconds, and for the
kind of question people ask an internal knowledge base ("what is our refund
window", "who approves travel") it retrieves well.

The interface is deliberately the same shape a neural embedder would have,
so swapping one in later means replacing this module and nothing else.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

from app.rag.tokenizer import tokenize


@dataclass(slots=True)
class TfidfModel:
    """Vocabulary plus document frequencies, fitted over a corpus."""

    vocabulary: dict[str, int] = field(default_factory=dict)
    document_frequency: dict[str, int] = field(default_factory=dict)
    document_count: int = 0

    def fit(self, documents: list[str]) -> TfidfModel:
        """Learn the vocabulary and document frequencies from raw texts."""
        self.vocabulary = {}
        self.document_frequency = {}
        self.document_count = len(documents)

        for document in documents:
            # A set, not a list: document frequency counts documents a term
            # appears in, not how many times it appears.
            for term in set(tokenize(document)):
                self.document_frequency[term] = self.document_frequency.get(term, 0) + 1

        # Sorted so the index assignment is stable across runs, which keeps
        # persisted vectors valid after a restart.
        for position, term in enumerate(sorted(self.document_frequency)):
            self.vocabulary[term] = position

        return self

    def idf(self, term: str) -> float:
        """Smoothed inverse document frequency.

        The +1 in the denominator stops a term that appears in every document
        from dividing by zero, and the +1 outside keeps the weight positive
        rather than collapsing to zero for ubiquitous terms.
        """
        df = self.document_frequency.get(term, 0)
        return math.log((1 + self.document_count) / (1 + df)) + 1.0

    def transform(self, text: str) -> dict[int, float]:
        """Return a sparse, L2-normalised TF-IDF vector for ``text``."""
        tokens = tokenize(text)
        if not tokens:
            return {}

        counts = Counter(tokens)
        total = len(tokens)

        vector: dict[int, float] = {}
        for term, count in counts.items():
            position = self.vocabulary.get(term)
            if position is None:
                # An out-of-vocabulary term contributes nothing. Silently
                # dropping it is correct here: the query is still scored on
                # the terms the corpus actually knows.
                continue
            tf = count / total
            vector[position] = tf * self.idf(term)

        return _l2_normalise(vector)

    def transform_many(self, texts: list[str]) -> list[dict[int, float]]:
        return [self.transform(text) for text in texts]

    def to_dict(self) -> dict:
        return {
            "vocabulary": self.vocabulary,
            "document_frequency": self.document_frequency,
            "document_count": self.document_count,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> TfidfModel:
        return cls(
            vocabulary=dict(payload["vocabulary"]),
            document_frequency=dict(payload["document_frequency"]),
            document_count=int(payload["document_count"]),
        )


def _l2_normalise(vector: dict[int, float]) -> dict[int, float]:
    norm = math.sqrt(sum(value * value for value in vector.values()))
    if norm == 0.0:
        return {}
    return {index: value / norm for index, value in vector.items()}


def cosine_similarity(left: dict[int, float], right: dict[int, float]) -> float:
    """Cosine similarity of two L2-normalised sparse vectors.

    Because both inputs are already unit length, this is just the dot
    product — no division needed. Iterating the smaller vector keeps it
    proportional to the shorter document.
    """
    if not left or not right:
        return 0.0
    if len(left) > len(right):
        left, right = right, left
    return sum(value * right.get(index, 0.0) for index, value in left.items())
