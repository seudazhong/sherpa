"""Async database engine + session (SQLAlchemy 2.0 / asyncpg).

Contract: docs/contracts/data-model.md. Engine/session are created lazily;
importing this module does NOT open a connection (safe for tests).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings

engine: AsyncEngine = create_async_engine(settings.database_url, pool_pre_ping=True)
SessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a request-scoped session."""
    async with SessionLocal() as session:
        yield session


async def ping_db() -> bool:
    """Return True if the database answers `SELECT 1`."""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
