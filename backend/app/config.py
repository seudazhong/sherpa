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


settings = Settings()
