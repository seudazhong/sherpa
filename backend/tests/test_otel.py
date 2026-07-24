"""OTel bootstrap + genai wrapper (OBS.1, ADR-033).

No DB. Asserts the disabled default is a true no-op (non-recording spans, never
exported) and that forcing a provider with an in-memory exporter records spans
with the centralized `gen_ai.*` attribute names; `None` attribute values are
dropped so partial metadata stays terse.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.observability import genai
from app.observability.otel import (
    configure_tracing,
    get_tracer,
    reset_tracing,
    tracing_enabled,
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


def test_genai_attribute_names_are_stable() -> None:
    # Contract: the wrapper is the single source of truth for these names.
    assert genai.SYSTEM == "gen_ai.system"
    assert genai.REQUEST_MODEL == "gen_ai.request.model"
    assert genai.RESPONSE_FINISH_REASONS == "gen_ai.response.finish_reasons"
    assert genai.USAGE_INPUT_TOKENS == "gen_ai.usage.input_tokens"
    assert genai.TOOL_NAME == "gen_ai.tool.name"
    assert genai.AGENT_RUN_ID == "agent.run_id"
    assert genai.AGENT_STOP_REASON == "agent.stop_reason"
