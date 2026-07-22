"""Service error taxonomy (ADR-023, docs/11 §5).

One typed hierarchy the capability layer raises; each carries a stable `code` and
an `http_status`, and renders a bounded `tool_observation` string. Adapters map it
without duplicating logic:

* REST:  `except ServiceError as e: raise HTTPException(e.http_status, e.code)`
* Tool:  `except ServiceError as e: raise ToolError(e.tool_observation)`

The module is pure (no FastAPI / tools imports) so services stay transport-agnostic.
"""

from __future__ import annotations


class ServiceError(Exception):
    """Base for capability-layer failures. Never leaks tenant data in `code`."""

    code: str = "service_error"
    http_status: int = 400

    @property
    def message(self) -> str:
        return str(self) if str(self) else self.code

    @property
    def tool_observation(self) -> str:
        """Bounded observation fed back to the model (loop turns it into tool-error)."""
        return f"error: {self.code}: {self.message}"


class NotFound(ServiceError):
    code = "not_found"
    http_status = 404


class VersionConflict(ServiceError):
    code = "version_conflict"
    http_status = 409


class Forbidden(ServiceError):
    code = "forbidden"
    http_status = 403


class Invalid(ServiceError):
    code = "invalid"
    http_status = 422


class Conflict(ServiceError):
    code = "conflict"
    http_status = 409


class Internal(ServiceError):
    code = "internal"
    http_status = 500


class TooLarge(ServiceError):
    code = "payload_too_large"
    http_status = 413


class InsufficientStorage(ServiceError):
    code = "insufficient_storage"
    http_status = 507
