# Events and effects contract

**Status:** FROZEN for v1  
**Envelope version:** `1.0`  
**Authority:** [ADR-006, ADR-016, and ADR-017](../decisions.md)

The words **MUST**, **MUST NOT**, **SHOULD**, and **MAY** are normative. This
document owns event semantics, ordering, delivery, and effect execution rules.
It does not define table DDL, HTTP routes, approval-envelope fields, or
deployment configuration.

## 1. Event envelope

All public v1 events are session- and run-scoped. A scheduler or connector
pipeline MUST create its durable session/run before publishing candidate or
firing events.

### 1.1 Frozen JSON Schema

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://sherpa.local/schemas/event-envelope-1.0.json",
  "title": "Sherpa event envelope v1.0",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "event_id",
    "schema_version",
    "tenant_id",
    "session_id",
    "run_id",
    "session_seq",
    "seq",
    "ts",
    "type",
    "payload",
    "redaction"
  ],
  "properties": {
    "event_id": { "$ref": "#/$defs/ulid" },
    "schema_version": { "const": "1.0" },
    "tenant_id": { "type": "string", "format": "uuid" },
    "session_id": { "type": "string", "format": "uuid" },
    "run_id": { "type": "string", "format": "uuid" },
    "session_seq": {
      "type": "integer",
      "minimum": 1,
      "maximum": 9007199254740991
    },
    "seq": {
      "type": "integer",
      "minimum": 1,
      "maximum": 9007199254740991
    },
    "ts": { "type": "string", "format": "date-time" },
    "type": {
      "type": "string",
      "enum": [
        "run.started",
        "text-delta",
        "reasoning-delta",
        "tool-call",
        "tool-result",
        "tool-error",
        "turn.end",
        "run.settled",
        "permission.asked",
        "permission.resolved",
        "candidate.created",
        "firing.created",
        "firing.started",
        "firing.succeeded",
        "firing.missed",
        "firing.failed",
        "firing.unknown"
      ]
    },
    "payload": {
      "type": "object",
      "maxProperties": 64
    },
    "redaction": { "$ref": "#/$defs/redaction" }
  },
  "$defs": {
    "ulid": {
      "type": "string",
      "pattern": "^[0-9A-HJKMNP-TV-Z]{26}$"
    },
    "redaction": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "payload_redacted",
        "payload_truncated",
        "contains_user_content",
        "contains_tool_content",
        "policy_version",
        "paths"
      ],
      "properties": {
        "payload_redacted": { "type": "boolean" },
        "payload_truncated": { "type": "boolean" },
        "contains_user_content": { "type": "boolean" },
        "contains_tool_content": { "type": "boolean" },
        "policy_version": { "const": "redaction.v1" },
        "paths": {
          "type": "array",
          "maxItems": 64,
          "uniqueItems": true,
          "items": { "type": "string", "maxLength": 512 }
        }
      }
    }
  }
}
```

The envelope schema and the matching payload schema in §2 are both required.
Every payload schema is closed (`additionalProperties: false`). Unknown fields
or unknown enum values require a new schema version; consumers MUST NOT guess.

### 1.2 Example

```json
{
  "event_id": "01J2Q2Z4J7F8YQ6D9M3N5R7T8V",
  "schema_version": "1.0",
  "tenant_id": "2c0b8d1e-0e45-45c8-b1ad-dc2ddbf0ab61",
  "session_id": "c87956ef-5fc6-476b-a745-a30e5a0d3873",
  "run_id": "42b92447-43c2-4e8b-912d-3c574b879d46",
  "session_seq": 42,
  "seq": 1,
  "ts": "2026-07-19T20:05:45.795Z",
  "type": "run.started",
  "payload": {
    "trigger": "user",
    "admitted_seq": 18,
    "recovery_of_run_id": null,
    "model": {
      "provider": "openai-compatible",
      "name": "configured-model"
    },
    "prompt_version": "chat.v1"
  },
  "redaction": {
    "payload_redacted": false,
    "payload_truncated": false,
    "contains_user_content": false,
    "contains_tool_content": false,
    "policy_version": "redaction.v1",
    "paths": []
  }
}
```

### 1.3 Field rules

| Field | Type | Contract |
|---|---|---|
| `event_id` | ULID | Globally unique, immutable, generated once before journal insertion. Also the transport deduplication key. |
| `schema_version` | `"1.0"` | Version of the envelope and payload catalog. A breaking field, enum, or semantic change requires a new version. |
| `tenant_id` | UUID | Required tenant boundary. It MUST agree with the authenticated/request transaction tenant. |
| `session_id` | UUID | Session owning the event. Redis Streams are partitioned by tenant and session. |
| `run_id` | UUID | Durable run owning the event. All public v1 event types are run-scoped. |
| `session_seq` | integer | Starts at `1`; strictly increases and is unique within `(tenant_id, session_id)`. It is the total session event order and the SSE cursor source. Consumers MUST NOT require gap-free values. |
| `seq` | integer | Starts at `1`; strictly increases and is unique within `(tenant_id, run_id)`. Consumers MUST NOT require gap-free values. |
| `ts` | RFC 3339 UTC | Journal commit timestamp, serialized with `Z`; it is not used for ordering. |
| `type` | closed enum | One of the 17 values in §2. |
| `payload` | object | Must validate against the schema for `type`. Canonical JSON payload is at most 65,536 UTF-8 bytes. |
| `redaction` | object | Declares whether content was removed/truncated and whether user/tool-derived content remains. `paths` contains affected JSON Pointers. |

The journaled envelope is immutable. Redaction happens **before** the journal
and outbox transaction; secrets, credentials, raw provider requests, raw tool
output, and raw chain-of-thought MUST NOT enter the journal. A payload that
cannot be made safe and bounded MUST use a durable authorized reference plus a
redacted summary, or the event MUST fail closed.

## 2. Event-type catalog

Notation below is JSON with `type` alternatives separated by `|`. A `?` suffix
means the field may be omitted. `T | null` means the field is required and may
be null. UUIDs are lowercase RFC 4122 strings; hashes are lowercase 64-character
SHA-256 hex strings. Strings default to 1–1,024 characters unless a tighter
limit is stated.

Shared enums:

```text
EffectClass = read_only | idempotent_write | reconcilable_write | non_idempotent_write
RetryPolicy = transient_before_dispatch | same_key | after_reconcile | never

