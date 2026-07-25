"""Single source of truth for OpenTelemetry GenAI span/attribute names (ADR-033).

The OTel GenAI semantic conventions are still *Development* status, so their
attribute names churn between releases. Every `gen_ai.*` / `agent.*` name the
loop emits is centralized here (mirroring the OTel GenAI semconv where one
exists, plus Sherpa-specific `agent.*` keys) so that a semconv change touches
exactly one module. No content is ever recorded here — attributes are
low-cardinality metadata only (names, ids, counts, reasons), never prompt or
tool text.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from opentelemetry.trace import Status, StatusCode

from app.security.redaction import redact

if TYPE_CHECKING:
    from opentelemetry.trace import Span

    from app.providers import ToolCall

# ---------------------------------------------------------------------------
# Span names (low-cardinality; values go in attributes, never the span name).
# ---------------------------------------------------------------------------
SPAN_INVOKE_AGENT = "invoke_agent"
SPAN_CHAT = "chat"
SPAN_EXECUTE_TOOL = "execute_tool"

# ---------------------------------------------------------------------------
# GenAI semconv attribute names (OTel GenAI, Development status).
# ---------------------------------------------------------------------------
OPERATION_NAME = "gen_ai.operation.name"
SYSTEM = "gen_ai.system"  # provider family, e.g. "openai_compatible" / "mock"
REQUEST_MODEL = "gen_ai.request.model"
REQUEST_TEMPERATURE = "gen_ai.request.temperature"
REQUEST_MAX_TOKENS = "gen_ai.request.max_tokens"
RESPONSE_MODEL = "gen_ai.response.model"
RESPONSE_FINISH_REASONS = "gen_ai.response.finish_reasons"
USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
TOOL_NAME = "gen_ai.tool.name"
TOOL_CALL_ID = "gen_ai.tool.call.id"

# Operation-name attribute values.
OP_INVOKE_AGENT = "invoke_agent"
OP_CHAT = "chat"
OP_EXECUTE_TOOL = "execute_tool"

# ---------------------------------------------------------------------------
# Sherpa-specific `agent.*` attributes (no semconv equivalent).
# ---------------------------------------------------------------------------
AGENT_RUN_ID = "agent.run_id"
AGENT_SESSION_ID = "agent.session_id"
AGENT_TENANT_ID = "agent.tenant_id"
AGENT_LOOP_COUNT = "agent.loop_count"
AGENT_STOP_REASON = "agent.stop_reason"
AGENT_TOTAL_COST_USD = "agent.total_cost_usd"
AGENT_TOOL_SUCCESS = "agent.tool.success"

# ---------------------------------------------------------------------------
# OpenInference semantic conventions (Phoenix-native; Arize-ai/openinference).
# Phoenix ingests these directly and renders the messages / tools / waterfall.
# Used only when content capture is on (OBSB.1, ADR-033 Phase B).
# ---------------------------------------------------------------------------
SPAN_KIND = "openinference.span.kind"
KIND_AGENT = "AGENT"
KIND_LLM = "LLM"
KIND_TOOL = "TOOL"

INPUT_VALUE = "input.value"
INPUT_MIME = "input.mime_type"
OUTPUT_VALUE = "output.value"
OUTPUT_MIME = "output.mime_type"
MIME_JSON = "application/json"
MIME_TEXT = "text/plain"

# Per-field size cap for captured content (chars). The flattened input.value gets
# a larger budget since it carries the whole assembled window.
_MAX_FIELD_CHARS = 8_000
_MAX_FLATTENED_CHARS = 32_000


def _bounded(text: str, limit: int = _MAX_FIELD_CHARS) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n…[truncated {len(text) - limit} chars]"


def set_attrs(span: Span, attrs: dict[str, Any]) -> None:
    """Set span attributes, skipping any whose value is None.

    Keeps call sites terse when only part of the metadata is available (e.g. a
    provider that does not report usage). Never pass prompt or tool *content*
    here — attributes are metadata only.
    """
    for key, value in attrs.items():
        if value is not None:
            span.set_attribute(key, value)


def capture_llm_io(
    span: Span,
    *,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    output_text: str,
    output_tool_calls: list[ToolCall],
) -> None:
    """Attach the full assembled prompt + response as OpenInference attributes.

    Content capture (OBSB.1, ADR-033 §决策4). **Caller-gated** — only invoke when
    `otel_capture_message_content` is on. Message content text is preserved (that
    is the debug value the owner wants to see) but size-capped; structured parts
    (tool schemas, tool-call arguments) pass through key-wise secret redaction.
    API keys live in the HTTP header, never in `messages`; redaction is defense
    in depth for anything a user stored under a sensitive-named field.
    """
    for i, m in enumerate(messages):
        span.set_attribute(f"llm.input_messages.{i}.message.role", str(m.get("role", "")))
        content = m.get("content")
        if content is None and m.get("tool_calls"):
            content = json.dumps(redact(m["tool_calls"]), ensure_ascii=False)
        if content is not None:
            span.set_attribute(f"llm.input_messages.{i}.message.content", _bounded(str(content)))

    for i, tool in enumerate(tools or []):
        span.set_attribute(
            f"llm.tools.{i}.tool.json_schema",
            _bounded(json.dumps(redact(tool), ensure_ascii=False)),
        )

    span.set_attribute("llm.output_messages.0.message.role", "assistant")
    if output_text:
        span.set_attribute("llm.output_messages.0.message.content", _bounded(output_text))
    if output_tool_calls:
        calls = [{"name": tc.name, "arguments": tc.args} for tc in output_tool_calls]
        span.set_attribute(
            "llm.output_messages.0.message.tool_calls",
            _bounded(json.dumps(redact(calls), ensure_ascii=False)),
        )

    span.set_attribute(
        INPUT_VALUE,
        _bounded(json.dumps(redact(messages), ensure_ascii=False), _MAX_FLATTENED_CHARS),
    )
    span.set_attribute(INPUT_MIME, MIME_JSON)
    if output_text:
        span.set_attribute(OUTPUT_VALUE, _bounded(output_text))
        span.set_attribute(OUTPUT_MIME, MIME_TEXT)


def record_tool_result(span: Span, *, success: bool) -> None:
    """Record a tool execution outcome: `agent.tool.success` + ERROR status on failure.

    Only for tools that actually ran (or were refused). A gated tool awaiting
    approval leaves `agent.tool.success` unset — it neither succeeded nor failed.
    """
    span.set_attribute(AGENT_TOOL_SUCCESS, success)
    if not success:
        span.set_status(Status(StatusCode.ERROR))
