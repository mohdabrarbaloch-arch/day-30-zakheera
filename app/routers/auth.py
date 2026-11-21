"""Authentication routes: register, log in, current user."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user, rate_limit
from app.models import User
from app.schemas import TokenOut, UserCreate, UserLogin, UserOut
from app.security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=TokenOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit)],
)
def register(payload: UserCreate, db: Session = Depends(get_db)) -> TokenOut:
    email = payload.email.lower().strip()

    existing = db.scalar(select(User).where(User.email == email))
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists",
        )

    # The very first account becomes the admin. Every later one does not.
    # Checking the count rather than a flag means a fresh install has an
    # administrator without a hardcoded account or a seed step.
    user_count = db.scalar(select(func.count(User.id))) or 0

    user = User(
        email=email,
        password_hash=hash_password(payload.password),
        display_name=payload.display_name.strip() or email.split("@")[0],
        is_admin=user_count == 0,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_access_token(str(user.id))
    return TokenOut(access_token=token, user=UserOut.model_validate(user))


@router.post("/login", response_model=TokenOut, dependencies=[Depends(rate_limit)])
def login(payload: UserLogin, db: Session = Depends(get_db)) -> TokenOut:
    email = payload.email.lower().strip()
    user = db.scalar(select(User).where(User.email == email))

    # One message for both "no such account" and "wrong password". Telling
    # them apart turns the login form into an account-enumeration oracle.
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )

    token = create_access_token(str(user.id))
    return TokenOut(access_token=token, user=UserOut.model_validate(user))


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> UserOut:
    return UserOut.model_validate(user)