SafeError = {
  code: string,
  message: string,          // redacted, user-safe, <= 1024 UTF-8 bytes
  retryable: boolean,
  details_ref?: string
}
```

### 2.1 Run lifecycle

#### `run.started`

Exactly once per run and normally `seq = 1`. Worker recovery of the same run
continues its sequence and MUST NOT emit a second `run.started`.

```text
{
  trigger: user | schedule | connector | retry | recovery | system,
  admitted_seq: integer | null,
  recovery_of_run_id: uuid | null,
  model: {
    provider: string,
    name: string
  },
  prompt_version: string
}
```

#### `turn.end`

A durable turn checkpoint. It is emitted only after the selected assistant
attempt, its messages, and every completed tool result are committed.

```text
{
  turn_index: integer >= 1,
  turn_attempt: integer >= 1,
  stop_reason: end_turn | tool_use | max_tokens | content_filter | interrupted | error,
  assistant_message_id: uuid | null,
  tool_invocation_ids: uuid[],
  will_continue: boolean
}
```

`turn.end` **does not mean the run is complete**. `will_continue` may be true,
and retry, compaction, steering, or another model turn may still follow.

#### `run.settled`

Exactly once, and always the greatest `seq` in a run. It is emitted only when
all retry, compaction, continuation, and queued run work has ended.

```text
{
  terminal_reason:
      completed
    | stopped:budget
    | stopped:no_progress
    | interrupted
    | timeout
    | aborted
    | failed
    | effect_unknown,
  last_completed_turn: integer >= 0,
  final_message_id: uuid | null,
  error: SafeError | null,
  usage: {
    input_tokens: integer >= 0,
    output_tokens: integer >= 0,
    tool_calls: integer >= 0
  },
  duration_ms: integer >= 0
}
```

| Terminal reason | Meaning |
|---|---|
| `completed` | The model produced the intended final response. |
| `stopped:budget` | A configured turn/token/tool budget ended the run, including any bounded grace call. |
| `stopped:no_progress` | Doom-loop or repeated-call detection ended the run. |
| `interrupted` | An explicit user/system interrupt was observed at a safe boundary. |
| `timeout` | The run deadline expired. |
| `aborted` | The run was administratively cancelled or superseded. |
| `failed` | A known terminal error occurred. |
| `effect_unknown` | A side effect may have happened; execution stopped for reconciliation. |

When several terminal conditions race, use the existing guardrail precedence
`timeout > aborted > failed > completed`; `effect_unknown` overrides any
condition that would otherwise permit automatic continuation.

### 2.2 Streaming and tools

Streaming chunks are presentation boundaries, not provider token boundaries.
Producers MAY coalesce adjacent chunks. Each `delta` is at most 8,192 UTF-8
bytes. `turn_attempt` lets clients distinguish a recovered/re-executed attempt;
only the attempt named by `turn.end` is a completed turn.

#### `text-delta`

```text
{
  turn_index: integer >= 1,
  turn_attempt: integer >= 1,
  message_id: uuid,
  part_id: uuid,
  delta: string
}
```

#### `reasoning-delta`

```text
{
  turn_index: integer >= 1,
  turn_attempt: integer >= 1,
  rationale_id: uuid,
  rationale_kind: "curated",
  delta: string,
  raw_reasoning_included: false
}
```

This event contains only a curated rationale suitable for the user. Provider
chain-of-thought, hidden reasoning tokens, scratchpads, and raw reasoning
summaries MUST NOT be persisted or streamed.

#### `tool-call`

Emitted only after the call is complete, schema-valid, assigned an invocation,
and durably persisted. Partial streamed arguments MUST NOT produce this event
and MUST never be executed.

```text
{
  turn_index: integer >= 1,
  turn_attempt: integer >= 1,
  invocation_id: uuid,
  tool_call_id: string <= 128 bytes,
  tool_name: string <= 128 bytes,
  arguments_redacted: object,
  args_hash: sha256,
  effect_class: EffectClass,
  retry_policy: RetryPolicy,
  permission_state: not_required | allowed | asked | denied
}
```

#### `tool-result`

Only a definitely successful invocation uses this event.

```text
{
  turn_index: integer >= 1,
  turn_attempt: integer >= 1,
  invocation_id: uuid,
  tool_call_id: string <= 128 bytes,
  tool_name: string <= 128 bytes,
  summary: string <= 8192 UTF-8 bytes,
  result_ref: string | null,
  truncated: boolean,
  reused: boolean
}
```

`reused = true` means recovery reused a persisted successful invocation rather
than executing the effect again.

#### `tool-error`

```text
{
  turn_index: integer >= 1,
  turn_attempt: integer >= 1,
  invocation_id: uuid,
  tool_call_id: string <= 128 bytes,
  tool_name: string <= 128 bytes,
  effect_outcome: failed | effect_unknown,
  error: SafeError,
  reconciliation_required: boolean
}
```

`reconciliation_required` MUST be true exactly when `effect_outcome` is
`effect_unknown`.

### 2.3 Permission

The complete semantic approval envelope, authorized-decider rules, nonce,
expiry, and first-valid-response-wins behavior are owned by
[`api.md`](api.md). Events carry only a stable projection/reference and MUST
not redefine or weaken that envelope.

#### `permission.asked`

```text
{
  approval_envelope_id: uuid,
  approval_envelope_version: string,
  correlation_id: string <= 128 bytes,
  invocation_id: uuid,
  expires_at: RFC3339 UTC
}
```

#### `permission.resolved`

```text
{
  approval_envelope_id: uuid,
  correlation_id: string <= 128 bytes,
  invocation_id: uuid,
  resolution:
      approved_once
    | approved_session
    | approved_always
    | rejected
    | expired
    | cancelled,
  resolved_at: RFC3339 UTC
}
```

### 2.4 Candidate

#### `candidate.created`

Emitted after the candidate and its provenance are durable. Connector content
is not copied into this event; clients fetch the authorized candidate resource.

```text
{
  candidate_id: uuid,
  extraction_id: uuid,
  generation_id: uuid,
  status: "pending",
  dedupe_key: string <= 256 bytes,
  source: {
    connector_item_id: uuid,
    revision: string <= 256 bytes
  }
}
```

### 2.5 Schedule firing

Every firing event includes this common projection:

```text
{
  firing_id: uuid,
  schedule_id: uuid,
  firing_key: string <= 256 bytes,
  scheduled_for: RFC3339 UTC
}
```

`firing_key` identifies a logical schedule slot and is unique with
`(tenant_id, schedule_id)`.

#### `firing.created`

Emitted in the transaction that creates the unique firing and its outbox row.

```text
CommonFiring & {
  delivery_policy: prefer_no_duplicate | eventual_delivery,
  deadline_at: RFC3339 UTC | null
}
```

#### `firing.started`

May occur more than once under at-least-once processing. Every attempt uses the
same invocation and idempotency key.

```text
CommonFiring & {
  invocation_id: uuid,
  attempt: integer >= 1
}
```

#### `firing.succeeded`

```text
CommonFiring & {
  invocation_id: uuid,
  attempt: integer >= 1,
  provider_receipt_ref: string | null,
  reconciled_from_unknown: boolean
}
```

#### `firing.missed`

Used only for an explicit, durable decision not to deliver later. It is never
an implicit consequence of a crashed worker or lost queue message.

```text
CommonFiring & {
  reason:
      deadline_expired
    | schedule_disabled
    | policy_suppressed
    | target_unavailable,
  detail: string <= 1024 UTF-8 bytes
}
```

#### `firing.failed`

A known terminal failure. If attempts remain, record the attempt internally
and retry the same invocation; do not emit terminal `firing.failed` yet.

```text
CommonFiring & {
  invocation_id: uuid | null,
  attempt: integer >= 0,
  error: SafeError,
  retry_exhausted: boolean,
  reconciled_from_unknown: boolean
}
```

#### `firing.unknown`

The delivery may have happened. This event pauses delivery and requires
reconciliation; it MUST NOT trigger an automatic resend.

```text
CommonFiring & {
  invocation_id: uuid,
  attempt: integer >= 1,
  error: SafeError,
  reconciliation_required: true
}
```

After successful reconciliation, `firing.succeeded` or `firing.failed` may
follow with `reconciled_from_unknown = true`. Reconciliation runs under a new
durable `run_id`, so the originating run's `run.settled` remains its last
event. If the result remains indeterminate, the firing remains unknown and
visible to the user/operator.

### 2.6 Core memory and formation (ADR-032, post-v1 — additive, not part of the frozen v1 catalog)

#### `core_memory.loaded`

Emitted once per run at prompt assembly, recording which core-memory blocks were
injected. Makes each run self-document what memory it saw (today this must be
reconstructed from `memory_blocks.updated_at` vs `run.started`). Durability may be
`debug` (observability, not an audit fact).

```text
{
  labels: string[],                 // e.g. ["profile","preferences"]
  chars: integer,                   // total injected characters
  block_versions: { string: integer }
}
```

#### `memory.formed`

One event per applied memory write (hot-path or background formation), carrying the
conflict-resolution decision. Background formation is a run side-effect: each op
carries an `idempotency_key` so replay after a worker crash is safe (ADR-017).

```text
{
  scope: core | archival,
  op: add | update | invalidate | noop,
  target_label: string | null,      // core block label (scope=core)
  passage_id: uuid | null,          // archival row (scope=archival)
  origin: user | agent_auto,
  idempotency_key: string,
  source_run_id: uuid | null
}
```

`noop` is emitted (not suppressed) so the formation pipeline is observable end to
end. `invalidate` sets `memory_passages.invalid_at` (soft-invalidation), never a
delete.

## 3. Delivery and SSE

### 3.1 Required path

```text
business mutation
      │  one PostgreSQL transaction
      ├── append immutable PostgreSQL `events` journal row
      └── append transactional outbox row
                 │
                 ▼
           outbox relay
                 │ XADD original envelope
                 ▼
 Redis Stream per tenant/session
                 │
                 ▼
       FastAPI SSE connection
