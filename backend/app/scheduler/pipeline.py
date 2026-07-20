"""Periodic pipeline: sync a connector, then analyze its new items -> candidates.

Closes the Gmail -> candidate loop: after an incremental sync, every latest
connector_item without a successful extraction is analyzed (one
candidate_extraction run each). Idempotent — already-analyzed items are skipped.
"""

from __future__ import annotations

import dataclasses
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.analysis import run_extraction
from app.connectors.gmail import GmailSyncClient
from app.connectors.sync import sync_gmail
from app.models import Connector, ConnectorItem, Extraction, Run
from app.providers import Provider


@dataclasses.dataclass(frozen=True)
class PipelineResult:
    synced: int
    analyzed: int
    candidates: int


async def _unanalyzed_latest_items(
    session: AsyncSession, connector: Connector
) -> list[ConnectorItem]:
    analyzed = select(Extraction.connector_item_id).where(
        Extraction.tenant_id == connector.tenant_id, Extraction.status == "succeeded"
    )
    rows = (
        (
            await session.execute(
                select(ConnectorItem).where(
                    ConnectorItem.tenant_id == connector.tenant_id,
                    ConnectorItem.connector_id == connector.id,
                    ConnectorItem.is_latest.is_(True),
                    ConnectorItem.id.notin_(analyzed),
                )
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def sync_and_analyze(
    session: AsyncSession,
    *,
    connector: Connector,
    sync_client: GmailSyncClient,
    provider: Provider,
    provider_name: str,
    model: str,
) -> PipelineResult:
    """Sync the connector, then analyze each new latest item. Caller commits."""
    sync_result = await sync_gmail(session, connector=connector, client=sync_client)

    analyzed = 0
    candidates = 0
    for item in await _unanalyzed_latest_items(session, connector):
        run_id = uuid.uuid4()
        session.add(
            Run(
                tenant_id=connector.tenant_id,
                id=run_id,
                run_kind="candidate_extraction",
                prompt_version="connector_analysis.v1",
            )
        )
        await session.flush()
        result = await run_extraction(
            session,
            connector_item=item,
            run_id=run_id,
            provider=provider,
            provider_name=provider_name,
            model=model,
        )
        analyzed += 1
        candidates += result.candidate_count

    return PipelineResult(synced=sync_result.new_items, analyzed=analyzed, candidates=candidates)
