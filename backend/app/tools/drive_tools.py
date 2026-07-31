"""Personal Drive tools (ADR-030, W1): the agent operates the user's Drive.

Thin adapters over ``app.services.drive`` — the same capability layer the Drive
REST/UI use (ADR-023 parity). Agents may list/search/make folders/write/read/
move/trash/restore; permanent purge is human-only and is intentionally NOT a tool.
Paths are POSIX-style logical paths, e.g. ``projects/notes.md``; folders are
auto-created on write. Read output is bounded by the loop.
"""

from __future__ import annotations

from app.services import ServiceError, drive
from app.tools.adapter import as_tool_error, require_session, to_caller
from app.tools.base import ToolContext, ToolFlags, ToolResult
from app.tools.validate import validate_args

_PATH: dict[str, object] = {
    "type": "string",
    "description": "logical Drive path, e.g. projects/notes.md",
}
_WRITE = ToolFlags(is_read_only=False, is_concurrency_safe=True, is_destructive=False)


class DriveWriteTool:
    name = "drive.write"
    description = (
        "Create or overwrite a text file in the user's Drive (folders auto-created). "
        "Own-data write; keeps prior versions; no approval needed."
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
            node = await drive.write_path(
                db,
                cc,
                path=str(args["path"]),
                data=str(args["content"]).encode("utf-8"),
                content_type="text/plain; charset=utf-8",
            )
            path = await drive.node_path(db, cc, node)
        except ServiceError as e:
            raise as_tool_error(e) from None
        return ToolResult(llm_content=f"wrote {path} ({node.size_bytes} bytes, v{node.version})")


class DriveReadTool:
    name = "drive.read"
    description = "Read a text file from the user's Drive by path. Read-only."
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
            node = await drive.resolve_file_by_path(db, cc, str(args["path"]))
            _node, data = await drive.read_node(db, cc, node.id)
        except ServiceError as e:
            raise as_tool_error(e) from None
        return ToolResult(llm_content=data.decode("utf-8", errors="replace"))


class DriveListTool:
    name = "drive.list"
    description = (
        "List a folder in the user's Drive (name, type, size, version). "
        "Omit path for the Drive root. Read-only."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "folder path; omit for root"}},
    }
    flags = ToolFlags(is_read_only=True)

    async def execute(self, ctx: ToolContext, args: dict[str, object]) -> ToolResult:
        validate_args(self.input_schema, args)
        db, cc = require_session(ctx), to_caller(ctx)
        raw = str(args.get("path") or "").strip().strip("/")
        try:
            parent_id = None
            if raw:
                folder = await drive.resolve_file_by_path(db, cc, raw)
                if folder.node_type != "folder":
                    raise as_tool_error(drive.Invalid("not a folder"))
                parent_id = folder.id
            page = await drive.list_nodes(db, cc, parent_id=parent_id, limit=200)
        except ServiceError as e:
            raise as_tool_error(e) from None
        if not page.items:
            return ToolResult(llm_content="empty folder")
        lines = []
        for n in page.items:
            if n.node_type == "folder":
                lines.append(f"- {n.name}/ (folder)")
            else:
                lines.append(f"- {n.name} ({n.size_bytes} bytes, v{n.version})")
        return ToolResult(llm_content="contents:\n" + "\n".join(lines))


class DriveSearchTool:
    name = "drive.search"
    description = "Search the user's Drive by file/folder name. Read-only."
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    }
    flags = ToolFlags(is_read_only=True)

    async def execute(self, ctx: ToolContext, args: dict[str, object]) -> ToolResult:
        validate_args(self.input_schema, args)
        db, cc = require_session(ctx), to_caller(ctx)
        try:
            page = await drive.list_nodes(db, cc, query=str(args["query"]), limit=50)
            results = []
            for n in page.items:
                path = await drive.node_path(db, cc, n)
                suffix = "/" if n.node_type == "folder" else f" ({n.size_bytes} bytes)"
                results.append(f"- {path}{suffix}")
        except ServiceError as e:
            raise as_tool_error(e) from None
        if not results:
            return ToolResult(llm_content="no matches")
        return ToolResult(llm_content="matches:\n" + "\n".join(results))


class DriveMakeFolderTool:
    name = "drive.make_folder"
    description = "Create a folder (and any missing parents) in the user's Drive. Own-data."
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "folder path, e.g. projects/x"}},
        "required": ["path"],
    }
    flags = _WRITE

    async def execute(self, ctx: ToolContext, args: dict[str, object]) -> ToolResult:
        validate_args(self.input_schema, args)
        db, cc = require_session(ctx), to_caller(ctx)
        parts = drive._split_path(str(args["path"]))
        if not parts:
            raise as_tool_error(drive.Invalid("path required"))
        try:
            uid = drive._require_user(cc)
            await drive._resolve_folder(db, cc, uid, parts, create=True)
        except ServiceError as e:
            raise as_tool_error(e) from None
        return ToolResult(llm_content=f"created folder {'/'.join(parts)}")


class DriveMoveTool:
    name = "drive.move"
    description = "Move or rename a Drive file/folder. Own-data; no approval needed."
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "current path"},
            "to": {"type": "string", "description": "destination path (folder/newname)"},
        },
        "required": ["path", "to"],
    }
    flags = _WRITE

    async def execute(self, ctx: ToolContext, args: dict[str, object]) -> ToolResult:
        validate_args(self.input_schema, args)
        db, cc = require_session(ctx), to_caller(ctx)
        try:
            node = await drive.resolve_file_by_path(db, cc, str(args["path"]))
            uid = drive._require_user(cc)
            to_parts = drive._split_path(str(args["to"]))
            if not to_parts:
                raise as_tool_error(drive.Invalid("destination required"))
            new_parent = await drive._resolve_folder(db, cc, uid, to_parts[:-1], create=True)
            moved = await drive.move(
                db,
                cc,
                node.id,
                if_version=node.version,
                parent_id=new_parent,
                new_parent_given=True,
                name=to_parts[-1],
            )
            path = await drive.node_path(db, cc, moved)
        except ServiceError as e:
            raise as_tool_error(e) from None
        return ToolResult(llm_content=f"moved to {path}")


class DriveTrashTool:
    name = "drive.trash"
    description = (
        "Move a Drive file/folder to the trash (restorable). Own-data; permanent "
        "purge is human-only and not available to the agent."
    )
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
            node = await drive.resolve_file_by_path(db, cc, str(args["path"]))
            await drive.trash(db, cc, node.id)
        except ServiceError as e:
            raise as_tool_error(e) from None
        return ToolResult(llm_content=f"trashed {args['path']}")


# `drive_restore` deleted in Phase TR P2.0 (backlog B-10): it required a `node_id`
# that no tool ever emits — `DriveListTool` prints name/type/size/version and
# `DriveSearchTool` prints paths — so the agent could only ever call it with a
# hallucinated id. Restoring from the trash stays a human action in the Drive UI
# (`drive.restore` in the service layer is still used by the REST route).


def drive_tools() -> list[object]:
    return [
        DriveWriteTool(),
        DriveReadTool(),
        DriveListTool(),
        DriveSearchTool(),
        DriveMakeFolderTool(),
        DriveMoveTool(),
        DriveTrashTool(),
    ]
