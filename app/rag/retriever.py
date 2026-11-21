"""Hybrid retrieval: BM25 and vector search, fused with Reciprocal Rank Fusion.

Why RRF rather than adding the scores together: BM25 scores are unbounded and
depend on corpus statistics, while cosine similarity is bounded to [0, 1].
Adding them means whichever happens to have the larger scale wins, and that
scale shifts as the corpus grows. RRF throws the magnitudes away and keeps
only the ordering, which is the part both rankers are actually good at.

    RRF(d) = sum over rankers of 1 / (k + rank(d))

``k`` (60 by convention) damps the influence of the very top ranks so that a
document ranked first by one ranker and tenth by the other does not
automatically beat one ranked third by both.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.rag.index import IndexedChunk, SearchIndex
from app.rag.tokenizer import tokenize

RRF_K = 60


@dataclass(slots=True)
class ScoredChunk:
    chunk: IndexedChunk
    bm25_score: float
    vector_score: float
    fused_score: float
    bm25_rank: int | None
    vector_rank: int | None


def _ranked(scores: dict[int, float], limit: int) -> list[tuple[int, float]]:
    """Sort by score descending, breaking ties by position for determinism.

    Without the tie-break, two chunks with identical scores could swap order
    between runs and produce a different answer for the same question — which
    would make the whole system feel unreliable.
    """
    ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    return ordered[:limit]


def retrieve(
    index: SearchIndex,
    question: str,
    *,
    top_k: int = 8,
    candidate_pool: int | None = None,
) -> list[ScoredChunk]:
    """Return the best chunks for ``question``, best first.

    ``candidate_pool`` is how many results each ranker contributes before
    fusion. It defaults to four times ``top_k`` so that a chunk ranked
    poorly by one ranker but well by the other still has a chance to surface.
    """
    if index.is_empty:
        return []

    pool = candidate_pool or max(top_k * 4, 20)
    query_terms = tokenize(question)

    bm25_scores = index.bm25_scores(query_terms)
    vector_scores = index.vector_scores(question)

    bm25_ranked = _ranked(bm25_scores, pool)
    vector_ranked = _ranked(vector_scores, pool)

    bm25_rank_of = {position: rank for rank, (position, _) in enumerate(bm25_ranked, start=1)}
    vector_rank_of = {position: rank for rank, (position, _) in enumerate(vector_ranked, start=1)}

    fused: dict[int, float] = {}
    for position, rank in bm25_rank_of.items():
        fused[position] = fused.get(position, 0.0) + 1.0 / (RRF_K + rank)
    for position, rank in vector_rank_of.items():
        fused[position] = fused.get(position, 0.0) + 1.0 / (RRF_K + rank)

    ordered = sorted(fused.items(), key=lambda item: (-item[1], item[0]))[:top_k]

    return [
        ScoredChunk(
            chunk=index.chunks[position],
            bm25_score=bm25_scores.get(position, 0.0),
            vector_score=vector_scores.get(position, 0.0),
            fused_score=score,
            bm25_rank=bm25_rank_of.get(position),
            vector_rank=vector_rank_of.get(position),
        )
        for position, score in ordered
    ]
