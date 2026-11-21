"""Database engine, session factory and the declarative base."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

settings = get_settings()

# check_same_thread is a SQLite-only concern: FastAPI hands requests to a
# thread pool, and SQLite refuses cross-thread use by default. Postgres needs
# no such flag, so it is passed conditionally.
_connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}

engine = create_engine(
    settings.database_url,
    connect_args=_connect_args,
    pool_pre_ping=True,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a session that is always closed."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def utcnow() -> datetime:
    """Timezone-aware UTC now.

    SQLite has no native timezone support and hands back naive datetimes, so
    every comparison against an aware value would raise TypeError. Storing
    aware values and normalising on read (see ``as_utc``) keeps both backends
    behaving the same way.
    """
    return datetime.now(UTC)


def as_utc(value: datetime | None) -> datetime | None:
    """Attach UTC to a naive datetime coming back from SQLite."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
