"""Shared test fixtures.

Each test gets a fresh in-memory SQLite database and a fresh app, so tests
cannot leak state into each other. The settings cache is cleared first
because ``get_settings`` is memoised and would otherwise hand every test the
same configuration.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Must be set before app.config is imported anywhere.
os.environ.setdefault("SECRET_KEY", "test-secret-key-long-enough-to-pass-validation-1234567890")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")


@pytest.fixture()
def client():
    """A TestClient backed by a fresh in-memory database."""
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app import database
    from app.config import get_settings

    get_settings.cache_clear()

    # The rate limiter is a module-level singleton keyed by client IP. Every
    # test uses the same TestClient host, so without this reset the suite
    # would exhaust the per-minute budget partway through and start getting
    # 429s — which looks like a broken endpoint rather than a working limiter.
    from app import deps

    deps._limiter = None

    # StaticPool keeps one connection alive for the whole test, which is what
    # makes an in-memory SQLite database persist across requests.
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    database.engine = engine
    database.SessionLocal = sessionmaker(
        bind=engine, autoflush=False, expire_on_commit=False, future=True
    )

    from app.main import create_app

    app = create_app()
    database.Base.metadata.create_all(bind=engine)

    with TestClient(app) as test_client:
        yield test_client

    database.Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture()
def auth_client(client):
    """A client with a registered user, plus that user's token."""
    response = client.post(
        "/api/auth/register",
        json={
            "email": "tester@example.com",
            "password": "correct-horse-battery",
            "display_name": "Tester",
        },
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    return client, payload["access_token"], payload["user"]


@pytest.fixture()
def workspace(auth_client):
    """A workspace owned by the authenticated test user."""
    client, token, _ = auth_client
    response = client.post(
        "/api/workspaces",
        json={"name": "Test Base", "description": "for tests"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 201, response.text
    return client, token, response.json()