```

1. The producer MUST commit the business transition, event, and outbox row in
   one PostgreSQL transaction.
2. The journal is the source of truth for recovery, replay, projection, and
   streaming. See [`data-model.md`](data-model.md) for DDL and constraints.
3. The outbox relay MAY publish more than once. It MUST preserve the original
   `event_id`, `session_seq`, `run_id`, `seq`, and envelope bytes.
4. Redis uses one Stream per tenant/session, conceptually
   `sherpa:v1:events:{tenant_id}:{session_id}`. Its entry ID is an internal
   acceleration cursor, not the public SSE cursor.
5. Redis Stream loss, trimming, restart, or relay delay MUST NOT lose a
   journaled event.
6. Redis pub/sub MUST NOT be used anywhere in the correctness path.

Relay and SSE delivery are at-least-once. Relays and clients deduplicate by
`event_id`; clients additionally ignore duplicate `session_seq` values. The
SSE server MUST emit the session stream in ascending `session_seq` order even
if Redis delivery is duplicated or delayed. Within each run, `seq` MUST also
remain ascending.

### 3.2 SSE framing

SSE subscriptions are session-scoped through
`GET /sessions/{session_id}/events`; authentication, query parameters,
snapshots, and error envelopes are defined in [`api.md`](api.md). The wire
framing is:

```text
id: 42
event: text-delta
data: {"event_id":"01J2Q...","schema_version":"1.0",...,"session_seq":42,"seq":17,"type":"text-delta",...}

