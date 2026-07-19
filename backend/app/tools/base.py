"""Tool interface (docs/05, contracts/api.md §7).

Every tool — built-in, MCP, or sub-agent — presents identically: schema-validated
JSON in, a structured result out with two faces (model-facing `llm_content` and
user-facing `return_display`). Errors are surfaced as observations, not crashes.
"""

from __future__ import annotations

import dataclasses
from typing import Protocol


class ToolError(Exception):
    """Raised for invalid args or execution failure; fed back to the model as an observation."""


@dataclasses.dataclass(frozen=True)
class ToolResult:
    llm_content: str
    return_display: str | None = None


@dataclasses.dataclass(frozen=True)
class ToolFlags:
    is_read_only: bool = True
    is_concurrency_safe: bool = True
    is_destructive: bool = False


class Tool(Protocol):
    name: str
    description: str
    input_schema: dict[str, object]
    flags: ToolFlags

    async def execute(self, args: dict[str, object]) -> ToolResult: ...
