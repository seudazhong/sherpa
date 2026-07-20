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
    source_candidate_id: uuid.UUID | None
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


class TodoCreate(StrictModel):
    title: Annotated[str, Field(min_length=1, max_length=300)]
    description: Annotated[str, Field(max_length=8_000)] | None = None
    due_at: datetime.datetime | None = None
    priority: Priority = "medium"


class CandidateAcceptance(StrictModel):
    candidate: Candidate
    todo: Todo


# --- Notifications + settings (api.md §4.6) ---
class Notification(StrictModel):
    firing_id: uuid.UUID
    schedule_id: uuid.UUID
    schedule_name: str
    channel: str
    scheduled_for: datetime.datetime
    status: str
    delivery_outcome: str | None
    settled_at: datetime.datetime | None


class NotificationPage(StrictModel):
    items: list[Notification]
    next_cursor: str | None


class Settings(StrictModel):
    notifications_enabled: bool
    web_enabled: bool
    email_digest_enabled: bool
    timezone: str
    quiet_hours_enabled: bool
    quiet_hours_start: datetime.time
    quiet_hours_end: datetime.time
    daily_cap: int
    version: int


class SettingsPatch(StrictModel):
    if_version: int
    notifications_enabled: bool | None = None
    web_enabled: bool | None = None
    email_digest_enabled: bool | None = None
    timezone: str | None = None
    quiet_hours_enabled: bool | None = None
    daily_cap: Annotated[int, Field(ge=0, le=100)] | None = None


# --- Approval envelope (api.md §6) — FROZEN (ADR-020) ---
EffectClass = Literal[
    "read_only",
    "idempotent_write",
    "reconcilable_write",
    "non_idempotent_write",
]


class ApprovalActor(StrictModel):
    type: Literal["user"]
    id: uuid.UUID


class ApprovalBound(StrictModel):
    tenant_id: uuid.UUID
    run_id: uuid.UUID
    invocation_id: uuid.UUID


class ApprovalAction(StrictModel):
    tool_name: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
    permission_scope: Annotated[str, Field(min_length=1, max_length=512)]
    session_id: uuid.UUID


class ApprovalPreviewDetail(StrictModel):
    label: Annotated[str, Field(min_length=1, max_length=100)]
    value: Annotated[str, Field(max_length=1000)]


class ApprovalPreview(StrictModel):
    action: Annotated[str, Field(min_length=1, max_length=200)]
    summary: Annotated[str, Field(min_length=1, max_length=2000)]
    details: Annotated[list[ApprovalPreviewDetail], Field(max_length=20)]
    risk: Annotated[str, Field(max_length=1000)] | None = None


class ApprovalDecision(StrictModel):
    actor: ApprovalActor
    channel: Literal["web", "qq", "email"]
    choice: Literal["allow_once", "allow_session", "always", "reject"]


class ApprovalEnvelope(StrictModel):
    schema_version: Literal["1.0"]
    correlation_id: uuid.UUID
    bound: ApprovalBound
    action: ApprovalAction
    effect_class: EffectClass
    normalized_args_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    human_readable_preview: ApprovalPreview
    policy_version: Annotated[str, Field(min_length=1, max_length=200)]
    expires_at: datetime.datetime
    nonce: Annotated[str, Field(min_length=43, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")]
    authorized_actor: ApprovalActor
    decision: ApprovalDecision | None


class ApprovalResolution(StrictModel):
    correlation_id: uuid.UUID
    state: Literal["resolved"]
    winning_decision: ApprovalDecision
    decided_at: datetime.datetime


class PendingApproval(StrictModel):
    """Read projection of a pending envelope for the web inbox (no nonce)."""

    correlation_id: uuid.UUID
    tenant_id: uuid.UUID
    run_id: uuid.UUID
    session_id: uuid.UUID
    invocation_id: uuid.UUID
    tool_name: str
    permission_scope: str
    effect_class: EffectClass
    policy_version: str
    normalized_args_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    human_readable_preview: ApprovalPreview
    authorized_actor: ApprovalActor
    expires_at: datetime.datetime
    requested_at: datetime.datetime


class PendingApprovalPage(StrictModel):
    items: list[PendingApproval]
    next_cursor: str | None


# --- Activity ledger + data controls (ADR-021) ---
class ActivityReceipt(StrictModel):
    id: uuid.UUID
    receipt_type: str
    actor_type: str
    trigger_type: str
    action: str
    outcome: str
    reversible: bool
    summary: dict[str, object]
    run_id: uuid.UUID | None
    subject_type: str | None
    subject_id: uuid.UUID | None
    occurred_at: datetime.datetime


class ActivityPage(StrictModel):
    items: list[ActivityReceipt]
    next_cursor: str | None


class DeleteImportedResult(StrictModel):
    deleted: dict[str, int]