```

- `id:` is the v1 decimal serialization of `session_seq`. Clients MUST treat
  the cursor as opaque and echo it unchanged.
- `event:` is exactly the envelope `type`.
- `data:` is one compact UTF-8 JSON serialization of the complete envelope.
- Each event ends with a blank line.
- Heartbeats MAY be SSE comment lines such as `: keep-alive`; they have no ID,
  are not journal events, and do not advance the cursor.

### 3.3 Reconnect and catch-up

The client reconnects with `Last-Event-ID: <cursor>` or the equivalent initial
`cursor=<cursor>` defined by `api.md`. Both represent a position in the
selected session's `session_seq` order. Header/query precedence and malformed
cursor errors are owned by `api.md`.

The server MUST:

1. authenticate the caller and authorize the tenant and selected session;
2. resolve the opaque cursor to `session_seq` and verify it belongs to that
   session and is not ahead of the journal;
3. query PostgreSQL for
   `session_seq > cursor_session_seq ORDER BY session_seq`;
4. begin and buffer consumption from the session Redis Stream;
5. query PostgreSQL once more after stream consumption begins, closing the
   journal/read-subscribe race;
6. emit journal catch-up events first, then buffered/live events, deduplicated
   and ordered by `session_seq`.

Redis is never the only catch-up source. A cursor that cannot be served because
the underlying event was lawfully deleted or its retention expired MUST
produce the HTTP `409` reset/snapshot response defined in `api.md`; the server
MUST NOT silently start at the live tail.

## 4. Effect and idempotency contract

This contract applies to every executable tool call and every side effect,
including connector writes and notification delivery. Read-only tool calls use
the same invocation machinery so recovery behavior is uniform.

### 4.1 Invocation identity

Before any external call, filesystem mutation, or notification send, Sherpa
MUST commit an invocation containing at least:

```text
invocation_id, tenant_id, run_id, idempotency_key, effect_class,
retry_policy, args_hash, state, attempt_count
```

DDL, leases, indexes, and receipt columns belong in
[`data-model.md`](data-model.md).

The idempotency key:

- is unique within a tenant;
- is deterministic for the logical effect;
- is reused across worker retries and turn recovery;
- MUST NOT contain a timestamp, random attempt ID, or retry number;
- MUST be sent to the external provider when it supports idempotency keys; and
- MUST be protected from untrusted client override.

Recommended canonical forms are:

```text
tool:<run_id>:<turn_index>:<logical_call_id>
connector:<connector_id>:<logical_operation_id>
firing:<schedule_id>:<firing_key>:<target_fingerprint>
```

If a provider imposes a shorter key, send a versioned SHA-256-derived key and
retain the full canonical key in PostgreSQL.

### 4.2 Effect and retry classes

| Effect class | Meaning | Automatic retry rule |
|---|---|---|
| `read_only` | No externally visible mutation. | Retry known transient failures within bounds. |
| `idempotent_write` | The same key is guaranteed to produce at most one logical effect. | Retry known failures with the same key; an ambiguous outcome still enters reconciliation first. |
| `reconcilable_write` | The provider can be queried by key/receipt to establish whether the effect happened. | Never retry an ambiguous outcome before reconciliation proves it was not applied. |
| `non_idempotent_write` | Neither deduplication nor authoritative reconciliation is available. | Never automatically retry after dispatch may have begun. |

`retry_policy` is closed:

- `transient_before_dispatch`: retry only when it is known that dispatch did
  not begin;
- `same_key`: bounded retry with the same provider-enforced key;
- `after_reconcile`: retry only after reconciliation proves no effect;
- `never`: no automatic retry.

Every adapter MUST declare both values. `is_read_only` alone is insufficient.

### 4.3 Outcomes and state diagram

The only durable effect outcomes are:

```text
succeeded | failed | effect_unknown
```

`pending`, `executing`, and `reconciling` are processing states, not outcomes.

```text
                  claim
  [persisted] pending ─────────► executing
       ▲                           │
       │ known retryable failure   ├── definite success ──► succeeded
       │ (same invocation/key)     │
       └───────────────────────────┤
                                   ├── definite terminal failure ──► failed
                                   │
                                   └── dispatch may have happened
                                                  │
                                                  ▼
                                           effect_unknown
                                                  │ STOP
                                                  ▼
                                            reconciling
                                      ┌───────────┼───────────┐
                                      │           │           │
                                   applied    not applied  indeterminate
                                      │           │           │
                                      ▼           ▼           ▼
                                  succeeded   pending or    effect_unknown
                                              failed
