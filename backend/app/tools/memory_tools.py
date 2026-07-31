"""Core-memory tools (milestone 1a): the agent remembers user facts across sessions.

Thin adapters over `app.services.memory` — the same capability layer a Memory UI
will use. Own-data key-value: reads are SAFE (read-only), writes act on the user's
own tenant on their instruction, so the policy engine allows them (no approval).
This is the bounded core-memory tier; embeddings/RAG are deferred (ADR-012/022).
"""

from __future__ import annotations

from app.services import ServiceError, memory, passages
from app.tools.adapter import arg_int, as_tool_error, require_session, to_caller
from app.tools.base import ToolContext, ToolFlags, ToolResult
from app.tools.validate import validate_args

_KEY: dict[str, object] = {
    "type": "string",
    "description": "lowercase key, e.g. 'timezone' or 'prefers.concise' (^[a-z][a-z0-9_.-]{0,63}$)",
}
_WRITE = ToolFlags(is_read_only=False, is_concurrency_safe=True, is_destructive=False)


class MemorySetTool:
    name = "memory_user_set"
    description = (
        "Remember a durable fact or preference about the user under a key "
        "(overwrites that key). Use for stable facts worth recalling in future "
        "sessions. Own-data write; no approval needed."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "key": _KEY,
            "value": {"type": "string", "minLength": 1, "maxLength": 16384},
        },
        "required": ["key", "value"],
    }
    flags = _WRITE

    async def execute(self, ctx: ToolContext, args: dict[str, object]) -> ToolResult:
        validate_args(self.input_schema, args)
        db, cc = require_session(ctx), to_caller(ctx)
        try:
            row = await memory.set_memory(db, cc, key=str(args["key"]), value=str(args["value"]))
        except ServiceError as e:
            raise as_tool_error(e) from None
        return ToolResult(llm_content=f"remembered '{row.memory_key}' (v{row.version})")


class MemoryGetTool:
    name = "memory_user_get"
    description = (
        "Recall stored facts about the user. Give a key for one fact; omit key for all. Read-only."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {"key": _KEY},
    }
    flags = ToolFlags(is_read_only=True)

    async def execute(self, ctx: ToolContext, args: dict[str, object]) -> ToolResult:
        validate_args(self.input_schema, args)
        db, cc = require_session(ctx), to_caller(ctx)
        key = args.get("key")
        try:
            if key is None:
                rows = await memory.list_memory(db, cc)
                if not rows:
                    return ToolResult(llm_content="no memories stored yet")
                body = "\n".join(f"- {r.memory_key}: {r.value_text}" for r in rows)
                return ToolResult(llm_content="stored memories:\n" + body)
            row = await memory.get_memory(db, cc, key=str(key))
        except ServiceError as e:
            raise as_tool_error(e) from None
        if row is None:
            return ToolResult(llm_content=f"no memory stored for '{key}'")
        return ToolResult(llm_content=f"{row.memory_key}: {row.value_text}")


# `memory_user_list` deleted in Phase TR P2.0 (backlog B-10, ADR-046 §8): it is
# `memory_user_get` with the key omitted. Both become `memory.recall` in P2.2.


class MemoryDeleteTool:
    name = "memory_user_delete"
    description = "Forget a stored fact about the user by key. Own-data; no approval needed."
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {"key": _KEY},
        "required": ["key"],
    }
    flags = _WRITE

    async def execute(self, ctx: ToolContext, args: dict[str, object]) -> ToolResult:
        validate_args(self.input_schema, args)
        db, cc = require_session(ctx), to_caller(ctx)
        try:
            await memory.delete_memory(db, cc, key=str(args["key"]))
        except ServiceError as e:
            raise as_tool_error(e) from None
        return ToolResult(llm_content=f"forgot '{args['key']}'")


class MemoryNoteTool:
    name = "memory_note"
    description = (
        "Save a longer note or passage to durable archival memory for later "
        "semantic recall (e.g. context, a decision, a summary). Retrieve later with "
        "memory_search. Prefer memory_user_set for short structured facts. Own-data "
        "write; no approval needed."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {"text": {"type": "string", "minLength": 1, "maxLength": 65536}},
        "required": ["text"],
    }
    flags = _WRITE

    async def execute(self, ctx: ToolContext, args: dict[str, object]) -> ToolResult:
        validate_args(self.input_schema, args)
        db, cc = require_session(ctx), to_caller(ctx)
        try:
            row = await passages.add_passage(db, cc, text=str(args["text"]))
        except ServiceError as e:
            raise as_tool_error(e) from None
        return ToolResult(llm_content=f"noted (id {row.id})")


class MemorySearchTool:
    name = "memory_search"
    description = (
        "Search the user's archival memory (saved notes/passages) by meaning and "
        "keywords; returns the most relevant notes. Read-only."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "minLength": 1},
            "k": {"type": "integer", "minimum": 1, "maximum": 10},
        },
        "required": ["query"],
    }
    flags = ToolFlags(is_read_only=True)

    async def execute(self, ctx: ToolContext, args: dict[str, object]) -> ToolResult:
        validate_args(self.input_schema, args)
        db, cc = require_session(ctx), to_caller(ctx)
        k = arg_int(args["k"]) if "k" in args else 5
        try:
            hits = await passages.search_passages(db, cc, query=str(args["query"]), k=k)
        except ServiceError as e:
            raise as_tool_error(e) from None
        if not hits:
            return ToolResult(llm_content="no matching notes")
        return ToolResult(llm_content="relevant notes:\n" + "\n".join(f"- {h.text}" for h in hits))


def memory_tools() -> list[object]:
    return [
        MemorySetTool(),
        MemoryGetTool(),
        MemoryDeleteTool(),
        MemoryNoteTool(),
        MemorySearchTool(),
    ]
