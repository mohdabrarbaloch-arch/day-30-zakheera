"""Document routes: upload a file, paste text, read, delete."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.deps import get_owned_workspace, rate_limit
from app.models import Document, Workspace
from app.rag.extractors import UnsupportedFileTypeError, extract
from app.schemas import DocumentDetail, DocumentOut, TextIngest
from app.services import delete_document, ingest_document

router = APIRouter(prefix="/api/workspaces/{workspace_id}/documents", tags=["documents"])


@router.post(
    "/upload",
    response_model=DocumentOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit)],
)
async def upload_document(
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    workspace: Workspace = Depends(get_owned_workspace),
    db: Session = Depends(get_db),
) -> DocumentOut:
    settings = get_settings()
    filename = file.filename or "untitled"

    data = await file.read()

    if not data:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="The uploaded file is empty"
        )

    # Checked after reading, because UploadFile has no reliable size before
    # that. The limit is enforced here rather than by the server so the error
    # is a clear 413 instead of a dropped connection.
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                f"File is {len(data):,} bytes; the limit is "
                f"{settings.max_upload_bytes:,} bytes."
            ),
        )

    try:
        extraction = extract(filename, data)
    except UnsupportedFileTypeError as exc:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=str(exc)
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    if not extraction.text.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=extraction.warning or "No text could be extracted from this file.",
        )

    document = ingest_document(
        db,
        workspace,
        filename=filename,
        title=(title or filename).strip()[:255],
        content=extraction.text,
        mime_type=extraction.mime_type,
        size_bytes=len(data),
    )

    return DocumentOut.model_validate(document)


@router.post(
    "/text",
    response_model=DocumentOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit)],
)
def ingest_text(
    payload: TextIngest,
    workspace: Workspace = Depends(get_owned_workspace),
    db: Session = Depends(get_db),
) -> DocumentOut:
    settings = get_settings()

    if len(payload.content.encode("utf-8")) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Text exceeds the {settings.max_upload_bytes:,} byte limit.",
        )

    document = ingest_document(
        db,
        workspace,
        filename=f"{payload.title.strip()[:80]}.txt",
        title=payload.title.strip()[:255],
        content=payload.content,
        mime_type="text/plain",
        size_bytes=len(payload.content.encode("utf-8")),
    )

    return DocumentOut.model_validate(document)


@router.get("", response_model=list[DocumentOut])
def list_documents(
    workspace: Workspace = Depends(get_owned_workspace), db: Session = Depends(get_db)
) -> list[DocumentOut]:
    documents = db.scalars(
        select(Document)
        .where(Document.workspace_id == workspace.id)
        .order_by(Document.created_at.desc())
    ).all()
    return [DocumentOut.model_validate(document) for document in documents]


@router.get("/{document_id}", response_model=DocumentDetail)
def get_document(
    document_id: int,
    workspace: Workspace = Depends(get_owned_workspace),
    db: Session = Depends(get_db),
) -> DocumentDetail:
    document = db.scalar(
        select(Document).where(Document.id == document_id, Document.workspace_id == workspace.id)
    )
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    preview = document.content[:1200]
    if len(document.content) > 1200:
        preview += "…"

    return DocumentDetail(
        **DocumentOut.model_validate(document).model_dump(),
        preview=preview,
    )


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def remove_document(
    document_id: int,
    workspace: Workspace = Depends(get_owned_workspace),
    db: Session = Depends(get_db),
):
    document = db.scalar(
        select(Document).where(Document.id == document_id, Document.workspace_id == workspace.id)
    )
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    delete_document(db, workspace, document)
