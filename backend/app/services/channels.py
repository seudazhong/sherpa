"""Channel config + thread-state service (ADR-028).

Owns reading/writing ``channel_configs`` (sealing/unsealing the secret via the
KEK) and ``channel_thread_state`` (last inbound msg id per session for QQ passive
replies). The secret is only ever decrypted here (capability-gated), never
returned to a route — the REST layer exposes a masked ``secret_set`` flag only.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ChannelConfig, ChannelThreadState
from app.security.channel_secret import (
    ChannelSeal,
    ChannelSecretIdentity,
    open_channel_secret,
    seal_channel_secret,
)
from app.security.keyring import load_keyring
from app.security.vault import connector_vault_capability


@dataclass(frozen=True)
class QQConfig:
    enabled: bool
    app_id: str
    owner_external_id: str
    secret_set: bool


async def get_config(
    session: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID, channel: str
) -> ChannelConfig | None:
    return await session.get(ChannelConfig, (tenant_id, user_id, channel))


async def get_qq_config(
    session: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> QQConfig | None:
    row = await get_config(session, tenant_id, user_id, "qq")
    if row is None:
        return None
    return QQConfig(
        enabled=row.enabled,
        app_id=row.app_id,
        owner_external_id=row.owner_external_id,
        secret_set=bool(row.secret_enc),
    )


async def set_qq_config(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    app_id: str,
    enabled: bool,
    owner_external_id: str,
    secret: str | None,
) -> ChannelConfig:
    """Upsert the QQ config; seal the secret only when a new one is provided."""
    row = await get_config(session, tenant_id, user_id, "qq")
    if row is None:
        row = ChannelConfig(tenant_id=tenant_id, user_id=user_id, channel="qq", kind="qq_official")
        session.add(row)
    row.kind = "qq_official"
    row.app_id = app_id
    row.enabled = enabled
    row.owner_external_id = owner_external_id
    if secret:
        seal = seal_channel_secret(
            secret,
            ChannelSecretIdentity(tenant_id=tenant_id, user_id=user_id, channel="qq"),
            load_keyring(),
        )
        row.secret_enc = seal.secret_enc
        row.secret_nonce = seal.nonce
        row.kek_id = seal.kek_id
        row.key_version = seal.key_version
    await session.flush()
    return row


def reveal_secret(row: ChannelConfig) -> str | None:
    """Decrypt the sealed secret (capability-gated). None if unset."""
    if not row.secret_enc:
        return None
    seal = ChannelSeal(
        secret_enc=row.secret_enc,
        nonce=row.secret_nonce,
        kek_id=row.kek_id,
        key_version=row.key_version,
        algorithm="AES-256-GCM",
        aad_version=1,
    )
    identity = ChannelSecretIdentity(
        tenant_id=row.tenant_id, user_id=row.user_id, channel=row.channel
    )
    return open_channel_secret(seal, identity, connector_vault_capability(), load_keyring())


async def active_qq_configs(session: AsyncSession) -> list[ChannelConfig]:
    """All enabled QQ configs with an appid + secret (for the worker to connect)."""
    rows = (
        (
            await session.execute(
                select(ChannelConfig).where(
                    ChannelConfig.channel == "qq",
                    ChannelConfig.enabled.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    return [r for r in rows if r.app_id and r.secret_enc]


async def remember_inbound_msg_id(
    session: AsyncSession, tenant_id: uuid.UUID, session_id: uuid.UUID, msg_id: str
) -> None:
    """Upsert the last inbound external message id for a session."""
    row = await session.get(ChannelThreadState, (tenant_id, session_id))
    if row is None:
        row = ChannelThreadState(
            tenant_id=tenant_id, session_id=session_id, last_inbound_msg_id=msg_id
        )
        session.add(row)
    else:
        row.last_inbound_msg_id = msg_id
    await session.flush()


async def last_inbound_msg_id(
    session: AsyncSession, tenant_id: uuid.UUID, session_id: uuid.UUID
) -> str | None:
    row = await session.get(ChannelThreadState, (tenant_id, session_id))
    return row.last_inbound_msg_id if row and row.last_inbound_msg_id else None
