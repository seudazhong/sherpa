"""Explicit RuntimeSession open/close tools."""

from __future__ import annotations

import uuid

from app.services import ServiceError
from app.services import project_runtime as svc
from app.tools.adapter import arg_opt_str, arg_uuid, as_tool_error, require_session, to_caller
from app.tools.base import ToolContext, ToolError, ToolFlags, ToolResult
from app.tools.validate import validate_args

_WRITE = ToolFlags(is_read_only=False, is_concurrency_safe=True, is_destructive=False)


def _session_id(ctx: ToolContext) -> uuid.UUID:
    if ctx.session_id is None:
        raise ToolError("runtime tools require a chat session")
    return ctx.session_id


class RuntimeOpenTool:
    name = "runtime_open"
    description = "Open an explicit offline coding runtime and return its id and capabilities."
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "scope": {"type": "string", "enum": ["project", "ephemeral"]},
            "reason": {"type": "string", "maxLength": 200},
        },
    }
    flags = _WRITE

    async def execute(self, ctx: ToolContext, args: dict[str, object]) -> ToolResult:
        validate_args(self.input_schema, args)
        try:
            action = await svc.open_runtime(
                require_session(ctx),
                to_caller(ctx),
                session_id=_session_id(ctx),
                scope=str(args.get("scope", "project")),
                reason=arg_opt_str(args.get("reason")),
            )
        except ServiceError as exc:
            raise as_tool_error(exc) from None
        runtime = action.runtime_session
        lines = [
            f"runtime_session_id={runtime.id}",
            f"state={runtime.state}",
            f"scope={runtime.scope}",
            f"expires_at={runtime.expires_at}",
            f"capabilities={runtime.capabilities or {}}",
        ]
        if action.failure_note:
            lines.append(action.failure_note)
        return ToolResult(llm_content="\n".join(lines))


class RuntimeCloseTool:
    name = "runtime_close"
    description = "Close one owned coding runtime after its persisted execution boundary."
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {"runtime_session_id": {"type": "string"}},
        "required": ["runtime_session_id"],
    }
    flags = _WRITE

    async def execute(self, ctx: ToolContext, args: dict[str, object]) -> ToolResult:
        validate_args(self.input_schema, args)
        try:
            action = await svc.close_runtime(
                require_session(ctx),
                to_caller(ctx),
                session_id=_session_id(ctx),
                runtime_session_id=arg_uuid(args["runtime_session_id"]),
            )
        except ServiceError as exc:
            raise as_tool_error(exc) from None
        runtime = action.runtime_session
        content = f"runtime_session_id={runtime.id}\nstate={runtime.state}"
        if action.failure_note:
            content += f"\n{action.failure_note}"
        return ToolResult(llm_content=content)


def runtime_tools() -> list[object]:
    return [RuntimeOpenTool(), RuntimeCloseTool()]
