"""Transcript compaction (m2-22): shrink, no-orphan-tool-results, verify-shrank.

Pure-unit tests for `compact()` plus one loop integration proving a long window
compacts (a `compaction` event is emitted) and the loop still settles.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import compact, estimate_size
from app.core.compaction import _drop_orphan_tool_results
from app.db import SessionLocal, ping_db
from app.models import EventJournal, Message, Part, Run, Tenant, User
from app.models import Session as SessionModel
from app.providers import Finish, MockProvider, TextDelta


def _msgs(n: int, body: str) -> list[dict[str, object]]:
    out: list[dict[str, object]] = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "first question"},
    ]
    for i in range(n):
        out.append({"role": "assistant" if i % 2 else "user", "content": f"{body}-{i}"})
    return out


def test_compact_keeps_head_and_recent_and_shrinks() -> None:
    messages = _msgs(20, "x" * 200)
    before = estimate_size(messages)
    result = compact(messages, keep_head=2, keep_recent=4)
    assert result.shrank is True
    assert result.after < before
    assert len(result.messages) < len(messages)
    # Head preserved.
    assert result.messages[0] == {"role": "system", "content": "sys"}
    assert result.messages[1] == {"role": "user", "content": "first question"}
    # A summary marker replaces the omitted middle.
    assert any("Compacted" in str(m.get("content", "")) for m in result.messages)
    # The most recent messages are preserved verbatim.
    assert result.messages[-4:] == messages[-4:]
    assert result.omitted == 20 + 2 - 2 - 4


def test_compact_rejects_non_shrinking() -> None:
    # Tiny window: a summary marker would be larger than what it replaces.
    messages = _msgs(6, "a")
    result = compact(messages, keep_head=2, keep_recent=2)
    assert result.shrank is False
    assert result.messages is messages  # unchanged


def test_no_orphan_tool_results_after_compaction() -> None:
    # An assistant tool_call in the middle, its tool result in the recent window.
    messages: list[dict[str, object]] = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "first"},
    ]
    for i in range(6):
        messages.append({"role": "user", "content": f"pad-{'z' * 300}-{i}"})
    messages.append(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "call-1", "type": "function", "function": {"name": "t", "arguments": "{}"}}
            ],
        }
    )
    messages.append({"role": "tool", "tool_call_id": "call-1", "content": "tool output"})
    messages.append({"role": "user", "content": "latest"})

    result = compact(messages, keep_head=2, keep_recent=2)
    assert result.shrank is True
    # The recent window would start with the orphaned tool result; it must be dropped.
    assert not any(
        m.get("role") == "tool" and str(m.get("tool_call_id")) == "call-1" for m in result.messages
    ), "orphan tool result leaked into the compacted window"


def test_drop_orphan_keeps_paired_tool_results() -> None:
    paired: list[dict[str, object]] = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "t", "arguments": "{}"}}
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "ok"},
    ]
    assert _drop_orphan_tool_results(paired) == paired


async def _seed_long_transcript(s: AsyncSession) -> tuple[uuid.UUID, uuid.UUID, Run]:
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
    for seq in range(1, 13):
        mid = uuid.uuid4()
        s.add(
            Message(
                tenant_id=tid,
                id=mid,
                session_id=sid,
                run_id=rid,
                author_user_id=uid if seq % 2 else None,
                seq=seq,
                role="user" if seq % 2 else "assistant",
            )
        )
        await s.flush()
        s.add(
            Part(
                tenant_id=tid,
                id=uuid.uuid4(),
                message_id=mid,
                ordinal=0,
                kind="text",
                content_redacted={"text": "padding " * 60},
            )
        )
        await s.flush()
    return tid, rid, run


@pytest.mark.asyncio
async def test_loop_compacts_long_window(monkeypatch: pytest.MonkeyPatch) -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    from app.config import settings

    monkeypatch.setattr(settings, "compaction_char_budget", 200)
    monkeypatch.setattr(settings, "compaction_keep_head", 2)
    monkeypatch.setattr(settings, "compaction_keep_recent", 4)

    async with SessionLocal() as s:
        try:
            tid, rid, run = await _seed_long_transcript(s)
            from app.core import execute_run
            from app.tools import build_default_registry

            reason = await execute_run(
                s,
                run=run,
                provider=MockProvider(script=[[TextDelta("done"), Finish("stop")]]),
                registry=build_default_registry(),
                tier="full",
            )
            assert reason == "completed"
            assert run.status == "succeeded"

            types = (
                (
                    await s.execute(
                        select(EventJournal.event_type).where(
                            EventJournal.tenant_id == tid, EventJournal.run_id == rid
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert "compaction" in set(types)
        finally:
            await s.rollback()
