"""Regression: cross-run provider history carries prior tool_use (item 0 fix).

Reproduces the reported bug — a follow-up prompt (a new run) must let the model
see that it already called a tool in the previous run, instead of rebuilding a
text-only transcript and "forgetting" the call. Integration test: skips without
a database; seeds + rolls back.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Sequence

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import execute_run
from app.core.history import assemble_provider_history
from app.db import SessionLocal, ping_db
from app.models import Message, Part, Run, Tenant, User
from app.models import Session as SessionModel
from app.providers import (
    Finish,
    MockProvider,
    ProviderEvent,
    TextDelta,
    ToolCall,
    ToolSchema,
)
from app.providers import (
    Message as ProviderMessage,
)
from app.tools import build_default_registry


class RecordingProvider:
    """Wraps a scripted mock and captures the messages passed to each stream call."""

    name = "recording"

    def __init__(self, script: Sequence[Sequence[ProviderEvent]]) -> None:
        self._mock = MockProvider(script=script)
        self.calls: list[list[ProviderMessage]] = []

    async def stream(
        self,
        *,
        messages: list[ProviderMessage],
        tools: list[ToolSchema] | None = None,
        model: str | None = None,
    ) -> AsyncIterator[ProviderEvent]:
        self.calls.append([dict(m) for m in messages])
        async for event in self._mock.stream(messages=messages, tools=tools, model=model):
            yield event


async def _seed_session(s: AsyncSession) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    tid, uid, sid = (uuid.uuid4() for _ in range(3))
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
    return tid, uid, sid


async def _add_prompt_run(
    s: AsyncSession, tid: uuid.UUID, uid: uuid.UUID, sid: uuid.UUID, seq: int, text: str
) -> Run:
    rid = uuid.uuid4()
    run = Run(
        tenant_id=tid,
        id=rid,
        session_id=sid,
        run_kind="web_chat",
        prompt_version="v1",
        admitted_seq=seq,
    )
    s.add(run)
    await s.flush()
    mid = uuid.uuid4()
    s.add(
        Message(
            tenant_id=tid,
            id=mid,
            session_id=sid,
            run_id=rid,
            author_user_id=uid,
            seq=seq,
            role="user",
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
            content_redacted={"text": text},
        )
    )
    await s.flush()
    return run


def _tool_call_names(messages: list[dict[str, object]]) -> list[str]:
    names: list[str] = []
    for m in messages:
        calls = m.get("tool_calls") if m.get("role") == "assistant" else None
        if isinstance(calls, list):
            for c in calls:
                fn = c.get("function", {}) if isinstance(c, dict) else {}
                names.append(str(fn.get("name", "")))
    return names


@pytest.mark.asyncio
async def test_assembled_history_carries_tool_use() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid, sid = await _seed_session(s)
            run1 = await _add_prompt_run(s, tid, uid, sid, 1, "what time is it?")
            await execute_run(
                s,
                run=run1,
                provider=MockProvider(
                    script=[
                        [ToolCall(id="c1", name="core_get_time", args={}), Finish("tool_use")],
                        [TextDelta("It is 3pm."), Finish("stop")],
                    ]
                ),
                registry=build_default_registry(),
                tier="full",
            )

            history = await assemble_provider_history(s, tid, sid)

            # The prior tool call is present as an assistant tool_calls entry...
            assert "core_get_time" in _tool_call_names(history)
            # ...followed by a role:tool result (protocol-valid pairing)...
            tool_msgs = [m for m in history if m.get("role") == "tool"]
            assert any(m.get("tool_call_id") == "c1" for m in tool_msgs)
            # ...and the user prompt + final answer survive as well.
            assert any(
                m.get("role") == "user" and m.get("content") == "what time is it?" for m in history
            )
            assert any(
                m.get("role") == "assistant" and m.get("content") == "It is 3pm." for m in history
            )
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_second_run_provider_sees_prior_tool_use() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid, sid = await _seed_session(s)
            run1 = await _add_prompt_run(s, tid, uid, sid, 1, "what time is it?")
            await execute_run(
                s,
                run=run1,
                provider=MockProvider(
                    script=[
                        [ToolCall(id="c1", name="core_get_time", args={}), Finish("tool_use")],
                        [TextDelta("It is 3pm."), Finish("stop")],
                    ]
                ),
                registry=build_default_registry(),
                tier="full",
            )

            # Follow-up prompt = a NEW run. The model must see run 1's tool_use.
            run2 = await _add_prompt_run(s, tid, uid, sid, 3, "and now?")
            recording = RecordingProvider(script=[[TextDelta("Still 3pm."), Finish("stop")]])
            await execute_run(
                s,
                run=run2,
                provider=recording,
                registry=build_default_registry(),
                tier="full",
            )

            first_call = recording.calls[0]
            assert "core_get_time" in _tool_call_names(first_call), (
                "run 2's model call must include run 1's tool_use — otherwise the "
                "model 'forgets' it called the tool"
            )
            assert any(
                m.get("role") == "tool" and m.get("tool_call_id") == "c1" for m in first_call
            )
        finally:
            await s.rollback()
