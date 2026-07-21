"""Personal file tools (milestone 2): the agent reads/writes the user's workspace.

Thin adapters over app.services.files — the same capability layer the Files
REST/UI use. Text content for the agent; own-data workspace writes → the policy
engine allows them (no approval). Read output is bounded by the loop.
"""

from __future__ import annotations

from app.services import ServiceError, files
from app.tools.adapter import as_tool_error, require_session, to_caller
from app.tools.base import ToolContext, ToolFlags, ToolResult
from app.tools.validate import validate_args

_PATH: dict[str, object] = {
    "type": "string",
    "description": "logical workspace path, e.g. notes/todo.md",
}
_WRITE = ToolFlags(is_read_only=False, is_concurrency_safe=True, is_destructive=False)


class FileWriteTool:
    name = "file_write"
    description = (
        "Create or overwrite a text file in the user's private workspace. Own-data "
        "write; no approval needed."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {"path": _PATH, "content": {"type": "string"}},
        "required": ["path", "content"],
    }
    flags = _WRITE

    async def execute(self, ctx: ToolContext, args: dict[str, object]) -> ToolResult:
        validate_args(self.input_schema, args)
        db, cc = require_session(ctx), to_caller(ctx)
        try:
            data = str(args["content"]).encode("utf-8")
            row = await files.put_file(
                db,
                cc,
                path=str(args["path"]),
                data=data,
                content_type="text/plain; charset=utf-8",
            )
        except ServiceError as e:
            raise as_tool_error(e) from None
        return ToolResult(llm_content=f"wrote {row.path} ({row.size_bytes} bytes, v{row.version})")


class FileReadTool:
    name = "file_read"
    description = "Read a text file from the user's workspace by path. Read-only."
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {"path": _PATH},
        "required": ["path"],
    }
    flags = ToolFlags(is_read_only=True)

    async def execute(self, ctx: ToolContext, args: dict[str, object]) -> ToolResult:
        validate_args(self.input_schema, args)
        db, cc = require_session(ctx), to_caller(ctx)
        try:
            _row, data = await files.read_file(db, cc, path=str(args["path"]))
        except ServiceError as e:
            raise as_tool_error(e) from None
        return ToolResult(llm_content=data.decode("utf-8", errors="replace"))


class FileListTool:
    name = "file_list"
    description = "List the files in the user's workspace (path, size, version). Read-only."
    input_schema: dict[str, object] = {"type": "object", "properties": {}}
    flags = ToolFlags(is_read_only=True)

    async def execute(self, ctx: ToolContext, args: dict[str, object]) -> ToolResult:
        validate_args(self.input_schema, args)
        db, cc = require_session(ctx), to_caller(ctx)
        try:
            rows = await files.list_files(db, cc)
        except ServiceError as e:
            raise as_tool_error(e) from None
        if not rows:
            return ToolResult(llm_content="no files")
        body = "\n".join(f"- {r.path} ({r.size_bytes} bytes, v{r.version})" for r in rows)
        return ToolResult(llm_content="files:\n" + body)


class FileDeleteTool:
    name = "file_delete"
    description = "Delete a file from the user's workspace by path. Own-data; no approval needed."
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {"path": _PATH},
        "required": ["path"],
    }
    flags = _WRITE

    async def execute(self, ctx: ToolContext, args: dict[str, object]) -> ToolResult:
        validate_args(self.input_schema, args)
        db, cc = require_session(ctx), to_caller(ctx)
        try:
            await files.delete_file(db, cc, path=str(args["path"]))
        except ServiceError as e:
            raise as_tool_error(e) from None
        return ToolResult(llm_content=f"deleted {args['path']}")


def file_tools() -> list[object]:
    return [FileWriteTool(), FileReadTool(), FileListTool(), FileDeleteTool()]
