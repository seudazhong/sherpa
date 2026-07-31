"""OTel bootstrap + genai wrapper (OBS.1, ADR-033).

No DB. Asserts the disabled default is a true no-op (non-recording spans, never
exported) and that forcing a provider with an in-memory exporter records spans
with the centralized `gen_ai.*` attribute names; `None` attribute values are
dropped so partial metadata stays terse.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.observability import genai
from app.observability.logging import JsonFormatter
from app.observability.otel import (
    configure_tracing,
    get_tracer,
    reset_tracing,
    tracing_enabled,
)


def _format(record: logging.LogRecord) -> dict[str, object]:
    return json.loads(JsonFormatter().format(record))


def _record() -> logging.LogRecord:
    return logging.LogRecord(
        name="app.core.loop",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="llm call",
        args=(),
        exc_info=None,
    )


@pytest.fixture(autouse=True)
def _clean_tracing() -> Iterator[None]:
    reset_tracing()
    yield
    reset_tracing()


def test_disabled_by_default_is_noop() -> None:
    # settings.otel_enabled defaults False -> configure installs nothing.
    assert configure_tracing() is None
    assert tracing_enabled() is False
    tracer = get_tracer()
    assert type(tracer).__name__ == "NoOpTracer"
    with tracer.start_as_current_span("x") as span:
        assert span.is_recording() is False


def test_log_has_no_trace_id_when_tracing_disabled() -> None:
    # LOG.4: no active/valid span -> the formatter adds no trace correlation.
    payload = _format(_record())
    assert "trace_id" not in payload
    assert "span_id" not in payload


def test_log_carries_trace_id_when_span_active() -> None:
    # LOG.4: inside an active span, every log line carries trace_id/span_id so
    # logs and the OTel trace correlate.
    configure_tracing(force=True, exporter=InMemorySpanExporter())
    with get_tracer("sherpa").start_as_current_span(genai.SPAN_CHAT):
        payload = _format(_record())
    assert len(str(payload["trace_id"])) == 32
    assert len(str(payload["span_id"])) == 16
    assert int(str(payload["trace_id"]), 16) != 0


def test_forced_provider_records_and_exports() -> None:
    exporter = InMemorySpanExporter()
    provider = configure_tracing(force=True, exporter=exporter)
    assert provider is not None
    assert tracing_enabled() is True

    tracer = get_tracer("sherpa")
    with tracer.start_as_current_span(genai.SPAN_CHAT) as span:
        genai.set_attrs(
            span,
            {
                genai.OPERATION_NAME: genai.OP_CHAT,
                genai.REQUEST_MODEL: "claude-sonnet-4.6",
                genai.USAGE_INPUT_TOKENS: 12,
                genai.USAGE_OUTPUT_TOKENS: None,  # dropped
            },
        )

    finished = exporter.get_finished_spans()
    assert [s.name for s in finished] == [genai.SPAN_CHAT]
    attrs = dict(finished[0].attributes or {})
    assert attrs[genai.OPERATION_NAME] == genai.OP_CHAT
    assert attrs[genai.REQUEST_MODEL] == "claude-sonnet-4.6"
    assert attrs[genai.USAGE_INPUT_TOKENS] == 12
    assert genai.USAGE_OUTPUT_TOKENS not in attrs  # None was skipped


def test_reset_returns_to_noop() -> None:
    configure_tracing(force=True, exporter=InMemorySpanExporter())
    assert tracing_enabled() is True
    reset_tracing()
    assert tracing_enabled() is False
    assert type(get_tracer()).__name__ == "NoOpTracer"


def test_disabled_bootstrap_says_so_once(caplog: pytest.LogCaptureFixture) -> None:
    # Backlog B-4: a silently-off exporter is indistinguishable from a healthy
    # stack, so the disabled path must announce itself (and how to turn it on).
    with caplog.at_level(logging.INFO, logger="app.observability.otel"):
        assert configure_tracing() is None
    records = [r for r in caplog.records if r.name == "app.observability.otel"]
    assert len(records) == 1
    assert "tracing disabled" in records[0].getMessage()
    assert "OTEL_ENABLED=true" in records[0].getMessage()
    assert records[0].otel_enabled is False


def test_enabled_bootstrap_logs_exporter_and_endpoint(caplog: pytest.LogCaptureFixture) -> None:
    # The enabled path states where spans go, so "Phoenix stopped collecting"
    # can be diagnosed from `docker compose logs` alone.
    with caplog.at_level(logging.INFO, logger="app.observability.otel"):
        configure_tracing(force=True, exporter=InMemorySpanExporter())
    records = [r for r in caplog.records if r.name == "app.observability.otel"]
    assert len(records) == 1
    assert records[0].getMessage() == "tracing enabled"
    assert records[0].otel_enabled is True
    assert records[0].otel_exporter == "explicit"
    assert records[0].otel_sampler == "always_on"
    assert records[0].otel_capture_message_content is False


def test_genai_attribute_names_are_stable() -> None:
    # Contract: the wrapper is the single source of truth for these names.
    assert genai.SYSTEM == "gen_ai.system"
    assert genai.REQUEST_MODEL == "gen_ai.request.model"
    assert genai.RESPONSE_FINISH_REASONS == "gen_ai.response.finish_reasons"
    assert genai.USAGE_INPUT_TOKENS == "gen_ai.usage.input_tokens"
    assert genai.TOOL_NAME == "gen_ai.tool.name"
    assert genai.AGENT_RUN_ID == "agent.run_id"
    assert genai.AGENT_STOP_REASON == "agent.stop_reason"
    assert genai.SPAN_KIND == "openinference.span.kind"


def test_capture_llm_io_writes_openinference_attrs_redacted_and_bounded() -> None:
    # OBSB.1: full assembled prompt + response as OpenInference attrs, with
    # secret-named fields masked, message text preserved, and size caps applied.
    from app.providers import ToolCall

    configure_tracing(force=True, exporter=(exp := InMemorySpanExporter()))
    tracer = get_tracer("sherpa")
    big = "x" * 20_000
    with tracer.start_as_current_span(genai.SPAN_CHAT) as span:
        genai.capture_llm_io(
            span,
            messages=[
                {"role": "system", "content": "You are Sherpa.\nMemory: user likes tea."},
                {"role": "user", "content": big},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {"name": "lookup", "arguments": '{"q":"x"}'},
                        }
                    ],
                },
            ],
            tools=[
                {"name": "email.send", "input_schema": {"type": "object"}, "api_key": "sk-TOOL"}
            ],
            output_text="Here you go.",
            output_tool_calls=[ToolCall(id="c2", name="core.get_time", args={})],
        )

    attrs = dict(exp.get_finished_spans()[0].attributes or {})
    # Messages captured verbatim (the debug value), incl. the injected memory.
    assert attrs["llm.input_messages.0.message.role"] == "system"
    assert "Memory: user likes tea." in str(attrs["llm.input_messages.0.message.content"])
    # Size cap on a huge message.
    assert "…[truncated" in str(attrs["llm.input_messages.1.message.content"])
    # Assistant tool calls flattened into OpenInference indexed sub-attrs.
    in_tc = "llm.input_messages.2.message.tool_calls.0.tool_call"
    assert attrs[f"{in_tc}.function.name"] == "lookup"
    assert "q" in str(attrs[f"{in_tc}.function.arguments"])
    # Secret-named fields masked in structured parts (tool schema).
    assert "sk-TOOL" not in str(attrs["llm.tools.0.tool.json_schema"])
    assert "email.send" in str(attrs["llm.tools.0.tool.json_schema"])
    # Output + flattened value present.
    assert attrs["llm.output_messages.0.message.role"] == "assistant"
    assert attrs["llm.output_messages.0.message.content"] == "Here you go."
    # Tool calls flattened into OpenInference indexed sub-attrs (NOT a JSON string
    # — Phoenix .map()s over these, so a string would crash the trace view).
    out_tc_name = "llm.output_messages.0.message.tool_calls.0.tool_call.function.name"
    assert attrs[out_tc_name] == "core.get_time"
    assert "llm.output_messages.0.message.tool_calls" not in attrs  # no scalar string attr
    assert attrs["input.mime_type"] == genai.MIME_JSON
    assert "You are Sherpa." in str(attrs["input.value"])
