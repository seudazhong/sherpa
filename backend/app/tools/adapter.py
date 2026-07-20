"""Shared helpers for tool adapters (docs/11 §6,§10).

A tool is a thin adapter: validate args → build a `CallerContext(actor="agent")`
from the injected `ToolContext` → call a capability-layer service → format a
`ToolResult`. These helpers centralize the context/session extraction and guard
that the runtime actually injected them.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import CallerContext
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
