"""Shared helpers for tool adapters (docs/11 §6,§10).

A tool is a thin adapter: validate args → build a `CallerContext(actor="agent")`
from the injected `ToolContext` → call a capability-layer service → format a
`ToolResult`. These helpers centralize the context/session extraction and guard
that the runtime actually injected them.
"""

from __future__ import annotations

import datetime
import uuid
from typing import cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import CallerContext, ServiceError
from app.tools.base import ToolContext, ToolError


def to_caller(ctx: ToolContext) -> CallerContext:
    if ctx.user_id is None:
        raise ToolError("no user in tool context")
    return CallerContext(
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        actor="agent",
        session_id=ctx.session_id,
        run_id=ctx.run_id,
        invocation_id=ctx.invocation_id,
    )


def require_session(ctx: ToolContext) -> AsyncSession:
    if ctx.session is None:
        raise ToolError("no session in tool context")
    return ctx.session


def as_tool_error(e: ServiceError) -> ToolError:
    return ToolError(e.tool_observation)


def arg_uuid(value: object) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError) as exc:
        raise ToolError(f"invalid uuid: {value!r}") from exc


def arg_int(value: object) -> int:
    return cast("int", value)  # schema-validated to integer before execution


def arg_opt_str(value: object) -> str | None:
    return None if value is None else str(value)


def arg_due(value: object) -> datetime.datetime | None:
    if value is None or value == "":
        return None
    try:
        return datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ToolError(f"invalid datetime: {value!r}") from exc
