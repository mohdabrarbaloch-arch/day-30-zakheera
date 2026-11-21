"""Admin routes: platform-wide counters and the most-asked questions."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import require_admin
from app.models import Chunk, Document, QueryLog, User, Workspace
from app.schemas import AdminStats

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/stats", response_model=AdminStats)
def stats(db: Session = Depends(get_db), _: User = Depends(require_admin)) -> AdminStats:
    user_count = db.scalar(select(func.count(User.id))) or 0
    workspace_count = db.scalar(select(func.count(Workspace.id))) or 0
    document_count = db.scalar(select(func.count(Document.id))) or 0
    chunk_count = db.scalar(select(func.count(Chunk.id))) or 0
    question_count = db.scalar(select(func.count(QueryLog.id))) or 0
    answered_count = (
        db.scalar(select(func.count(QueryLog.id)).where(QueryLog.answered.is_(True))) or 0
    )

    # Grouping on the question text is crude — "how do I get a refund" and
    # "How do I get a refund?" are different rows — but it is honest about
    # what it measures, and normalising would need a clustering step this
    # project does not have.
    top = db.execute(
        select(QueryLog.question, func.count(QueryLog.id).label("count"))
        .group_by(QueryLog.question)
        .order_by(func.count(QueryLog.id).desc())
        .limit(10)
    ).all()

    return AdminStats(
        user_count=user_count,
        workspace_count=workspace_count,
        document_count=document_count,
        chunk_count=chunk_count,
        question_count=question_count,
        answer_rate=round(answered_count / question_count, 4) if question_count else 0.0,
        top_questions=[{"question": row[0], "count": row[1]} for row in top],
    )
