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


# --- Gmail connector (api.md §3.4) ---
ConnectorStatus = Literal[
    "pending_oauth",
    "active",
    "paused",
    "syncing",
    "degraded",
    "disconnecting",
    "revoked",
    "error",
]


class GmailSyncScope(StrictModel):
    lookback_days: Annotated[int, Field(ge=1, le=365)] = 30
    label_ids: Annotated[list[str], Field(max_length=50)] = Field(default_factory=lambda: ["INBOX"])
    include_spam_trash: Literal[False] = False


class GmailConnectRequest(StrictModel):
    return_to: Annotated[str, Field(pattern=r"^/[A-Za-z0-9/_?&=.-]*$")]
    sync_scope: GmailSyncScope = Field(default_factory=GmailSyncScope)


class OAuthStart(StrictModel):
    authorization_url: str
    expires_at: datetime.datetime


class ConnectorSyncStatus(StrictModel):
    cursor_present: bool
    last_started_at: datetime.datetime | None
    last_succeeded_at: datetime.datetime | None
    last_error_code: str | None
    last_run_id: uuid.UUID | None


class Connector(StrictModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    kind: Literal["gmail"]
    status: ConnectorStatus
    account_email: str | None
    granted_scopes: list[str]
    sync_scope: GmailSyncScope
    sync: ConnectorSyncStatus
    version: int
    created_at: datetime.datetime
    updated_at: datetime.datetime


class ConnectorAdmission(StrictModel):
    connector_id: uuid.UUID
    run_id: uuid.UUID
    state: Literal["queued"] = "queued"
    admitted_at: datetime.datetime


# --- Candidates and todos (api.md §3.3) ---
CandidateStatus = Literal["pending", "accepted", "edited", "dismissed"]
Priority = Literal["low", "medium", "high"]


class CandidateSource(StrictModel):
    kind: Literal["gmail"]
    connector_id: uuid.UUID
    item_id: uuid.UUID
    revision: str
    thread_id: str
    subject: str | None
    sender: str | None
    received_at: datetime.datetime
    excerpt: str | None
    deep_link: str | None


class InferredField(StrictModel):
    field: Literal["title", "description", "due_at", "priority"]
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    evidence: str | None


class Candidate(StrictModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    status: CandidateStatus
    title: Annotated[str, Field(min_length=1, max_length=300)]
    description: Annotated[str, Field(max_length=8_000)] | None
    due_at: datetime.datetime | None
    priority: Priority
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    inferred_fields: list[InferredField]
    source: CandidateSource
    accepted_todo_id: uuid.UUID | None
    version: int
    created_at: datetime.datetime
    updated_at: datetime.datetime


class CandidatePage(StrictModel):
    items: list[Candidate]
    next_cursor: str | None


class CandidateAccept(StrictModel):
    if_version: int


class CandidateEdit(StrictModel):
    if_version: int
    title: Annotated[str, Field(min_length=1, max_length=300)] | None = None
    description: Annotated[str, Field(max_length=8_000)] | None = None
    due_at: datetime.datetime | None = None
    priority: Priority | None = None


class CandidateDismiss(StrictModel):
    if_version: int
    reason: Annotated[str, Field(max_length=500)] | None = None


class Todo(StrictModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    source_candidate_id: uuid.UUID
    title: Annotated[str, Field(min_length=1, max_length=300)]
    description: Annotated[str, Field(max_length=8_000)] | None
    status: Literal["open", "completed", "cancelled"]
    due_at: datetime.datetime | None
    snoozed_until: datetime.datetime | None
    completed_at: datetime.datetime | None
    priority: Priority
    version: int
    created_at: datetime.datetime
    updated_at: datetime.datetime


class TodoPage(StrictModel):
    items: list[Todo]
    next_cursor: str | None


class TodoPatch(StrictModel):
    if_version: int
    title: Annotated[str, Field(min_length=1, max_length=300)] | None = None
    description: Annotated[str, Field(max_length=8_000)] | None = None
    status: Literal["open", "completed", "cancelled"] | None = None
    due_at: datetime.datetime | None = None
    snoozed_until: datetime.datetime | None = None
    priority: Priority | None = None


class CandidateAcceptance(StrictModel):
    candidate: Candidate
    todo: Todo
