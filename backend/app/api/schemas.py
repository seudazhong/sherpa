"""Public API request/response models (api.md §2.4, §3). Closed schemas (extra=forbid).

Deviation note: the contract types `email` as `EmailStr`; v1 uses `str` to avoid
the extra email-validator dependency (single-owner login). Field names, other
types, nullability, and enum values match the contract.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

RunState = Literal["queued", "running", "needs_attention", "completed", "failed", "interrupted"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- Auth (api.md §3.1) ---
class LoginRequest(StrictModel):
    email: str
    password: Annotated[str, Field(min_length=1, max_length=1024)]


class AuthSession(StrictModel):
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    email: str
    csrf_token: str
    expires_at: datetime.datetime


# --- Sessions, admissions, messages (api.md §3.2) ---
class SessionCreate(StrictModel):
    title: Annotated[str, Field(min_length=1, max_length=200)] | None = None


class SessionSummary(StrictModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    channel: Literal["web"]
    umo_key: str
    title: str | None
    latest_run_state: RunState | None
    last_message_preview: str | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class SessionPage(StrictModel):
    items: list[SessionSummary]
    next_cursor: str | None


class PromptRequest(StrictModel):
    client_message_id: uuid.UUID
    text: Annotated[str, Field(min_length=1, max_length=32_000)]


class PromptAdmission(StrictModel):
    session_id: uuid.UUID
    message_id: uuid.UUID
    run_id: uuid.UUID
    admitted_seq: int
    state: Literal["queued"] = "queued"
    event_cursor: str
    events_url: str


class PublicMessagePart(StrictModel):
    kind: Literal["text", "status", "tool_summary"]
    text: str


class PublicMessage(StrictModel):
    id: uuid.UUID
    session_id: uuid.UUID
    seq: int
    role: Literal["user", "assistant"]
    parts: list[PublicMessagePart]
    run_id: uuid.UUID | None
    created_at: datetime.datetime


class MessagePage(StrictModel):
    items: list[PublicMessage]
    next_cursor: str | None
    event_cursor: str
