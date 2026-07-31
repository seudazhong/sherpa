"""Starter built-in tools.

Dependency-free built-ins registered alongside the DB-backed tool modules.
`get_time` is SAFE-tier (available even to untrusted-content sessions);
`send_email` is the first external, approval-gated action.
"""

from __future__ import annotations

import datetime

from app.tools.base import ToolContext, ToolFlags, ToolResult
from app.tools.registry import ToolRegistry
from app.tools.validate import validate_args


class GetTimeTool:
    name = "core_get_time"
    description = "Return the current UTC time as an ISO-8601 string. Read-only."
    input_schema: dict[str, object] = {"type": "object", "properties": {}}
    flags = ToolFlags()

    async def execute(self, ctx: ToolContext, args: dict[str, object]) -> ToolResult:
        validate_args(self.input_schema, args)
        now = datetime.datetime.now(datetime.UTC).isoformat()
        return ToolResult(llm_content=now)


class SendEmailTool:
    """First external (non-idempotent) action. Gated by the permission engine:
    the loop never dispatches it without an approved envelope (ADR-020)."""

    name = "email_send"
    description = (
        "Send an email on the user's behalf. This is an external action and requires "
        "explicit approval before it is sent."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "to": {"type": "string"},
            "subject": {"type": "string"},
            "body": {"type": "string"},
        },
        "required": ["to", "subject", "body"],
    }
    flags = ToolFlags(is_read_only=False, is_concurrency_safe=False, is_destructive=True)

    async def execute(self, ctx: ToolContext, args: dict[str, object]) -> ToolResult:
        validate_args(self.input_schema, args)
        # Only reachable after an approval grant resumes the invocation. Goes through
        # the single email send seam (roadmap unify-note): recording stub by default,
        # real AgentMail send when email_kind=agentmail (ADR-027).
        from app.notifications import build_email_sender

        ok = await build_email_sender().send(
            to=str(args["to"]), subject=str(args["subject"]), body=str(args["body"])
        )
        if not ok:
            return ToolResult(llm_content=f"email send failed for {args['to']}")
        return ToolResult(llm_content=f"email sent to {args['to']}")


def build_default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(GetTimeTool(), safe=True)
    registry.register(SendEmailTool(), safe=False)
    from app.tools.candidate_tools import candidate_tools
    from app.tools.connector_tools import connector_tools
    from app.tools.drive_tools import drive_tools
    from app.tools.insight_tools import insight_tools
    from app.tools.knowledge_tools import knowledge_tools
    from app.tools.memory_tools import memory_tools
    from app.tools.project_tools import project_tools
    from app.tools.schedule_tools import schedule_tools
    from app.tools.todo_tools import todo_tools

    for tool in [
        *candidate_tools(),
        *todo_tools(),
        *connector_tools(),
        *schedule_tools(),
        *insight_tools(),
        *memory_tools(),
        *drive_tools(),
        *knowledge_tools(),
        *project_tools(),
    ]:
        registry.register(tool, safe=False)  # type: ignore[arg-type]
    return registry
