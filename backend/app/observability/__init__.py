"""Observability: run→trace projection + structured logging (docs/07, ADR-021)."""

from __future__ import annotations

from app.observability.logging import (
    JsonFormatter,
    bind_context,
    configure_logging,
    request_id_var,
    run_id_var,
    session_id_var,
    tenant_id_var,
)
from app.observability.otel import (
    configure_tracing,
    get_tracer,
    shutdown_tracing,
    tracing_enabled,
)
from app.observability.projection import project_run_trace

__all__ = [
    "JsonFormatter",
    "bind_context",
    "configure_logging",
    "configure_tracing",
    "get_tracer",
    "shutdown_tracing",
    "tracing_enabled",
    "tenant_id_var",
    "run_id_var",
    "session_id_var",
    "request_id_var",
    "project_run_trace",
]
