"""Observability over a real loop run (OBS.3 + OBS.4, ADR-033).

With an InMemorySpanExporter + OTEL enabled, a scripted mock run must produce:
- a span tree invoke_agent > {chat, chat, execute_tool} with real token attrs,
  finish_reasons, and the tool span marked success;
- NO prompt/tool content on any span (every attribute key is gen_ai.*/agent.*);
- durable model.request/model.response debug events carrying a sha-256 digest and
  counts only (no content);
- one generations row per model call with real usage.

Integration test — skips when no database is reachable; rolls back.
"""

from __future__ import annotations

import uuid

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core import execute_run
from app.db import SessionLocal, ping_db
from app.models import EventJournal, Generation, Message, Part, Run, Tenant, Trace, User
from app.models import Session as SessionModel
from app.observability import genai
from app.observability.otel import configure_tracing, reset_tracing
from app.observability.projection import project_run_trace
from app.providers import Finish, MockProvider, TextDelta, ToolCall
from app.tools import build_default_registry

_PROMPT = "what time is it right now exactly?"


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
    mid = uuid.uuid4()
    s.add(
        Message(
            tenant_id=tid,
            id=mid,
            session_id=sid,
            run_id=rid,
            author_user_id=uid,
            seq=1,
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
            content_redacted={"text": _PROMPT},
        )
    )
    await s.flush()
    return tid, rid, run


