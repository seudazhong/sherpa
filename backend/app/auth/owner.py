"""Idempotent single-owner tenant/user seed (v1 is single-user, ADR-022).

Owner tenant/user ids are derived deterministically from OWNER_EMAIL so a
restart maps to the same owner. Seeding is idempotent via ON CONFLICT DO NOTHING.
"""

from __future__ import annotations

import uuid

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Tenant, User

_NS = uuid.NAMESPACE_URL


def owner_ids() -> tuple[uuid.UUID, uuid.UUID]:
    """Deterministic (tenant_id, user_id) for the configured owner email."""
    tenant_id = uuid.uuid5(_NS, f"sherpa:tenant:{settings.owner_email}")
    user_id = uuid.uuid5(_NS, f"sherpa:user:{settings.owner_email}")
    return tenant_id, user_id


async def ensure_owner(session: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    """Create the owner tenant + user if absent; return their ids. Caller commits."""
    tenant_id, user_id = owner_ids()
    if await session.get(User, (tenant_id, user_id)) is not None:
        return tenant_id, user_id

    await session.execute(
        pg_insert(Tenant)
        .values(tenant_id=tenant_id, slug="personal", display_name="Owner", kind="personal")
        .on_conflict_do_nothing()
    )
    await session.execute(
        pg_insert(User)
        .values(
            tenant_id=tenant_id,
            id=user_id,
            email=settings.owner_email,
            display_name="Owner",
            status="active",
        )
        .on_conflict_do_nothing()
    )
    await session.flush()
    return tenant_id, user_id
