"""Tool interface (docs/05, contracts/api.md §7).

Every tool — built-in, MCP, or sub-agent — presents identically: schema-validated
JSON in, a structured result out with two faces (model-facing `llm_content` and
user-facing `return_display`). Errors are surfaced as observations, not crashes.
"""

from __future__ import annotations

import dataclasses
import datetime
import uuid
from typing import Protocol


class ToolError(Exception):
    """Raised for invalid args or execution failure; fed back to the model as an observation."""


@dataclasses.dataclass(frozen=True)
class ToolContext:
    """Runtime-injected tool-execution context (api.md §7); never model-controlled.

    `tenant_id` is always set at execution time; the run binding is populated by the
    core loop. The tool adapter converts this into a `CallerContext(actor="agent")`
    before calling the capability layer.
    """

    tenant_id: uuid.UUID
    user_id: uuid.UUID | None = None
    session_id: uuid.UUID | None = None
    run_id: uuid.UUID | None = None
    invocation_id: uuid.UUID | None = None
    source: str = "web"
    deadline: datetime.datetime | None = None


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

    async def execute(self, ctx: ToolContext, args: dict[str, object]) -> ToolResult: ...
