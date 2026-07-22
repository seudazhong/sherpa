"""Channel config + per-thread state models (ADR-028; migration 0018).

``ChannelConfig`` is one row per (tenant, user, channel) holding non-secret config
(appid, enabled, owner external id) plus a sealed secret (AES-256-GCM under the
KEK, see ``app/security/channel_secret.py``). ``ChannelThreadState`` remembers the
last inbound external message id per session, so an official QQ reply can be sent
as a passive reply (``post_c2c_message`` needs the triggering ``msg_id``).
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import Boolean, DateTime, Integer, LargeBinary, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ChannelConfig(Base):
    __tablename__ = "channel_configs"

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    channel: Mapped[str] = mapped_column(Text, primary_key=True)
    kind: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, server_default="false")
    app_id: Mapped[str] = mapped_column(Text, server_default="")
    owner_external_id: Mapped[str] = mapped_column(Text, server_default="")
    # Sealed secret (AES-256-GCM under the KEK). Empty secret_enc => not set.
    secret_enc: Mapped[bytes] = mapped_column(LargeBinary, default=b"")
    secret_nonce: Mapped[bytes] = mapped_column(LargeBinary, default=b"")
    kek_id: Mapped[str] = mapped_column(Text, server_default="")
    key_version: Mapped[int] = mapped_column(Integer, server_default="0")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ChannelThreadState(Base):
    __tablename__ = "channel_thread_state"

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    last_inbound_msg_id: Mapped[str] = mapped_column(Text, server_default="")
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
