"""Project-bound host filesystem tools over the durable working-copy effective tree."""

from __future__ import annotations

import uuid

from app.services import ServiceError
from app.services import project_fs as svc
from app.tools.adapter import arg_int, as_tool_error, require_session, to_caller
from app.tools.base import ToolContext, ToolError, ToolFlags, ToolResult
from app.tools.validate import validate_args

_WRITE = ToolFlags(is_read_only=False, is_concurrency_safe=True, is_destructive=False)
_PATH = {"type": "string", "minLength": 1, "maxLength": 1024}
_HASH = {"type": "string", "pattern": "^[0-9a-f]{64}$"}


def _session_id(ctx: ToolContext) -> uuid.UUID:
    if ctx.session_id is None:
        raise ToolError("fs tools require a Project-bound chat session")
    return ctx.session_id


class FsListTool:
    name = "fs_list"
    description = "List the current Project working tree by path. Read-only, bounded, no sandbox."
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "maxLength": 1024},
            "max_entries": {"type": "integer", "minimum": 1, "maximum": 500},
        },
    }
    flags = ToolFlags()

    async def execute(self, ctx: ToolContext, args: dict[str, object]) -> ToolResult:
        validate_args(self.input_schema, args)
        try:
            page = await svc.list_entries(
                require_session(ctx),
                to_caller(ctx),
                session_id=_session_id(ctx),
                path=str(args.get("path", ".")),
                max_entries=arg_int(args.get("max_entries", 200)),
            )
        except ServiceError as exc:
            raise as_tool_error(exc) from None
        lines = [
            f"{entry.path}{'/' if entry.entry_kind == 'dir' else ''} "
            f"[{entry.entry_kind}, {entry.size_bytes} bytes"
            f"{', executable' if entry.executable else ''}]"
            for entry in page.entries
        ]
        if not lines:
            lines.append("(empty)")
        if page.truncated:
            lines.append("PARTIAL: more entries exist; narrow path or raise max_entries.")
        return ToolResult(llm_content="\n".join(lines))


class FsReadTool:
    name = "fs_read"
    description = "Read bounded UTF-8 lines from one file in the current Project working tree."
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "path": _PATH,
            "start_line": {"type": "integer", "minimum": 1},
            "max_lines": {"type": "integer", "minimum": 1, "maximum": 2000},
        },
        "required": ["path"],
    }
    flags = ToolFlags()

    async def execute(self, ctx: ToolContext, args: dict[str, object]) -> ToolResult:
        validate_args(self.input_schema, args)
        try:
            page = await svc.read_file(
                require_session(ctx),
                to_caller(ctx),
                session_id=_session_id(ctx),
                path=str(args["path"]),
                start_line=arg_int(args.get("start_line", 1)),
                max_lines=arg_int(args.get("max_lines", 500)),
            )
        except ServiceError as exc:
            raise as_tool_error(exc) from None
        header = (
            f"{page.path} sha256={page.content_hash} lines "
            f"{page.start_line}-{page.start_line + max(len(page.lines) - 1, 0)}"
            f"/{page.total_lines}"
        )
        body = "\n".join(
            f"{page.start_line + index}: {line}" for index, line in enumerate(page.lines)
        )
        suffix = "\nPARTIAL: more lines exist." if page.truncated else ""
        return ToolResult(llm_content=f"{header}\n{body}{suffix}".rstrip())


class FsGrepTool:
    name = "fs_grep"
    description = "Find a literal UTF-8 string in bounded Project files. Read-only, no sandbox."
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "minLength": 1, "maxLength": 1000},
            "path": {"type": "string", "maxLength": 1024},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 500},
        },
        "required": ["pattern"],
    }
    flags = ToolFlags()

    async def execute(self, ctx: ToolContext, args: dict[str, object]) -> ToolResult:
        validate_args(self.input_schema, args)
        try:
            page = await svc.grep(
                require_session(ctx),
                to_caller(ctx),
                session_id=_session_id(ctx),
                pattern=str(args["pattern"]),
                path=str(args.get("path", ".")),
                max_results=arg_int(args.get("max_results", 100)),
            )
        except ServiceError as exc:
            raise as_tool_error(exc) from None
        lines = [f"{match.path}:{match.line}: {match.text}" for match in page.matches]
        if not lines:
            lines.append("(no matches)")
        if page.skipped_binary or page.skipped_large:
            lines.append(f"Skipped binary={page.skipped_binary}, oversized={page.skipped_large}.")
        if page.truncated:
            lines.append("PARTIAL: result or scan bound reached.")
        return ToolResult(llm_content="\n".join(lines))