```

Examples of known retryable failure are connection refusal before any bytes
were sent, or a provider response that explicitly states the operation was not
accepted. Timeout/reset after dispatch, a missing receipt, or worker death
after dispatch starts is `effect_unknown` unless authoritative reconciliation
proves otherwise.

On `effect_unknown`, the current execution MUST stop, persist the unknown
outcome, emit `tool-error` or `firing.unknown`, and schedule/require
reconciliation in a new durable run. It MUST NOT call the effect adapter again
merely because a job was redelivered.

### 4.4 Worker pseudo-flow

```python
def execute_effect(spec, args, context):
    key = spec.make_idempotency_key(context, args)
    args_hash = sha256(canonical_json(args))

    # Transaction 1: identity exists before any effect.
    with db.transaction():
        inv = invocations.insert_or_get(
            tenant_id=context.tenant_id,
            invocation_id=spec.stable_invocation_id(context, args_hash),
            idempotency_key=key,
            args_hash=args_hash,
            effect_class=spec.effect_class,
            retry_policy=spec.retry_policy,
        )
        assert inv.args_hash == args_hash

        if inv.outcome == "succeeded":
            return reuse_persisted_result(inv)
        if inv.outcome == "effect_unknown":
            raise ReconciliationRequired(inv.invocation_id)
        if inv.outcome == "failed" and inv.is_terminal:
            raise PersistedEffectFailure(inv.invocation_id)
        if inv.has_live_lease:
            raise RetryJobLater()

        inv.claim_with_fence()

    # Transaction 2: persist the dispatch marker before external work.
    with db.transaction():
        inv.mark_dispatch_started()

    try:
        receipt = spec.adapter.execute(args, idempotency_key=key)
    except DefinitelyNotDispatched as error:
        with db.transaction():
            if spec.can_retry(error) and inv.attempts_left:
                inv.record_attempt(error)
                inv.requeue_same_identity_via_outbox()
            else:
                inv.finish("failed", error=error)
                append_effect_failure_event(inv, error)
        return
    except AmbiguousDispatch as error:
        with db.transaction():
            inv.finish("effect_unknown", error=error)
            append_unknown_event(inv, error)
            enqueue_reconciliation(inv)
        raise ReconciliationRequired(inv.invocation_id)
    else:
        with db.transaction():
            inv.finish("succeeded", receipt=receipt)
            append_success_event(inv, receipt)
        return receipt
