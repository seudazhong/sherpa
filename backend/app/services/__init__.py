"""Capability layer (ADR-023, docs/11).

`app/services/` is the single, transport-agnostic home for business logic. Both
the REST handlers (human client) and the agent tools (agent client) are thin
adapters over these functions: they parse input, build a `CallerContext`, call a
service, and map the result / typed `ServiceError` back to their transport.

Service functions take `(session, ctx, *, ...)`, perform domain validation +
mutation + `flush`, and **do not commit** — the adapter owns the transaction.
"""

from __future__ import annotations

from app.services.context import CallerContext
from app.services.errors import (
    Conflict,
    Forbidden,
    Invalid,
    NotFound,
    ServiceError,
    VersionConflict,
)

__all__ = [
    "CallerContext",
    "ServiceError",
    "NotFound",
    "VersionConflict",
    "Forbidden",
    "Invalid",
    "Conflict",
]
