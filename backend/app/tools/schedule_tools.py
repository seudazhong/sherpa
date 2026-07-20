"""Schedule tools (m-tools T6): the agent sets reminders and daily digests.

Thin adapters over `app.services.schedules`. Own-tenant writes on the user's
instruction → policy allows them. A reminder needs a todo_id + an absolute
remind_at; a digest needs a daily local time.
"""

from __future__ import annotations

import datetime

from app.services import ServiceError, schedules
from app.tools.adapter import arg_due, arg_int, arg_uuid, as_tool_error, require_session, to_caller
from app.tools.base import ToolContext, ToolError, ToolFlags, ToolResult
from app.tools.validate import validate_args

_WRITE = ToolFlags(is_read_only=False, is_concurrency_safe=True, is_destructive=False)


def _parse_time(value: object) -> datetime.time:
    try:
        parts = str(value).split(":")
        return datetime.time(int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)
    except (ValueError, IndexError) as exc:
        raise ToolError(f"invalid time (want HH:MM): {value!r}") from exc


class CreateReminderTool:
    name = "create_reminder"
    description = (
        "Set a one-time reminder for a to-do at an absolute time. Needs todo_id and "
        "remind_at (ISO-8601). reminder_kind is due_soon (default) or overdue."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "todo_id": {"type": "string", "description": "todo id (uuid)"},
            "remind_at": {"type": "string", "description": "ISO-8601 datetime (future)"},
            "reminder_kind": {"type": "string", "enum": ["due_soon", "overdue"]},
            "name": {"type": "string"},
        },
        "required": ["todo_id", "remind_at"],
    }
    flags = _WRITE

    async def execute(self, ctx: ToolContext, args: dict[str, object]) -> ToolResult:
        validate_args(self.input_schema, args)
        db, cc = require_session(ctx), to_caller(ctx)
        try:
            sched = await schedules.create_schedule(
                db,
                cc,
                kind="todo_reminder",
                name=str(args.get("name", "Reminder")),
                todo_id=arg_uuid(args["todo_id"]),
                reminder_kind=str(args.get("reminder_kind", "due_soon")),
                next_fire_at=arg_due(args["remind_at"]),
            )
        except ServiceError as e:
            raise as_tool_error(e) from None
        return ToolResult(
            llm_content=f"created reminder {sched.id} firing at {sched.next_fire_at.isoformat()}"
        )


class CreateDigestTool:
    name = "create_daily_digest"
    description = (
        "Set a daily digest at a local time. Needs local_time (HH:MM); timezone defaults to UTC."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "local_time": {"type": "string", "description": "HH:MM"},
            "timezone": {"type": "string", "description": "IANA tz, e.g. Asia/Shanghai"},
            "name": {"type": "string"},
        },
        "required": ["local_time"],
    }
    flags = _WRITE

    async def execute(self, ctx: ToolContext, args: dict[str, object]) -> ToolResult:
        validate_args(self.input_schema, args)
        db, cc = require_session(ctx), to_caller(ctx)
        try:
            sched = await schedules.create_schedule(
                db,
                cc,
                kind="daily_digest",
                name=str(args.get("name", "Daily digest")),
                timezone=str(args.get("timezone", "UTC")),
                local_time=_parse_time(args["local_time"]),
            )
        except ServiceError as e:
            raise as_tool_error(e) from None
        return ToolResult(
            llm_content=f"created daily digest {sched.id}; next at {sched.next_fire_at.isoformat()}"
        )


class ListSchedulesTool:
    name = "list_schedules"
    description = "List the user's reminders and digests with id, kind, next fire time and version."
    input_schema: dict[str, object] = {"type": "object", "properties": {}}
    flags = ToolFlags(is_read_only=True)

    async def execute(self, ctx: ToolContext, args: dict[str, object]) -> ToolResult:
        validate_args(self.input_schema, args)
        db, cc = require_session(ctx), to_caller(ctx)
        page = await schedules.list_schedules(db, cc)
        if not page.items:
            return ToolResult(llm_content="no schedules")
        lines = [
            f"- {s.id} · {s.kind} · [{s.status}] next {s.next_fire_at.isoformat()} · v{s.version}"
            for s in page.items
        ]
        return ToolResult(llm_content="schedules:\n" + "\n".join(lines))


class CancelScheduleTool:
    name = "cancel_schedule"
    description = "Cancel (disable) a reminder or digest. Needs schedule_id + if_version."
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "schedule_id": {"type": "string", "description": "schedule id (uuid)"},
            "if_version": {"type": "integer", "description": "if_version from list_schedules"},
        },
        "required": ["schedule_id", "if_version"],
    }
    flags = _WRITE

    async def execute(self, ctx: ToolContext, args: dict[str, object]) -> ToolResult:
        validate_args(self.input_schema, args)
        db, cc = require_session(ctx), to_caller(ctx)
        try:
            sched = await schedules.cancel_schedule(
                db,
                cc,
                schedule_id=arg_uuid(args["schedule_id"]),
                if_version=arg_int(args["if_version"]),
            )
        except ServiceError as e:
            raise as_tool_error(e) from None
        return ToolResult(llm_content=f"cancelled schedule {sched.id}")


def schedule_tools() -> list[object]:
    return [
        CreateReminderTool(),
        CreateDigestTool(),
        ListSchedulesTool(),
        CancelScheduleTool(),
    ]
