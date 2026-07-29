"""Faithful provider-history reconstruction across runs (item 0 fix, ADR-016).

`_load_transcript` used to rebuild the provider window from ``messages``/``parts``
alone — user/assistant **text only**. Tool calls/results live in the event journal
(the declared audit source of truth) and were therefore dropped across runs: on a
follow-up prompt (a new run) the model saw its own "done" claim with zero tool
evidence, "forgot" it had called a tool, and re-did or denied the work.

`assemble_provider_history` reconstructs the exact OpenAI-protocol window by
**merging two sources**, since the event journal has no user-message events
(admission emits none): user turns come from ``messages`` (role=user); the
assistant text + `tool_calls` + `role:tool` results come from the event journal.
Runs are serial in v1, so we walk runs in ``admitted_seq`` order and, per run,
emit the user prompt then replay the run's events in ``run_seq`` order.

Invariants preserved: assistant messages carry their `tool_calls`; each call is
followed by a `role:tool` result (a placeholder is synthesized for any call left
unresolved by a crash), so the provider never sees an orphaned call or result.
"""

from __future__ import annotations

import dataclasses
import json
import uuid
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.attachments import (
    ATTACHMENT_KINDS,
    AssemblyBudget,
    ResolvedAttachment,
    from_payload,
    render_attachment_content,
)
from app.models import EventJournal, Message, Part, Run
from app.models import Session as SessionModel

# Journal event types that carry conversation content we replay to the provider.
_TEXT = "text-delta"
_TOOL_CALL = "tool-call"
_TURN_END = "turn.end"
_TOOL_RESULT = "tool-result"
_TOOL_ERROR = "tool-error"
_PERMISSION_ASKED = "permission.asked"

_PENDING_APPROVAL_OBSERVATION = (
    "permission_required: approval requested; the action was NOT performed and "
    "awaits the user's decision."
)
_UNRESOLVED_PLACEHOLDER = "[no result recorded — the run was interrupted]"


@dataclasses.dataclass(frozen=True)
class _UserTurn:
    """One admitted user message: its text plus any Drive-backed attachments (ADR-043)."""

    text: str
    attachments: list[ResolvedAttachment]


async def _user_turns_by_run(
    session: AsyncSession, tenant_id: uuid.UUID, session_id: uuid.UUID
) -> dict[uuid.UUID, list[_UserTurn]]:
    """Map each run to its user turn(s), ordered by message seq."""
    rows = (
        await session.execute(
            select(Message.id, Message.run_id)
            .where(
                Message.tenant_id == tenant_id,
                Message.session_id == session_id,
                Message.role == "user",
            )
            .order_by(Message.seq)
        )
    ).all()
    if not rows:
        return {}
    ids = [r.id for r in rows]
    parts = (
        await session.execute(
            select(Part.message_id, Part.kind, Part.content_redacted)
            .where(Part.tenant_id == tenant_id, Part.message_id.in_(ids))
            .order_by(Part.ordinal)
        )
    ).all()
    text_by_msg: dict[uuid.UUID, list[str]] = defaultdict(list)
    atts_by_msg: dict[uuid.UUID, list[ResolvedAttachment]] = defaultdict(list)
    for message_id, kind, content in parts:
        if kind in ATTACHMENT_KINDS:
            atts_by_msg[message_id].append(from_payload(kind, content or {}))
        else:
            text_by_msg[message_id].append(str((content or {}).get("text", "")))
    out: dict[uuid.UUID, list[_UserTurn]] = defaultdict(list)
    for message_id, run_id in rows:
        if run_id is not None:
            out[run_id].append(
                _UserTurn(
                    text=" ".join(text_by_msg.get(message_id, [])),
                    attachments=atts_by_msg.get(message_id, []),
                )
            )
    return out


def _assistant_message(
    text_parts: list[str], tool_calls: list[dict[str, object]]
) -> dict[str, object]:
    msg: dict[str, object] = {"role": "assistant", "content": "".join(text_parts) or None}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return msg


