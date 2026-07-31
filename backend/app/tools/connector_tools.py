"""Connector tools (m-tools T5): the agent lists connectors and triggers sync.

`sync_connector` runs the Gmail→candidate pipeline inline so the agent can pull new
email and generate candidates on the user's instruction (own-data, policy allows).
Thin adapters over `app.services.connectors`.
"""

from __future__ import annotations

from app.config import settings
from app.connectors.gmail import build_gmail_sync_client
from app.providers import build_provider
from app.services import ServiceError, connectors
from app.tools.adapter import arg_uuid, as_tool_error, require_session, to_caller
from app.tools.base import ToolContext, ToolFlags, ToolResult
from app.tools.validate import validate_args


class ListConnectorsTool:
    name = "connector_list"
    description = (
        "List the user's connected accounts (e.g. Gmail) with id, account and status. Read-only."
    )
    input_schema: dict[str, object] = {"type": "object", "properties": {}}
    flags = ToolFlags(is_read_only=True)

    async def execute(self, ctx: ToolContext, args: dict[str, object]) -> ToolResult:
        validate_args(self.input_schema, args)
        db, cc = require_session(ctx), to_caller(ctx)
        try:
            rows = await connectors.list_connectors(db, cc)
        except ServiceError as e:
            raise as_tool_error(e) from None
        if not rows:
            return ToolResult(llm_content="no connectors")
        lines = [f"- {c.id} · {c.kind} · {c.account_email} · {c.status}" for c in rows]
        return ToolResult(llm_content="connectors:\n" + "\n".join(lines))


class SyncConnectorTool:
    name = "connector_sync"
    description = (
        "Sync a connected account now and analyze new items into action candidates. "
        "Needs connector_id (from connector_list). Own-data read; no approval needed."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {"connector_id": {"type": "string", "description": "connector id (uuid)"}},
        "required": ["connector_id"],
    }
    flags = ToolFlags(is_read_only=False, is_concurrency_safe=True, is_destructive=False)

    async def execute(self, ctx: ToolContext, args: dict[str, object]) -> ToolResult:
        validate_args(self.input_schema, args)
        db, cc = require_session(ctx), to_caller(ctx)
        try:
            result = await connectors.sync_connector(
                db,
                cc,
                connector_id=arg_uuid(args["connector_id"]),
                sync_client=build_gmail_sync_client(),
                provider=build_provider(),
                provider_name=settings.provider_kind,
                model=settings.provider_model,
            )
        except ServiceError as e:
            raise as_tool_error(e) from None
        return ToolResult(
            llm_content=(
                f"synced {result.synced} new item(s); analyzed {result.analyzed}; "
                f"created {result.candidates} candidate(s)"
            )
        )


def connector_tools() -> list[object]:
    return [ListConnectorsTool(), SyncConnectorTool()]
