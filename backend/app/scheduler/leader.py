"""Single-fire leader election via Redis SET NX (ADR-017).

Only the worker that wins the lock runs a scheduler tick, so a slot fires at
most once even with multiple workers. The lock auto-expires (PX) so a crashed
leader does not wedge the schedule.
"""

from __future__ import annotations

from app.redis_client import client as redis

_PREFIX = "sherpa:v1:leader:"


async def try_acquire_leader(name: str, ttl_ms: int = 55_000) -> bool:
    """Return True iff this caller acquired the named leader lock."""
    acquired = await redis.set(_PREFIX + name, "1", nx=True, px=ttl_ms)
    return bool(acquired)


async def release_leader(name: str) -> None:
    await redis.delete(_PREFIX + name)
