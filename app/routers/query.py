"""Query route: ask a question, get an answer with citations."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user, get_owned_workspace, rate_limit
from app.models import User, Workspace
from app.schemas import Citation, QueryRequest, QueryResponse, RetrievedChunk
from app.services import answer_question

router = APIRouter(prefix="/api/workspaces/{workspace_id}", tags=["query"])


@router.post(
    "/query",
    response_model=QueryResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(rate_limit)],
)
def query(
    payload: QueryRequest,
    workspace: Workspace = Depends(get_owned_workspace),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> QueryResponse:
    """Ask a question about this workspace's documents.

    Returns 200 whether or not the question was answerable. An abstention is
    a valid, successful result — not an error — and the response says why it
    abstained so the caller can act on it.
    """
    result = answer_question(
        db,
        workspace,
        question=payload.question.strip(),
        user_id=user.id,
        top_k=payload.top_k,
    )

    return QueryResponse(
        question=payload.question.strip(),
        answered=result.answered,
        answer=result.answer,
        abstain_reason=result.abstain_reason,
        coverage=result.coverage,
        coverage_threshold=result.coverage_threshold,
        missing_terms=result.missing_terms,
        citations=[
            Citation(
                marker=citation.marker,
                document_id=citation.document_id,
                document_title=citation.document_title,
                filename=citation.filename,
                chunk_ordinal=citation.chunk_ordinal,
                start_char=citation.start_char,
                end_char=citation.end_char,
                quote=citation.quote,
                score=citation.score,
            )
            for citation in result.citations
        ],
        retrieved=[
            RetrievedChunk(
                document_id=scored.chunk.document_id,
                document_title=scored.chunk.document_title,
                filename=scored.chunk.filename,
                chunk_ordinal=scored.chunk.ordinal,
                start_char=scored.chunk.start_char,
                end_char=scored.chunk.end_char,
                text=scored.chunk.text,
                bm25_score=round(scored.bm25_score, 4),
                vector_score=round(scored.vector_score, 4),
                fused_score=round(scored.fused_score, 6),
                bm25_rank=scored.bm25_rank,
                vector_rank=scored.vector_rank,
            )
            for scored in result.retrieved
        ],
        latency_ms=result.latency_ms,
        synthesis=result.synthesis,
    )
