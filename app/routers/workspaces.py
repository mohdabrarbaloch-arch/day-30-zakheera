"""Workspace routes: create, list, read, delete, stats."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user, get_owned_workspace, rate_limit
from app.models import User, Workspace
from app.schemas import WorkspaceCreate, WorkspaceOut, WorkspaceStats
from app.services import index_cache, workspace_stats

router = APIRouter(prefix="/api/workspaces", tags=["workspaces"])

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(name: str) -> str:
    slug = _SLUG_RE.sub("-", name.lower()).strip("-")
    return slug[:60] or "workspace"


@router.get("", response_model=list[WorkspaceOut])
def list_workspaces(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> list[WorkspaceOut]:
    workspaces = db.scalars(
        select(Workspace).where(Workspace.owner_id == user.id).order_by(Workspace.created_at.desc())
    ).all()
    return [WorkspaceOut.model_validate(workspace) for workspace in workspaces]


@router.post(
    "",
    response_model=WorkspaceOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit)],
)
def create_workspace(
    payload: WorkspaceCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> WorkspaceOut:
    base = _slugify(payload.name)

    # Slugs are unique per owner, so two workspaces called "Handbook" get
    # "handbook" and "handbook-2" rather than a 500 from the constraint.
    slug = base
    suffix = 2
    while db.scalar(
        select(Workspace.id).where(Workspace.owner_id == user.id, Workspace.slug == slug)
    ):
        slug = f"{base}-{suffix}"
        suffix += 1

    workspace = Workspace(
        owner_id=user.id,
        name=payload.name.strip(),
        slug=slug,
        description=payload.description.strip(),
    )
    db.add(workspace)
    db.commit()
    db.refresh(workspace)

    return WorkspaceOut.model_validate(workspace)


@router.get("/{workspace_id}", response_model=WorkspaceOut)
def get_workspace(workspace: Workspace = Depends(get_owned_workspace)) -> WorkspaceOut:
    return WorkspaceOut.model_validate(workspace)


@router.get("/{workspace_id}/stats", response_model=WorkspaceStats)
def get_stats(
    workspace: Workspace = Depends(get_owned_workspace), db: Session = Depends(get_db)
) -> WorkspaceStats:
    stats = workspace_stats(db, workspace)
    return WorkspaceStats(workspace=WorkspaceOut.model_validate(workspace), **stats)


@router.delete("/{workspace_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def delete_workspace(
    workspace: Workspace = Depends(get_owned_workspace), db: Session = Depends(get_db)
):
    workspace_id = workspace.id
    db.delete(workspace)
    db.commit()
    index_cache.invalidate(workspace_id)


@router.post("/{workspace_id}/reindex", response_model=WorkspaceOut)
def reindex(
    workspace: Workspace = Depends(get_owned_workspace), db: Session = Depends(get_db)
) -> WorkspaceOut:
    """Force a rebuild of the in-memory index.

    Not needed in normal use — the version counter handles it — but useful
    after a manual database edit, and it makes the cache behaviour testable.
    """
    workspace.index_version += 1
    db.commit()
    db.refresh(workspace)
    index_cache.invalidate(workspace.id)
    return WorkspaceOut.model_validate(workspace)


@router.get("/{workspace_id}/documents", response_model=list)
def list_documents(
    workspace: Workspace = Depends(get_owned_workspace), db: Session = Depends(get_db)
) -> list:
    from app.models import Document
    from app.schemas import DocumentOut

    documents = db.scalars(
        select(Document)
        .where(Document.workspace_id == workspace.id)
        .order_by(Document.created_at.desc())
    ).all()
    return [DocumentOut.model_validate(document) for document in documents]


@router.get("/{workspace_id}/questions", response_model=list)
def list_questions(
    workspace: Workspace = Depends(get_owned_workspace),
    db: Session = Depends(get_db),
    limit: int = 20,
) -> list:
    from app.models import QueryLog
    from app.schemas import QueryLogOut

    if not 1 <= limit <= 100:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="limit must be between 1 and 100",
        )

    logs = db.scalars(
        select(QueryLog)
        .where(QueryLog.workspace_id == workspace.id)
        .order_by(QueryLog.created_at.desc())
        .limit(limit)
    ).all()
    return [QueryLogOut.model_validate(log) for log in logs]