```

No database transaction is held open during remote work. Claims require a
lease/fencing token. When an executing lease expires, read-only work may be
retried; a side-effecting invocation whose dispatch marker was set becomes
`effect_unknown`.

The reconciler calls a separate `adapter.reconcile(invocation)`, never the
effect method:

```text
applied       -> succeeded
not_applied   -> pending only if retry_policy permits; otherwise failed
rejected      -> failed
indeterminate -> remain effect_unknown and surface needs-attention
```

### 4.5 Schedule firings

Schedule delivery is at-least-once:

1. In one transaction, the scheduler inserts the unique
   `(tenant_id, schedule_id, firing_key)` firing, appends `firing.created`, and
   writes its outbox job.
2. arq may deliver that job multiple times.
3. Every delivery attempt resolves to the same invocation and the canonical
   `firing:<schedule_id>:<firing_key>:<target_fingerprint>` idempotency key.
4. A known transient failure may retry according to the firing's delivery
   policy and attempt budget.
5. An ambiguous send becomes `effect_unknown`/`firing.unknown`; it is
   reconciled and never blindly resent.
6. `firing.missed` is an explicit policy/deadline result. A dropped queue
   message or worker crash is never silently converted into a miss.

`prefer_no_duplicate` may stop after an unresolved unknown result.
`eventual_delivery` may retry only after provider reconciliation or
provider-enforced same-key idempotency establishes that doing so cannot create
a second logical delivery.

## 5. Ordering and recovery

1. Journal insertion atomically allocates `session_seq` per session and `seq`
   per run. PostgreSQL order by `session_seq` is the canonical session order;
   `seq` is the canonical order within a run. Timestamps and Redis entry IDs
   are not ordering authorities.
2. Within a run, `run.started` is first and `run.settled` is last. Committed
   events are never updated or renumbered.
3. Business state and its semantic event/outbox row commit together. A crash
   may cause transport duplication, never an event without its state or state
   without its required event.
4. The completed-turn recovery checkpoint is the greatest committed
   `turn.end`. Recovery rebuilds context from durable state at that boundary
   and resumes/re-executes the unfinished turn as required by ADR-006.
5. A recovered unfinished turn increments `turn_attempt`. Its old deltas are
   provisional presentation history; only the attempt referenced by the next
   `turn.end` is the completed turn. Canonical transcript snapshots come from
   the REST resource defined in `api.md`.
6. Recovery first inspects persisted invocations: reuse `succeeded`, respect
   terminal `failed`, stop/reconcile `effect_unknown`, and retry only a
   declared-safe pending/known-failed invocation with the same key.
7. Already committed events are not recreated. A missing/duplicate outbox
   delivery republishes the same envelope and `event_id`; new execution events
   continue with new, greater `session_seq` and `seq` values.
8. Turn-granular replay can revisit tool execution. Therefore the
   invocation/idempotency contract is mandatory for every tool, connector
   write, notification, and schedule delivery; it is not an optimization.

## 6. Contract ownership

| Concern | Authoritative document |
|---|---|
| Event, outbox, invocation, and firing table DDL; indexes; retention | [`data-model.md`](data-model.md) |
| REST/SSE endpoints; auth/errors/snapshots; complete approval envelope | [`api.md`](api.md) |
| Redis/Postgres URLs, timeouts, retry budgets, retention, and secrets | [`config-and-secrets.md`](config-and-secrets.md) |

Configuration may tighten limits or disable optional presentation streaming,
but it MUST NOT weaken source-of-truth, ordering, redaction, idempotency, or
`effect_unknown` rules in this frozen contract.
