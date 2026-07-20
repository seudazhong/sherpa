"""CONNECTOR_ANALYSIS extraction (m2-16): email -> candidates with provenance.

Deterministic via a scripted mock provider (no real model calls). Integration
test — skips without a database; seeds + rolls back (FKs are immediate).
"""

from __future__ import annotations

import datetime
import hashlib
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.analysis import run_extraction
from app.db import SessionLocal, ping_db
from app.models import (
    Candidate,
    Connector,
    ConnectorItem,
    Extraction,
    Generation,
    Run,
    Tenant,
    User,
)
from app.providers import Finish, MockProvider, TextDelta

_CANDIDATES_JSON = (
    '{"candidates": [{"title": "Pay the Q3 invoice", "description": "Invoice #123 is due",'
    ' "due_at": "2026-07-24T09:00:00Z", "priority": "high", "confidence": 0.92,'
    ' "rationale": "The email states the invoice is due Thursday",'
    ' "source_excerpt": "Invoice #123 due Thursday"}]}'
)


async def _seed(s: AsyncSession) -> tuple[uuid.UUID, ConnectorItem, uuid.UUID]:
    tid, uid, cid, iid, rid = (uuid.uuid4() for _ in range(5))
    s.add(Tenant(tenant_id=tid, slug=f"t-{tid.hex[:8]}", display_name="T", kind="personal"))
    await s.flush()
    s.add(User(tenant_id=tid, id=uid, email="d@e.co", display_name="D", status="active"))
    await s.flush()
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
            "from": "billing@vendor.com",
            "subject": "Invoice #123 due Thursday",
            "snippet": "Please pay invoice #123 by Thursday.",
            "date": "Wed, 01 Jul 2026 10:00:00 +0000",
        },
        is_latest=True,
    )
    s.add(item)
    await s.flush()
    run = Run(
        tenant_id=tid, id=rid, session_id=None, run_kind="candidate_extraction", prompt_version="x"
    )
    s.add(run)
    await s.flush()
    return tid, item, rid


def _mock(text: str) -> MockProvider:
    return MockProvider(script=[[TextDelta(text), Finish("stop")]])


@pytest.mark.asyncio
async def test_extraction_creates_candidate_with_provenance() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, item, rid = await _seed(s)
            result = await run_extraction(
                s,
                connector_item=item,
                run_id=rid,
                provider=_mock(_CANDIDATES_JSON),
                provider_name="mock",
                model="mock-v1",
            )
            assert result.status == "succeeded" and result.candidate_count == 1

            extraction = await s.get(Extraction, (tid, result.extraction_id))
            assert extraction is not None
            assert extraction.status == "succeeded" and extraction.completed_at is not None

            gen = await s.get(Generation, (tid, result.generation_id))
            assert gen is not None
            assert gen.purpose == "candidate_extraction"
            assert gen.extraction_id == result.extraction_id

            cand = (
                await s.execute(
                    select(Candidate).where(
                        Candidate.tenant_id == tid,
                        Candidate.extraction_id == result.extraction_id,
                    )
                )
            ).scalar_one()
            assert cand.title == "Pay the Q3 invoice"
            assert cand.priority == "high"
            assert float(cand.confidence) == pytest.approx(0.92)
            assert cand.status == "pending"
            assert cand.generation_id == result.generation_id  # provenance chain
            assert cand.source_excerpt_redacted == "Invoice #123 due Thursday"
            assert cand.due_at is not None
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_extraction_empty_list_makes_no_candidates() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            _tid, item, rid = await _seed(s)
            result = await run_extraction(
                s,
                connector_item=item,
                run_id=rid,
                provider=_mock('{"candidates": []}'),
                provider_name="mock",
                model="mock-v1",
            )
            assert result.status == "succeeded" and result.candidate_count == 0
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_extraction_bad_output_marks_failed() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, item, rid = await _seed(s)
            result = await run_extraction(
                s,
                connector_item=item,
                run_id=rid,
                provider=_mock("sorry, I cannot help with that"),
                provider_name="mock",
                model="mock-v1",
            )
            assert result.status == "failed" and result.candidate_count == 0
            extraction = await s.get(Extraction, (tid, result.extraction_id))
            assert extraction is not None and extraction.status == "failed"
            assert extraction.error_redacted is not None
        finally:
            await s.rollback()
