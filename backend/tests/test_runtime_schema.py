"""Phase TR P4 runtime exec persistence contract."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.db import SessionLocal, ping_db


@pytest.mark.asyncio
async def test_project_exec_run_dispatch_columns_and_unique_index_exist() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as session:
        columns = set(
            (
                await session.execute(
                    text(
                        """
                        SELECT column_name
                        FROM information_schema.columns
                        WHERE table_schema = 'public'
                          AND table_name = 'project_exec_runs'
                        """
                    )
                )
            )
            .scalars()
            .all()
        )
        assert {
            "invocation_id",
            "command_text",
            "timeout_seconds",
            "stdout_head",
            "stderr_tail",
            "cancel_requested_at",
        } <= columns
        index = await session.scalar(
            text(
                """
                SELECT indexdef
                FROM pg_indexes
                WHERE schemaname = 'public'
                  AND tablename = 'project_exec_runs'
                  AND indexname = 'uq_per_invocation'
                """
            )
        )
        assert index is not None
        assert "UNIQUE INDEX" in index
        assert "invocation_id IS NOT NULL" in index
