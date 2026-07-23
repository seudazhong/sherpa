# Sherpa v1 API + Tool Interface Contract

> **Status: FROZEN for implementation (2026-07-20).** This contract is the
> public HTTP boundary and the internal tool narrow waist for Sherpa v1.
> Breaking changes require an ADR and a new schema/API version.

Normative words **MUST**, **MUST NOT**, **SHOULD**, and **MAY** are used in their
RFC 2119 sense.

## 1. v1 profile and ownership of adjacent contracts

Sherpa v1 is a self-hosted, single-instance, single-user Gmail-to-Action
assistant with a secondary Web chat surface (ADR-022).

This document intentionally does not redefine:

- event envelope, event types, run/effect states, ordering, cursor/reset
  semantics, or SSE wire framing: [`events-and-effects.md`](events-and-effects.md);
- persistence tables, keys, constraints, and provenance:
  [`data-model.md`](data-model.md);
- configuration names, secret sources, cookie settings, OAuth credentials, and
  retention settings: [`config-and-secrets.md`](config-and-secrets.md).

Only the following HTTP surfaces are in v1. QQ/IM, generic webhook inbound,
agentic-email inbound, GitHub, files, sandbox/code execution, external write
actions, arbitrary cron jobs, WebSocket streaming, team APIs, and approval
renderers are **reserved, not in v1**. No placeholder route for those surfaces
may appear in the v1 OpenAPI document.

The approval envelope and resolution route are frozen now so later renderers
cannot invent incompatible semantics. No v1 action produces an approval request,
and candidate accept/edit/dismiss is a normal business workflow, not permission
approval.

## 2. HTTP conventions, authentication, and tenancy

### 2.1 Representation rules

- Paths below are literal. There is no additional `/api/v1` prefix.
- JSON requests and responses use `application/json; charset=utf-8`.
- Pydantic v2 models use `ConfigDict(extra="forbid")`.
- IDs are opaque UUID strings. Clients MUST NOT infer ordering from them.
- Timestamps are timezone-aware RFC 3339 UTC strings (`...Z`).
- Local times use `HH:MM`; time zones use IANA names such as `Asia/Shanghai`.
- List endpoints use opaque cursor pagination: `limit` is `1..100`, default
  `50`; responses contain `items` and nullable `next_cursor`.
- Mutable resources expose an integer `version`. A mutation supplies
  `if_version`; stale writes return `409 version_conflict`.
- Unknown fields, invalid enums, and invalid field combinations return `422`.
- Tenant-scoped resources outside the authenticated tenant return `404`, never
  `403`, to avoid an identifier oracle.

### 2.2 Session authentication

`POST /auth/login` creates an opaque server-side session and sets the cookie
configured in `config-and-secrets.md`. Production cookies MUST be `HttpOnly`,
`Secure`, and `SameSite=Lax`. Bearer tokens are not a v1 browser API.

The login response also returns a CSRF token. Every authenticated unsafe request
(`POST`, `PATCH`, `DELETE`) MUST send it as `X-CSRF-Token`. The OAuth callback is
instead protected by its single-use signed `state`. SSE uses the session cookie
and does not require a CSRF header.

### 2.3 Tenant context

There is exactly one owner and one personal tenant in v1, but tenant context is
mandatory everywhere:

1. Authentication resolves `(user_id, tenant_id)` into `RequestContext`.
2. The server MUST ignore/reject client-supplied `tenant_id`; there is no
   `X-Tenant-ID` header.
3. Every repository call, queue message, event, run, invocation, cache key, and
   `ToolContext` receives the resolved `tenant_id`.
4. OAuth callback requests recover the same tenant from the signed, one-use
   OAuth state created by the connect request.
5. `resolve_inbound()` remains the single identity/session admission boundary.
   Web chat resolves to a server-created `web:chat:<session-id>` UMO key. Gmail
   connector items enter the connector pipeline, not a user-authenticated chat.

### 2.4 Common models

Pydantic-ish definitions below are normative in field name, type, nullability,
and enum value; implementation-only validators may be added.

```python
from datetime import datetime, time
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ApiErrorBody(StrictModel):
    code: str
    message: str
    request_id: str
    details: dict[str, Any] | None = None


class ApiError(StrictModel):
    error: ApiErrorBody


class CursorPage(StrictModel):
    items: list[Any]
    next_cursor: str | None
```

All errors use `ApiError`. `400` means malformed transport/query data; `401`
means no valid login session; `403` means valid login but failed CSRF or
operation-level authorization; `404` means no tenant-visible resource; `409`
means a state/version/idempotency conflict; `410` means an expiring capability
has expired; `422` means schema/semantic validation; `429` means a documented
rate/cap limit; and `503` means a required dependency is unavailable.

## 3. Resource schemas

### 3.1 Auth

```python
class LoginRequest(StrictModel):
    email: EmailStr
    password: Annotated[str, Field(min_length=1, max_length=1024)]


class AuthSession(StrictModel):
    user_id: UUID
    tenant_id: UUID
    email: EmailStr
    csrf_token: str
    expires_at: datetime
```

### 3.2 Sessions, admissions, and messages

```python
RunState = Literal[
    "queued", "running", "needs_attention", "completed", "failed", "interrupted"
]


class SessionCreate(StrictModel):
    title: Annotated[str, Field(min_length=1, max_length=200)] | None = None


class SessionSummary(StrictModel):
    id: UUID
    tenant_id: UUID
    channel: Literal["web"]
    umo_key: str
    title: str | None
    latest_run_state: RunState | None
    last_message_preview: str | None
    created_at: datetime
    updated_at: datetime


class PromptRequest(StrictModel):
    client_message_id: UUID
    text: Annotated[str, Field(min_length=1, max_length=32_000)]


class PromptAdmission(StrictModel):
    session_id: UUID
    message_id: UUID
    run_id: UUID
    admitted_seq: int
    state: Literal["queued"]
    event_cursor: str
    events_url: str


class PublicMessagePart(StrictModel):
    kind: Literal["text", "status", "tool_summary"]
    text: str


class PublicMessage(StrictModel):
    id: UUID
    session_id: UUID
    seq: int
    role: Literal["user", "assistant"]
    parts: list[PublicMessagePart]
    run_id: UUID | None
    created_at: datetime


class MessagePage(StrictModel):
    items: list[PublicMessage]
    next_cursor: str | None
    event_cursor: str
```

`PublicMessage` MUST NOT expose system prompts, raw tool arguments/results,
connector bodies, secrets, or hidden chain-of-thought. Curated rationale and
bounded tool summaries are allowed.

`latest_run_state` is a UI projection, not a second canonical state machine:
`queued` precedes `run.started`; `running` follows it; `needs_attention` means a
pending approval or `effect_unknown`; `completed` maps only to terminal
`completed`; `interrupted` maps `interrupted|timeout|aborted`; and `failed` maps
`failed|stopped:budget|stopped:no_progress`. Canonical lifecycle and precedence
remain in `events-and-effects.md`.

`client_message_id` is unique within `(tenant_id, session_id)`. Retrying the same
value with the same body returns the original `202 PromptAdmission`; reusing it
with a different body returns `409 idempotency_conflict`.

`MessagePage.event_cursor` is the session journal tail captured in the same
database snapshot as the returned messages. `PromptAdmission.event_cursor` is
the committed session tail immediately after admission. These cursors close the
snapshot-to-stream race without redefining the event cursor format.

### 3.3 Candidates and todos

