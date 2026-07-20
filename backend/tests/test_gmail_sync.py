"""Gmail sync -> connector_items (m2-15): new messages captured, re-sync idempotent.

Integration test — skips without a database; commits and cleans up the tenant.
"""

from __future__ import annotations

import datetime
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy import text as sqltext
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.sync import sync_gmail
from app.db import SessionLocal, ping_db
from app.models import Connector, ConnectorItem, Tenant, User
from app.security import ConnectorTokenIdentity, load_keyring, seal_connector_token


def _msg(mid: str, subject: str, history: str = "100") -> dict[str, object]:
    return {
        "id": mid,
        "thread_id": f"thread-{mid}",
        "history_id": history,
        "internal_date": datetime.datetime(2026, 7, 1, tzinfo=datetime.UTC),
        "from": "sender@example.com",
        "subject": subject,
        "date": "Wed, 01 Jul 2026 10:00:00 +0000",
        "snippet": f"snippet for {subject}",
        "label_ids": ["INBOX"],
    }


class _FakeGmailSync:
    def __init__(self, messages: list[dict[str, object]]) -> None:
        self.messages = messages

    async def refresh(self, *, refresh_token: str) -> dict[str, object]:
        return {"access_token": "fresh-access", "token_type": "Bearer", "expires_in": 3599}

    async def list_message_ids(
        self, *, access_token: str, query: str, max_results: int = 100
    ) -> list[str]:
        return [str(m["id"]) for m in self.messages]

    async def get_message(self, *, access_token: str, message_id: str) -> dict[str, object]:
        return next(m for m in self.messages if m["id"] == message_id)


async def _seed_connector(s: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    tid, uid, cid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    s.add(Tenant(tenant_id=tid, slug=f"t-{tid.hex[:8]}", display_name="T", kind="personal"))
    await s.flush()
    s.add(User(tenant_id=tid, id=uid, email="d@e.co", display_name="D", status="active"))
    await s.flush()
    seal = seal_connector_token(
        {"refresh_token": "rt", "access_token": "at"},
        ConnectorTokenIdentity(
            tenant_id=tid, connector_id=cid, external_account_id="owner@gmail.com"
        ),
        load_keyring(),
    )
    s.add(
        Connector(
            tenant_id=tid,
            id=cid,
            user_id=uid,
            kind="gmail",
            channel_installation_id="gmail:owner@gmail.com",
            external_account_id="owner@gmail.com",
            token_enc=seal.token_enc,
            nonce=seal.nonce,
            kek_id=seal.kek_id,
            key_version=seal.key_version,
            token_algorithm=seal.token_algorithm,
            aad_version=seal.aad_version,
            scopes=["https://www.googleapis.com/auth/gmail.readonly"],
            status="active",
            cursor={"sync_scope": {"lookback_days": 30, "label_ids": ["INBOX"]}},
        )
    )
    await s.flush()
    return tid, cid


async def _count(tid: uuid.UUID) -> int:
    async with SessionLocal() as s:
        return int(
            await s.scalar(
                select(func.count())
                .select_from(ConnectorItem)
                .where(ConnectorItem.tenant_id == tid)
            )
            or 0
        )


async def _drop(tid: uuid.UUID) -> None:
    async with SessionLocal() as s:
        await s.execute(sqltext("DELETE FROM tenants WHERE tenant_id = :t"), {"t": tid})
        await s.commit()


@pytest.mark.asyncio
async def test_sync_captures_and_is_idempotent() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")

    async with SessionLocal() as s:
        tid, cid = await _seed_connector(s)
        await s.commit()
    try:
        client = _FakeGmailSync([_msg("m1", "Invoice due"), _msg("m2", "Standup notes")])

        # first sync captures both
        async with SessionLocal() as s:
            conn = await s.get(Connector, (tid, cid))
            assert conn is not None
            result = await sync_gmail(s, connector=conn, client=client)
            await s.commit()
            assert result.new_items == 2 and result.seen == 2
        assert await _count(tid) == 2

        # re-sync is idempotent (no duplicates)
        async with SessionLocal() as s:
            conn = await s.get(Connector, (tid, cid))
            assert conn is not None
            result = await sync_gmail(s, connector=conn, client=client)
            await s.commit()
            assert result.new_items == 0 and result.seen == 2
        assert await _count(tid) == 2

        # a new message is captured on the next sync
        client.messages.append(_msg("m3", "New lead"))
        async with SessionLocal() as s:
            conn = await s.get(Connector, (tid, cid))
            assert conn is not None
            result = await sync_gmail(s, connector=conn, client=client)
            await s.commit()
            assert result.new_items == 1
        assert await _count(tid) == 3

        # the stored item carries provenance + a 32-byte digest, marked latest
        async with SessionLocal() as s:
            item = (
                await s.execute(
                    select(ConnectorItem).where(
                        ConnectorItem.tenant_id == tid, ConnectorItem.provider_item_id == "m1"
                    )
                )
            ).scalar_one()
            assert item.is_latest is True
            assert item.provider_thread_id == "thread-m1"
            assert len(item.content_digest) == 32
            assert item.content_json is not None and item.content_json["subject"] == "Invoice due"
    finally:
        await _drop(tid)
