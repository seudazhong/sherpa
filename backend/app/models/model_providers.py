"""Model provider registry model (ADR-041; migration 0031).

One user-configured model source (OpenAI / Anthropic / Gemini / DeepSeek / Qwen / …). The
API key is AEAD-sealed under the active KEK (``security/model_provider_key.py``), reusing the
connectors column shape (``token_enc/nonce/kek_id/key_version/token_algorithm/aad_version``);
it is decrypted ONLY at the ``Provider.stream()`` / test-connection boundary. Every table
carries ``tenant_id`` + composite tenant-scoped keys (ADR-015).
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import Boolean, DateTime, Integer, LargeBinary, SmallInteger, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ModelProvider(Base):
    __tablename__ = "model_providers"

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    kind: Mapped[str] = mapped_column(Text)  # openai_compatible | anthropic | gemini
    display_name: Mapped[str] = mapped_column(Text)
    base_url: Mapped[str | None] = mapped_column(Text)
    token_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    nonce: Mapped[bytes | None] = mapped_column(LargeBinary)
    kek_id: Mapped[str | None] = mapped_column(Text)
    key_version: Mapped[int | None] = mapped_column(Integer)
    token_algorithm: Mapped[str | None] = mapped_column(Text)
    aad_version: Mapped[int | None] = mapped_column(SmallInteger)
    models: Mapped[list[str]] = mapped_column(ARRAY(Text))
    default_model: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, server_default="true")
    is_default: Mapped[bool] = mapped_column(Boolean, server_default="false")
    status: Mapped[str] = mapped_column(Text, server_default="pending")
    last_error_redacted: Mapped[str | None] = mapped_column(Text)
    supports_vision: Mapped[bool] = mapped_column(Boolean, server_default="true")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
