"""Candidate tools (m-tools T3): the agent drives the Candidate Inbox.

Thin adapters over `app.services.candidates` — the same capability layer the REST
endpoints use. Own-tenant reads/writes on the user's explicit instruction, so the
policy engine allows them (no approval). `if_version` gives optimistic concurrency;
the agent reads it via `list_candidates` before accepting/editing/dismissing.
"""

from __future__ import annotations

import datetime
import uuid
from typing import cast

from app.services import ServiceError, candidates
from app.tools.adapter import require_session, to_caller
from app.tools.base import ToolContext, ToolError, ToolFlags, ToolResult
from app.tools.validate import validate_args

_ID = {"type": "string", "description": "candidate id (uuid)"}
_VER = {"type": "integer", "description": "if_version from list_candidates (optimistic lock)"}
_WRITE = ToolFlags(is_read_only=False, is_concurrency_safe=True, is_destructive=False)


def _to_error(e: ServiceError) -> ToolError:
    return ToolError(e.tool_observation)


def _uuid(value: object) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError) as exc:
        raise ToolError(f"invalid uuid: {value!r}") from exc


def _int(value: object) -> int:
    return cast("int", value)  # schema-validated to integer before execution


def _opt_str(value: object) -> str | None:
    return None if value is None else str(value)


def _parse_due(value: object) -> datetime.datetime | None:
    if value is None or value == "":
        return None
    try:
        return datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ToolError(f"invalid due_at: {value!r}") from exc


class ListCandidatesTool:
    name = "list_candidates"
    description = (
        "List the user's action candidates (proposals extracted from connected email) "
        "with their id, title, priority and version. Read-only."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["pending", "accepted", "edited", "dismissed"]},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        },
    }
    flags = ToolFlags(is_read_only=True)

    async def execute(self, ctx: ToolContext, args: dict[str, object]) -> ToolResult:
        validate_args(self.input_schema, args)
        db, cc = require_session(ctx), to_caller(ctx)
        status = str(args.get("status", "pending"))
        limit = _int(args["limit"]) if "limit" in args else 20
        try:
            page = await candidates.list_candidates(db, cc, status_filter=status, limit=limit)
        except ServiceError as e:
            raise _to_error(e) from None
        if not page.items:
            return ToolResult(llm_content=f"no {status} candidates")
        lines = [
            f"- {c.id} · {c.title} · {c.priority} · confidence {c.confidence:.2f} · v{c.version}"
            for c in page.items
        ]
        return ToolResult(llm_content=f"{status} candidates:\n" + "\n".join(lines))


class AcceptCandidateTool:
    name = "accept_candidate"
    description = (
        "Accept a candidate, creating a linked to-do. Needs candidate_id + if_version. "
        "Pass any of title/description/due_at/priority to edit those fields first."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "candidate_id": _ID,
            "if_version": _VER,
            "title": {"type": "string"},
            "description": {"type": "string"},
            "due_at": {"type": "string", "description": "ISO-8601 datetime"},
            "priority": {"type": "string", "enum": ["low", "medium", "high"]},
        },
        "required": ["candidate_id", "if_version"],
    }
    flags = _WRITE

    async def execute(self, ctx: ToolContext, args: dict[str, object]) -> ToolResult:
        validate_args(self.input_schema, args)
        db, cc = require_session(ctx), to_caller(ctx)
        candidate_id, if_version = _uuid(args["candidate_id"]), _int(args["if_version"])
        title = _opt_str(args.get("title"))
        description = _opt_str(args.get("description"))
        due_at = _parse_due(args.get("due_at"))
        priority = _opt_str(args.get("priority"))
        edited = any(v is not None for v in (title, description, due_at, priority))
        try:
            if edited:
                result = await candidates.edit_candidate(
                    db,
                    cc,
                    candidate_id=candidate_id,
                    if_version=if_version,
                    title=title,
                    description=description,
                    due_at=due_at,
                    priority=priority,
                )
            else:
                result = await candidates.accept_candidate(
                    db, cc, candidate_id=candidate_id, if_version=if_version
                )
        except ServiceError as e:
            raise _to_error(e) from None
        verb = "edited + accepted" if edited else "accepted"
        todo = result.todo
        text = f"{verb} candidate {result.candidate.id}; created todo {todo.id}: {todo.title}"
        return ToolResult(llm_content=text)


# `edit_candidate` deleted in Phase TR P2.0 (backlog B-10): it was `accept` plus an
# optional patch — same effect class, same approval scope — so the model had to choose
# between two tools for one intent. The capability-layer split is unchanged: the REST
# surface still has separate accept/edit endpoints for the Inbox UI's two buttons.


class DismissCandidateTool:
    name = "dismiss_candidate"
    description = "Dismiss (discard) a candidate. Needs candidate_id + if_version."
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {"candidate_id": _ID, "if_version": _VER, "reason": {"type": "string"}},
        "required": ["candidate_id", "if_version"],
    }
    flags = _WRITE

    async def execute(self, ctx: ToolContext, args: dict[str, object]) -> ToolResult:
        validate_args(self.input_schema, args)
        db, cc = require_session(ctx), to_caller(ctx)
        try:
            cand = await candidates.dismiss_candidate(
                db,
                cc,
                candidate_id=_uuid(args["candidate_id"]),
                if_version=_int(args["if_version"]),
                reason=_opt_str(args.get("reason")),
            )
        except ServiceError as e:
            raise _to_error(e) from None
        return ToolResult(llm_content=f"dismissed candidate {cand.id}")


def candidate_tools() -> list[object]:
    return [
        ListCandidatesTool(),
        AcceptCandidateTool(),
        DismissCandidateTool(),
    ]
