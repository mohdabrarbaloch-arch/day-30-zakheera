"""Application configuration, loaded from the environment.

Every value here has a working default for local development except
``SECRET_KEY``, which is deliberately not defaulted to anything guessable.
"""

from __future__ import annotations

import secrets
import sys
from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MIN_SECRET_LENGTH = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Core ---------------------------------------------------------------
    secret_key: str = ""
    environment: str = "development"
    access_token_expire_minutes: int = 720
    database_url: str = "sqlite:///./zakheera.db"
    cors_origins: str = "http://localhost:8000,http://127.0.0.1:8000"
    max_upload_bytes: int = 5_000_000
    rate_limit_per_minute: int = 60

    # --- Retrieval ----------------------------------------------------------
    chunk_target_chars: int = 900
    chunk_overlap_chars: int = 150
    retrieval_top_k: int = 8
    abstain_coverage_threshold: float = 0.5

    # --- Optional LLM synthesis --------------------------------------------
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    @field_validator("environment")
    @classmethod
    def _known_environment(cls, value: str) -> str:
        normalised = value.strip().lower()
        if normalised not in {"development", "production", "test"}:
            raise ValueError("environment must be development, production or test")
        return normalised

    @field_validator("abstain_coverage_threshold")
    @classmethod
    def _sane_threshold(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("abstain_coverage_threshold must be between 0 and 1")
        return value

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def llm_enabled(self) -> bool:
        return bool(self.openai_api_key.strip())


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings, validating the secret key once.

    In production a missing or short ``SECRET_KEY`` is fatal: booting with a
    guessable signing key means anyone can mint a token for any account. In
    development we generate an ephemeral key so a fresh clone runs without
    setup, and say so loudly — tokens stop working when the process restarts.
    """
    settings = Settings()

    if len(settings.secret_key) >= MIN_SECRET_LENGTH:
        return settings

    if settings.is_production:
        raise RuntimeError(
            "SECRET_KEY is missing or shorter than "
            f"{MIN_SECRET_LENGTH} characters. Generate one with:\n"
            '  python -c "import secrets; print(secrets.token_urlsafe(48))"'
        )

    settings.secret_key = secrets.token_urlsafe(48)
    print(
        "WARNING: SECRET_KEY is not set, so an ephemeral one was generated. "
        "Access tokens will stop working when this process restarts. "
        "Set SECRET_KEY in .env to keep sessions across restarts.",
        file=sys.stderr,
    )
    return settings