```python
CandidateStatus = Literal["pending", "accepted", "edited", "dismissed"]
Priority = Literal["low", "medium", "high"]


class CandidateSource(StrictModel):
    kind: Literal["gmail"]
    connector_id: UUID
    item_id: UUID
    revision: str
    thread_id: str
    subject: str | None
    sender: str | None
    received_at: datetime
    excerpt: str | None
    deep_link: str | None


class InferredField(StrictModel):
    field: Literal["title", "description", "due_at", "priority"]
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    evidence: str | None


class Candidate(StrictModel):
    id: UUID
    tenant_id: UUID
    status: CandidateStatus
    title: Annotated[str, Field(min_length=1, max_length=300)]
    description: Annotated[str, Field(max_length=8_000)] | None
    due_at: datetime | None
    priority: Priority
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    inferred_fields: list[InferredField]
    source: CandidateSource
    accepted_todo_id: UUID | None
    version: int
    created_at: datetime
    updated_at: datetime


class CandidateAccept(StrictModel):
    if_version: int


class CandidateEdit(StrictModel):
    if_version: int
    # At least one editable field must be supplied.
    title: Annotated[str, Field(min_length=1, max_length=300)] | None = None
    description: Annotated[str, Field(max_length=8_000)] | None = None
    due_at: datetime | None = None
    priority: Priority | None = None


class CandidateDismiss(StrictModel):
    if_version: int
    reason: Annotated[str, Field(max_length=500)] | None = None


class Todo(StrictModel):
    id: UUID
    tenant_id: UUID
    source_candidate_id: UUID
    title: Annotated[str, Field(min_length=1, max_length=300)]
    description: Annotated[str, Field(max_length=8_000)] | None
    status: Literal["open", "completed", "cancelled"]
    due_at: datetime | None
    snoozed_until: datetime | None
    completed_at: datetime | None
    priority: Priority
    version: int
    created_at: datetime
    updated_at: datetime


class TodoPatch(StrictModel):
    if_version: int
    title: Annotated[str, Field(min_length=1, max_length=300)] | None = None
    description: Annotated[str, Field(max_length=8_000)] | None = None
    status: Literal["open", "completed", "cancelled"] | None = None
    due_at: datetime | None = None
    snoozed_until: datetime | None = None
    priority: Priority | None = None


class CandidateAcceptance(StrictModel):
    candidate: Candidate
    todo: Todo
```

Accepting or editing a candidate MUST atomically create exactly one todo linked
through the provenance chain in `data-model.md`. Accepting without edits produces
candidate state `accepted`; edit-and-accept produces `edited`; both are terminal.
Dismissal never deletes provenance. Only a `pending` candidate may transition.

### 3.4 Gmail connector

```python
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
    label_ids: Annotated[list[str], Field(max_length=50)] = Field(
        default_factory=lambda: ["INBOX"]
    )
    include_spam_trash: Literal[False] = False


class GmailConnectRequest(StrictModel):
    return_to: Annotated[str, Field(pattern=r"^/[A-Za-z0-9/_?&=.-]*$")]
    sync_scope: GmailSyncScope


class OAuthStart(StrictModel):
    authorization_url: str
    expires_at: datetime


class ConnectorSyncStatus(StrictModel):
    cursor_present: bool
    last_started_at: datetime | None
    last_succeeded_at: datetime | None
    last_error_code: str | None
    last_run_id: UUID | None


class Connector(StrictModel):
    id: UUID
    tenant_id: UUID
    kind: Literal["gmail"]
    status: ConnectorStatus
    account_email: EmailStr | None
    granted_scopes: list[str]
    sync_scope: GmailSyncScope
    sync: ConnectorSyncStatus
    version: int
    created_at: datetime
    updated_at: datetime


class AsyncAdmission(StrictModel):
    run_id: UUID
    state: Literal["queued"]
    admitted_at: datetime


class ConnectorAdmission(AsyncAdmission):
    connector_id: UUID
```

Only Gmail OAuth read scopes approved in `config-and-secrets.md` may be
requested. The callback MUST encrypt tokens before committing the connector and
MUST NOT return tokens, authorization codes, provider errors containing secrets,
or raw credential metadata.

### 3.5 Schedules and firings

v1 schedules are product schedules, not a generic agent cron API. The only
kinds are one-time todo reminders and daily digests.

```python
class OnceTrigger(StrictModel):
    type: Literal["once"]
    at: datetime


class DailyTrigger(StrictModel):
    type: Literal["daily"]
    local_time: time


class ScheduleTarget(StrictModel):
    todo_id: UUID | None = None
    reminder_kind: Literal["due_soon", "overdue"] | None = None


class ScheduleCreate(StrictModel):
    kind: Literal["todo_reminder", "daily_digest"]
    name: Annotated[str, Field(min_length=1, max_length=200)]
    timezone: str
    trigger: OnceTrigger | DailyTrigger
    target: ScheduleTarget
    delivery_channel: Literal["web", "digest_email"]
    enabled: bool = True


class SchedulePatch(StrictModel):
    if_version: int
    name: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    timezone: str | None = None
    trigger: OnceTrigger | DailyTrigger | None = None
    target: ScheduleTarget | None = None
    delivery_channel: Literal["web", "digest_email"] | None = None
    enabled: bool | None = None


class Schedule(StrictModel):
    id: UUID
    tenant_id: UUID
    kind: Literal["todo_reminder", "daily_digest"]
    name: str
    timezone: str
    trigger: OnceTrigger | DailyTrigger
    target: ScheduleTarget
    delivery_channel: Literal["web", "digest_email"]
    status: Literal["active", "paused", "completed", "disabled"]
    next_run_at: datetime | None
    last_firing_at: datetime | None
    version: int
    created_at: datetime
    updated_at: datetime


class ScheduleFiring(StrictModel):
    id: UUID
    schedule_id: UUID
    scheduled_for: datetime
    status: Literal["pending", "running", "succeeded", "missed", "failed", "unknown"]
    attempts: int
    delivery_outcome: Literal["succeeded", "missed", "failed", "unknown"] | None
    run_id: UUID | None
    invocation_id: UUID | None
    receipt_id: UUID | None
    started_at: datetime | None
    settled_at: datetime | None
```

Validation MUST enforce:

- `todo_reminder` uses `OnceTrigger`, has `target.todo_id`, and may use `web`;
- `daily_digest` uses `DailyTrigger`, has no `todo_id`, and may use
  `web` or `digest_email`;
- `target.reminder_kind` is required for `todo_reminder` and null for
  `daily_digest`;
- arbitrary cron strings, prompts, tool names, and executable payloads are
  rejected;
- deleting a schedule prevents future firings but retains firing/audit history.

### 3.6 Settings

```python
class QuietHours(StrictModel):
    enabled: bool
    start: time
    end: time


class NotificationSettings(StrictModel):
    enabled: bool                         # opt-in; default false
    web_enabled: bool
    email_digest_enabled: bool
    timezone: str
    digest_time: time
    quiet_hours: QuietHours
    daily_cap: Annotated[int, Field(ge=0, le=100)]
    event_types: list[
        Literal["new_candidate", "due_soon", "overdue", "run_failed"]
    ]
    eventual_delivery_kinds: list[Literal["due_soon", "overdue"]]


class AutonomySettings(StrictModel):
    connector_analysis: Literal["off", "candidate_first"]
    todo_promotion: Literal["manual"] = "manual"
    external_actions: Literal["approval_required"] = "approval_required"


class Settings(StrictModel):
    tenant_id: UUID
    notifications: NotificationSettings
    autonomy: AutonomySettings
    version: int
    updated_at: datetime


class SettingsPatch(StrictModel):
    if_version: int
    notifications: NotificationSettings | None = None
    autonomy: AutonomySettings | None = None
```

