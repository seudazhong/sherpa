"""Layered application settings.

Full config + secret contract: docs/contracts/config-and-secrets.md (ADR-019).
Secrets are read from the environment and MUST NOT be logged.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    app_name: str = "sherpa"
    environment: str = "dev"
    log_level: str = "INFO"

    # Infra (sane localhost defaults for `uv run`; compose injects real values)
    database_url: str = "postgresql+asyncpg://sherpa:sherpa@localhost:5432/sherpa"
    redis_url: str = "redis://localhost:6379/0"

    # Model provider — mock by default; real provider is an open §5 decision.
    provider: str = "mock"

    # App/session auth (contract: docs/contracts/config-and-secrets.md §2).
    # Dev defaults keep `uv run`/tests working; production MUST override via env
    # and set session_cookie_secure=true. These values are never logged.
    app_secret: str = "dev-insecure-app-secret-change-me-32bytes+"
    session_cookie_name: str = "sherpa_session"
    session_ttl_seconds: int = 604_800
    session_cookie_secure: bool = False

    # Single owner credential (v1 is single-user). Overridden by env in prod.
    owner_email: str = "owner@localhost"
    owner_password: str = "sherpa-dev-password"

    # Credential encryption (contract: config-and-secrets.md §3, ADR-019).
    # KEK is base64 of exactly 32 bytes (AES-256). The dev default keeps tests
    # runnable; production MUST override KEK/KEK_ID via env and never reuse this.
    kek: str = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="
    kek_id: str = "env-dev"
    kek_key_version: int = 1
    kek_previous_keys: str = "{}"


settings = Settings()
