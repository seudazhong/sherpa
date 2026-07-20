"""Tool output spill through the loop (m-tools T8, api.md §7.2)."""

from __future__ import annotations

import pathlib
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core import execute_run
from app.db import SessionLocal, ping_db
from app.models import EffectInvocation, Run, Tenant, User
from app.models import Session as SessionModel
from app.providers import Finish, MockProvider, TextDelta, ToolCall
from app.tools import ToolContext, ToolFlags, ToolRegistry, ToolResult


class _BigTool:
    name = "big_output"
    description = "Returns a very large output (for spill testing)."
    input_schema: dict[str, object] = {"type": "object", "properties": {}}
    flags = ToolFlags(is_read_only=True)

    async def execute(self, ctx: ToolContext, args: dict[str, object]) -> ToolResult:
        return ToolResult(llm_content="line\n" * 5000)


async def _seed(s: AsyncSession) -> tuple[uuid.UUID, uuid.UUID, Run]:
    tid, uid, sid, rid = (uuid.uuid4() for _ in range(4))
    s.add(Tenant(tenant_id=tid, slug=f"t-{tid.hex[:8]}", display_name="T", kind="personal"))
    await s.flush()
    s.add(User(tenant_id=tid, id=uid, email="d@e.co", display_name="D", status="active"))
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
    run = Run(tenant_id=tid, id=rid, session_id=sid, run_kind="web_chat", prompt_version="v1")
    s.add(run)
    await s.flush()
    return tid, rid, run


@pytest.mark.asyncio
async def test_loop_spills_oversized_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    monkeypatch.setattr(settings, "tool_output_root", str(tmp_path))
    async with SessionLocal() as s:
        try:
            tid, rid, run = await _seed(s)
            reg = ToolRegistry()
            reg.register(_BigTool(), safe=False)
            provider = MockProvider(
                script=[
                    [ToolCall(id="c1", name="big_output", args={}), Finish("tool_use")],
                    [TextDelta("done"), Finish("stop")],
                ]
            )
            reason = await execute_run(s, run=run, provider=provider, registry=reg, tier="full")
            assert reason == "completed"

            inv = (
                await s.execute(
                    select(EffectInvocation).where(
                        EffectInvocation.tenant_id == tid, EffectInvocation.run_id == rid
                    )
                )
            ).scalar_one()
            assert inv.result_redacted is not None
            assert inv.result_redacted["truncated"] is True
            ref = inv.result_redacted["spill_ref"]
            assert isinstance(ref, str) and ref.startswith("tool-output:")

            spilled = tmp_path / f"{ref.split(':', 1)[1]}.txt"
            assert spilled.exists()
            assert spilled.read_text(encoding="utf-8").count("line") == 5000
        finally:
            await s.rollback()