`todo_promotion="manual"` is a v1 invariant. `external_actions` reserves the
future boundary; external actions themselves are not in v1. Notification
settings are global gates over all schedules: a firing may exist while delivery
is suppressed by opt-out, quiet hours, or cap, and that outcome must remain
visible in firing history. There is at most one `daily_digest` schedule per
tenant. Its local time/time zone and `NotificationSettings.digest_time/timezone`
are one logical value: writes through either endpoint update both projections in
one transaction and increment both resource versions.

## 4. REST endpoints

“Session” below means a valid cookie. “CSRF” means the valid
`X-CSRF-Token` bound to that session.

### 4.1 Auth

| Method and path | Request → response | Auth | Status codes |
|---|---|---|---|
| `POST /auth/login` | `LoginRequest` → `AuthSession`; sets session cookie | Public; same-origin check and login rate limit | `200`, `401`, `422`, `429`, `503` |
| `GET /auth/session` | none → `AuthSession` with a refreshed CSRF token | Session | `200`, `401` |
| `POST /auth/logout` | none → empty | Session + CSRF | `204`, `401`, `403` |

```http
POST /auth/login
Content-Type: application/json

{"email":"owner@example.com","password":"correct horse battery staple"}
```

```json
{
  "user_id": "019bbec4-c741-7cc3-bdd7-1524c9a98997",
  "tenant_id": "019bbec4-d00c-744c-9bd4-b48b34da4dcc",
  "email": "owner@example.com",
  "csrf_token": "opaque-session-bound-token",
  "expires_at": "2026-07-21T04:05:45Z"
}
```

### 4.2 Web chat sessions

| Method and path | Request → response | Auth | Status codes |
|---|---|---|---|
| `POST /sessions` | `SessionCreate` → `SessionSummary` | Session + CSRF | `201`, `401`, `403`, `422`, `429`, `503` |
| `POST /sessions/{id}/prompt` | `PromptRequest` → `PromptAdmission` | Session + CSRF | `202`, `401`, `403`, `404`, `409`, `422`, `429`, `503` |
| `GET /sessions?cursor=&limit=` | none → `CursorPage[SessionSummary]` | Session | `200`, `400`, `401`, `422`, `503` |
| `GET /sessions/{id}/messages?cursor=&limit=` | none → `MessagePage` | Session | `200`, `400`, `401`, `404`, `422`, `503` |

`POST /sessions/{id}/prompt` implements durable admission:

1. resolve the authenticated tenant/session;
2. in one database transaction persist the user message, allocate
   `admitted_seq`, create the run, and create the outbox record;
3. commit;
4. return `202` without running the core loop in the Web process.

Queue delivery may occur before or after the HTTP response, but the response
MUST NOT be sent before the database commit. Exact run/event/effect state
semantics are owned by `events-and-effects.md`.

```http
POST /sessions/019bbeca-9ce4-7c47-926d-b374f1e0c0ef/prompt
X-CSRF-Token: opaque-session-bound-token
Content-Type: application/json

{
  "client_message_id": "019bbecd-3a84-7e48-886f-ff75368967c6",
  "text": "Show my high-priority Gmail candidates."
}
```

```http
HTTP/1.1 202 Accepted
Location: /sessions/019bbeca-9ce4-7c47-926d-b374f1e0c0ef
```

```json
{
  "session_id": "019bbeca-9ce4-7c47-926d-b374f1e0c0ef",
  "message_id": "019bbecd-c2ec-7189-be4a-6f41327744b4",
  "run_id": "019bbece-35ac-70cd-9874-c98e4e275d69",
  "admitted_seq": 42,
  "state": "queued",
  "event_cursor": "opaque-journal-cursor",
  "events_url": "/sessions/019bbeca-9ce4-7c47-926d-b374f1e0c0ef/events?cursor=opaque-journal-cursor"
}
```

### 4.3 Candidate inbox and todos

| Method and path | Request → response | Auth | Status codes |
|---|---|---|---|
| `GET /candidates?status=&cursor=&limit=` | none → `CursorPage[Candidate]`; default `status=pending` | Session | `200`, `400`, `401`, `422`, `503` |
| `POST /candidates/{id}/accept` | `CandidateAccept` → `CandidateAcceptance` | Session + CSRF | `201`, `401`, `403`, `404`, `409`, `422`, `503` |
| `POST /candidates/{id}/edit` | `CandidateEdit` → `CandidateAcceptance` | Session + CSRF | `201`, `401`, `403`, `404`, `409`, `422`, `503` |
| `POST /candidates/{id}/dismiss` | `CandidateDismiss` → `Candidate` | Session + CSRF | `200`, `401`, `403`, `404`, `409`, `422`, `503` |
| `GET /todos?status=&due_before=&cursor=&limit=` | none → `CursorPage[Todo]` | Session | `200`, `400`, `401`, `422`, `503` |
| `PATCH /todos/{id}` | `TodoPatch` → `Todo` | Session + CSRF | `200`, `401`, `403`, `404`, `409`, `422`, `503` |

```http
POST /candidates/019bbed1-7ab6-7f4e-90f9-24724d207a70/edit
X-CSRF-Token: opaque-session-bound-token
Content-Type: application/json

{
  "if_version": 3,
  "title": "Submit quarterly report",
  "due_at": "2026-07-24T09:00:00Z",
  "priority": "high"
}
```

Both accept and edit return `201 CandidateAcceptance` and set
`Location: /todos/{todo.id}`. Repeating acceptance after the candidate has
already been accepted returns the existing linked result only when the request
is semantically identical; otherwise it returns `409 invalid_candidate_state`.

### 4.4 Gmail connector

| Method and path | Request → response | Auth | Status codes |
|---|---|---|---|
| `GET /connectors?cursor=&limit=` | none → `CursorPage[Connector]` | Session | `200`, `400`, `401`, `422`, `503` |
| `POST /connectors/gmail/connect` | `GmailConnectRequest` → `OAuthStart` | Session + CSRF | `200`, `401`, `403`, `409`, `422`, `429`, `503` |
| `GET /connectors/gmail/oauth/callback?code=&state=&error=` | query → `303` to the allowlisted `return_to` | One-use signed OAuth state | `303`, `400`, `409`, `410`, `502`, `503` |
| `POST /connectors/{id}/sync` | none → `ConnectorAdmission` | Session + CSRF | `202`, `401`, `403`, `404`, `409`, `429`, `503` |
| `POST /connectors/{id}/pause` | none → `Connector` | Session + CSRF | `200`, `401`, `403`, `404`, `409`, `503` |
| `POST /connectors/{id}/resume` | none → `Connector` | Session + CSRF | `200`, `401`, `403`, `404`, `409`, `503` |
| `DELETE /connectors/{id}` | none → `ConnectorAdmission` | Session + CSRF | `202`, `401`, `403`, `404`, `409`, `503` |

```json
{
  "return_to": "/connectors",
  "sync_scope": {
    "lookback_days": 30,
    "label_ids": ["INBOX"],
    "include_spam_trash": false
  }
}
```

The connect response contains a provider authorization URL. `return_to` is a
relative SPA path, never an OAuth redirect URI; the OAuth redirect URI comes
only from trusted configuration.

The callback validates state, PKCE, requested/granted scopes, and account
binding, then immediately encrypts credentials and commits the connector before
redirecting. It redirects with a short non-secret result code such as
`?gmail=connected` or `?gmail=failed&code=scope_mismatch`.

Manual sync is durably admitted and returns `202`. A paused connector rejects
new sync admissions with `409 connector_paused`; an in-flight sync observes the
pause at a batch boundary. Resume does not implicitly start a sync.

