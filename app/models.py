"""SQLAlchemy models.

The schema is deliberately small: a user owns workspaces, a workspace holds
documents, a document is split into chunks, and every question is logged so
the dashboard can show what people actually ask.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, utcnow


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    workspaces: Mapped[list[Workspace]] = relationship(
        back_populates="owner", cascade="all, delete-orphan"
    )


class Workspace(Base):
    """A knowledge base. Documents and questions are scoped to one."""

    __tablename__ = "workspaces"
    __table_args__ = (UniqueConstraint("owner_id", "slug", name="uq_workspace_owner_slug"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # Bumped whenever a document is added or removed. The in-memory index
    # cache compares this to decide whether it has to rebuild, which is far
    # cheaper than hashing every chunk on every request.
    index_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    owner: Mapped[User] = relationship(back_populates="workspaces")
    documents: Mapped[list[Document]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True, nullable=False
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(120), nullable=False, default="text/plain")
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # The full extracted text. Kept because citations are character offsets
    # into this exact string — without it a citation could not be verified.
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    workspace: Mapped[Workspace] = relationship(back_populates="documents")
    chunks: Mapped[list[Chunk]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class Chunk(Base):
    """A slice of a document, with the offsets that produced it."""

    __tablename__ = "chunks"
    __table_args__ = (
        Index("ix_chunks_workspace_ordinal", "workspace_id", "document_id", "ordinal"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True, nullable=False
    )
    workspace_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    start_char: Mapped[int] = mapped_column(Integer, nullable=False)
    end_char: Mapped[int] = mapped_column(Integer, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    document: Mapped[Document] = relationship(back_populates="chunks")


class QueryLog(Base):
    """One row per question asked, answered or not.

    Abstentions are logged too — a knowledge base that refuses to answer is
    telling you something about the corpus, and that is worth measuring.
    """

    __tablename__ = "query_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    coverage: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
