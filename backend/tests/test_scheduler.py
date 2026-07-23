"""Scheduler (m2-18): leader lock, firing tick (advance-cursor / no double-fire /
missed visible), and the sync+analyze pipeline. Integration — skips without deps."""

from __future__ import annotations

import datetime
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal, ping_db
from app.models import Candidate, Connector, Schedule, ScheduleFiring, Tenant, User
from app.providers import Finish, MockProvider, TextDelta
from app.redis_client import ping_redis
from app.scheduler import fire_due_schedules, release_leader, try_acquire_leader
from app.scheduler.pipeline import sync_and_analyze
from app.security import ConnectorTokenIdentity, load_keyring, seal_connector_token

_UTC = datetime.UTC


@pytest.mark.asyncio
async def test_leader_lock_prevents_double_fire() -> None:
    if not await ping_redis():
        pytest.skip("redis not reachable")
    name = f"test-{uuid.uuid4().hex}"
    try:
        assert await try_acquire_leader(name, ttl_ms=5_000) is True
        assert await try_acquire_leader(name, ttl_ms=5_000) is False
    finally:
        await release_leader(name)
    assert await try_acquire_leader(name, ttl_ms=5_000) is True
    await release_leader(name)


async def _seed_tenant(s: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    tid, uid = uuid.uuid4(), uuid.uuid4()
    s.add(Tenant(tenant_id=tid, slug=f"t-{tid.hex[:8]}", display_name="T", kind="personal"))
    await s.flush()
    s.add(User(tenant_id=tid, id=uid, email="d@e.co", display_name="D", status="active"))
    await s.flush()
    return tid, uid


def _digest_schedule(
    tid: uuid.UUID, uid: uuid.UUID, next_fire_at: datetime.datetime, *, misfire: str, channel: str
) -> Schedule:
    return Schedule(
        tenant_id=tid,
        id=uuid.uuid4(),
        user_id=uid,
        kind="daily_digest",
        name="Digest",
        delivery_channel=channel,
        timezone="UTC",
        local_time=datetime.time(8, 0),
        next_fire_at=next_fire_at,
        misfire_policy=misfire,
        duplicate_policy="prefer_no_duplicate",
        status="active",
    )


@pytest.mark.asyncio
async def test_fire_due_creates_one_firing_and_advances_cursor() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid = await _seed_tenant(s)
            now = datetime.datetime.now(_UTC)
            sched = _digest_schedule(
                tid, uid, now - datetime.timedelta(minutes=1), misfire="fire_once", channel="web"
            )
            s.add(sched)
            await s.flush()

            created = await fire_due_schedules(s, now)
            assert len(created) == 1
            refreshed = await s.get(Schedule, (tid, sched.id))
            assert refreshed is not None and refreshed.next_fire_at > now  # advance-cursor

            count = await s.scalar(
                select(func.count())
                .select_from(ScheduleFiring)
                .where(ScheduleFiring.tenant_id == tid)
            )
            assert count == 1

            # running again is a no-op (not due; no double-fire)
            again = await fire_due_schedules(s, now)
            assert again == []
            count2 = await s.scalar(
                select(func.count())
                .select_from(ScheduleFiring)
                .where(ScheduleFiring.tenant_id == tid)
            )
            assert count2 == 1
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_once_schedule_completes_after_firing() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid = await _seed_tenant(s)
            now = datetime.datetime.now(_UTC)
            sched = Schedule(
                tenant_id=tid,
                id=uuid.uuid4(),
                user_id=uid,
                kind="daily_digest",
                name="One-off",
                delivery_channel="web",
                timezone="UTC",
                local_time=datetime.time(8, 0),
                cadence_kind="once",
                next_fire_at=now - datetime.timedelta(minutes=1),
                misfire_policy="fire_once",
                duplicate_policy="prefer_no_duplicate",
                status="active",
            )
            s.add(sched)
            await s.flush()

            created = await fire_due_schedules(s, now)
            assert len(created) == 1
            refreshed = await s.get(Schedule, (tid, sched.id))
            assert refreshed is not None and refreshed.status == "completed"

            # A completed schedule is no longer due.
            again = await fire_due_schedules(s, now)
            assert again == []
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_missed_firing_is_visible() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid = await _seed_tenant(s)
            now = datetime.datetime.now(_UTC)
            sched = _digest_schedule(
                tid, uid, now - datetime.timedelta(days=2), misfire="skip", channel="digest_email"
            )
            s.add(sched)
            await s.flush()

            await fire_due_schedules(s, now)
            missed = (
                await s.execute(
                    select(ScheduleFiring).where(
                        ScheduleFiring.tenant_id == tid,
                        ScheduleFiring.delivery_outcome == "missed",
                    )
                )
            ).scalar_one()
            assert missed.status == "settled" and missed.settled_at is not None
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_sync_and_analyze_produces_candidates() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    from tests.test_gmail_sync import _FakeGmailSync, _msg  # reuse fixtures

    async with SessionLocal() as s:
        try:
            tid, uid = await _seed_tenant(s)
            cid = uuid.uuid4()
            seal = seal_connector_token(
                {"refresh_token": "rt"},
                ConnectorTokenIdentity(
                    tenant_id=tid, connector_id=cid, external_account_id="o@g.co"
                ),
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
            connector = await s.get(Connector, (tid, cid))
            assert connector is not None

            client = _FakeGmailSync([_msg("s1", "Invoice due"), _msg("s2", "Renew subscription")])
            provider = MockProvider(
                script=[
                    [
                        TextDelta(
                            '{"candidates":[{"title":"A","priority":"high","confidence":0.8}]}'
                        ),
                        Finish("stop"),
                    ],
                    [
                        TextDelta(
                            '{"candidates":[{"title":"B","priority":"low","confidence":0.6}]}'
                        ),
                        Finish("stop"),
                    ],
                ]
            )
            result = await sync_and_analyze(
                s,
                connector=connector,
                sync_client=client,
                provider=provider,
                provider_name="mock",
                model="mock-v1",
            )
            assert result.synced == 2 and result.analyzed == 2 and result.candidates == 2
            total = await s.scalar(
                select(func.count()).select_from(Candidate).where(Candidate.tenant_id == tid)
            )
            assert total == 2
        finally:
            await s.rollback()
