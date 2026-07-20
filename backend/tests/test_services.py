"""Capability-layer scaffolding (m-tools T1): CallerContext + ServiceError mapping.

Pure unit tests — no DB. Verifies each `ServiceError` carries a stable code + HTTP
status and renders a bounded tool observation, and that both adapters can map it.
"""

from __future__ import annotations

import uuid

from fastapi import HTTPException

from app.services import (
    CallerContext,
    Conflict,
    Forbidden,
    Invalid,
    NotFound,
    ServiceError,
    VersionConflict,
)
from app.tools import ToolError


def test_caller_context_fields() -> None:
    tid, uid = uuid.uuid4(), uuid.uuid4()
    ctx = CallerContext(tenant_id=tid, user_id=uid, actor="agent", run_id=uuid.uuid4())
    assert ctx.actor == "agent"
    assert ctx.tenant_id == tid and ctx.user_id == uid
    assert ctx.session_id is None  # optional binding


def test_service_error_codes_and_status() -> None:
    cases = [
        (NotFound, 404, "not_found"),
        (VersionConflict, 409, "version_conflict"),
        (Forbidden, 403, "forbidden"),
        (Invalid, 422, "invalid"),
        (Conflict, 409, "conflict"),
    ]
    for cls, status, code in cases:
        err = cls("boom")
        assert isinstance(err, ServiceError)
        assert err.http_status == status
        assert err.code == code
        assert err.message == "boom"


def test_maps_to_http_and_tool_observation() -> None:
    err = VersionConflict("stale version")
    # REST adapter mapping
    http = HTTPException(err.http_status, err.code)
    assert http.status_code == 409 and http.detail == "version_conflict"
    # Tool adapter mapping — bounded observation, never crashes the loop
    tool_err = ToolError(err.tool_observation)
    assert str(tool_err) == "error: version_conflict: stale version"


def test_default_message_falls_back_to_code() -> None:
    assert NotFound().message == "not_found"
    assert NotFound().tool_observation == "error: not_found: not_found"
