"""Shared FastAPI dependencies: current user, workspace access, rate limiting."""

from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import User, Workspace
from app.security import decode_access_token

bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_access_token(credentials.credentials)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    subject = payload.get("sub")
    if not subject:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    try:
        user_id = int(subject)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
        ) from None

    user = db.get(User, user_id)
    if user is None:
        # The token is well-formed but the account is gone. 401, not 404:
        # the caller's credential is what is wrong.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Account no longer exists"
        )
    return user


def get_owned_workspace(
    workspace_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Workspace:
    """Fetch a workspace the caller owns, or 404.

    Deliberately 404 rather than 403 for someone else's workspace: a 403
    confirms the record exists, which leaks the id space to anyone probing.
    """
    workspace = db.scalar(
        select(Workspace).where(Workspace.id == workspace_id, Workspace.owner_id == user.id)
    )
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    return workspace


def require_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return user


class SlidingWindowLimiter:
    """Per-IP request limiter.

    In-process and therefore per-worker: with N uvicorn workers the effective
    limit is N times the configured value. That is fine for a single container
    and is called out in the README; a shared store (Redis) is the fix when
    this runs behind more than one process.
    """

    def __init__(self, limit: int, window_seconds: float = 60.0) -> None:
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> tuple[bool, int]:
        now = time.monotonic()
        bucket = self._hits[key]
        cutoff = now - self.window
        while bucket and bucket[0] < cutoff:
            bucket.popleft()

        if len(bucket) >= self.limit:
            retry_after = max(1, int(self.window - (now - bucket[0])) + 1)
            return False, retry_after

        bucket.append(now)
        return True, 0


_limiter: SlidingWindowLimiter | None = None


def get_limiter() -> SlidingWindowLimiter:
    global _limiter
    if _limiter is None:
        _limiter = SlidingWindowLimiter(get_settings().rate_limit_per_minute)
    return _limiter


def rate_limit(request: Request) -> None:
    """Dependency that enforces the per-IP limit.

    Implemented as a plain dependency rather than a decorator on purpose: a
    decorator wrapping the view function changes the function FastAPI
    introspects, and string annotations then resolve against the wrapper's
    module instead of the router's — which silently turns request bodies into
    query parameters.
    """
    client = request.client.host if request.client else "unknown"
    allowed, retry_after = get_limiter().check(client)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please slow down.",
            headers={"Retry-After": str(retry_after)},
        )
