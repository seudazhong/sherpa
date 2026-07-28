"""Provider factory: select the model provider from config (ADR-008, docs/08).

``build_provider`` returns the env-configured provider (offline/mock or the single
``PROVIDER_*`` source). ``build_from_config`` constructs an adapter by ``kind`` for a
user-configured ``model_providers`` row (ADR-041); the DB resolution that picks which row
lives in the service/loop layer (MP.3).
"""

from __future__ import annotations

from app.config import settings
from app.providers.anthropic import AnthropicProvider
from app.providers.base import Provider
from app.providers.gemini import GeminiProvider
from app.providers.mock import MockProvider
from app.providers.openai_compatible import OpenAICompatibleProvider


def build_provider() -> Provider:
    """Return the env-configured provider. `mock` keeps dev/tests offline."""
    if settings.provider_kind == "openai_compatible":
        return OpenAICompatibleProvider(
            base_url=settings.provider_base_url,
            api_key=settings.provider_api_key,
            model=settings.provider_model,
            timeout=float(settings.provider_timeout_seconds),
        )
    return MockProvider()


def build_from_config(
    *,
    kind: str,
    api_key: str,
    model: str,
    base_url: str | None = None,
    timeout: float | None = None,
) -> Provider:
    """Build a provider adapter for a user-configured source (ADR-041). `kind` selects the
    wire adapter; `openai_compatible` covers OpenAI/DeepSeek/Qwen/Moonshot/xAI/OpenRouter/
    Ollama/… via `base_url`, `anthropic`/`gemini` are native."""
    t = float(timeout if timeout is not None else settings.provider_timeout_seconds)
    if kind == "anthropic":
        return AnthropicProvider(api_key=api_key, model=model, base_url=base_url, timeout=t)
    if kind == "gemini":
        return GeminiProvider(api_key=api_key, model=model, base_url=base_url, timeout=t)
    return OpenAICompatibleProvider(
        base_url=base_url or settings.provider_base_url,
        api_key=api_key,
        model=model,
        timeout=t,
    )
