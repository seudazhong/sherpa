"""User-private core memory service (ADR-004, milestone 1a).

Bounded key-value "core memory" scoped to (tenant, user): durable facts the agent
keeps about the user and recalls across sessions. This is the always-available
tier (Letta-style core memory), NOT the deferred pgvector/RAG passage tier.

Own-data reads/writes on the user's behalf; the adapter (REST or tool) owns the
transaction and commits. Keys/values are bounded to match the frozen table
constraints so a bad write fails as a clean `Invalid`, not a DB error.
"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import UserMemory
from app.services.context import CallerContext
from app.services.errors import Invalid, NotFound

_KEY_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_MAX_BYTES = 16384


def _require_user(ctx: CallerContext) -> None:
    if ctx.user_id is None:
        raise Invalid("core memory requires a user context")


def _validate_key(key: str) -> str:
    if not _KEY_RE.match(key):
        raise Invalid("memory_key must match ^[a-z][a-z0-9_.-]{0,63}$")
    return key


async def get_memory(db: AsyncSession, ctx: CallerContext, *, key: str) -> UserMemory | None:
    _require_user(ctx)
    return await db.get(UserMemory, (ctx.tenant_id, ctx.user_id, _validate_key(key)))


async def list_memory(db: AsyncSession, ctx: CallerContext) -> list[UserMemory]:
    _require_user(ctx)
    rows = (
        (
            await db.execute(
                select(UserMemory)
                .where(
                    UserMemory.tenant_id == ctx.tenant_id,
                    UserMemory.user_id == ctx.user_id,
                )
                .order_by(UserMemory.memory_key)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def set_memory(db: AsyncSession, ctx: CallerContext, *, key: str, value: str) -> UserMemory:
    _require_user(ctx)
    _validate_key(key)
    if len(value.encode("utf-8")) > _MAX_BYTES:
        raise Invalid(f"value exceeds {_MAX_BYTES} bytes")
    row = await db.get(UserMemory, (ctx.tenant_id, ctx.user_id, key))
    if row is None:
        row = UserMemory(
            tenant_id=ctx.tenant_id, user_id=ctx.user_id, memory_key=key, value_text=value
        )
        db.add(row)
    else:
        row.value_text = value
        row.version += 1
    await db.flush()
    return row


async def delete_memory(db: AsyncSession, ctx: CallerContext, *, key: str) -> None:
    _require_user(ctx)
    row = await db.get(UserMemory, (ctx.tenant_id, ctx.user_id, _validate_key(key)))
    if row is None:
        raise NotFound("memory not found")
    await db.delete(row)
    await db.flush()
