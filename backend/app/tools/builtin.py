"""Starter read-only built-in tools.

v1 ships two dependency-free read-only tools so the loop can exercise tool-calling
before the workspace/DB-backed tools (read/glob/grep, memory, connectors) land.
All are SAFE-tier (available even to untrusted-content sessions).
"""

from __future__ import annotations

import datetime

from app.tools.base import ToolFlags, ToolResult
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

    async def execute(self, args: dict[str, object]) -> ToolResult:
        validate_args(self.input_schema, args)
        return ToolResult(llm_content=str(args["text"]))


class GetTimeTool:
    name = "get_time"
    description = "Return the current UTC time as an ISO-8601 string. Read-only."
    input_schema: dict[str, object] = {"type": "object", "properties": {}}
    flags = ToolFlags()

    async def execute(self, args: dict[str, object]) -> ToolResult:
        validate_args(self.input_schema, args)
        now = datetime.datetime.now(datetime.UTC).isoformat()
        return ToolResult(llm_content=now)


def build_default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(EchoTool(), safe=True)
    registry.register(GetTimeTool(), safe=True)
    return registry
