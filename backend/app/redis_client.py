"""Async Redis client (queue + streams + locks).

Contract: docs/contracts/events-and-effects.md. The client is created lazily;
importing this module does NOT open a connection (safe for tests).
"""

from __future__ import annotations

import redis.asyncio as aioredis

from app.config import settings

client: aioredis.Redis = aioredis.from_url(settings.redis_url, decode_responses=True)


async def ping_redis() -> bool:
    """Return True if Redis answers PING."""
    try:
        return bool(await client.ping())
    except Exception:
        return False
