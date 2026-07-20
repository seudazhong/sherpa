"""Permission engine (ADR-020/008): policy classification + approval envelopes."""

from __future__ import annotations

from app.permissions.service import (
    ApprovalActorMismatch,
    ApprovalAlreadyResolved,
    ApprovalBindingMismatch,
    ApprovalExpired,
    ApprovalNotFound,
    CreatedApproval,
    Resolution,
    ResolveError,
    build_preview,
    correlation_for,
    nonce_hash,
    request_approval,
    resolve_approval,
)

__all__ = [
    "request_approval",
    "resolve_approval",
    "correlation_for",
    "nonce_hash",
    "build_preview",
    "CreatedApproval",
    "Resolution",
    "ResolveError",
    "ApprovalNotFound",
    "ApprovalBindingMismatch",
    "ApprovalActorMismatch",
    "ApprovalAlreadyResolved",
    "ApprovalExpired",
]
