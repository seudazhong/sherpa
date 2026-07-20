"""Starter read-only built-in tools.

v1 ships two dependency-free read-only tools so the loop can exercise tool-calling
before the workspace/DB-backed tools (read/glob/grep, memory, connectors) land.
All are SAFE-tier (available even to untrusted-content sessions).
"""

from __future__ import annotations

import datetime

from app.tools.base import ToolContext, ToolFlags, ToolResult
from app.tools.registry import ToolRegistry
from app.tools.validate import validate_args


class EchoTool:
    name = "echo"
    description = "Echo the provided text back verbatim. Read-only."
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }
    flags = ToolFlags()

    async def execute(self, ctx: ToolContext, args: dict[str, object]) -> ToolResult:
        validate_args(self.input_schema, args)
        return ToolResult(llm_content=str(args["text"]))


class GetTimeTool:
    name = "get_time"
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

    name = "send_email"
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
        # Only reachable after an approval grant resumes the invocation (post-v1).
        return ToolResult(llm_content=f"email sent to {args['to']}")


def build_default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(EchoTool(), safe=True)
    registry.register(GetTimeTool(), safe=True)
    registry.register(SendEmailTool(), safe=False)
    from app.tools.candidate_tools import candidate_tools
    from app.tools.todo_tools import todo_tools

    for tool in [*candidate_tools(), *todo_tools()]:
        registry.register(tool, safe=False)  # type: ignore[arg-type]
    return registry
