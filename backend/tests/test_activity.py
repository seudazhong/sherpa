"""Activity ledger + data controls (m2-21).

Drives the real instrumentation: an extraction records an ``inference`` receipt,
which surfaces in ``GET /activity``; ``GET /activity/export`` bundles imported +
derived data; ``POST /activity/delete-imported`` erases items/candidates/todos
while the ledger is retained. Integration test — skips without Postgres + Redis.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import uuid

import httpx
import pytest
from httpx import ASGITransport
from sqlalchemy import select

from app.audit import READ, record_receipt
from app.auth import owner_ids
from app.config import settings
from app.connectors.analysis import run_extraction
from app.db import SessionLocal, ping_db
from app.main import app
from app.models import Candidate, Connector, ConnectorItem, Run
from app.providers import Finish, MockProvider, TextDelta
from app.redis_client import ping_redis
from tests.db_guard import drop_owner_tenant

_JSON = (
    '{"candidates": [{"title": "Review Q3 budget", "description": "Send feedback",'
    ' "due_at": "2026-07-24T09:00:00Z", "priority": "high", "confidence": 0.9,'
    ' "rationale": "Manager asked", "source_excerpt": "review the Q3 budget"}]}'
)


async def _drop_owner() -> None:
    await drop_owner_tenant()


async def _seed_candidate_and_read() -> uuid.UUID:
    """Seed a connector/item/candidate via the extraction pipeline; also a read receipt."""
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
            content_json={"from": "boss@acme.com", "subject": "Q3", "snippet": "review"},
            is_latest=True,
        )
        s.add(item)
        await s.flush()
        s.add(Run(tenant_id=tid, id=rid, run_kind="candidate_extraction", prompt_version="x"))
        await s.flush()
        await record_receipt(
            s,
            tenant_id=tid,
            receipt_type=READ,
            actor_type="connector",
            trigger_type="sync",
            action="gmail_sync",
            outcome="succeeded",
            subject_type="connector",
            subject_id=cid,
            summary={"seen": 1, "new_items": 1},
        )
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
async def test_activity_ledger_export_and_delete() -> None:
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
            headers = {"X-CSRF-Token": login.json()["csrf_token"]}

            candidate_id = await _seed_candidate_and_read()

            # Activity lists both a read (sync) and an inference (extraction).
            act = await client.get("/activity")
            assert act.status_code == 200
            types = {r["receipt_type"] for r in act.json()["items"]}
            assert {"read", "inference"} <= types
            actions = {r["action"] for r in act.json()["items"]}
            assert {"gmail_sync", "extract_candidates"} <= actions

            # Type filter narrows results.
            infer = await client.get("/activity?type=inference")
            assert infer.status_code == 200
            assert all(r["receipt_type"] == "inference" for r in infer.json()["items"])
            assert len(infer.json()["items"]) >= 1

            # Accept the candidate to produce a linked todo.
            cand = next(c for c in (await client.get("/candidates")).json()["items"])
            acc = await client.post(
                f"/candidates/{candidate_id}/accept",
                json={"if_version": cand["version"]},
                headers=headers,
            )
            assert acc.status_code == 201

            # Export bundles imported + derived data (+ the ledger).
            exp = await client.get("/activity/export")
            assert exp.status_code == 200
            assert exp.headers["content-disposition"].startswith("attachment")
            bundle = json.loads(exp.content)
            assert len(bundle["connectors"]) >= 1
            assert len(bundle["connector_items"]) >= 1
            assert len(bundle["candidates"]) >= 1
            assert len(bundle["todos"]) >= 1
            assert len(bundle["activity"]) >= 2

            # Delete imported data: items/candidates/todos gone; ledger retained.
            dele = await client.post("/activity/delete-imported", headers=headers)
            assert dele.status_code == 200
            deleted = dele.json()["deleted"]
            assert deleted["connector_items"] >= 1
            assert deleted["candidates"] >= 1
            assert deleted["todos"] >= 1

            assert (await client.get("/candidates")).json()["items"] == []
            assert (await client.get("/todos")).json()["items"] == []

            bundle2 = json.loads((await client.get("/activity/export")).content)
            assert bundle2["connector_items"] == []
            assert bundle2["candidates"] == []

            # The audit ledger persists as the record of what happened.
            act2 = await client.get("/activity")
            assert {"read", "inference"} <= {r["receipt_type"] for r in act2.json()["items"]}

            # Connectors themselves are retained (only imported items are erased).
            async with SessionLocal() as s:
                remaining = (
                    (await s.execute(select(Connector).where(Connector.tenant_id == tid)))
                    .scalars()
                    .all()
                )
                assert len(remaining) >= 1
    finally:
        await _drop_owner()
