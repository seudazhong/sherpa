"""GitHub connection model (ADR-038, W2b; migration 0029).

A ``github_connections`` row is a GitHub credential record (one active connection per
owner in v1). It reuses the connectors AEAD column shape (ADR-019): the token is sealed
DIRECTLY under the active KEK and decrypted ONLY by the import worker at the connector
boundary (:mod:`app.security.github_token`). ``auth_kind`` is extensible: ``pat`` =
fine-grained PAT (``contents:read``, first version); ``app_installation`` = GitHub App
installation token (forward path, no schema change). Every table carries ``tenant_id`` +
composite tenant-scoped keys (ADR-015).
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import DateTime, Integer, LargeBinary, SmallInteger, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class GithubConnection(Base):
    __tablename__ = "github_connections"

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    auth_kind: Mapped[str] = mapped_column(Text)
    account_login: Mapped[str | None] = mapped_column(Text)
    installation_id: Mapped[str | None] = mapped_column(Text)
    token_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    nonce: Mapped[bytes | None] = mapped_column(LargeBinary)
    kek_id: Mapped[str | None] = mapped_column(Text)
    key_version: Mapped[int | None] = mapped_column(Integer)
    token_algorithm: Mapped[str | None] = mapped_column(Text)
    aad_version: Mapped[int | None] = mapped_column(SmallInteger)
    scopes: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    status: Mapped[str] = mapped_column(Text, server_default="pending")
    last_error_redacted: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
