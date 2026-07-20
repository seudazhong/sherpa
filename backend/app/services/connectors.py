"""Connector capability (ADR-023, docs/11). Shared by REST + agent tools.

`list_connectors` is a read projection. `sync_connector` runs the Gmail→candidate
pipeline inline (sync + analyze) so the agent gets immediate counts in its turn;
the REST "sync now" button enqueues the same pipeline as a background run instead.
Both reuse `sync_and_analyze`. Functions flush but never commit — adapter owns the
transaction.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    Connector as ConnectorSchema,
)
from app.api.schemas import (
    ConnectorSyncStatus,
    GmailSyncScope,
)
from app.connectors.gmail import GmailSyncClient
from app.models import Connector
from app.providers import Provider
from app.scheduler.pipeline import PipelineResult, sync_and_analyze
from app.services.context import CallerContext
from app.services.errors import Conflict, NotFound

_SYNCABLE = ("active", "degraded", "error")


def connector_schema(row: Connector) -> ConnectorSchema:
    return ConnectorSchema(
        id=row.id,
        tenant_id=row.tenant_id,
        kind="gmail",
        status=row.status,  # type: ignore[arg-type]
        account_email=row.external_account_id,
        granted_scopes=list(row.scopes),
        sync_scope=GmailSyncScope.model_validate(row.cursor.get("sync_scope") or {}),
        sync=ConnectorSyncStatus(
            cursor_present=bool(row.cursor.get("history_id")),
            last_started_at=None,
            last_succeeded_at=row.last_sync_at,
            last_error_code=None,
            last_run_id=None,
        ),
        version=row.refresh_version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def list_connectors(db: AsyncSession, ctx: CallerContext) -> list[ConnectorSchema]:
    rows = (
        (
            await db.execute(
                select(Connector)
                .where(Connector.tenant_id == ctx.tenant_id)
                .order_by(Connector.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [connector_schema(r) for r in rows]


async def sync_connector(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    connector_id: uuid.UUID,
    sync_client: GmailSyncClient,
    provider: Provider,
    provider_name: str,
    model: str,
) -> PipelineResult:
    """Sync + analyze a connector inline. Raises NotFound/Conflict; caller commits."""
    conn = await db.get(Connector, (ctx.tenant_id, connector_id))
    if conn is None:
        raise NotFound("connector not found")
    if conn.status == "paused":
        raise Conflict("connector_paused")
    if conn.status not in _SYNCABLE:
        raise Conflict("connector_not_ready")
    return await sync_and_analyze(
        db,
        connector=conn,
        sync_client=sync_client,
        provider=provider,
        provider_name=provider_name,
        model=model,
    )