Disconnect first marks the connector `disconnecting`, prevents new work, and
durably admits credential revocation/deletion. Provider revocation uses the
effect semantics in `events-and-effects.md`; an unknown remote outcome is
visible and is never blindly retried. Local plaintext credentials never exist
in an API response, event, log, or error.

### 4.5 Schedules and firing history

| Method and path | Request → response | Auth | Status codes |
|---|---|---|---|
| `POST /schedules` | `ScheduleCreate` → `Schedule` | Session + CSRF | `201`, `401`, `403`, `409`, `422`, `503` |
| `GET /schedules?kind=&status=&cursor=&limit=` | none → `CursorPage[Schedule]` | Session | `200`, `400`, `401`, `422`, `503` |
| `GET /schedules/{id}` | none → `Schedule` | Session | `200`, `401`, `404`, `503` |
| `PATCH /schedules/{id}` | `SchedulePatch` → `Schedule` | Session + CSRF | `200`, `401`, `403`, `404`, `409`, `422`, `503` |
| `DELETE /schedules/{id}` | none → empty | Session + CSRF | `204`, `401`, `403`, `404`, `409`, `503` |
| `POST /schedules/{id}/cancel` | `{if_version}` → `Schedule` | Session + CSRF | `200`, `401`, `403`, `404`, `409`, `503` |
| `POST /schedules/{id}/status` | `{if_version, status}` (active\|paused) → `Schedule` | Session + CSRF | `200`, `401`, `403`, `404`, `409`, `422`, `503` |
| `POST /schedules/{id}/run-now` | none → `ScheduleFiring` | Session + CSRF | `202`, `401`, `403`, `404`, `409`, `429`, `503` |
| `GET /schedules/{id}/firings?status=&cursor=&limit=` | none → `CursorPage[ScheduleFiring]` | Session | `200`, `400`, `401`, `404`, `422`, `503` |

```json
{
  "kind": "daily_digest",
  "name": "Morning Gmail action digest",
  "timezone": "Asia/Shanghai",
  "trigger": {
    "type": "daily",
    "local_time": "08:00:00"
  },
  "target": {"todo_id": null, "reminder_kind": null},
  "delivery_channel": "digest_email",
  "enabled": true
}
```

Creating/updating a schedule persists its next firing state transactionally.
Firings, outbox delivery, idempotency, `unknown`, and reconciliation follow
`events-and-effects.md`; storage follows `data-model.md`.

**General cron / recurring agent tasks (ADR-031).** `kind` additionally accepts
`agent_task`, and `trigger.type` accepts `cron`, `interval`, `weekly`, `monthly`,
and `once` beyond `daily`. `delivery_channel` additionally accepts `email` and
`qq`. `POST /schedules/{id}/run-now` inserts an immediate firing without advancing
the recurrence cursor (`202`; `429` if a per-user frequency/concurrency cap is
hit). An `agent_task` firing enqueues a `run_kind='scheduled_task'` run seeded with
`prompt`; its `run_id` is recorded on the `ScheduleFiring` for run history, and the
delivered body is the run's output (not static text). External side effects inside
the run remain approval-gated. Example `agent_task` create:

```json
{
  "kind": "agent_task",
  "name": "Weekday morning inbox triage",
  "timezone": "Asia/Shanghai",
  "trigger": {"type": "cron", "cron_expr": "0 9 * * 1-5"},
  "prompt": "Summarize my unread email, list what needs a reply, and draft replies. Ask before sending anything.",
  "delivery_channel": "qq",
  "enabled": true
}
```

`trigger` fields by `type`: `daily`/`weekly`/`monthly` use `local_time` (+
`weekly_days` CSV of 0..6, or `monthly_day` 1..31); `cron` uses `cron_expr`
(5-field); `interval` uses `interval_seconds` (≥ 60); `once` uses an absolute
`at`. `ScheduleFiring` gains `run_id` (nullable) and, for `agent_task`, a bounded
result reference.

### 4.6 Settings

| Method and path | Request → response | Auth | Status codes |
|---|---|---|---|
| `GET /settings` | none → `Settings` | Session | `200`, `401`, `503` |
| `PATCH /settings` | `SettingsPatch` → `Settings` | Session + CSRF | `200`, `401`, `403`, `409`, `422`, `503` |

```json
{
  "if_version": 7,
  "notifications": {
    "enabled": true,
    "web_enabled": true,
    "email_digest_enabled": true,
    "timezone": "Asia/Shanghai",
    "digest_time": "08:00:00",
    "quiet_hours": {
      "enabled": true,
      "start": "22:00:00",
      "end": "08:00:00"
    },
    "daily_cap": 6,
    "event_types": ["new_candidate", "due_soon", "overdue", "run_failed"],
    "eventual_delivery_kinds": ["overdue"]
  },
  "autonomy": {
    "connector_analysis": "candidate_first",
    "todo_promotion": "manual",
    "external_actions": "approval_required"
  }
}
```

### 4.7 Permission resolution

| Method and path | Request → response | Auth | Status codes |
|---|---|---|---|
| `POST /permissions/{id}/resolve` | resolved `ApprovalEnvelope` → `ApprovalResolution` | Session + CSRF; actor must match `authorized_actor` | `200`, `401`, `403`, `404`, `409`, `410`, `422`, `503` |

`id` is the approval `correlation_id`. The route is part of the frozen contract,
but no v1 renderer calls it and no v1 action emits `permission.asked`.

### 4.8 Health and readiness

```python
class Health(StrictModel):
    status: Literal["ok"]
    service: Literal["sherpa-web"]
    version: str


class ComponentReadiness(StrictModel):
    status: Literal["ok", "failed"]
    detail: str | None = None


class Readiness(StrictModel):
    status: Literal["ready", "not_ready"]
    components: dict[
        Literal["postgres", "migrations", "redis", "worker"],
        ComponentReadiness,
    ]
```

| Method and path | Request → response | Auth | Status codes |
|---|---|---|---|
| `GET /healthz` | none → `Health` | Public | `200` |
| `GET /readyz` | none → `Readiness` | Public | `200`, `503` |

`healthz` only proves the Web process is alive. `readyz` is `200` only when
Postgres is reachable, migrations are current, Redis admission is reachable,
and a non-stale worker heartbeat exists. Responses MUST NOT expose DSNs,
credentials, host filesystem paths, or stack traces.

## 5. Session event stream

### `GET /sessions/{id}/events?cursor=`

- **Auth:** session cookie; no CSRF header.
- **Response:** `text/event-stream`.
- **Codes:** `200`, `400` (invalid/contradictory/ahead cursor), `401`, `404`,
  `409` (reset required), `503`.
- **Framing and payload:** exactly the SSE framing and versioned event envelope
  in `events-and-effects.md`; this document does not redefine them.
- **Catch-up:** an opaque `cursor` query value resumes strictly after that
  cursor. If absent, `Last-Event-ID` is used. If both are supplied and differ,
  return `400 cursor_mismatch`.
- **No cursor:** capture the current journal tail and stream only subsequent
  events. Initial page loads SHOULD use `MessagePage.event_cursor`; prompt
  callers SHOULD use `PromptAdmission.event_cursor`.
- **Reconnect:** journal catch-up happens before live Redis-accelerated delivery.
  Deduplication, ordering, retention gaps, and reset behavior are exactly those
  in `events-and-effects.md`.
- **Heartbeat:** when no event has been sent for 15 seconds, send the SSE comment
  `: heartbeat\n\n`. It has no event ID, is not journaled, and does not advance
  the cursor.
