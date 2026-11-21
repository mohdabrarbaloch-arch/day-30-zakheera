"""The searchable index: an inverted index for BM25 plus TF-IDF vectors.

Two rankers over the same chunks, because they fail differently:

* **BM25** is a lexical ranker. It rewards rare terms and saturates on
  repetition, which makes it excellent at exact identifiers — a policy
  number, a product code, a person's name. It is bad at paraphrase.
* **TF-IDF cosine** is a vector ranker. It compares whole-vector direction,
  so it tolerates word order and some variation, but it dilutes a rare term
  among many common ones.

Fusing them (see ``retriever``) beats either alone, and the UI shows both
component scores so a surprising result can be explained.

The index is built in memory from the database and cached per workspace,
keyed by the workspace's ``index_version``. Rebuilding is O(chunks) and only
happens when a document is added or removed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from app.rag.embedder import TfidfModel, cosine_similarity
from app.rag.tokenizer import tokenize

# BM25 parameters. k1 controls term-frequency saturation (how quickly more
# occurrences stop helping); b controls length normalisation. These are the
# standard defaults from the literature and are not worth tuning for a corpus
# this size.
BM25_K1 = 1.5
BM25_B = 0.75


@dataclass(slots=True)
class IndexedChunk:
    """A chunk as the index sees it, with its provenance intact."""

    chunk_id: int
    document_id: int
    document_title: str
    filename: str
    ordinal: int
    text: str
    start_char: int
    end_char: int
    tokens: list[str] = field(default_factory=list)
    term_frequencies: dict[str, int] = field(default_factory=dict)
    vector: dict[int, float] = field(default_factory=dict)

    @property
    def length(self) -> int:
        return len(self.tokens)


@dataclass(slots=True)
class SearchIndex:
    """Everything needed to answer a question about one workspace."""

    chunks: list[IndexedChunk] = field(default_factory=list)
    # term -> {chunk position -> term frequency}
    postings: dict[str, dict[int, int]] = field(default_factory=dict)
    model: TfidfModel = field(default_factory=TfidfModel)
    average_length: float = 0.0
    version: int = -1

    @property
    def is_empty(self) -> bool:
        return not self.chunks

    def build(self, raw_chunks: list[dict], version: int = 0) -> SearchIndex:
        """Build the index from plain dicts.

        Taking dicts rather than ORM objects keeps this module free of any
        database import, which is what makes it testable without a session.
        """
        self.version = version
        self.chunks = []
        self.postings = {}

        texts: list[str] = []
        for raw in raw_chunks:
            tokens = tokenize(raw["text"])
            if not tokens:
                # A chunk with no indexable tokens (a page of punctuation, a
                # scanned PDF that extracted to nothing) can never be
                # retrieved. Keeping it would only dilute the average length.
                continue

            frequencies: dict[str, int] = {}
            for token in tokens:
                frequencies[token] = frequencies.get(token, 0) + 1

            self.chunks.append(
                IndexedChunk(
                    chunk_id=raw["chunk_id"],
                    document_id=raw["document_id"],
                    document_title=raw["document_title"],
                    filename=raw["filename"],
                    ordinal=raw["ordinal"],
                    text=raw["text"],
                    start_char=raw["start_char"],
                    end_char=raw["end_char"],
                    tokens=tokens,
                    term_frequencies=frequencies,
                )
            )
            texts.append(raw["text"])

        if not self.chunks:
            self.model = TfidfModel()
            self.average_length = 0.0
            return self

        self.average_length = sum(chunk.length for chunk in self.chunks) / len(self.chunks)

        # Inverted index: one pass over the chunks, not one pass per term.
        for position, chunk in enumerate(self.chunks):
            for term, frequency in chunk.term_frequencies.items():
                self.postings.setdefault(term, {})[position] = frequency

        self.model = TfidfModel().fit(texts)
        for chunk in self.chunks:
            chunk.vector = self.model.transform(chunk.text)

        return self

    # --- BM25 ---------------------------------------------------------------
    def idf(self, term: str) -> float:
        """BM25's probabilistic IDF.

        The +0.5 terms are the standard smoothing. Note this differs from the
        TF-IDF model's IDF — BM25's is designed for its own scoring formula,
        and mixing the two would be a subtle bug.
        """
        total = len(self.chunks)
        df = len(self.postings.get(term, {}))
        return math.log(1 + (total - df + 0.5) / (df + 0.5))

    def bm25_scores(self, query_terms: list[str]) -> dict[int, float]:
        """Score every chunk containing at least one query term."""
        scores: dict[int, float] = {}

        for term in query_terms:
            postings = self.postings.get(term)
            if not postings:
                continue

            idf = self.idf(term)
            for position, frequency in postings.items():
                length = self.chunks[position].length
                denominator = frequency + BM25_K1 * (
                    1 - BM25_B + BM25_B * (length / self.average_length)
                )
                contribution = idf * (frequency * (BM25_K1 + 1)) / denominator
                scores[position] = scores.get(position, 0.0) + contribution

        return scores

    # --- Vector -------------------------------------------------------------
    def vector_scores(self, query: str) -> dict[int, float]:
        """Cosine similarity of the query against every chunk."""
        query_vector = self.model.transform(query)
        if not query_vector:
            return {}

        scores: dict[int, float] = {}
        for position, chunk in enumerate(self.chunks):
            similarity = cosine_similarity(query_vector, chunk.vector)
            if similarity > 0.0:
                scores[position] = similarity
        return scores

    # --- Corpus coverage ----------------------------------------------------
    def max_observed_idf(self) -> float:
        """The highest IDF among terms that actually occur in the corpus.

        Used to cap the weight of terms that do not occur. See
        ``corpus_coverage`` for why.
        """
        if not self.postings:
            return 0.0
        return max(self.idf(term) for term in self.postings)

    def corpus_coverage(self, query_terms: list[str]) -> tuple[float, list[str]]:
        """IDF-weighted share of query terms that exist anywhere in the corpus.

        This is the abstention signal, and it is deliberately measured
        against the whole corpus rather than the retrieved chunks. If a term
        is absent from the corpus, no amount of retrieval will find it — the
        honest answer is "this knowledge base does not discuss that", not a
        confident paragraph assembled from whatever scored highest.

        Weighting by IDF matters: a question like "what is the refund policy
        for international orders" should not be considered 80% covered just
        because "the", "for" and "is" are common. Rare terms carry the
        meaning, so they carry the weight.

        **Absent terms are capped at the highest IDF actually observed.**
        Without the cap, a term with zero occurrences gets the theoretical
        maximum IDF and can dominate the whole score on its own — which made
        "how long do customers have to request a refund" abstain, because
        "long" happened to be missing while every meaningful term was present.
        We cannot know how rare an unseen term is, so the honest assumption is
        "as rare as the rarest thing we have seen", not "infinitely rare".
        """
        if not query_terms:
            return 0.0, []

        unique_terms = list(dict.fromkeys(query_terms))
        cap = self.max_observed_idf()
        total_weight = 0.0
        matched_weight = 0.0
        missing: list[str] = []

        for term in unique_terms:
            if term in self.postings:
                weight = self.idf(term)
                matched_weight += weight
            else:
                weight = cap
                missing.append(term)
            total_weight += weight

        if total_weight == 0.0:
            return 0.0, missing

        return matched_weight / total_weight, missing
