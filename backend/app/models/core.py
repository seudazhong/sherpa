"""Core transcript/run spine models (tenant/user/identity/session/run/message/part).

Mirrors docs/contracts/data-model.md. Composite (tenant_id, id) PKs and composite
FKs enforce tenant scoping (ADR-015). CHECK constraints and partial/functional
indexes live in the migration (DB-enforced), not duplicated here.
"""

from __future__ import annotations

import datetime
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


def _ts() -> Any:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Tenant(Base):
    __tablename__ = "tenants"

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    slug: Mapped[str] = mapped_column(String(63), unique=True)
    display_name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(Text, server_default="personal")
    created_at = _ts()
    updated_at = _ts()


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    email: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(Text, server_default="active")
    created_at = _ts()
    updated_at = _ts()


class Identity(Base):
    __tablename__ = "identities"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["tenant_id", "user_id"], ["users.tenant_id", "users.id"], ondelete="RESTRICT"
        ),
        UniqueConstraint(
            "tenant_id",
            "channel",
            "channel_installation_id",
            "scope_type",
            "external_scope_id",
            name="uq_identities_canonical_scope",
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    channel: Mapped[str] = mapped_column(Text)
    channel_installation_id: Mapped[str] = mapped_column(Text)
    scope_type: Mapped[str] = mapped_column(Text)
    external_scope_id: Mapped[str] = mapped_column(Text)
    external_actor_id: Mapped[str] = mapped_column(Text)
    verified_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True))
    created_at = _ts()


class Session(Base):
    __tablename__ = "sessions"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["tenant_id", "user_id"], ["users.tenant_id", "users.id"], ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "identity_id"],
            ["identities.tenant_id", "identities.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "umo_key", name="uq_sessions_umo_key"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    identity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    umo_key: Mapped[str] = mapped_column(Text)
    channel: Mapped[str] = mapped_column(Text)
    channel_installation_id: Mapped[str] = mapped_column(Text)
    scope_type: Mapped[str] = mapped_column(Text)
    external_scope_id: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="open")
    admitted_seq: Mapped[int | None] = mapped_column(BigInteger)
    promoted_seq: Mapped[int | None] = mapped_column(BigInteger)
    fence_token: Mapped[int] = mapped_column(BigInteger, server_default="0")
    input_tokens_rollup: Mapped[int] = mapped_column(BigInteger, server_default="0")
    output_tokens_rollup: Mapped[int] = mapped_column(BigInteger, server_default="0")
    cost_usd_rollup: Mapped[Decimal] = mapped_column(Numeric(20, 8), server_default="0")
    last_activity_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    title: Mapped[str | None] = mapped_column(Text)
    project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at = _ts()
    updated_at = _ts()


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["tenant_id", "session_id"], ["sessions.tenant_id", "sessions.id"], ondelete="RESTRICT"
        ),
        UniqueConstraint("tenant_id", "id", "session_id", name="uq_runs_session_binding"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    run_kind: Mapped[str] = mapped_column(Text)
    admitted_seq: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(Text, server_default="queued")
    attempt: Mapped[int] = mapped_column(Integer, server_default="0")
    fence_token: Mapped[int] = mapped_column(BigInteger, server_default="0")
    prompt_version: Mapped[str] = mapped_column(Text)
    deadline_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    settled_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    worker_id: Mapped[str | None] = mapped_column(Text)
    error_redacted: Mapped[str | None] = mapped_column(Text)
    created_at = _ts()
    updated_at = _ts()


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["tenant_id", "session_id"], ["sessions.tenant_id", "sessions.id"], ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "run_id"], ["runs.tenant_id", "runs.id"], ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "author_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "session_id", "seq", name="uq_messages_session_seq"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    author_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    client_message_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    seq: Mapped[int] = mapped_column(BigInteger)
    role: Mapped[str] = mapped_column(Text)
    created_at = _ts()


class Part(Base):
    __tablename__ = "parts"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["tenant_id", "message_id"], ["messages.tenant_id", "messages.id"], ondelete="RESTRICT"
        ),
        UniqueConstraint("tenant_id", "message_id", "ordinal", name="uq_parts_message_ordinal"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    message_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    ordinal: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(Text)
    content_redacted: Mapped[dict[str, object]] = mapped_column(JSONB)
    created_at = _ts()
