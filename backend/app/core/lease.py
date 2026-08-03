"""Run liveness lease (ADR-029, Session Library P0).

The worker commits the run at settle, so ``runs.status`` is not visible to other
transactions mid-run. To let the Session Library tell a *live* run from a *dead*
worker, the worker claims a short lease in an independent committed transaction
and refreshes it on a heartbeat. A run is "live" only while
``status='running' AND lease_expires_at > now()``; a running row past its lease is
stale and must be recovered, never silently reconnected.

The lease writes live outside ``execute_run`` on purpose: ``execute_run`` no
longer takes an early write lock on the ``runs`` row, so the heartbeat's
independent transaction is never blocked by the long run transaction.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import os
import socket
import uuid

from sqlalchemy import select, update

from app.db import SessionLocal
from app.models import Run

LEASE_TTL_SECONDS = 45
HEARTBEAT_INTERVAL_SECONDS = 15


def worker_identity() -> str:
    """Stable-ish worker id for the lease (host:pid)."""
    return f"{socket.gethostname()}:{os.getpid()}"


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def run_is_live(status: str | None, lease_expires_at: datetime.datetime | None) -> bool:
    """True only while a run is running and its lease has not expired."""
    if status != "running" or lease_expires_at is None:
        return False
    expires = lease_expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=datetime.UTC)
    return expires > _now()


async def claim_run_lease(
    run_id: uuid.UUID, worker_id: str
) -> tuple[uuid.UUID, uuid.UUID | None] | None:
    """Mark the run running and take a fresh lease in a committed transaction.

    Returns ``(tenant_id, session_id)`` only for an atomic ``queued -> running``
    claim, ``None`` if another delivery already claimed/settled it, and raises
    ``LookupError`` if the run is gone.
    """
    async with SessionLocal() as session:
        run = (
            await session.execute(select(Run).where(Run.id == run_id).with_for_update())
        ).scalar_one_or_none()
        if run is None:
            raise LookupError(str(run_id))
        if run.status != "queued":
            return None
        now = _now()
        run.status = "running"
        run.started_at = run.started_at or now
        run.heartbeat_at = now
        run.lease_expires_at = now + datetime.timedelta(seconds=LEASE_TTL_SECONDS)
        run.worker_id = worker_id
        await session.commit()
        return run.tenant_id, run.session_id


async def refresh_run_lease(run_id: uuid.UUID, worker_id: str) -> bool:
    """Bump the lease for a still-running run. Returns False once it is not running."""
    async with SessionLocal() as session:
        now = _now()
        result = await session.execute(
            update(Run)
            .where(
                Run.id == run_id,
                Run.status == "running",
                Run.worker_id == worker_id,
                Run.lease_expires_at.is_not(None),
            )
            .values(
                heartbeat_at=now,
                lease_expires_at=now + datetime.timedelta(seconds=LEASE_TTL_SECONDS),
            )
            .returning(Run.id)
        )
        await session.commit()
        return result.scalar_one_or_none() is not None


async def heartbeat_loop(run_id: uuid.UUID, worker_id: str) -> None:
    """Refresh the lease until the run stops running or the task is cancelled."""
    try:
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
            if not await refresh_run_lease(run_id, worker_id):
                return
    except asyncio.CancelledError:
        raise
    except Exception:
        return


@contextlib.asynccontextmanager
async def run_heartbeat(run_id: uuid.UUID, worker_id: str):  # type: ignore[no-untyped-def]
    """Run a background lease heartbeat for the duration of the block."""
    task = asyncio.create_task(heartbeat_loop(run_id, worker_id))
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
