"""Tool interface (docs/05, contracts/api.md §7).

Every tool — built-in, MCP, or sub-agent — presents identically: schema-validated
JSON in, a structured result out with two faces (model-facing `llm_content` and
user-facing `return_display`). Errors are surfaced as observations, not crashes.
"""

from __future__ import annotations

import dataclasses
import datetime
import uuid
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class ToolError(Exception):
    """Raised for invalid args or execution failure; fed back to the model as an observation."""


@dataclasses.dataclass(frozen=True)
class ToolContext:
    """Runtime-injected tool-execution context (api.md §7); never model-controlled.

    `tenant_id` is always set at execution time; the run binding + `session` are
    populated by the core loop. The tool adapter converts this into a
    `CallerContext(actor="agent")` and calls the capability layer with `session`.
    The `session` is a runtime handle only — never serialized to the model.
    """

    tenant_id: uuid.UUID
    user_id: uuid.UUID | None = None
    session_id: uuid.UUID | None = None
    run_id: uuid.UUID | None = None
    invocation_id: uuid.UUID | None = None
    source: str = "web"
    deadline: datetime.datetime | None = None
    session: AsyncSession | None = None


@dataclasses.dataclass(frozen=True)
class DisplayPayload:
    """User-facing tool projection (api.md §7): a format + bounded content.

    Separately sanitized from `llm_content`; never fed back to the model and never
    trusted as executable markup by a renderer.
    """

    format: str  # "text" | "markdown" | "json"
    content: object


@dataclasses.dataclass(frozen=True)
class ToolResult:
    llm_content: str
    return_display: DisplayPayload | None = None


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
