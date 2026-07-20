"""Gmail incremental sync -> connector_items (docs/06, ADR-017).

Unseals the connector token (connector-vault capability), refreshes the access
token, lists messages for the sync scope, and writes each as an immutable
connector_item. Idempotent: an item is deduped by (connector, provider_item_id,
revision); a new revision flips the prior latest. Re-running the same window
adds nothing new. Content is bounded/normalized — no raw bodies.
"""

from __future__ import annotations

import dataclasses
import datetime
import hashlib
import json
import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.gmail import GmailSyncClient
from app.models import Connector, ConnectorItem
from app.security import (
    ConnectorSeal,
    ConnectorTokenIdentity,
    connector_vault_capability,
    load_keyring,
    open_connector_token,
)


@dataclasses.dataclass(frozen=True)
class SyncResult:
    seen: int
    new_items: int


def _build_query(scope: dict[str, object]) -> str:
    raw_lookback = scope.get("lookback_days", 30)
    lookback = raw_lookback if isinstance(raw_lookback, int) else 30
    raw_labels = scope.get("label_ids")
    labels = raw_labels if isinstance(raw_labels, list) else ["INBOX"]
    label_terms = " ".join(f"label:{str(label).lower()}" for label in labels)
    return f"newer_than:{lookback}d {label_terms}".strip()


def _digest(content: dict[str, object]) -> bytes:
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).digest()


async def sync_gmail(
    session: AsyncSession, *, connector: Connector, client: GmailSyncClient
) -> SyncResult:
    """Pull the connector's Gmail window into connector_items. Caller commits."""
    seal = ConnectorSeal(
        token_enc=connector.token_enc or b"",
        nonce=connector.nonce or b"",
        kek_id=connector.kek_id or "",
        key_version=connector.key_version or 0,
        token_algorithm=connector.token_algorithm or "",
        aad_version=connector.aad_version or 0,
    )
    identity = ConnectorTokenIdentity(
        tenant_id=connector.tenant_id,
        connector_id=connector.id,
        external_account_id=connector.external_account_id,
    )
    token = open_connector_token(seal, identity, connector_vault_capability(), load_keyring())

    refreshed = await client.refresh(refresh_token=str(token.get("refresh_token", "")))
    access_token = str(refreshed.get("access_token", ""))

    scope = connector.cursor.get("sync_scope", {})
    query = _build_query(scope)
    message_ids = await client.list_message_ids(access_token=access_token, query=query)

    new_items = 0
    for message_id in message_ids:
        msg = await client.get_message(access_token=access_token, message_id=message_id)
        provider_item_id = str(msg["id"])
        revision = str(msg.get("history_id") or "1")

        exists = await session.scalar(
            select(ConnectorItem.id).where(
                ConnectorItem.tenant_id == connector.tenant_id,
                ConnectorItem.connector_id == connector.id,
                ConnectorItem.provider_item_id == provider_item_id,
                ConnectorItem.revision == revision,
            )
        )
        if exists is not None:
            continue

        await session.execute(
            update(ConnectorItem)
            .where(
                ConnectorItem.tenant_id == connector.tenant_id,
                ConnectorItem.connector_id == connector.id,
                ConnectorItem.provider_item_id == provider_item_id,
                ConnectorItem.is_latest.is_(True),
            )
            .values(is_latest=False)
        )

        content: dict[str, object] = {
            "from": msg.get("from", ""),
            "subject": msg.get("subject", ""),
            "date": msg.get("date", ""),
            "snippet": msg.get("snippet", ""),
            "thread_id": msg.get("thread_id"),
            "label_ids": msg.get("label_ids", []),
        }
        session.add(
            ConnectorItem(
                tenant_id=connector.tenant_id,
                id=uuid.uuid4(),
                connector_id=connector.id,
                provider_item_id=provider_item_id,
                revision=revision,
                provider_thread_id=msg.get("thread_id"),
                received_at=msg["internal_date"],
                content_digest=_digest(content),
                content_json=content,
                is_latest=True,
            )
        )
        await session.flush()
        new_items += 1

    now = datetime.datetime.now(datetime.UTC)
    connector.cursor = {**connector.cursor, "last_synced_at": now.isoformat()}
    connector.last_sync_at = now
    if connector.status in ("pending_oauth", "syncing"):
        connector.status = "active"
    await session.flush()

    return SyncResult(seen=len(message_ids), new_items=new_items)
