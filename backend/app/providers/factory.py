"""Provider factory: select the model provider from config (ADR-008, docs/08)."""

from __future__ import annotations

from app.config import settings
from app.providers.base import Provider
from app.providers.mock import MockProvider
from app.providers.openai_compatible import OpenAICompatibleProvider


def build_provider() -> Provider:
    """Return the configured provider. `mock` keeps dev/tests offline."""
    if settings.provider_kind == "openai_compatible":
        return OpenAICompatibleProvider(
            base_url=settings.provider_base_url,
            api_key=settings.provider_api_key,
            model=settings.provider_model,
            timeout=float(settings.provider_timeout_seconds),
        )
    return MockProvider()
