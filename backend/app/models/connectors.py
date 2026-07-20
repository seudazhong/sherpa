"""Gmail connector model (docs/06, contracts/data-model.md).

Token columns hold a direct AES-256-GCM envelope under the active KEK; CHECKs
(all-or-none, nonce=12, algorithm) live in the migration. connector_items lands
with M2 #15.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    LargeBinary,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Connector(Base):
    __tablename__ = "connectors"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["tenant_id", "user_id"], ["users.tenant_id", "users.id"], ondelete="RESTRICT"
        ),
        UniqueConstraint(
            "tenant_id", "kind", "external_account_id", name="uq_connectors_external_account"
        ),
        UniqueConstraint("tenant_id", "channel_installation_id", name="uq_connectors_installation"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    kind: Mapped[str] = mapped_column(Text)
    channel_installation_id: Mapped[str] = mapped_column(Text)
    external_account_id: Mapped[str] = mapped_column(Text)
    token_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    nonce: Mapped[bytes | None] = mapped_column(LargeBinary)
    kek_id: Mapped[str | None] = mapped_column(Text)
    key_version: Mapped[int | None] = mapped_column(Integer)
    token_algorithm: Mapped[str | None] = mapped_column(Text)
    aad_version: Mapped[int | None] = mapped_column(SmallInteger)
    scopes: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    status: Mapped[str] = mapped_column(Text, server_default="pending_oauth")
    cursor: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    refresh_version: Mapped[int] = mapped_column(BigInteger, server_default="0")
    last_sync_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_redacted: Mapped[str | None] = mapped_column(Text)
    rotated_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ConnectorItem(Base):
    __tablename__ = "connector_items"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["tenant_id", "connector_id"],
            ["connectors.tenant_id", "connectors.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "tenant_id",
            "connector_id",
            "provider_item_id",
            "revision",
            name="uq_connector_items_revision",
        ),
        UniqueConstraint("tenant_id", "id", "revision", name="uq_connector_items_id_revision"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    connector_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    provider_item_id: Mapped[str] = mapped_column(Text)
    revision: Mapped[str] = mapped_column(Text)
    provider_thread_id: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    content_digest: Mapped[bytes] = mapped_column(LargeBinary)
    content_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    is_latest: Mapped[bool] = mapped_column(Boolean, server_default="true")
    deletion_state: Mapped[str] = mapped_column(Text, server_default="present")
    source_deleted_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