- **Headers:** `Cache-Control: no-cache, no-transform`,
  `Connection: keep-alive`, and `X-Accel-Buffering: no`.
- **Slow clients:** bounded buffering and disconnect/reset behavior follow the
  event contract. The Web process MUST NOT allow an unbounded per-client queue.

If a syntactically valid cursor is no longer serviceable because retained
journal history was lawfully deleted/expired, the server does not open an SSE
stream. It returns `409 application/json`:

```python
class EventStreamReset(StrictModel):
    code: Literal["stream_reset_required"]
    reason: Literal["cursor_expired", "history_deleted"]
    message: str
    snapshot_url: str
    reset_cursor: str
```

The client fetches `snapshot_url` (`/sessions/{id}/messages`), replaces its local
projection, and reconnects with `reset_cursor`. A cursor ahead of the journal is
invalid (`400`), not a reset.

## 6. Approval envelope — FROZEN (ADR-020)

The envelope is semantic JSON, not HTML, Markdown, an email template, or a
surface-specific card. Renderers are built only when the first `ask` action
ships. Until then, this schema and resolution transaction are tested but have no
UI.

Arguments are validated against the tool schema and canonicalized using RFC 8785
JSON Canonicalization Scheme (JCS). `normalized_args_hash` is the lowercase
64-character hexadecimal SHA-256 digest of the canonical UTF-8 bytes. It is the
same value as the invocation `args_hash` in `events-and-effects.md`.

### 6.1 Pending envelope example

```json
{
  "schema_version": "1.0",
  "correlation_id": "019bbee2-2d6f-735a-85a1-0a7a56a1a8d1",
  "bound": {
    "tenant_id": "019bbec4-d00c-744c-9bd4-b48b34da4dcc",
    "run_id": "019bbece-35ac-70cd-9874-c98e4e275d69",
    "invocation_id": "019bbee3-4e84-73cd-a80b-5b5b8b948fb0"
  },
  "action": {
    "tool_name": "gmail_send",
    "permission_scope": "gmail.send",
    "session_id": "019bbeca-9ce4-7c47-926d-b374f1e0c0ef"
  },
  "effect_class": "reconcilable_write",
  "normalized_args_hash": "10ae12f8b9e7d87b64bc491d9366c06d74d205dc08af4c74ed546d431bb7f0f2",
  "human_readable_preview": {
    "action": "Send an email",
    "summary": "Send the reviewed quarterly report reply.",
    "details": [
      {"label": "To", "value": "finance@example.com"},
      {"label": "Subject", "value": "Re: Quarterly report"}
    ],
    "risk": "This represents you to an external recipient."
  },
  "policy_version": "policy-2026-07-20.1",
  "expires_at": "2026-07-20T04:20:45Z",
  "nonce": "7nyN5x5PMgOqgPcmt_XOHiZ6pZ5bL6mZ9X0Lh5c2bCo",
  "authorized_actor": {
    "type": "user",
    "id": "019bbec4-c741-7cc3-bdd7-1524c9a98997"
  },
  "decision": null
}
```

All preview strings are plain text, bounded, and redacted. A renderer MUST escape
them and MUST NOT treat them as markup.

### 6.2 Decision object

The resolver echoes the complete immutable envelope and replaces `decision`:

```json
{
  "actor": {
    "type": "user",
    "id": "019bbec4-c741-7cc3-bdd7-1524c9a98997"
  },
  "channel": "web",
  "choice": "allow_once"
}
```

```python
EffectClass = Literal[
    "read_only",
    "idempotent_write",
    "reconcilable_write",
    "non_idempotent_write",
]


class ApprovalActor(StrictModel):
    type: Literal["user"]
    id: UUID


class ApprovalBound(StrictModel):
    tenant_id: UUID
    run_id: UUID
    invocation_id: UUID


class ApprovalAction(StrictModel):
    tool_name: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
    permission_scope: Annotated[str, Field(min_length=1, max_length=512)]
    session_id: UUID


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
    correlation_id: UUID
    bound: ApprovalBound
    action: ApprovalAction
    effect_class: EffectClass
    normalized_args_hash: Annotated[
        str, Field(pattern=r"^[0-9a-f]{64}$")
    ]
    human_readable_preview: ApprovalPreview
    policy_version: Annotated[str, Field(min_length=1, max_length=200)]
    expires_at: datetime
    nonce: Annotated[
        str, Field(min_length=43, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    ]
    authorized_actor: ApprovalActor
    decision: ApprovalDecision | None


class ApprovalResolution(StrictModel):
    correlation_id: UUID
    state: Literal["resolved"]
    winning_decision: ApprovalDecision
    decided_at: datetime
```

Only `channel="web"` is accepted in v1. `qq`, `email`, and other channels are
reserved contract values and require a future renderer/security review.

### 6.3 Field table

| Field | Type | Frozen meaning and validation |
|---|---|---|
| `schema_version` | string | Exact semantic schema version. v1 accepts `1.0`; unknown major versions are rejected. |
| `correlation_id` | UUID | Globally unique approval identity and `{id}` in the resolve route. |
| `bound.tenant_id` | UUID | Must equal authenticated tenant and stored invocation tenant. |
| `bound.run_id` | UUID | Exact suspended run; never a caller-selected replacement run. |
| `bound.invocation_id` | UUID | Exact persisted invocation awaiting permission. |
| `action.tool_name` | string | Registered tool identity attached to the persisted invocation. |
| `action.permission_scope` | string | Exact policy scope proposed by the policy engine; clients cannot broaden it. |
| `action.session_id` | UUID | Session containing the bound run; basis of `allow_session`. |
| `effect_class` | string | Value from the effect taxonomy in `events-and-effects.md`. |
| `normalized_args_hash` | 64 lowercase hex characters | SHA-256 of schema-validated RFC 8785 canonical arguments; exactly equals the invocation `args_hash`; any argument change invalidates the approval. |
| `human_readable_preview` | object | Plain-text `action`, `summary`, ordered `details[{label,value}]`, and optional `risk`; redacted and safe to display after escaping. |
| `policy_version` | string | Immutable policy snapshot that produced `ask`; policy changes invalidate the pending envelope. |
| `expires_at` | datetime | UTC expiry. Resolution at or after this instant returns `410 approval_expired`. |
| `nonce` | string | Single-use, at least 256 bits of cryptographic randomness, base64url without padding. |
| `authorized_actor` | object | Exact user authorized to decide. v1 has one owner, but the check is still mandatory. |
| `decision` | object or null | Null while pending; otherwise exact `{actor, channel, choice}`. |

Choice semantics are fixed:

- `allow_once`: authorize only the bound invocation with the exact argument hash;
- `allow_session`: authorize the exact tool/policy scope within
  `action.session_id` until that session auth grant is revoked or expires;
- `always`: atomically persist the exact `permission_scope` under the current
  tenant and policy model; it MUST NOT synthesize a broader wildcard;
- `reject`: fail the bound invocation as rejected and resume the run with a
  bounded tool error.

### 6.4 First-valid-response-wins

Resolution is one database transaction:

1. authenticate session/CSRF and derive actor/tenant/channel;
2. load by `(tenant_id, correlation_id)` and compare every immutable envelope
   field with the stored pending record;
3. verify actor, nonce, expiry, policy version, invocation state, argument hash,
   and allowed choice;
4. atomically change `pending -> resolved` only if still pending;
5. persist any `allow_session`/`always` grant, semantic audit receipt, and outbox
   wake-up in the same transaction.

