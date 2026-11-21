"""Workspace index cache and the ingest/query services.

The index lives in memory, keyed by workspace id, and is rebuilt only when
the workspace's ``index_version`` changes. That version is bumped inside the
same transaction that adds or removes a document, so a stale index can never
outlive the data it describes.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Chunk, Document, QueryLog, Workspace
from app.rag.chunker import chunk_text
from app.rag.index import SearchIndex
from app.rag.retriever import ScoredChunk, retrieve
from app.rag.synthesizer import Citation, synthesise
from app.rag.tokenizer import tokenize


@dataclass(slots=True)
class Answer:
    answered: bool
    answer: str
    abstain_reason: str | None
    coverage: float
    coverage_threshold: float
    missing_terms: list[str]
    citations: list[Citation]
    retrieved: list[ScoredChunk]
    latency_ms: int
    synthesis: str


class IndexCache:
    """Thread-safe cache of built indexes, one per workspace.

    A lock is used rather than a plain dict because uvicorn runs sync
    endpoints in a thread pool: two requests for the same cold workspace
    would otherwise both build the index and one would win arbitrarily.
    """

    def __init__(self) -> None:
        self._indexes: dict[int, SearchIndex] = {}
        self._lock = threading.Lock()

    def get(self, db: Session, workspace: Workspace) -> SearchIndex:
        with self._lock:
            cached = self._indexes.get(workspace.id)
            if cached is not None and cached.version == workspace.index_version:
                return cached

        # Build outside the lock: it is the slow part, and holding the lock
        # for it would serialise every other workspace's queries too.
        built = self._build(db, workspace)

        with self._lock:
            self._indexes[workspace.id] = built
        return built

    def invalidate(self, workspace_id: int) -> None:
        with self._lock:
            self._indexes.pop(workspace_id, None)

    def _build(self, db: Session, workspace: Workspace) -> SearchIndex:
        rows = db.execute(
            select(
                Chunk.id,
                Chunk.document_id,
                Chunk.ordinal,
                Chunk.text,
                Chunk.start_char,
                Chunk.end_char,
                Document.title,
                Document.filename,
            )
            .join(Document, Document.id == Chunk.document_id)
            .where(Chunk.workspace_id == workspace.id)
            .order_by(Chunk.document_id, Chunk.ordinal)
        ).all()

        raw = [
            {
                "chunk_id": row[0],
                "document_id": row[1],
                "ordinal": row[2],
                "text": row[3],
                "start_char": row[4],
                "end_char": row[5],
                "document_title": row[6],
                "filename": row[7],
            }
            for row in rows
        ]

        return SearchIndex().build(raw, version=workspace.index_version)


index_cache = IndexCache()


def ingest_document(
    db: Session,
    workspace: Workspace,
    *,
    filename: str,
    title: str,
    content: str,
    mime_type: str,
    size_bytes: int,
) -> Document:
    """Store a document and its chunks, then bump the index version."""
    settings = get_settings()

    document = Document(
        workspace_id=workspace.id,
        filename=filename,
        title=title,
        mime_type=mime_type,
        size_bytes=size_bytes,
        char_count=len(content),
        content=content,
    )
    db.add(document)
    db.flush()  # assigns document.id without committing

    spans = chunk_text(
        content,
        target_chars=settings.chunk_target_chars,
        overlap_chars=settings.chunk_overlap_chars,
    )

    for span in spans:
        db.add(
            Chunk(
                document_id=document.id,
                workspace_id=workspace.id,
                ordinal=span.ordinal,
                text=span.text,
                start_char=span.start_char,
                end_char=span.end_char,
                token_count=len(tokenize(span.text)),
            )
        )

    document.chunk_count = len(spans)
    workspace.index_version += 1

    db.commit()
    db.refresh(document)
    index_cache.invalidate(workspace.id)
    return document


def delete_document(db: Session, workspace: Workspace, document: Document) -> None:
    db.delete(document)
    workspace.index_version += 1
    db.commit()
    index_cache.invalidate(workspace.id)


def answer_question(
    db: Session,
    workspace: Workspace,
    *,
    question: str,
    user_id: int,
    top_k: int | None = None,
) -> Answer:
    """Retrieve, decide whether to answer, synthesise, and log."""
    settings = get_settings()
    started = time.perf_counter()

    index = index_cache.get(db, workspace)
    limit = top_k or settings.retrieval_top_k

    if index.is_empty:
        latency = int((time.perf_counter() - started) * 1000)
        _log(db, workspace.id, user_id, question, False, 0.0, latency)
        return Answer(
            answered=False,
            answer="",
            abstain_reason=(
                "This knowledge base has no documents yet. Add a document and ask again."
            ),
            coverage=0.0,
            coverage_threshold=settings.abstain_coverage_threshold,
            missing_terms=[],
            citations=[],
            retrieved=[],
            latency_ms=latency,
            synthesis="extractive",
        )

    query_terms = tokenize(question)
    coverage, missing = index.corpus_coverage(query_terms)
    threshold = settings.abstain_coverage_threshold

    scored = retrieve(index, question, top_k=limit)

    # Abstain before synthesising. Building an answer from chunks that do not
    # contain the question's terms is how a RAG system produces confident
    # nonsense, and it is the single most common failure of these tools.
    if coverage < threshold:
        latency = int((time.perf_counter() - started) * 1000)
        _log(db, workspace.id, user_id, question, False, coverage, latency)
        return Answer(
            answered=False,
            answer="",
            abstain_reason=_abstain_reason(coverage, threshold, missing),
            coverage=round(coverage, 4),
            coverage_threshold=threshold,
            missing_terms=missing[:8],
            citations=[],
            retrieved=scored,
            latency_ms=latency,
            synthesis="extractive",
        )

    synthesis = synthesise(question, scored, index)

    if not synthesis.citations:
        latency = int((time.perf_counter() - started) * 1000)
        _log(db, workspace.id, user_id, question, False, coverage, latency)
        return Answer(
            answered=False,
            answer="",
            abstain_reason=(
                "The corpus mentions these terms, but no sentence in the retrieved "
                "passages actually addresses the question. Try rephrasing it, or add "
                "a document that covers this topic."
            ),
            coverage=round(coverage, 4),
            coverage_threshold=threshold,
            missing_terms=missing[:8],
            citations=[],
            retrieved=scored,
            latency_ms=latency,
            synthesis="extractive",
        )

    latency = int((time.perf_counter() - started) * 1000)
    _log(db, workspace.id, user_id, question, True, coverage, latency)

    return Answer(
        answered=True,
        answer=synthesis.answer,
        abstain_reason=None,
        coverage=round(coverage, 4),
        coverage_threshold=threshold,
        missing_terms=missing[:8],
        citations=synthesis.citations,
        retrieved=scored,
        latency_ms=latency,
        synthesis=synthesis.mode,
    )


def _abstain_reason(coverage: float, threshold: float, missing: list[str]) -> str:
    percent = round(coverage * 100)
    needed = round(threshold * 100)
    if missing:
        listed = ", ".join(f"'{term}'" for term in missing[:4])
        return (
            f"Only {percent}% of this question's meaningful terms appear anywhere in "
            f"the corpus (the threshold is {needed}%). Not found: {listed}. "
            "This knowledge base does not appear to cover it."
        )
    return (
        f"Only {percent}% of this question's terms appear in the corpus, below the "
        f"{needed}% threshold. This knowledge base does not appear to cover it."
    )


def _log(
    db: Session,
    workspace_id: int,
    user_id: int,
    question: str,
    answered: bool,
    coverage: float,
    latency_ms: int,
) -> None:
    db.add(
        QueryLog(
            workspace_id=workspace_id,
            user_id=user_id,
            question=question,
            answered=answered,
            coverage=coverage,
            latency_ms=latency_ms,
        )
    )
    db.commit()


def workspace_stats(db: Session, workspace: Workspace) -> dict:
    document_count = db.scalar(
        select(func.count(Document.id)).where(Document.workspace_id == workspace.id)
    )
    chunk_count = db.scalar(select(func.count(Chunk.id)).where(Chunk.workspace_id == workspace.id))
    total_chars = db.scalar(
        select(func.coalesce(func.sum(Document.char_count), 0)).where(
            Document.workspace_id == workspace.id
        )
    )
    question_count = db.scalar(
        select(func.count(QueryLog.id)).where(QueryLog.workspace_id == workspace.id)
    )
    answered_count = db.scalar(
        select(func.count(QueryLog.id)).where(
            QueryLog.workspace_id == workspace.id, QueryLog.answered.is_(True)
        )
    )

    latencies = db.scalars(
        select(QueryLog.latency_ms)
        .where(QueryLog.workspace_id == workspace.id)
        .order_by(QueryLog.latency_ms)
    ).all()

    median = 0
    if latencies:
        middle = len(latencies) // 2
        median = (
            latencies[middle]
            if len(latencies) % 2
            else (latencies[middle - 1] + latencies[middle]) // 2
        )

    questions = question_count or 0
    answered = answered_count or 0

    return {
        "document_count": document_count or 0,
        "chunk_count": chunk_count or 0,
        "total_chars": total_chars or 0,
        "question_count": questions,
        "answered_count": answered,
        "abstained_count": questions - answered,
        "answer_rate": round(answered / questions, 4) if questions else 0.0,
        "median_latency_ms": median,
    }
