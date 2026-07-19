"""Structured JSON logging with correlation ids + secret redaction (config §3.5).

Correlation ids (tenant/run/session/request) are carried in contextvars and
attached to every log line. Log `extra` fields pass through the redaction helper
so secret-named fields never reach stdout. OAuth/provider bodies are never logged
at all (enforced at the call sites); this is defense in depth for structured data.
"""

from __future__ import annotations

import contextvars
import datetime
import json
import logging
from typing import Any

from app.config import settings
from app.security.redaction import redact

tenant_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "tenant_id", default=None
)
run_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("run_id", default=None)
session_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "session_id", default=None
)
request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)

_CORRELATION = (
    ("tenant_id", tenant_id_var),
    ("run_id", run_id_var),
    ("session_id", session_id_var),
    ("request_id", request_id_var),
)

# Standard LogRecord attributes; anything else on the record is caller `extra`.
_RESERVED = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.datetime.now(datetime.UTC).isoformat().replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for name, var in _CORRELATION:
            value = var.get()
            if value is not None:
                payload[name] = value

        extra = {k: v for k, v in record.__dict__.items() if k not in _RESERVED}
        if extra:
            payload.update(redact(extra))
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, separators=(",", ":"), default=str)


def configure_logging() -> None:
    """Install the JSON formatter on the root logger at the configured level."""
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(settings.log_level.upper())


def bind_context(
    *,
    tenant_id: str | None = None,
    run_id: str | None = None,
    session_id: str | None = None,
    request_id: str | None = None,
) -> None:
    """Set correlation ids for the current context (call at run/request entry)."""
    if tenant_id is not None:
        tenant_id_var.set(tenant_id)
    if run_id is not None:
        run_id_var.set(run_id)
    if session_id is not None:
        session_id_var.set(session_id)
    if request_id is not None:
        request_id_var.set(request_id)