def _replay_run_events(events: list[EventJournal], out: list[dict[str, object]]) -> None:
    """Replay one run's journal events (run_seq order) into provider messages."""
    text_parts: list[str] = []
    tool_calls: list[dict[str, object]] = []

    def flush() -> None:
        if text_parts or tool_calls:
            out.append(_assistant_message(text_parts, list(tool_calls)))
        text_parts.clear()
        tool_calls.clear()

    for event in events:
        payload = event.payload_redacted or {}
        etype = event.event_type
        if etype == _TEXT:
            text_parts.append(str(payload.get("text", "")))
        elif etype == _TOOL_CALL:
            tool_calls.append(
                {
                    "id": str(payload.get("id", "")),
                    "type": "function",
                    "function": {
                        "name": str(payload.get("name", "")),
                        "arguments": json.dumps(payload.get("args", {})),
                    },
                }
            )
        elif etype == _TURN_END:
            flush()
        elif etype in (_TOOL_RESULT, _TOOL_ERROR):
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": str(payload.get("id", "")),
                    "content": str(payload.get("output", "")),
                }
            )
        elif etype == _PERMISSION_ASKED:
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": str(payload.get("id", "")),
                    "content": str(payload.get("observation") or _PENDING_APPROVAL_OBSERVATION),
                }
            )
        # run.started / run.settled / compaction / other events carry no
        # conversation content — the loop re-compacts as needed.
    flush()  # trailing turn without a turn.end (e.g. crashed mid-turn)


def _backfill_orphan_tool_calls(messages: list[dict[str, object]]) -> list[dict[str, object]]:
    """Ensure every assistant tool_call has a following role:tool (OpenAI protocol)."""
    answered = {str(m.get("tool_call_id")) for m in messages if m.get("role") == "tool"}
    out: list[dict[str, object]] = []
    for m in messages:
        out.append(m)
        calls = m.get("tool_calls") if m.get("role") == "assistant" else None
        if isinstance(calls, list):
            for call in calls:
                call_id = str(call.get("id", "")) if isinstance(call, dict) else ""
                if call_id and call_id not in answered:
                    out.append(
                        {
                            "role": "tool",
                            "tool_call_id": call_id,
                            "content": _UNRESOLVED_PLACEHOLDER,
                        }
                    )
                    answered.add(call_id)
    return out


async def assemble_provider_history(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    session_id: uuid.UUID,
    *,
    supports_vision: bool = True,
) -> list[dict[str, object]]:
    """Rebuild the full provider message window for a session (tool history intact).

    A user turn with attachments becomes an OpenAI-shape content array (text block +
    one block per attachment, expanded from Drive under a shared byte budget); a turn
    **without** attachments keeps the plain-string `content`, so an existing session's
    cached prefix stays byte-stable (docs/04 invariant ⑤).
    """
    turns_by_run = await _user_turns_by_run(session, tenant_id, session_id)

    events = (
        (
            await session.execute(
                select(EventJournal)
                .where(
                    EventJournal.tenant_id == tenant_id,
                    EventJournal.session_id == session_id,
                )
                .order_by(EventJournal.run_id, EventJournal.run_seq)
            )
        )
        .scalars()
        .all()
    )
    events_by_run: dict[uuid.UUID, list[EventJournal]] = defaultdict(list)
    for event in events:
        events_by_run[event.run_id].append(event)

    run_ids = (
        (
            await session.execute(
                select(Run.id)
                .where(Run.tenant_id == tenant_id, Run.session_id == session_id)
                .order_by(Run.admitted_seq)
            )
        )
        .scalars()
        .all()
    )

    has_attachments = any(t.attachments for turns in turns_by_run.values() for t in turns)
    owner_id: uuid.UUID | None = None
    budget = AssemblyBudget()
    if has_attachments:
        owner_id = await session.scalar(
            select(SessionModel.user_id).where(
                SessionModel.tenant_id == tenant_id, SessionModel.id == session_id
            )
        )

    messages: list[dict[str, object]] = []
    for run_id in run_ids:
        for turn in turns_by_run.get(run_id, []):
            if not turn.attachments or owner_id is None:
                messages.append({"role": "user", "content": turn.text})
                continue
            blocks: list[dict[str, object]] = [{"type": "text", "text": turn.text}]
            for att in turn.attachments:
                blocks.append(
                    await render_attachment_content(
                        session,
                        tenant_id=tenant_id,
                        user_id=owner_id,
                        attachment=att,
                        budget=budget,
                        supports_vision=supports_vision,
                    )
                )
            messages.append({"role": "user", "content": blocks})
        _replay_run_events(events_by_run.get(run_id, []), messages)

    return _backfill_orphan_tool_calls(messages)
