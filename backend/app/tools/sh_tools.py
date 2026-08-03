"""Sandbox-routed shell execution for an explicit RuntimeSession."""

from __future__ import annotations

import uuid

from app.services import ServiceError
from app.services import project_runtime as svc
from app.tools.adapter import arg_int, arg_uuid, as_tool_error, require_session, to_caller
from app.tools.base import ToolContext, ToolError, ToolFlags, ToolResult
from app.tools.validate import validate_args


def _session_id(ctx: ToolContext) -> uuid.UUID:
    if ctx.session_id is None:
        raise ToolError("sh_exec requires a chat session")
    return ctx.session_id


class ShExecTool:
    name = "sh_exec"
    description = "Execute one bounded command in an owned offline RuntimeSession."
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "runtime_session_id": {"type": "string"},
            "command": {"type": "string", "minLength": 1, "maxLength": 4000},
            "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 900},
        },
        "required": ["runtime_session_id", "command"],
    }
    flags = ToolFlags(is_read_only=False, is_concurrency_safe=False, is_destructive=True)

    async def execute(self, ctx: ToolContext, args: dict[str, object]) -> ToolResult:
        validate_args(self.input_schema, args)
        try:
            action = await svc.exec_runtime(
                require_session(ctx),
                to_caller(ctx),
                session_id=_session_id(ctx),
                runtime_session_id=arg_uuid(args["runtime_session_id"]),
                command=str(args["command"]),
                timeout_seconds=(
                    arg_int(args["timeout_seconds"])
                    if args.get("timeout_seconds") is not None
                    else None
                ),
                run_id=ctx.run_id,
                invocation_id=ctx.invocation_id,
            )
        except ServiceError as exc:
            raise as_tool_error(exc) from None
        exec_run = action.exec_run
        lines = [
            f"exec_run_id={exec_run.id}",
            f"runtime_session_id={action.runtime_session.id}",
            f"state={exec_run.state}",
            f"termination_reason={exec_run.termination_reason}",
            f"exit_code={exec_run.exit_code}",
        ]
        if action.failure_note:
            lines.append(action.failure_note)
        if exec_run.termination_reason == "environment_missing_dependencies":
            lines.append(f"available_capabilities={action.runtime_session.capabilities or {}}")
        if action.stdout:
            lines.append(f"[stdout]\n{action.stdout}")
        if action.stderr:
            lines.append(f"[stderr]\n{action.stderr}")
        if exec_run.change_set_id is not None:
            lines.append(f"change_set={exec_run.change_set_id}; review it before user-only Save.")
        return ToolResult(llm_content="\n".join(lines))


def sh_tools() -> list[object]:
    return [ShExecTool()]