@pytest.mark.asyncio
async def test_loop_emits_span_tree_tokens_and_generations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    monkeypatch.setattr(settings, "otel_enabled", True)
    exporter = InMemorySpanExporter()
    configure_tracing(force=True, exporter=exporter)
    async with SessionLocal() as s:
        try:
            tid, rid, run = await _seed(s)
            provider = MockProvider(
                script=[
                    [
                        ToolCall(id="c1", name="get_time", args={}),
                        Finish("tool_use", input_tokens=100, output_tokens=10),
                    ],
                    [
                        TextDelta("It is time to work."),
                        Finish("stop", input_tokens=120, output_tokens=8),
                    ],
                ]
            )
            reason = await execute_run(
                s, run=run, provider=provider, registry=build_default_registry(), tier="full"
            )
            await project_run_trace(s, tenant_id=tid, run_id=rid)
            assert reason == "completed"

            spans = exporter.get_finished_spans()
            by_name: dict[str, list] = {}
            for sp in spans:
                by_name.setdefault(sp.name, []).append(sp)

            # Span tree: one root invoke_agent, two chat calls, one execute_tool.
            assert len(by_name[genai.SPAN_INVOKE_AGENT]) == 1
            assert len(by_name[genai.SPAN_CHAT]) == 2
            assert len(by_name[genai.SPAN_EXECUTE_TOOL]) == 1
            root = by_name[genai.SPAN_INVOKE_AGENT][0]
            assert root.parent is None
            root_id = root.context.span_id
            for child in by_name[genai.SPAN_CHAT] + by_name[genai.SPAN_EXECUTE_TOOL]:
                assert child.parent is not None
                assert child.parent.span_id == root_id

            # Real token + finish attrs on the chat spans.
            chat_inputs = sorted(
                int(c.attributes[genai.USAGE_INPUT_TOKENS]) for c in by_name[genai.SPAN_CHAT]
            )
            assert chat_inputs == [100, 120]
            assert all(
                genai.RESPONSE_FINISH_REASONS in c.attributes for c in by_name[genai.SPAN_CHAT]
            )

            # Tool span succeeded; root carries loop_count + stop_reason.
            tool_span = by_name[genai.SPAN_EXECUTE_TOOL][0]
            assert tool_span.attributes[genai.TOOL_NAME] == "get_time"
            assert tool_span.attributes[genai.AGENT_TOOL_SUCCESS] is True
            assert root.attributes[genai.AGENT_STOP_REASON] == "completed"
            assert int(root.attributes[genai.AGENT_LOOP_COUNT]) == 2

            # Content capture OFF by default: every attribute key is
            # gen_ai.*/agent.*/openinference.span.kind, no llm.* content attrs,
            # and no value leaks the prompt text.
            for sp in spans:
                for key, value in (sp.attributes or {}).items():
                    assert key.startswith(("gen_ai.", "agent.", "openinference.")), key
                    assert not key.startswith("llm."), key
                    assert _PROMPT not in str(value)

            # Durable model.request/response debug events: digest + counts, no content.
            events = (
                (
                    await s.execute(
                        select(EventJournal).where(
                            EventJournal.tenant_id == tid,
                            EventJournal.run_id == rid,
                            EventJournal.event_type.in_(["model.request", "model.response"]),
                        )
                    )
                )
                .scalars()
                .all()
            )
            reqs = [e for e in events if e.event_type == "model.request"]
            resps = [e for e in events if e.event_type == "model.response"]
            assert len(reqs) == 2 and len(resps) == 2
            for e in events:
                assert e.durability == "debug"
                payload = e.payload_redacted
                assert "content" not in payload
                assert "text" not in payload
                assert "messages" not in payload
            for e in reqs:
                assert len(e.payload_redacted["input_digest"]) == 64
                assert e.payload_redacted["input_chars"] > 0
                assert _PROMPT not in str(e.payload_redacted)

            # One generations row per call with real usage.
            gens = (
                (
                    await s.execute(
                        select(Generation).where(
                            Generation.tenant_id == tid, Generation.run_id == rid
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(gens) == 2
            assert all(g.purpose == "web_chat" for g in gens)
            assert sorted(g.input_tokens for g in gens) == [100, 120]
            assert sorted(g.output_tokens for g in gens) == [8, 10]

            # Trace rollup uses real tokens (220/18), not the chars/4 estimate.
            trace = (
                await s.execute(select(Trace).where(Trace.tenant_id == tid, Trace.run_id == rid))
            ).scalar_one()
            assert trace.tags["input_tokens"] == 220
            assert trace.tags["output_tokens"] == 18
        finally:
            await s.rollback()
            reset_tracing()


@pytest.mark.asyncio
async def test_tool_error_marks_execute_tool_span_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    from opentelemetry.trace import StatusCode

    monkeypatch.setattr(settings, "otel_enabled", True)
    exporter = InMemorySpanExporter()
    configure_tracing(force=True, exporter=exporter)
    async with SessionLocal() as s:
        try:
            _tid, _rid, run = await _seed(s)
            # An unknown tool name -> registry.get raises ToolError -> error observation.
            provider = MockProvider(
                script=[
                    [ToolCall(id="c1", name="no_such_tool", args={}), Finish("tool_use")],
                    [TextDelta("done"), Finish("stop")],
                ]
            )
            await execute_run(
                s, run=run, provider=provider, registry=build_default_registry(), tier="full"
            )
            tool_spans = [
                sp for sp in exporter.get_finished_spans() if sp.name == genai.SPAN_EXECUTE_TOOL
            ]
            assert len(tool_spans) == 1
            span = tool_spans[0]
            assert span.status.status_code == StatusCode.ERROR
            assert span.attributes[genai.AGENT_TOOL_SUCCESS] is False
        finally:
            await s.rollback()
            reset_tracing()


@pytest.mark.asyncio
async def test_disabled_otel_writes_no_model_events_and_estimates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    monkeypatch.setattr(settings, "otel_enabled", False)
    reset_tracing()
    async with SessionLocal() as s:
        try:
            tid, rid, run = await _seed(s)
            provider = MockProvider(script=[[TextDelta("hi"), Finish("stop")]])
            await execute_run(
                s, run=run, provider=provider, registry=build_default_registry(), tier="full"
            )
            await project_run_trace(s, tenant_id=tid, run_id=rid)

            events = (
                (
                    await s.execute(
                        select(EventJournal.event_type).where(
                            EventJournal.tenant_id == tid,
                            EventJournal.run_id == rid,
                            EventJournal.event_type.in_(["model.request", "model.response"]),
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert events == []  # zero overhead when disabled: no debug journal
            gens = (
                (
                    await s.execute(
                        select(Generation).where(
                            Generation.tenant_id == tid, Generation.run_id == rid
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert gens == []  # no per-call usage -> no generation rows
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_content_capture_on_writes_openinference_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OBSB.1/OBSB.2: with content capture on, the chat span carries the full
    assembled prompt + response as OpenInference attrs, and the spans carry
    OpenInference span kinds (AGENT/LLM/TOOL) so Phoenix renders them."""
    if not await ping_db():
        pytest.skip("database not reachable")
    monkeypatch.setattr(settings, "otel_enabled", True)
    monkeypatch.setattr(settings, "otel_capture_message_content", True)
    exporter = InMemorySpanExporter()
    configure_tracing(force=True, exporter=exporter)
    async with SessionLocal() as s:
        try:
            _tid, _rid, run = await _seed(s)
            provider = MockProvider(
                script=[
                    [ToolCall(id="c1", name="get_time", args={}), Finish("tool_use")],
                    [TextDelta("It is time to work."), Finish("stop")],
                ]
            )
            await execute_run(
                s, run=run, provider=provider, registry=build_default_registry(), tier="full"
            )
            spans = exporter.get_finished_spans()
            by_name = {sp.name: sp for sp in spans}
            # Span kinds for Phoenix classification.
            assert by_name[genai.SPAN_INVOKE_AGENT].attributes[genai.SPAN_KIND] == genai.KIND_AGENT
            assert by_name[genai.SPAN_CHAT].attributes[genai.SPAN_KIND] == genai.KIND_LLM
            assert by_name[genai.SPAN_EXECUTE_TOOL].attributes[genai.SPAN_KIND] == genai.KIND_TOOL
            # The chat span carries the assembled prompt (system incl. prompt) + response.
            chat = next(sp for sp in spans if sp.name == genai.SPAN_CHAT)
            a = dict(chat.attributes or {})
            assert a["llm.input_messages.0.message.role"] == "system"
            assert _PROMPT in str(a["input.value"])  # the user's prompt is in the window
            assert any(k.startswith("llm.tools.") for k in a)  # tool schemas captured
        finally:
            await s.rollback()
            reset_tracing()


@pytest.mark.asyncio
async def test_loop_logs_llm_and_tool_calls_without_otel(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """LOG.2/LOG.3: one structured 'llm call' + 'tool call' line per step, even
    with OTEL off (stdout is useful by default)."""
    if not await ping_db():
        pytest.skip("database not reachable")
    monkeypatch.setattr(settings, "otel_enabled", False)
    reset_tracing()
    async with SessionLocal() as s:
        try:
            _tid, _rid, run = await _seed(s)
            provider = MockProvider(
                script=[
                    [ToolCall(id="c1", name="get_time", args={}), Finish("tool_use")],
                    [TextDelta("It is time."), Finish("stop")],
                ]
            )
            with caplog.at_level("INFO", logger="app.core.loop"):
                await execute_run(
                    s, run=run, provider=provider, registry=build_default_registry(), tier="full"
                )

            llm = [r for r in caplog.records if r.message == "llm call"]
            tools = [r for r in caplog.records if r.message == "tool call"]
            assert len(llm) == 2  # tool_use turn + final answer
            assert {r.__dict__["finish_reason"] for r in llm} == {"tool_use", "stop"}
            assert all(r.__dict__["provider"] == "mock" for r in llm)
            assert all("latency_ms" in r.__dict__ for r in llm)
            assert len(tools) == 1
            assert tools[0].__dict__["tool"] == "get_time"
            assert tools[0].__dict__["outcome"] == "ok"
        finally:
            await s.rollback()
