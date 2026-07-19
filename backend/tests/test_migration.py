"""Migration round-trip: insert tenant/user/session and read them back.

Integration test — skips when no database is reachable (e.g. CI without services).
Runs inside a transaction that is rolled back, leaving no rows behind.
"""

from __future__ import annotations

import datetime
import uuid

import pytest
from sqlalchemy import select

from app.db import SessionLocal, ping_db
from app.models import Session as SessionModel
from app.models import Tenant, User


@pytest.mark.asyncio
async def test_core_round_trip() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")

    tid = uuid.uuid4()
    uid = uuid.uuid4()
    sid = uuid.uuid4()

    async with SessionLocal() as s:
        try:
            s.add(
                Tenant(
                    tenant_id=tid,
                    slug=f"t-{tid.hex[:8]}",
                    display_name="Test Tenant",
                    kind="personal",
                )
            )
            await s.flush()
            s.add(
                User(
                    tenant_id=tid,
                    id=uid,
                    email="dana@example.com",
                    display_name="Dana",
                    status="active",
                )
            )
            await s.flush()
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

            # Reload from the DB so server defaults are visible.
            s.expire_all()
            got = (
                await s.execute(
                    select(SessionModel).where(
                        SessionModel.tenant_id == tid, SessionModel.id == sid
                    )
                )
            ).scalar_one()

            assert got.umo_key == f"web:chat:{sid}"
            assert got.status == "open"  # server_default applied by the DB
            assert got.user_id == uid
            assert isinstance(got.created_at, datetime.datetime)
        finally:
            await s.rollback()