class FsWriteTool:
    name = "fs_write"
    description = "Create or replace one UTF-8 file in the reviewable Project working copy."
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "path": _PATH,
            "content": {"type": "string", "maxLength": 1_000_000},
            "executable": {"type": "boolean"},
            "if_hash": _HASH,
        },
        "required": ["path", "content"],
    }
    flags = _WRITE

    async def execute(self, ctx: ToolContext, args: dict[str, object]) -> ToolResult:
        validate_args(self.input_schema, args)
        try:
            result = await svc.write_file(
                require_session(ctx),
                to_caller(ctx),
                session_id=_session_id(ctx),
                path=str(args["path"]),
                content=str(args["content"]),
                executable=bool(args.get("executable", False)),
                if_hash=str(args["if_hash"]) if args.get("if_hash") is not None else None,
            )
        except ServiceError as exc:
            raise as_tool_error(exc) from None
        return ToolResult(
            llm_content=(
                f"{result.change_kind} {result.path} sha256={result.content_hash}; "
                f"change_set={result.change_set_id}. Save remains user-only."
            )
        )


class FsEditTool:
    name = "fs_edit"
    description = "Replace an exact occurrence in one UTF-8 Project file; conflicts change nothing."
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "path": _PATH,
            "old_text": {"type": "string", "minLength": 1, "maxLength": 100_000},
            "new_text": {"type": "string", "maxLength": 100_000},
            "expect_occurrences": {"type": "integer", "minimum": 1, "maximum": 100},
        },
        "required": ["path", "old_text", "new_text"],
    }
    flags = _WRITE

    async def execute(self, ctx: ToolContext, args: dict[str, object]) -> ToolResult:
        validate_args(self.input_schema, args)
        try:
            result = await svc.edit_file(
                require_session(ctx),
                to_caller(ctx),
                session_id=_session_id(ctx),
                path=str(args["path"]),
                old_text=str(args["old_text"]),
                new_text=str(args["new_text"]),
                expect_occurrences=arg_int(args.get("expect_occurrences", 1)),
            )
        except ServiceError as exc:
            raise as_tool_error(exc) from None
        return ToolResult(
            llm_content=(
                f"modified {result.path} sha256={result.content_hash}; "
                f"change_set={result.change_set_id}. Save remains user-only."
            )
        )


class FsDeleteTool:
    name = "fs_delete"
    description = "Stage a file or explicit recursive directory deletion for human review."
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "path": _PATH,
            "recursive": {"type": "boolean"},
            "if_hash": _HASH,
        },
        "required": ["path"],
    }
    flags = _WRITE

    async def execute(self, ctx: ToolContext, args: dict[str, object]) -> ToolResult:
        validate_args(self.input_schema, args)
        try:
            result = await svc.delete_path(
                require_session(ctx),
                to_caller(ctx),
                session_id=_session_id(ctx),
                path=str(args["path"]),
                recursive=bool(args.get("recursive", False)),
                if_hash=str(args["if_hash"]) if args.get("if_hash") is not None else None,
            )
        except ServiceError as exc:
            raise as_tool_error(exc) from None
        return ToolResult(
            llm_content=(
                f"deleted {result.path}; change_set={result.change_set_id}. Save remains user-only."
            )
        )


def fs_tools() -> list[object]:
    return [
        FsListTool(),
        FsReadTool(),
        FsGrepTool(),
        FsWriteTool(),
        FsEditTool(),
        FsDeleteTool(),
    ]