The first transaction that satisfies all checks wins. An exact retry of the
winning submission returns `200` idempotently. A different later submission
returns `409 approval_already_resolved`. Invalid nonce/binding/actor does not
reveal the stored envelope. Expired approvals return `410`; no late response can
resume the invocation.

## 7. Internal Tool contract

The internal interface is asynchronous and provider-neutral:

```python
from typing import Any, Protocol


class ToolFlags(StrictModel):
    is_read_only: bool
    is_concurrency_safe: bool
    is_destructive: bool


class DisplayPayload(StrictModel):
    format: Literal["text", "markdown", "json"]
    content: str | dict[str, Any] | list[Any]


class ToolResult(StrictModel):
    llm_content: str
    return_display: DisplayPayload | None


class ToolContext(StrictModel):
    tenant_id: UUID
    user_id: UUID
    session_id: UUID
    run_id: UUID
    invocation_id: UUID
    source: Literal["web"]
    deadline: datetime


class Tool(Protocol):
    name: str
    description: str
    input_schema: dict[str, Any]       # canonical JSON Schema
    flags: ToolFlags

    async def execute(
        self, context: ToolContext, args: dict[str, Any]
    ) -> ToolResult: ...
```

Contract rules:

- `name` is stable, unique, `^[a-z][a-z0-9_]{0,63}$`.
- `description` is model-visible and MUST state purpose, boundary, and important
  non-obvious failure conditions; it MUST NOT contain secrets or tenant data.
- `input_schema` is JSON Schema Draft 2020-12. Object schemas default to
  `additionalProperties: false`; arguments are validated before execution.
- `ToolContext` is runtime-injected and never model-controlled.
- `llm_content` is the concise observation returned to the model.
- `return_display` is the separately sanitized user-facing projection. It is
  never fed back to the model and is never trusted as executable markup.
- Tool failures become bounded typed observations/events; they do not crash the
  core loop. Effect outcomes use `events-and-effects.md`.
- A tool implementation MUST NOT open its own tenant/session, policy, approval,
  event, or audit bypass.

### 7.1 Four mandatory gates

Every invocation follows one path:

```text
REGISTERED -> VISIBLE -> ALLOWED -> EXECUTABLE
```

1. **REGISTERED** — the implementation is in the process registry; its name and
   schema pass startup validation. Unknown/dynamically supplied tools fail
   closed.
2. **VISIBLE** — at turn construction, profile/source checks decide which
   registered tools enter the model request. The visible set is frozen for that
   turn. A `deny` known at this stage removes the tool before model exposure.
3. **ALLOWED** — the tenant policy engine evaluates the tool and intended scope.
   Effects are `allow | ask | deny`; last matching rule wins, equal-specificity
   conflict resolves `deny > ask > allow`, and the default is `ask`.
4. **EXECUTABLE** — immediately before execution, the runtime rechecks
   stop-reason=`tool_use`, schema, tenant/run/invocation binding, cancellation,
   budget/deadline, current policy, approval (if `ask`), effect/idempotency
   record, and connector/workspace scope. Any mismatch fails closed.

`is_read_only` does not skip the gates. `is_destructive` is a policy signal, not
an automatic authorization. Tools may execute concurrently only when every tool
in the batch is `is_concurrency_safe=true`, policy permits it, and their declared
resource scopes do not conflict; otherwise preserve model order and serialize.

### 7.2 Output bounding and spill

After redaction, the serialized result of one invocation is bounded to both
**2,000 lines** and **50,000 UTF-8 bytes (50 KB)**. If either limit is exceeded:

1. persist the full redacted result to the tenant/run-scoped path
   `TOOL_OUTPUT_ROOT/{invocation_id}.txt` (default
   `.sherpa/tool-output/{invocation_id}.txt`), up to the configured
   per-invocation spill cap;
2. replace `llm_content` and `return_display` with a bounded head/tail summary
   (at most 1,000 head and 1,000 tail lines, further byte-trimmed to 50,000
   bytes);
3. include the frozen spill reference below, original byte/line counts, expiry,
   and truncation marker;
4. never spill unredacted secrets, OAuth tokens, hidden prompts, or data outside
   the tool's authorized scope.

```python
class ToolOutputSpillReference(StrictModel):
    kind: Literal["tool_output_spill"]
    truncated: Literal[True]
    spill_ref: Annotated[
        str, Field(pattern=r"^tool-output:[0-9a-f-]{36}$")
    ]
    invocation_id: UUID
    relative_path: Annotated[
        str, Field(pattern=r"^[0-9a-f-]{36}\.txt$")
    ]
    original_lines: Annotated[int, Field(ge=0)]
    original_bytes: Annotated[int, Field(ge=1)]
    retained_lines: Annotated[int, Field(ge=0, le=2000)]
    retained_bytes: Annotated[int, Field(ge=0, le=50_000)]
    expires_at: datetime
```

`spill_ref` is the stable public/internal reference
`tool-output:{invocation_id}`; it is not a URL or host path. `relative_path` is
exactly `{invocation_id}.txt` beneath `TOOL_OUTPUT_ROOT`. The structured object
is placed in `return_display` as `format="json"`; `llm_content` contains the same
reference/counts plus the bounded head/tail text. Access to the backing file is
tenant/run/invocation-authorized and is never static serving. Validation also
requires `original_lines > 2000` or `original_bytes > 50000`.

If the configured spill cap or aggregate cap prevents persistence, return a
bounded `tool_output_spill_limit` error observation rather than silently dropping
or partially claiming the artifact. Spill root, caps, and retention are owned by
`config-and-secrets.md`. Runtime-owned spill writes do not grant the model a
general write tool.

### 7.3 v1 starter registry

| Stable tool name(s) | Boundary | Flags |
|---|---|---|
| `read`, `glob`, `grep` | Read only inside `WORKSPACE_ROOT`; normalized relative paths; no symlink escape; no file REST API | read-only, concurrency-safe, non-destructive |
| `candidate_list`, `candidate_edit`, `candidate_accept`, `candidate_dismiss` | Private candidate service and provenance rules; accept/edit requires an explicit authenticated-user instruction and atomically creates a todo | mixed read/write; mutations serialized; non-destructive |
| `todo_list`, `todo_update` | User-private accepted-candidate todos only; same validation/domain service as REST | mixed read/write; mutations serialized; non-destructive |
| `memory_user_get`, `memory_user_set` | Bounded user-private core memory only; no tenant-shared memory, embeddings, pgvector, or RAG | get is read-only/safe; set is serialized/non-destructive |
| `gmail_search`, `gmail_get_message` | Connected account, granted labels/scopes, read-only; bounded excerpts by default; no send/modify/delete | read-only, concurrency-safe, non-destructive |
| `ask_user` | Ask a clarification in the authenticated Web session and suspend at a safe turn boundary; cannot grant permission | non-read-only, serialized, non-destructive |

The canonical starter `input_schema` values are generated from these strict
Pydantic models:

