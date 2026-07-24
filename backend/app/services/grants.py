"""Pre-authorization grants service (ADR-034, Phase APPROVALS).

Owner-only CRUD over ``permission_grants`` + the helper that persists a grant from an
approved action (the ``always`` choice). Grants are tenant+user scoped; the agent has
no path here (no tool, no agent-actor writes). Functions flush, never commit — the
adapter owns the transaction.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PermissionGrant
from app.permissions.grants import derive_rule, is_grantable
from app.services.context import CallerContext
from app.services.errors import Forbidden, Invalid, NotFound

_MAX_MATCH_BYTES = 8192


def _require_owner(ctx: CallerContext) -> uuid.UUID:
    if ctx.actor == "agent":
        raise Forbidden("grants are owner-only")
    if ctx.user_id is None:
        raise Invalid("grants require a user context")
    return ctx.user_id


async def list_grants(db: AsyncSession, ctx: CallerContext) -> list[PermissionGrant]:
    uid = _require_owner(ctx)
    rows = (
        (
            await db.execute(
                select(PermissionGrant)
                .where(
                    PermissionGrant.tenant_id == ctx.tenant_id,
                    PermissionGrant.user_id == uid,
                    PermissionGrant.revoked_at.is_(None),
                )
                .order_by(PermissionGrant.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def create_grant(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    tool_name: str,
    match_json: dict,
    created_via: str = "manual",
) -> PermissionGrant:
    uid = _require_owner(ctx)
    if not is_grantable(tool_name):
        raise Invalid(f"tool does not support grants: {tool_name}")
    if not isinstance(match_json, dict) or not match_json:
        raise Invalid("match_json must be a non-empty object")
    import json

    if len(json.dumps(match_json)) > _MAX_MATCH_BYTES:
        raise Invalid("match_json too large")
    if created_via not in ("manual", "always"):
        raise Invalid("invalid created_via")
    row = PermissionGrant(
        tenant_id=ctx.tenant_id,
        id=uuid.uuid4(),
        user_id=uid,
        tool_name=tool_name,
        match_json=match_json,
        created_via=created_via,
    )
    db.add(row)
    await db.flush()
    return row


async def revoke_grant(db: AsyncSession, ctx: CallerContext, *, grant_id: uuid.UUID) -> None:
    uid = _require_owner(ctx)
    row = await db.get(PermissionGrant, (ctx.tenant_id, grant_id))
    if row is None or row.user_id != uid or row.revoked_at is not None:
        raise NotFound("grant not found")
    import datetime

    row.revoked_at = datetime.datetime.now(datetime.UTC)
    await db.flush()


async def grant_from_action(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    tool_name: str,
    args: dict,
) -> PermissionGrant | None:
    """Persist (merge) a grant derived from an approved action — the `always` path.

    Returns the created/updated grant, or None if the tool has no derivable rule.
    For `send_email` the recipient is merged into an existing manual/always grant to
    avoid a proliferation of one-recipient grants.
    """
    rule = derive_rule(tool_name, args)
    if rule is None:
        return None
    recipients = rule.get("recipients")
    if isinstance(recipients, list) and recipients:
        existing = (
            (
                await db.execute(
                    select(PermissionGrant).where(
                        PermissionGrant.tenant_id == tenant_id,
                        PermissionGrant.user_id == user_id,
                        PermissionGrant.tool_name == tool_name,
                        PermissionGrant.revoked_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        for grant in existing:
            current = grant.match_json.get("recipients")
            if isinstance(current, list):
                merged = sorted({*(str(a).lower() for a in current), *recipients})
                grant.match_json = {**grant.match_json, "recipients": merged}
                await db.flush()
                return grant
    row = PermissionGrant(
        tenant_id=tenant_id,
        id=uuid.uuid4(),
        user_id=user_id,
        tool_name=tool_name,
        match_json=rule,
        created_via="always",
    )
    db.add(row)
    await db.flush()
    return row
