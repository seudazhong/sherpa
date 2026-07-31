"""Todo tools (m-tools T4): the agent creates and manages to-dos.

Thin adapters over `app.services.todos` — the same capability layer the REST
endpoints use. Own-tenant reads/writes on the user's instruction → policy allows
them (no approval). `if_version` (from list_todos) guards concurrent edits.
"""

from __future__ import annotations

from app.services import ServiceError, todos
from app.tools.adapter import (
    arg_due,
    arg_int,
    arg_opt_str,
    arg_uuid,
    as_tool_error,
    require_session,
    to_caller,
)
from app.tools.base import ToolContext, ToolFlags, ToolResult
from app.tools.validate import validate_args

_ID = {"type": "string", "description": "todo id (uuid)"}
_VER = {"type": "integer", "description": "if_version from todo_list (optimistic lock)"}
_WRITE = ToolFlags(is_read_only=False, is_concurrency_safe=True, is_destructive=False)


class ListTodosTool:
    name = "todo_list"
    description = "List the user's to-dos with id, title, status, due date and version. Read-only."
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["open", "completed", "cancelled"]},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        },
    }
    flags = ToolFlags(is_read_only=True)

    async def execute(self, ctx: ToolContext, args: dict[str, object]) -> ToolResult:
        validate_args(self.input_schema, args)
        db, cc = require_session(ctx), to_caller(ctx)
        status = arg_opt_str(args.get("status"))
        limit = arg_int(args["limit"]) if "limit" in args else 50
        try:
            page = await todos.list_todos(db, cc, status_filter=status, limit=limit)
        except ServiceError as e:
            raise as_tool_error(e) from None
        if not page.items:
            return ToolResult(llm_content="no todos")
        lines = [
            f"- {t.id} · [{t.status}] {t.title}"
            + (f" · due {t.due_at.date()}" if t.due_at else "")
            + f" · v{t.version}"
            for t in page.items
        ]
        return ToolResult(llm_content="todos:\n" + "\n".join(lines))


class CreateTodoTool:
    name = "todo_create"
    description = (
        "Create a new to-do for the user. Own-data write; no approval needed. "
        "Provide a title; optionally description, due_at (ISO-8601) and priority."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "minLength": 1},
            "description": {"type": "string"},
            "due_at": {"type": "string", "description": "ISO-8601 datetime"},
            "priority": {"type": "string", "enum": ["low", "medium", "high"]},
        },
        "required": ["title"],
    }
    flags = _WRITE

    async def execute(self, ctx: ToolContext, args: dict[str, object]) -> ToolResult:
        validate_args(self.input_schema, args)
        db, cc = require_session(ctx), to_caller(ctx)
        try:
            todo = await todos.create_todo(
                db,
                cc,
                title=str(args["title"]),
                description=arg_opt_str(args.get("description")),
                due_at=arg_due(args.get("due_at")),
                priority=str(args.get("priority", "medium")),
            )
        except ServiceError as e:
            raise as_tool_error(e) from None
        return ToolResult(llm_content=f"created todo {todo.id}: {todo.title}")


class UpdateTodoTool:
    name = "todo_update"
    description = (
        "Update a to-do (title/description/status/due_at/priority). Needs todo_id + if_version. "
        "status is one of open/completed/cancelled."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "todo_id": _ID,
            "if_version": _VER,
            "title": {"type": "string"},
            "description": {"type": "string"},
            "status": {"type": "string", "enum": ["open", "completed", "cancelled"]},
            "due_at": {"type": "string", "description": "ISO-8601 datetime"},
            "priority": {"type": "string", "enum": ["low", "medium", "high"]},
        },
        "required": ["todo_id", "if_version"],
    }
    flags = _WRITE

    async def execute(self, ctx: ToolContext, args: dict[str, object]) -> ToolResult:
        validate_args(self.input_schema, args)
        db, cc = require_session(ctx), to_caller(ctx)
        try:
            todo = await todos.update_todo(
                db,
                cc,
                todo_id=arg_uuid(args["todo_id"]),
                if_version=arg_int(args["if_version"]),
                title=arg_opt_str(args.get("title")),
                description=arg_opt_str(args.get("description")),
                status=arg_opt_str(args.get("status")),
                due_at=arg_due(args.get("due_at")),
                priority=arg_opt_str(args.get("priority")),
            )
        except ServiceError as e:
            raise as_tool_error(e) from None
        return ToolResult(llm_content=f"updated todo {todo.id}: [{todo.status}] {todo.title}")


# `complete_todo` deleted in Phase TR P2.0 (backlog B-10): it was exactly
# `update_todo(status="completed")` — the service function it called was a
# one-line alias for `update_todo` — so it bought the model a second way to
# spell one action and nothing else.


def todo_tools() -> list[object]:
    return [ListTodosTool(), CreateTodoTool(), UpdateTodoTool()]