```python
class ReadArgs(StrictModel):
    path: Annotated[str, Field(min_length=1, max_length=1000)]
    start_line: Annotated[int, Field(ge=1)] = 1
    max_lines: Annotated[int, Field(ge=1, le=2000)] = 500


class GlobArgs(StrictModel):
    pattern: Annotated[str, Field(min_length=1, max_length=500)]
    path: Annotated[str, Field(max_length=1000)] = "."
    max_results: Annotated[int, Field(ge=1, le=1000)] = 200


class GrepArgs(StrictModel):
    pattern: Annotated[str, Field(min_length=1, max_length=1000)]
    path: Annotated[str, Field(max_length=1000)] = "."
    file_glob: Annotated[str, Field(max_length=500)] | None = None
    case_sensitive: bool = True
    max_results: Annotated[int, Field(ge=1, le=1000)] = 200


class CandidateListArgs(StrictModel):
    status: CandidateStatus = "pending"
    limit: Annotated[int, Field(ge=1, le=100)] = 50


class CandidateAcceptArgs(StrictModel):
    candidate_id: UUID
    if_version: int


class CandidateEditArgs(CandidateAcceptArgs):
    # At least one editable field must be supplied.
    title: Annotated[str, Field(min_length=1, max_length=300)] | None = None
    description: Annotated[str, Field(max_length=8000)] | None = None
    due_at: datetime | None = None
    priority: Priority | None = None


class CandidateDismissArgs(CandidateAcceptArgs):
    reason: Annotated[str, Field(max_length=500)] | None = None


class TodoListArgs(StrictModel):
    status: Literal["open", "completed", "cancelled"] | None = None
    due_before: datetime | None = None
    limit: Annotated[int, Field(ge=1, le=100)] = 50


class TodoUpdateArgs(StrictModel):
    todo_id: UUID
    if_version: int
    title: Annotated[str, Field(min_length=1, max_length=300)] | None = None
    description: Annotated[str, Field(max_length=8000)] | None = None
    status: Literal["open", "completed", "cancelled"] | None = None
    due_at: datetime | None = None
    snoozed_until: datetime | None = None
    priority: Priority | None = None


class MemoryUserGetArgs(StrictModel):
    key: Annotated[
        str, Field(pattern=r"^[a-z][a-z0-9_.-]{0,63}$")
    ]


class MemoryUserSetArgs(MemoryUserGetArgs):
    value: Annotated[str, Field(max_length=4000)]  # also <= 16,384 UTF-8 bytes
    if_version: int | None = None


class GmailSearchArgs(StrictModel):
    query: Annotated[str, Field(min_length=1, max_length=1000)]
    max_results: Annotated[int, Field(ge=1, le=50)] = 20


class GmailGetMessageArgs(StrictModel):
    message_id: Annotated[str, Field(min_length=1, max_length=512)]
    max_chars: Annotated[int, Field(ge=1, le=20_000)] = 10_000


class AskUserArgs(StrictModel):
    question: Annotated[str, Field(min_length=1, max_length=2000)]
    options: Annotated[list[str], Field(max_length=10)] = Field(
        default_factory=list
    )
    allow_free_text: bool = True
```

Path arguments are always interpreted relative to the canonical
`WORKSPACE_ROOT`; absolute paths, `..` escape, alternate data streams, device
paths, and resolved symlinks outside the root are rejected. Gmail tool results
are normalized/redacted and bounded before entering `ToolResult`; connector
credentials and raw HTTP responses never enter model context.

`candidate_accept` is not permission approval. It may run only when the current
authenticated Web input explicitly identifies/accepts the candidate; connector
content can never call it. `ask_user` is also not permission approval and cannot
produce `allow_*`/`always`.

There is no v1 `write`, `edit`, `bash`, `run_code`, generic network fetch,
external-send, GitHub, or sub-agent tool.

### 7.4 Built-ins, MCP, and sub-agents use the same path

- A built-in implements `Tool` directly.
- An MCP adapter converts an MCP tool description/schema/result into the same
  `Tool`/`ToolResult` and rejects unsupported or unbounded schema constructs.
- A sub-agent adapter presents delegation as a `Tool` with an explicit bounded
  input/output/budget and child invocation identity.

All three are registered in the same registry and pass the same four gates,
argument validation, effect persistence, permission handling, output bounding,
events, and audit path. MCP and sub-agent adapters are contract-reserved but not
registered in the v1 profile. No plugin transport may execute before
`EXECUTABLE`.

## 8. `CONNECTOR_ANALYSIS` no-tool mode (ADR-009)

Gmail-synced content does **not** enter a chat session with SAFE tools. It uses a
dedicated structured extraction capability:

```python
class ConnectorEvidence(StrictModel):
    excerpt: Annotated[str, Field(max_length=1000)]
    field: Literal["title", "description", "due_at", "priority"]


class CandidateDraft(StrictModel):
    title: Annotated[str, Field(min_length=1, max_length=300)]
    description: Annotated[str, Field(max_length=8000)] | None
    due_at: datetime | None
    priority: Priority
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    evidence: Annotated[list[ConnectorEvidence], Field(max_length=10)]


class ConnectorAnalysisOutput(StrictModel):
    schema_version: Literal["1.0"]
    source_item_id: UUID
    source_revision: str
    extraction_version: Annotated[int, Field(ge=1)]
    candidates: Annotated[list[CandidateDraft], Field(max_length=20)]
```

The mode receives only one normalized, size-bounded connector item/revision and
the extraction instruction/version. It receives:

- no tool definitions or tool-call execution;
- no chat transcript, workspace, user memory, other Gmail messages, arbitrary
  URL fetching, or connector credentials;
- no ability to create a todo, send a notification, mutate Gmail, or perform any
  other side effect.

The worker schema-validates the output, applies deterministic dedupe/provenance,
and persists private candidates through a non-model domain service. A model
tool-call response in this mode is a protocol error, not an executable request.
Notifications are evaluated later by deterministic opt-in settings and schedule
policy, never by connector content.

## 9. Frozen route inventory

The v1 contract contains **31 REST routes** plus **1 SSE route**:

- auth: 3;
- sessions/messages/prompts: 4;
- candidates/todos: 6;
- Gmail connector: 7;
- schedules/firings: 6;
- settings: 2;
- permission resolution: 1;
- health/readiness: 2;
- session SSE: 1.

Anything not listed is not a v1 API. In particular, `/webhooks/*`, `/qq/*`,
`/agentic-email/*`, `/connectors/github/*`, `/files/*`, `/sandbox/*`,
`/agents/*`, generic `/cron/*`, WebSocket routes, and approval-renderer routes
MUST be absent.

## 10. Post-v1 endpoints (ADR-029 / ADR-030)

> Added after the frozen v1 inventory. QQ (`/channels/qq/*`), agentic email
> (`/channels/email/*`), connectors config, memory, and files already shipped in
> post-v1 milestones. This section adds the **Session Library / search** (ADR-029)
> and **Personal Drive** (ADR-030) routes. All are `Session`-authenticated,
> tenant + user scoped, and follow the same representation/cursor rules as v1.

### 10.1 Session Library and search (ADR-029)

Extends the existing `SessionSummary` used by `GET /sessions`:

```python
ResumeState = Literal[
    "ready",        # idle; latest run settled → Resume session
    "running",      # run lease fresh → Reconnect
    "stale",        # status=running but lease expired → Recover run
    "approval",     # pending approval, not expired → Review approval
    "approval_expired",
    "interrupted",  # interrupted before an external effect → Continue from checkpoint
    "effect_unknown",  # needs reconciliation → Resolve outcome (never blind retry)
    "failed",       # → Review failure
    "archived",
]

class SessionSummary(StrictModel):
    id: UUID
    tenant_id: UUID
    channel: str                 # web|qq|email
    umo_key: str
    title: str | None
    resume_state: ResumeState
    latest_run_state: RunState | None
    last_message_preview: str | None
    last_activity_at: datetime | None
    created_at: datetime
    updated_at: datetime
    match: SessionMatch | None    # present only in search responses

class SessionMatch(StrictModel):
    kind: Literal["title", "user_message", "assistant_message", "tool", "action"]
    snippet: str                 # escaped; server never returns trusted HTML
    anchor_kind: Literal["message", "event", "audit", "session"]
    anchor_id: str
    additional_matches: int

class SessionTitleUpdate(StrictModel):
    title: Annotated[str, Field(min_length=1, max_length=200)]

class ResumeStateResponse(StrictModel):
    session_id: UUID
    resume_state: ResumeState
    latest_run_state: RunState | None
    live: bool                   # run lease fresh
    pending_approval_id: str | None
    unresolved_effect_id: str | None
    events_url: str
```

