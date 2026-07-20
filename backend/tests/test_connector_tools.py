"""Connector tools + service (m-tools T5): agent lists connectors and syncs.

Proves the agent can trigger a Gmail sync+analyze that produces candidates —
the capability the chat agent previously lacked. Uses a fake Gmail client + mock
provider (no network). Integration test — skips without Postgres.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import execute_run
from app.db import SessionLocal, ping_db
from app.models import Candidate, Connector, Run, Tenant, User
from app.models import Session as SessionModel
from app.providers import Finish, MockProvider, TextDelta, ToolCall
from app.security import ConnectorTokenIdentity, load_keyring, seal_connector_token
from app.services import CallerContext, connectors
from app.tools import ToolContext, build_default_registry

_CAND = '{"candidates":[{"title":"Pay invoice","priority":"high","confidence":0.8}]}'


async def _seed_active_connector(s: AsyncSession) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    tid, uid, cid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    s.add(Tenant(tenant_id=tid, slug=f"t-{tid.hex[:8]}", display_name="T", kind="personal"))
    await s.flush()
    s.add(User(tenant_id=tid, id=uid, email="d@e.co", display_name="D", status="active"))
    await s.flush()
    seal = seal_connector_token(
        {"refresh_token": "rt"},
        ConnectorTokenIdentity(tenant_id=tid, connector_id=cid, external_account_id="o@g.co"),
        load_keyring(),
    )
    s.add(
        Connector(
            tenant_id=tid,
            id=cid,
            user_id=uid,
            kind="gmail",
            channel_installation_id=f"gmail:{cid}",
            external_account_id="o@g.co",
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
    return tid, uid, cid


def _fake_client():  # type: ignore[no-untyped-def]
    from tests.test_gmail_sync import _FakeGmailSync, _msg

    return _FakeGmailSync([_msg("s1", "Invoice due")])


def _extraction_provider() -> MockProvider:
    return MockProvider(script=[[TextDelta(_CAND), Finish("stop")]])


@pytest.mark.asyncio
async def test_connector_service_sync_produces_candidates() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid, cid = await _seed_active_connector(s)
            ctx = CallerContext(tenant_id=tid, user_id=uid, actor="agent")

            rows = await connectors.list_connectors(s, ctx)
            assert any(c.id == cid for c in rows)

            result = await connectors.sync_connector(
                s,
                ctx,
                connector_id=cid,
                sync_client=_fake_client(),
                provider=_extraction_provider(),
                provider_name="mock",
                model="mock-v1",
            )
            assert result.synced == 1 and result.candidates == 1
            total = await s.scalar(
                select(func.count()).select_from(Candidate).where(Candidate.tenant_id == tid)
            )
            assert total == 1
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_sync_connector_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    monkeypatch.setattr("app.tools.connector_tools.build_gmail_sync_client", _fake_client)
    monkeypatch.setattr("app.tools.connector_tools.build_provider", _extraction_provider)
    async with SessionLocal() as s:
        try:
            tid, uid, cid = await _seed_active_connector(s)
            reg = build_default_registry()
            tctx = ToolContext(tenant_id=tid, user_id=uid, session=s)

            listing = await reg.get("list_connectors").execute(tctx, {})
            assert str(cid) in listing.llm_content

            synced = await reg.get("sync_connector").execute(tctx, {"connector_id": str(cid)})
            assert "created 1 candidate" in synced.llm_content
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_loop_agent_syncs_connector(monkeypatch: pytest.MonkeyPatch) -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    monkeypatch.setattr("app.tools.connector_tools.build_gmail_sync_client", _fake_client)
    monkeypatch.setattr("app.tools.connector_tools.build_provider", _extraction_provider)
    async with SessionLocal() as s:
        try:
            tid, uid, cid = await _seed_active_connector(s)
            sid, rid = uuid.uuid4(), uuid.uuid4()
            s.add(
                SessionModel(
                    tenant_id=tid,
                    id=sid,
                    user_id=uid,
                    umo_key=f"web:chat:{sid}",
                    channel="web",
                    channel_installation_id="local",
                    scope_type="chat",
                    external_scope_id=str(sid),
                )
            )
            await s.flush()
            run = Run(
                tenant_id=tid, id=rid, session_id=sid, run_kind="web_chat", prompt_version="v1"
            )
            s.add(run)
            await s.flush()

            provider = MockProvider(
                script=[
                    [
                        ToolCall(id="c1", name="sync_connector", args={"connector_id": str(cid)}),
                        Finish("tool_use"),
                    ],
                    [TextDelta("Synced."), Finish("stop")],
                ]
            )
            reason = await execute_run(
                s, run=run, provider=provider, registry=build_default_registry(), tier="full"
            )
            assert reason == "completed"
            total = await s.scalar(
                select(func.count()).select_from(Candidate).where(Candidate.tenant_id == tid)
            )
            assert total == 1  # agent-triggered sync produced a candidate
        finally:
            await s.rollback()
