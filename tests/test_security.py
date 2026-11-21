"""Tests for password hashing and JWT handling."""

from __future__ import annotations

import time

import pytest

from app.security import (
    MAX_PASSWORD_BYTES,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


class TestPasswordHashing:
    def test_hash_is_not_the_password(self):
        hashed = hash_password("correct-horse-battery")
        assert hashed != "correct-horse-battery"
        assert hashed.startswith("$2")

    def test_verify_accepts_the_right_password(self):
        hashed = hash_password("correct-horse-battery")
        assert verify_password("correct-horse-battery", hashed) is True

    def test_verify_rejects_the_wrong_password(self):
        hashed = hash_password("correct-horse-battery")
        assert verify_password("wrong-password", hashed) is False

    def test_same_password_hashes_differently(self):
        """A per-hash salt means two identical passwords must not produce the
        same digest."""
        assert hash_password("same-password") != hash_password("same-password")

    def test_unicode_password_round_trips(self):
        hashed = hash_password("پاس ورڈ-2026")
        assert verify_password("پاس ورڈ-2026", hashed) is True

    def test_overlong_password_is_rejected(self):
        """bcrypt silently truncates past 72 bytes, so accepting a longer
        password would mean ignoring part of it."""
        with pytest.raises(ValueError):
            hash_password("x" * (MAX_PASSWORD_BYTES + 1))

    def test_verify_handles_a_malformed_hash(self):
        """A corrupt stored hash must read as 'wrong password', not crash."""
        assert verify_password("anything", "not-a-bcrypt-hash") is False

    def test_verify_handles_an_overlong_candidate(self):
        hashed = hash_password("correct-horse-battery")
        assert verify_password("x" * 200, hashed) is False


class TestTokens:
    def test_round_trip(self):
        token = create_access_token("42")
        payload = decode_access_token(token)
        assert payload is not None
        assert payload["sub"] == "42"

    def test_extra_claims_are_included(self):
        token = create_access_token("42", {"role": "admin"})
        assert decode_access_token(token)["role"] == "admin"

    def test_garbage_token_is_rejected(self):
        assert decode_access_token("not.a.token") is None

    def test_tampered_token_is_rejected(self):
        token = create_access_token("42")
        head, payload, signature = token.split(".")
        tampered = f"{head}.{payload}.{'a' * len(signature)}"
        assert decode_access_token(tampered) is None

    def test_token_signed_with_another_key_is_rejected(self):
        from jose import jwt

        forged = jwt.encode(
            {"sub": "1", "exp": int(time.time()) + 3600}, "a-different-key", algorithm="HS256"
        )
        assert decode_access_token(forged) is None

    def test_expired_token_is_rejected(self):
        from jose import jwt

        from app.config import get_settings

        expired = jwt.encode(
            {"sub": "1", "exp": int(time.time()) - 10},
            get_settings().secret_key,
            algorithm="HS256",
        )
        assert decode_access_token(expired) is None
