"""OpenTelemetry tracing bootstrap for the agent loop (ADR-033).

A **derived, ephemeral diagnostic layer** over the ADR-016 event journal — the
journal stays the source of truth; spans are never correctness-critical. Tracing
is **off by default and zero-overhead when disabled**: no `TracerProvider` is
installed, so `get_tracer()` returns a no-op tracer whose spans are
non-recording and never exported.

When `OTEL_ENABLED` is true a `TracerProvider` is built with the configured
sampler (`always_on` at single-user scale) and an exporter:

* an explicit `exporter` (tests pass an `InMemorySpanExporter`), else
* an OTLP exporter when `OTEL_EXPORTER_OTLP_ENDPOINT` is set (Phase B; the
  optional `opentelemetry-exporter-otlp` package is lazily imported and we fall
  back to console if it is not installed), else
* a `ConsoleSpanExporter`.

Content (prompts / tool text) is never placed on spans here; see genai.py.
"""

from __future__ import annotations

import logging

from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
    SpanExporter,
)
from opentelemetry.sdk.trace.sampling import ALWAYS_OFF, ALWAYS_ON, Sampler
from opentelemetry.trace import NoOpTracer, Tracer

from app import __version__
from app.config import settings

logger = logging.getLogger(__name__)

_TRACER_NAME = "sherpa"
_provider: TracerProvider | None = None
_noop_tracer: Tracer = NoOpTracer()


def _resolve_sampler(name: str) -> Sampler:
    normalized = name.strip().lower()
    if normalized == "always_off":
        return ALWAYS_OFF
    # 100% sampling is fine at single-user scale; unknown values fail open to on.
    return ALWAYS_ON


def _build_exporter() -> tuple[SpanExporter, bool]:
    """Return (exporter, is_otlp). OTLP export (Phase B) is lazily imported."""
    endpoint = settings.otel_exporter_otlp_endpoint
    if endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                OTLPSpanExporter,
            )

            return OTLPSpanExporter(endpoint=str(endpoint)), True
        except ImportError:
            logger.warning(
                "OTEL_EXPORTER_OTLP_ENDPOINT is set but opentelemetry-exporter-otlp "
                "is not installed; falling back to the console exporter."
            )
    return ConsoleSpanExporter(), False


def configure_tracing(
    *, force: bool = False, exporter: SpanExporter | None = None
) -> TracerProvider | None:
    """Install a `TracerProvider` when tracing is enabled; otherwise a no-op.

    Idempotent: a second call while a provider is already installed is a no-op
    (returns the existing provider) unless `force` is set. `force`/`exporter`
    exist for deterministic tests (`InMemorySpanExporter`).
    """
    global _provider

    if not force and _provider is not None:
        return _provider

    if not force and not settings.otel_enabled:
        return None

    if force and _provider is not None:
        _provider.shutdown()
        _provider = None

    resource = Resource.create({"service.name": settings.app_name, "service.version": __version__})
    provider = TracerProvider(
        resource=resource, sampler=_resolve_sampler(settings.otel_traces_sampler)
    )

    if exporter is not None:
        # Explicit exporter (tests): export synchronously for determinism.
        provider.add_span_processor(SimpleSpanProcessor(exporter))
    else:
        span_exporter, is_otlp = _build_exporter()
        processor = (
            BatchSpanProcessor(span_exporter) if is_otlp else SimpleSpanProcessor(span_exporter)
        )
        provider.add_span_processor(processor)

    _provider = provider
    return provider


def get_tracer(name: str = _TRACER_NAME) -> Tracer:
    """Return a tracer bound to the installed provider, or a no-op when disabled."""
    if _provider is None:
        return _noop_tracer
    return _provider.get_tracer(name)


def tracing_enabled() -> bool:
    return _provider is not None


def shutdown_tracing() -> None:
    """Flush and tear down the provider (call on web/worker shutdown)."""
    global _provider
    if _provider is not None:
        _provider.shutdown()
        _provider = None


def reset_tracing() -> None:
    """Test hook: drop any installed provider so the next configure starts clean."""
    shutdown_tracing()