| Route | Body → response | Auth | Status |
|---|---|---|---|
| `GET /sessions?query=&status=&channel=&updated_before=&cursor=&limit=` | none → `CursorPage[SessionSummary]` | Session | `200`, `400`, `401`, `422`, `503` |
| `PATCH /sessions/{id}/title` | `SessionTitleUpdate` → `SessionSummary` | Session + CSRF | `200`, `401`, `404`, `409`, `422` |
| `GET /sessions/{id}/resume-state` | none → `ResumeStateResponse` | Session | `200`, `401`, `404` |
| `GET /sessions/{id}/timeline?anchor_kind=&anchor_id=&before_turns=&after_turns=` | none → `MessagePage` | Session | `200`, `400`, `401`, `404`, `422` |
| `POST /sessions/{id}/recover` | `RecoverRequest` → `ResumeStateResponse` | Session + CSRF | `202`, `401`, `404`, `409`, `422` |

- An empty `query` returns recent sessions ordered by `last_activity_at`
  (snapshot cursor). A non-empty `query` returns session-grouped matches ranked by
  fused FTS/CJK/trigram score (query-hash cursor). Both filter `tenant_id` **and**
  `user_id`.
- `resume_state` is computed, never advertised as an action that will immediately
  fail: `approval` requires `now() < expires_at`; a `running` status past the run
  lease is reported as `stale`, not `running`.
- `timeline` maps an `event`/`tool` anchor to its `run_id` and the surrounding
  message turn; it **never** compares `messages.seq` with
  `event_journal.session_seq`.
- `POST /sessions/{id}/recover` accepts only a state-specific reconciliation
  decision (`RecoverRequest.action ∈ recheck|verified|new_run`) and reuses
  invocation idempotency; it is not a generic retry.
- Search snippets are escaped text plus match ranges; `ts_headline` output is
  never returned as trusted HTML.
- Branching (`POST /sessions/{id}/branches`) is Phase C and out of scope here.

### 10.2 Personal Drive (ADR-030)

```python
class DriveNode(StrictModel):
    id: UUID
    parent_id: UUID | None
    node_type: Literal["folder", "file"]
    name: str
    size_bytes: int
    content_type: str
    version: int
    trashed: bool
    updated_at: datetime

class StorageAccount(StrictModel):
    quota_bytes: int
    used_bytes: int
    reserved_bytes: int
    trashed_bytes: int
    available_bytes: int

class FolderCreate(StrictModel):
    parent_id: UUID | None = None
    name: Annotated[str, Field(min_length=1, max_length=255)]

class NodeMove(StrictModel):
    if_version: int
    parent_id: UUID | None = None
    name: Annotated[str, Field(min_length=1, max_length=255)] | None = None
```

| Route | Body → response | Auth | Status |
|---|---|---|---|
| `GET /drive/nodes?parent=&query=&sort=&cursor=&limit=&trashed=` | none → `CursorPage[DriveNode]` | Session | `200`, `400`, `401`, `422` |
| `POST /drive/folders` | `FolderCreate` → `DriveNode` | Session + CSRF | `201`, `401`, `409`, `422`, `507` |
| `POST /drive/files` | multipart (`path`, file) → `DriveNode` | Session + CSRF | `201`, `401`, `409`, `413`, `422`, `507` |
| `GET /drive/nodes/{id}/content` | none → bytes | Session | `200`, `401`, `404` |
| `PATCH /drive/nodes/{id}` | `NodeMove` → `DriveNode` | Session + CSRF | `200`, `401`, `404`, `409`, `422` |
| `GET /drive/nodes/{id}/versions` | none → `list[DriveVersion]` | Session | `200`, `401`, `404` |
| `POST /drive/nodes/{id}/restore-version` | `{version:int}` → `DriveNode` | Session + CSRF | `200`, `401`, `404`, `422`, `507` |
| `POST /drive/nodes/{id}/trash` | none → `DriveNode` | Session + CSRF | `200`, `401`, `404` |
| `POST /drive/nodes/{id}/restore` | none → `DriveNode` | Session + CSRF | `200`, `401`, `404`, `409` |
| `DELETE /drive/nodes/{id}` | none → `204` (permanent purge) | Session + CSRF | `204`, `401`, `403`, `404` |
| `GET /drive/storage` | none → `StorageAccount` | Session | `200`, `401` |

- `507 insufficient_storage` when a reservation would exceed quota. Reservation is
  taken before the object write and released on failure.
- `trashed=true` returns a flat listing of the top-most trashed nodes (Trash view);
  omitted/false returns live nodes under `parent` (or the Drive root).
- Uploads exceeding the per-file cap return `413`.
- `DELETE` (permanent purge) is human-only/approval-gated; agent tools may trash
  and restore but not purge.
- Blob bytes are content-addressed and reference-counted; object deletion happens
  only in a reconciliation/GC worker after `ref_count = 0` past retention.
- The agent drives every non-purge capability through the same service layer
  (ADR-023): `drive_list`, `drive_search`, `drive_make_folder`, `drive_write`,
  `drive_read`, `drive_move`, `drive_trash`, `drive_restore`.

### 10.3 Core memory blocks (ADR-032)

Named, bounded, always-in-context core-memory blocks replace the free-form
`user_memory` KV as the primary core tier. Mirrors the `memory_core_*` agent tools
(ADR-023 dual adapter). The legacy `PUT/GET/DELETE /memory` KV endpoints remain but
become read-only during Phase A; archival `/memory/passages` is unchanged except
results now carry `origin`/`importance`/validity.

```python
class MemoryBlock(StrictModel):
    label: str                 # profile|preferences|agent_notes
    value: str
    char_limit: int
    chars_used: int
    version: int
    updated_at: datetime

class MemoryBlockEdit(StrictModel):
    op: Literal["set", "append", "replace", "remove"]
    value: str | None = None   # set/append
    old: str | None = None     # replace/remove — must match a unique substring
    new: str | None = None     # replace
    if_version: int            # optimistic CAS

class MemoryBlockHistoryItem(StrictModel):
    version: int
    op: Literal["set", "append", "replace", "remove", "formed"]
    value_before: str
    edited_at: datetime
```

| Route | Body → response | Auth | Status |
|---|---|---|---|
| `GET /memory/blocks` | none → `list[MemoryBlock]` | Session | `200`, `401` |
| `GET /memory/blocks/{label}` | none → `MemoryBlock` | Session | `200`, `401`, `404` |
| `PUT /memory/blocks/{label}` | `MemoryBlockEdit` → `MemoryBlock` | Session + CSRF | `200`, `401`, `404`, `409`, `422` |
| `GET /memory/blocks/{label}/history` | none → `CursorPage[MemoryBlockHistoryItem]` | Session | `200`, `401`, `404` |

- `409 char_limit_exceeded` when an edit would exceed `char_limit` — the caller
  (human or agent) must consolidate first; the write path never silently truncates.
- `409 version_conflict` when `if_version` does not match (concurrent edit /
  background formation); the caller re-reads and retries.
- `replace`/`remove` require `old` to match **exactly one** substring, else `422`.
- Both the block value and its history are escaped on render; content may originate
  from untrusted email and is threat-scanned before injection (ADR-009/019).
