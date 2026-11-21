"""Request and response models.

Response models are explicit rather than ``dict`` so that a field added to a
database model can never leak into an API response by accident.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


# --- Auth -------------------------------------------------------------------
class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)
    display_name: str = Field(default="", max_length=120)


class UserLogin(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=72)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    display_name: str
    is_admin: bool
    created_at: datetime


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


# --- Workspaces -------------------------------------------------------------
class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)


class WorkspaceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str
    description: str
    created_at: datetime


class WorkspaceStats(BaseModel):
    workspace: WorkspaceOut
    document_count: int
    chunk_count: int
    total_chars: int
    question_count: int
    answered_count: int
    abstained_count: int
    answer_rate: float
    median_latency_ms: int


# --- Documents --------------------------------------------------------------
class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    filename: str
    title: str
    mime_type: str
    size_bytes: int
    char_count: int
    chunk_count: int
    created_at: datetime


class DocumentDetail(DocumentOut):
    preview: str


class TextIngest(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1)


# --- Query ------------------------------------------------------------------
class QueryRequest(BaseModel):
    question: str = Field(min_length=2, max_length=1000)
    top_k: int | None = Field(default=None, ge=1, le=20)


class Citation(BaseModel):
    """A verifiable pointer into a source document.

    ``start_char`` and ``end_char`` index the document's stored text, so a
    client can highlight the exact span rather than trusting a paraphrase.
    """

    marker: int
    document_id: int
    document_title: str
    filename: str
    chunk_ordinal: int
    start_char: int
    end_char: int
    quote: str
    score: float


class RetrievedChunk(BaseModel):
    document_id: int
    document_title: str
    filename: str
    chunk_ordinal: int
    start_char: int
    end_char: int
    text: str
    bm25_score: float
    vector_score: float
    fused_score: float
    bm25_rank: int | None
    vector_rank: int | None


class QueryResponse(BaseModel):
    question: str
    answered: bool
    answer: str
    abstain_reason: str | None
    coverage: float
    coverage_threshold: float
    missing_terms: list[str]
    citations: list[Citation]
    retrieved: list[RetrievedChunk]
    latency_ms: int
    synthesis: str


class QueryLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    question: str
    answered: bool
    coverage: float
    latency_ms: int
    created_at: datetime


# --- Admin ------------------------------------------------------------------
class AdminStats(BaseModel):
    user_count: int
    workspace_count: int
    document_count: int
    chunk_count: int
    question_count: int
    answer_rate: float
    top_questions: list[dict]
