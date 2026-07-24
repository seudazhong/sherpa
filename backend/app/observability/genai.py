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

from typing import TYPE_CHECKING, Any

from opentelemetry.trace import Status, StatusCode

if TYPE_CHECKING:
    from opentelemetry.trace import Span

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


def set_attrs(span: Span, attrs: dict[str, Any]) -> None:
    """Set span attributes, skipping any whose value is None.

    Keeps call sites terse when only part of the metadata is available (e.g. a
    provider that does not report usage). Never pass prompt or tool *content*
    here — attributes are metadata only.
    """
    for key, value in attrs.items():
        if value is not None:
            span.set_attribute(key, value)


def record_tool_result(span: Span, *, success: bool) -> None:
    """Record a tool execution outcome: `agent.tool.success` + ERROR status on failure.

    Only for tools that actually ran (or were refused). A gated tool awaiting
    approval leaves `agent.tool.success` unset — it neither succeeded nor failed.
    """
    span.set_attribute(AGENT_TOOL_SUCCESS, success)
    if not success:
        span.set_status(Status(StatusCode.ERROR))
