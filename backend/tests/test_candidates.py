"""Candidate lifecycle (m2-17): list, accept -> linked todo, dismiss, version guard.

Seeds a candidate via the extraction service (mock provider), then drives the
HTTP endpoints. Integration test — skips without Postgres + Redis; commits
(deferred candidate<->todo FKs) and cleans up the owner tenant.
"""

from __future__ import annotations

import datetime
import hashlib
import uuid

import httpx
import pytest
from httpx import ASGITransport
from sqlalchemy import select, text

from app.auth import owner_ids
from app.config import settings
from app.connectors.analysis import run_extraction
from app.db import SessionLocal, ping_db
from app.main import app
from app.models import Candidate, Connector, ConnectorItem, Run, Todo
from app.providers import Finish, MockProvider, TextDelta
from app.redis_client import ping_redis

_JSON = (
    '{"candidates": [{"title": "Review Q3 budget", "description": "Send feedback",'
    ' "due_at": "2026-07-24T09:00:00Z", "priority": "high", "confidence": 0.9,'
    ' "rationale": "Manager asked", "source_excerpt": "review the Q3 budget"}]}'
)


async def _drop_owner() -> None:
    tid, _ = owner_ids()
    async with SessionLocal() as s:
        await s.execute(text("DELETE FROM tenants WHERE tenant_id = :t"), {"t": tid})
        await s.commit()


async def _seed_candidate() -> uuid.UUID:
    """Create a pending candidate under the owner tenant; return its id."""
    tid, uid = owner_ids()
    async with SessionLocal() as s:
        cid, iid, rid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        s.add(
            Connector(
                tenant_id=tid,
                id=cid,
                user_id=uid,
                kind="gmail",
                channel_installation_id=f"gmail:{cid}",
                external_account_id="owner@gmail.com",
                status="pending_oauth",
            )
        )
        await s.flush()
        item = ConnectorItem(
            tenant_id=tid,
            id=iid,
            connector_id=cid,
            provider_item_id="m1",
            revision="1",
            received_at=datetime.datetime(2026, 7, 1, tzinfo=datetime.UTC),
            content_digest=hashlib.sha256(b"x").digest(),
            content_json={
                "from": "boss@acme.com",
                "subject": "Q3 budget",
                "snippet": "review",
                "date": "Wed, 01 Jul 2026 10:00:00 +0000",
            },
            is_latest=True,
        )
        s.add(item)
        await s.flush()
        s.add(Run(tenant_id=tid, id=rid, run_kind="candidate_extraction", prompt_version="x"))
        await s.flush()
        result = await run_extraction(
            s,
            connector_item=item,
            run_id=rid,
            provider=MockProvider(script=[[TextDelta(_JSON), Finish("stop")]]),
            provider_name="mock",
            model="mock-v1",
        )
        await s.commit()
        cand = (
            await s.execute(
                select(Candidate).where(
                    Candidate.tenant_id == tid, Candidate.extraction_id == result.extraction_id
                )
            )
        ).scalar_one()
        return cand.id


@pytest.mark.asyncio
async def test_candidate_accept_creates_linked_todo_and_dismiss() -> None:
    if not await ping_db() or not await ping_redis():
        pytest.skip("database or redis not reachable")
    await _drop_owner()
    tid, _ = owner_ids()
    try:
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            login = await client.post(
                "/auth/login",
                json={"email": settings.owner_email, "password": settings.owner_password},
            )
            csrf = login.json()["csrf_token"]
            headers = {"X-CSRF-Token": csrf}

            candidate_id = await _seed_candidate()

            # list pending candidates -> our candidate with source metadata
            lst = await client.get("/candidates")
            assert lst.status_code == 200
            items = lst.json()["items"]
            assert any(c["id"] == str(candidate_id) for c in items)
            cand = next(c for c in items if c["id"] == str(candidate_id))
            assert cand["source"]["subject"] == "Q3 budget"
            assert cand["priority"] == "high"

            # accept -> 201 with a linked todo
            acc = await client.post(
                f"/candidates/{candidate_id}/accept",
                json={"if_version": cand["version"]},
                headers=headers,
            )
            assert acc.status_code == 201
            body = acc.json()
            assert body["candidate"]["status"] == "accepted"
            todo_id = body["todo"]["id"]
            assert body["todo"]["source_candidate_id"] == str(candidate_id)
            assert body["candidate"]["accepted_todo_id"] == todo_id

            # bidirectional link is committed
            async with SessionLocal() as s:
                todo = await s.get(Todo, (tid, uuid.UUID(todo_id)))
                assert todo is not None and str(todo.source_candidate_id) == str(candidate_id)

            # todos list shows it
            todos = await client.get("/todos")
            assert any(t["id"] == todo_id for t in todos.json()["items"])

            # re-accept a non-pending candidate -> 409
            again = await client.post(
                f"/candidates/{candidate_id}/accept", json={"if_version": 1}, headers=headers
            )
            assert again.status_code == 409

            # a second candidate: version conflict then dismiss
            other_id = await _seed_candidate2()
            bad = await client.post(
                f"/candidates/{other_id}/dismiss", json={"if_version": 999}, headers=headers
            )
            assert bad.status_code == 409
            dis = await client.post(
                f"/candidates/{other_id}/dismiss",
                json={"if_version": 1, "reason": "not relevant"},
                headers=headers,
            )
            assert dis.status_code == 200 and dis.json()["status"] == "dismissed"
    finally:
        await _drop_owner()


async def _seed_candidate2() -> uuid.UUID:
    tid, uid = owner_ids()
    async with SessionLocal() as s:
        cid, iid, rid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        s.add(
            Connector(
                tenant_id=tid,
                id=cid,
                user_id=uid,
                kind="gmail",
                channel_installation_id=f"gmail:{cid}",
                external_account_id=f"o2-{cid}@gmail.com",
                status="pending_oauth",
            )
        )
        await s.flush()
        item = ConnectorItem(
            tenant_id=tid,
            id=iid,
            connector_id=cid,
            provider_item_id="m2",
            revision="1",
            received_at=datetime.datetime(2026, 7, 2, tzinfo=datetime.UTC),
            content_digest=hashlib.sha256(b"y").digest(),
            content_json={
                "from": "x@y.com",
                "subject": "Other",
                "snippet": "s",
                "date": "Thu, 02 Jul 2026 10:00:00 +0000",
            },
            is_latest=True,
        )
        s.add(item)
        await s.flush()
        s.add(Run(tenant_id=tid, id=rid, run_kind="candidate_extraction", prompt_version="x"))
        await s.flush()
        result = await run_extraction(
            s,
            connector_item=item,
            run_id=rid,
            provider=MockProvider(script=[[TextDelta(_JSON), Finish("stop")]]),
            provider_name="mock",
            model="mock-v1",
        )
        await s.commit()
        cand = (
            await s.execute(
                select(Candidate).where(
                    Candidate.tenant_id == tid, Candidate.extraction_id == result.extraction_id
                )
            )
        ).scalar_one()
        return cand.id
