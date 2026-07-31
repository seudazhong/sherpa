"""Read + settings tools (m-tools T7): the agent inspects notifications, activity,
and manages notification settings. Thin adapters over `app.services.insights`.
"""

from __future__ import annotations

from app.services import ServiceError, insights
from app.tools.adapter import as_tool_error, require_session, to_caller
from app.tools.base import ToolContext, ToolFlags, ToolResult
from app.tools.validate import validate_args


class ListNotificationsTool:
    name = "notify.list"
    description = (
        "List delivered/missed reminders and digests (shown on the Today page). Read-only."
    )
    input_schema: dict[str, object] = {"type": "object", "properties": {}}
    flags = ToolFlags(is_read_only=True)

    async def execute(self, ctx: ToolContext, args: dict[str, object]) -> ToolResult:
        validate_args(self.input_schema, args)
        db, cc = require_session(ctx), to_caller(ctx)
        page = await insights.list_notifications(db, cc)
        if not page.items:
            return ToolResult(llm_content="no notifications")
        lines = [
            f"- {n.schedule_name} · {n.channel} · {n.delivery_outcome or n.status} · "
            f"{n.scheduled_for.isoformat()}"
            for n in page.items
        ]
        return ToolResult(llm_content="notifications:\n" + "\n".join(lines))


class ListActivityTool:
    name = "notify.list_activity"
    description = (
        "List the activity ledger — what Sherpa did on the user's behalf "
        "(reads/inferences/actions). Optional type filter. Read-only."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": ["read", "inference", "action"]},
        },
    }
    flags = ToolFlags(is_read_only=True)

    async def execute(self, ctx: ToolContext, args: dict[str, object]) -> ToolResult:
        validate_args(self.input_schema, args)
        db, cc = require_session(ctx), to_caller(ctx)
        rt = args.get("type")
        page = await insights.list_activity(db, cc, receipt_type=str(rt) if rt else None)
        if not page.items:
            return ToolResult(llm_content="no activity")
        lines = [
            f"- {r.receipt_type} · {r.action} · {r.outcome} · {r.occurred_at.isoformat()}"
            for r in page.items
        ]
        return ToolResult(llm_content="activity:\n" + "\n".join(lines))


class GetSettingsTool:
    name = "notify.get_settings"
    description = "Read the user's notification settings (incl. version). Read-only."
    input_schema: dict[str, object] = {"type": "object", "properties": {}}
    flags = ToolFlags(is_read_only=True)

    async def execute(self, ctx: ToolContext, args: dict[str, object]) -> ToolResult:
        validate_args(self.input_schema, args)
        db, cc = require_session(ctx), to_caller(ctx)
        s = await insights.get_settings(db, cc)
        return ToolResult(
            llm_content=(
                f"settings: notifications_enabled={s.notifications_enabled} "
                f"web_enabled={s.web_enabled} email_digest_enabled={s.email_digest_enabled} "
                f"timezone={s.timezone} quiet_hours_enabled={s.quiet_hours_enabled} "
                f"daily_cap={s.daily_cap} version={s.version}"
            )
        )


class UpdateSettingsTool:
    name = "notify.update_settings"
    description = (
        "Change the user's notification settings (any of notifications_enabled, "
        "web_enabled, email_digest_enabled, timezone, quiet_hours_enabled, daily_cap)."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "notifications_enabled": {"type": "boolean"},
            "web_enabled": {"type": "boolean"},
            "email_digest_enabled": {"type": "boolean"},
            "timezone": {"type": "string"},
            "quiet_hours_enabled": {"type": "boolean"},
            "daily_cap": {"type": "integer", "minimum": 0, "maximum": 100},
        },
    }
    flags = ToolFlags(is_read_only=False, is_concurrency_safe=True, is_destructive=False)

    async def execute(self, ctx: ToolContext, args: dict[str, object]) -> ToolResult:
        validate_args(self.input_schema, args)
        db, cc = require_session(ctx), to_caller(ctx)
        try:
            current = await insights.get_settings(db, cc)
            updated = await insights.update_settings(
                db,
                cc,
                if_version=current.version,
                notifications_enabled=args.get("notifications_enabled"),  # type: ignore[arg-type]
                web_enabled=args.get("web_enabled"),  # type: ignore[arg-type]
                email_digest_enabled=args.get("email_digest_enabled"),  # type: ignore[arg-type]
                timezone=args.get("timezone"),  # type: ignore[arg-type]
                quiet_hours_enabled=args.get("quiet_hours_enabled"),  # type: ignore[arg-type]
                daily_cap=args.get("daily_cap"),  # type: ignore[arg-type]
            )
        except ServiceError as e:
            raise as_tool_error(e) from None
        return ToolResult(
            llm_content=f"updated settings (version {updated.version}); "
            f"notifications_enabled={updated.notifications_enabled}"
        )


def insight_tools() -> list[object]:
    return [ListNotificationsTool(), ListActivityTool(), GetSettingsTool(), UpdateSettingsTool()]
