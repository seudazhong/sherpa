# Sherpa architecture cross-review

**Review date:** 2026-07-19  
**Role:** Software Architect  
**Inputs:** [Phase-1 architecture review](./architect-review.md), [PM review](./pm-review.md), [UI/UX review](./ui-design-review.md), and ADR-001–ADR-014 in [`decisions.md`](../decisions.md).

## Executive resolution

The PM's Inbox-to-Action narrowing is the right product cut and materially lowers architectural risk. It does **not** justify weakening tenant keys, RLS, durable jobs/events, Gmail credential isolation, ingress idempotency, or reminder delivery semantics. Those are cheap relative to repairing leaked data, duplicate candidates, lost reminders, or an unreplayable UI after real data exists.

The safe way to ship faster is to remove risky capability, not to implement it weakly:

- ship one personal workspace, Web, read-only Gmail, candidate confirmation, todos, Web notifications, and one digest-email path;
- do not ship code execution, arbitrary agent tools, QQ, agentic inbound email, team sharing, attachments, memory/RAG, or provider failover;
- keep a production-shaped tenant boundary and a PostgreSQL event journal from the first migration;
- use a pipeline-specific structured extraction contract, not the current broad SAFE toolset;
- persist current business state in normal tables and use an append-only event journal for recovery, streaming, and projections. Sherpa need not become a fully event-sourced system.

This reconciles the PM's §3.2 Inbox-to-Action MLP with the UI review's Executive assessment and §3 real-time obligations, while preserving the Phase-1 architecture review's §§2, 4, 5, and 10 safety invariants.

## 1. Response to the PM

### 1.1 Minimal safe architecture for Gmail → candidate → confirm → remind

I agree with the PM review §§0.1, 3.2–3.5, and 4.2: the first product milestone should be the complete vertical slice, not the original component order.

The minimum deployable topology is:

```text
Browser
  ├─ HTTPS/API ──> FastAPI web/auth
  └─ SSE ────────> authorized session/run stream
                         │
                         v
                    PostgreSQL
              canonical state + RLS
              run_events + outbox
                         │
                  outbox dispatcher
                         │
                         v
                Redis queue + Streams
                         │
           ┌─────────────┴──────────────┐
           v                            v
  connector/extraction worker    reminder/delivery worker
           │                            │
       Gmail API                 Web inbox / digest SMTP
```

The scheduler may share an image with the workers, but it remains a separate process/command. The web process must not execute extraction or delivery work, preserving ADR-005.

The MVP data path is deliberately non-agentic:

1. A read-only Gmail connector ingests only the selected label/time window. Each delivery/item is deduplicated by provider account, external item ID, and revision/history ID.
2. A worker supplies one bounded connector item to one provider and requests versioned, schema-validated candidate JSON. It has **no tools**, workspace, memory, arbitrary fetch, notification, or sandbox capability.
3. The service validates and stores suggestions. Model output cannot directly create a formal todo.
4. `accept`/`edit` is an authenticated business API transition from candidate to todo; it is not an ADR-008 security approval.
5. A todo reminder creates a durable firing and outbox item. A delivery attempt records `pending/sent/failed/unknown`; the Web inbox remains authoritative if email delivery fails.

The relevant minimum schema is:

```sql
connector_items(
  tenant_id, connector_id, external_item_id, external_revision,
  thread_id, received_at, content_ref, content_digest, deleted_at,
  UNIQUE (tenant_id, connector_id, external_item_id, external_revision)
);

candidate_generations(
  id, tenant_id, connector_item_id, extraction_version,
  run_id, status, created_at,
  UNIQUE (tenant_id, connector_item_id, extraction_version)
);

todo_candidates(
  id, tenant_id, generation_id, ordinal, state,
  title, due_at, confidence, rationale, source_excerpt,
  accepted_todo_id, created_at,
  UNIQUE (generation_id, ordinal)
);

schedule_firings(
  id, tenant_id, schedule_id, scheduled_for, status,
  UNIQUE (tenant_id, schedule_id, scheduled_for)
);

delivery_attempts(
  id, tenant_id, firing_id, destination_id, idempotency_key,
  status, provider_message_id, last_error, attempted_at,
  UNIQUE (tenant_id, idempotency_key)
);
```

Thread updates and extraction-version changes need explicit reconciliation rather than appending another set of candidates. Source deletion must define whether the todo remains with a tombstoned source, and deletion must remove retained mail bodies and derived embeddings if those are later added. This implements the PM review §§2.9, 5.9, and 9.1 rather than treating source traceability as presentation metadata.

PostgreSQL and Redis are sufficient. MinIO can be omitted while attachments and user files are excluded. pgvector, memory, and hybrid search are unnecessary for a bounded recent-mail extraction pipeline.

### 1.2 Effort and schedule reality

For one experienced engineer, rough implementation effort—not a delivery commitment—is:

| Slice | Incremental engineering effort | Main risk |
|---|---:|---|
| Tenant-shaped auth, migrations, RLS tests | 5–8 engineer-days | Async transaction context and privileged bootstrap paths |
| Durable runs/events/outbox, queue dispatch, SSE resume | 7–12 days backend + frontend | Reconnect race, ordering, retention, and projection repair |
| Gmail OAuth, encrypted tokens, cursor sync, dedupe, deletion | 10–15 days | Self-hosted callback setup, refresh races, Google quota/verification |
| Structured extraction, candidate lifecycle, source provenance | 7–12 days | Quality, thread reconciliation, deterministic retry |
| Todo, durable reminders, Web inbox, one digest path | 7–12 days | Timezone, misfire policy, delivery ambiguity |
| Minimum audit, cost trace, health, setup diagnostics, tests | 7–12 days | Redaction and failure-path coverage |

A credible private alpha is therefore roughly **8–12 person-weeks** end to end, plus external Google OAuth lead time and real-user quality evaluation. A UI mockup or happy-path demo can be much faster; it should not be called the MLP described in PM §3.5.

### 1.3 Which Phase-1 pre-P0 asks remain firm

The following remain non-negotiable:

1. **Tenant-qualified schema, RLS, and composite tenant foreign keys** from the first PostgreSQL migration (Phase-1 §§2 and 7). A personal-only UI does not make omitted tenant predicates safe.
2. **Qualified identity/session keys** under ADR-003. Store an internal session UUID and uniquely bind `(tenant_id, channel, channel_installation_id, scope_type, external_scope_id)`, not `channel:type:external_id`.
3. **Durable runs, event journal, outbox, and resumable SSE** under the amended ADR-005. Redis pub/sub cannot be a correctness path.
4. **Idempotent ingress, candidate generation, schedule firings, and deliveries.** ADR-011's current advance-before-enqueue at-most-once design must not ship reminders.
5. **A narrower, data/effect-aware ADR-009.** The PM's `CONNECTOR_ANALYSIS` proposal in §§5.3 and 6.5 is safer than SAFE and should be narrower still: structured output with no agent tools.
6. **Candidate-first and opt-in notification defaults** under amended ADR-010, agreeing with PM §§5.2 and 6.6.
7. **Gmail secret boundaries and encryption metadata** from the first stored token. The callback must encrypt immediately; only the connector workload receives decrypt authority. Tokens never enter prompts, events, logs, generic workers, or browser responses.
8. **One selected worker contract** with queue acknowledgement, job identity, retry, cancellation, and lost-worker reconciliation. For the async Python stack, `arq` is the simpler P0 choice; PostgreSQL outbox/state remains the correctness source so queue replacement is possible.
9. **Minimum semantic audit and usage/cost trace** in the first slice. P6 is too late, as both the PM §4.1/P6 and Phase-1 §11 conclude.

These Phase-1 asks can be relaxed without a one-way door or security hole:

- **Generic durable tool invocation machinery:** freeze the invocation/effect/idempotency contract now, but implement only the deterministic connector pipeline while no generic mutating tool is exposed. The full state machine becomes mandatory before the first external, workspace, or sandbox effect.
- **Cross-channel approval execution:** freeze the versioned semantic contract now; defer the store/resume engine and QQ/email renderers because the MLP performs no action requiring ADR-008 approval.
- **Sandbox hardening:** omit `run_code` entirely. A Docker-only sandbox must not be shipped as “temporary” multi-tenant security. The gVisor/dedicated-node work is a gate for the later code-capability milestone.
- **MinIO, files, memory, pgvector, team workflows, full RBAC, and shared-memory concurrency:** defer their implementation while retaining tenant IDs and visibility vocabulary in common contracts.
- **Multi-provider failover and full provider-attempt reconciliation:** use one provider. Persist provider/model/prompt/extraction versions and cost so a second provider can be added without losing provenance.
- **Managed KMS, HA, billing, and sophisticated quotas:** an environment KEK is acceptable only for a clearly labeled self-hosted private alpha. Basic budgets, backups, and recovery instructions remain required; managed multi-tenant operation has a separate gate.

The rule is: **narrow implementation, never a weaker implementation of an included capability**.

### 1.4 Features requested earlier: actual cost and risk

Estimates assume the durable/RLS foundation already exists.

| Capability | Reality | Recommendation |
|---|---|---|
| Demo candidate data and empty states | Cheap: 1–3 days | Do early; it reduces OAuth onboarding friction (PM §5.1). |
| Candidate accept/edit/dismiss, provenance, feedback | Cheap-to-moderate: 4–7 days | Core MLP, not polish. |
| Web notification inbox | Cheap-to-moderate: 3–5 days | First delivery surface and canonical receipt. |
| Fixed daily digest and simple due reminder | Moderate: 5–8 days | Ship before generalized cron; still requires durable firing/delivery state. |
| Gmail label/time-window controls | Moderate: 3–6 days after connector | Worth doing for privacy and cost. |
| Read-only GitHub assigned issues/reviews | Moderate: 1–2 weeks after connector abstractions | Add only after Gmail metrics; auth, cursor, and update semantics are connector-specific. |
| Setup wizard | Deceptively broad: 1–2 weeks for the supported happy path; ongoing support thereafter | Required for a deployable beta, but constrain the supported host/profile. |
| Bidirectional “file sync” | Expensive and underspecified | Keep v1 to upload/download when files are introduced, as PM §3.4 recommends. |
| Team collaboration | Expensive: commonly 6–12+ weeks before a safe beta | Tenant schema is not invite/offboarding, visibility, sharing, comments, notifications, and audit. Treat PM R6 as a separate product. |
| Multi-tenant code sandbox | Expensive: 4–8+ weeks plus continuing security/ops work | A Docker demo is cheap; a hostile-tenant boundary is not. Require gVisor, dedicated nodes, quotas, snapshots, and a narrow orchestrator API per Phase-1 §3. |
| QQ through an unofficial protocol | Prototype may take weeks; reliability cost is unbounded | Experimental adapter only, feature-flagged, no critical notification or approval dependency. Account challenges and platform policy cannot be engineered away (PM §§2.11, 7.1). |
| Agentic email | Deceptively expensive: 4–8+ weeks plus continuous deliverability/abuse operations | Ordinary transactional digest email is not agentic identity. SPF/DKIM/DMARC, reputation, bounce/complaint handling, sender verification, tenant address lifecycle, and abuse response make this a separate service/product (PM §§2.10, 6.9). |
| Token-by-token polished streaming | Moderate: 1–2 weeks for the first provider and UI, then provider-specific maintenance | Durable phase/status UX should ship first; see §2.4 below. |

### 1.5 Does narrower autonomy reduce architectural risk?

**Strongly agree**, with one qualification.

The PM's candidate-first ladder and `CONNECTOR_ANALYSIS` scope (PM §§5.2–5.3 and 6.5–6.6) eliminate several high-risk paths:

- no untrusted-email-induced workspace read or data exfiltration;
- no persistent memory poisoning;
- no formal todo pollution without a user transition;
- no arbitrary egress or SSRF through `web_fetch`;
- no shell, sandbox, or external side effect;
- no async security approval or ambiguous effect recovery in the extraction path.

This is a real reduction in blast radius, not merely friendlier UX. It also makes retries deterministic: a unique generation is reused, and validated candidates are committed transactionally.

The qualification is that “no tools” does not make untrusted mail trustworthy. Inputs still need MIME/size limits, bounded excerpts, attachment exclusion or scanning, prompt/data separation, output schema validation, tenant-scoped retrieval, redaction, and quality monitoring. The model must not choose its own capability set. Server code selects the pipeline and performs every state transition.

## 2. Response to the UI Designer

### 2.1 Durable progress and SSE catch-up

The UI review §§2 “Chat session” and 3 “Real-time and streaming UX” are technically correct: **durable progress states and reconnect catch-up require a durable per-session event log. Redis pub/sub is insufficient.**

Recommended design, refining ADR-005 and Phase-1 §4:

```sql
runs(
  id uuid,
  tenant_id uuid,
  session_id uuid,
  status text,                  -- queued/running/waiting/settled/failed
  admitted_seq bigint,
  fence_token bigint,
  prompt_version text,
  created_at timestamptz,
  settled_at timestamptz,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id, session_id) REFERENCES sessions(tenant_id, id)
);

run_events(
  tenant_id uuid,
  session_id uuid,
  session_seq bigint,
  run_id uuid,
  run_seq bigint,
  event_id uuid,
  type text,
  schema_version integer,
  payload_json jsonb,           -- bounded and redacted
  durability text,              -- durable/presentation
  created_at timestamptz,
  PRIMARY KEY (tenant_id, session_id, session_seq),
  UNIQUE (tenant_id, run_id, run_seq),
  UNIQUE (event_id)
);

outbox(
  id uuid PRIMARY KEY,
  tenant_id uuid,
  aggregate_id uuid,
  event_id uuid,
  topic text,
  payload_json jsonb,
  available_at timestamptz,
  attempts integer,
  published_at timestamptz,
  UNIQUE (event_id, topic)
);
```

The transaction changing `runs` or another canonical table also appends the semantic event and outbox row. A relay uses Redis Streams for low-latency delivery. Pub/sub may be a wake-up hint only.

`GET /sessions/{id}/events` authenticates membership and accepts `Last-Event-ID` encoding `(session_id, session_seq)`. It:

1. backfills PostgreSQL rows after the cursor;
2. attaches to the tenant/session-qualified Redis Stream;
3. closes the read/subscribe race by checking PostgreSQL again;
4. emits heartbeats;
5. returns a state snapshot plus a new cursor when the requested presentation history has expired.

Durable transition events include queued/started/waiting/settled, tool and approval boundaries when those features exist, and canonical message snapshots. Token deltas should be batched and short-retained; reconnect must be able to recover from a canonical message snapshot without replaying every token.

This foundation costs approximately **7–12 engineer-days across backend and frontend**, including migrations, outbox relay, authorization, reconnect tests, retention/reset behavior, and client dedupe. A happy-path SSE endpoint is much cheaper but does not satisfy the Designer's catch-up requirement. This work is P0 because changing event identity and ordering after clients and projections exist is costly.

### 2.2 One semantic approval across Web, QQ, and email

The UI review §2 “Approval card” and §7 P0 recommendation need a backend state machine, not only matching card copy.

```sql
approval_requests(
  id uuid PRIMARY KEY,                 -- canonical correlation ID
  short_code text UNIQUE,
  tenant_id uuid,
  run_id uuid,
  invocation_id uuid,
  action_type text,
  normalized_args_hash bytea,
  semantic_payload jsonb,              -- versioned, bounded, redacted preview
  schema_version integer,
  requested_by text,
  required_role text,
  policy_version text,
  status text,                         -- pending/approved/rejected/expired/superseded
  expires_at timestamptz,
  nonce_hash bytea,
  version integer,
  decided_by_user_id uuid,
  decided_via_channel text,
  decided_at timestamptz
);
```

Required behavior:

- The request binds the tenant, run, exact normalized action/arguments, policy version, expiry, and one-use nonce. A channel cannot approve a visually similar but different action.
- Signed Web/email actions or QQ reply codes carry only an opaque request ID and one-use token. Free-text “yes” is never sufficient.
- The decision endpoint reauthenticates the identity, tenant membership, role, expiry, policy, and arguments. A conditional `UPDATE ... WHERE status='pending' AND version=:expected` makes the first valid response win.
- The resulting receipt is appended to the event journal and outbox so every surface becomes `approved elsewhere`, `rejected`, or `expired`.
- A waiting run stores `waiting_approval`, releases worker/DB/Redis leases, and is resumed through a new fenced job after approval.
- “Allow for session” or “Always allow” creates a separately versioned policy grant; it must not mutate the one-time request into broader authority.

For the narrowed MLP, candidate accept/edit is not this approval protocol and QQ/email approvals are unnecessary. Freeze the semantic schema now if shared UI components are built, but defer the engine until the first `ask`-gated action.

### 2.3 “What the agent did on your behalf” audit view

This view is **not free** merely because `run_events` exists, and Sherpa is not fully event-sourced. Phase-1 §7 correctly describes CRUD state plus an event journal.

Technical run events are often too verbose, unstable, sensitive, or short-lived for a user/security audit. The audit view needs stable semantic action receipts with actor, trigger, tenant, action, target, source, approval, outcome, reversibility, timestamps, and redaction:

```sql
audit_entries(
  id uuid PRIMARY KEY,
  tenant_id uuid,
  occurred_at timestamptz,
  category text,
  actor_type text,
  actor_id text,
  trigger_type text,
  run_id uuid,
  action text,
  target_type text,
  target_id text,
  outcome text,
  approval_request_id uuid,
  reversible boolean,
  summary_json jsonb,
  schema_version integer
);
```

Business transactions should emit a stable action-receipt event; an idempotent projection can populate `audit_entries`, or the transaction can write both. The latter gives simpler MVP correctness. The table is append-only to application roles, RLS-protected, redacted before insertion, and retained separately from raw debug/token events.

The event journal provides ordering, run linkage, and projection replay, so it saves work. It does not provide the semantic mapping, redaction policy, access control, indexes, retention, undo/remediation rules, or UI query model. A useful MVP audit for connector reads, candidate creation/decision, todo changes, reminders, OAuth use/disconnect, and deletion is roughly **4–7 engineer-days** across backend and UI. Enterprise-grade tamper evidence, export, legal retention, and administrator views can follow.

### 2.4 Real-time token-streaming polish

Polished token streaming requires this end-to-end path:

```text
provider async iterator
  -> adapter-normalized deltas
  -> worker-side coalescing/backpressure
  -> Redis Stream presentation events
  -> authorized SSE fan-out
  -> client frame batching and in-place reconciliation
  -> durable canonical message snapshots/final message
```

It also requires cancellation, partial-stream failure, provider reset/failover semantics, bounded buffers, slow-client handling, proxy timeout configuration, client dedupe, scroll/focus stability, and accessibility behavior. Raw reasoning deltas must not be exposed, agreeing with UI review §7.

For the first provider, expect another **7–12 engineer-days** for robust backend-to-UI token streaming and failure tests. The MLP can safely ship durable `queued/running/finalizing/settled` states and whole-message updates first. Preserve the event vocabulary and snapshot/cursor model now; defer visual token streaming if it competes with Gmail quality or reminder reliability.

## 3. Cross-role tensions and resolutions

| Conflict | PM position | UI Designer position | Architect position / technical trade-off | Recommended resolution |
|---|---|---|---|---|
| Ship-fast MLP ↔ tenant isolation/RLS ↔ sandbox hardening | PM §§3–4: reach Gmail value early; move sandbox later; personal ICP first | UI §§1, 7: workspace scope must always be visible | Personal scope reduces UI/product complexity, but a missing tenant predicate is still a future breach. Hostile sandbox isolation is expensive. | **Personal product, multi-tenant-shaped storage.** Use tenant IDs, RLS, and composite FKs now; expose only personal workspace. Remove sandbox entirely rather than ship weak Docker isolation. Gate later sandbox on gVisor/dedicated nodes. |
| Real-time UX ↔ durable event-log cost | PM §6.3 requires async progress but pressures time-to-value | UI §3 requires run states, reconnect, catch-up, and no duplicate cards | Pub/sub is cheap but loses events; persisting every token is costly and noisy. | Persist semantic transitions and canonical message snapshots in PostgreSQL; use outbox + Redis Streams for latency; batch/expire token deltas. Pay the 7–12 day foundation now, defer token polish. |
| Broad autonomy ↔ blast radius | PM §§5.2–5.3 wants candidate-first and narrower tools | UI §5 wants visible autonomy levels, receipts, and undo | ADR-009 SAFE can read private data/write durable state; ADR-010 auto-todo/notify creates trust and security exposure. | Add a pipeline-specific, no-tool structured extraction mode. Auto-create candidates only; user accepts formal todos; notifications opt-in with digest/caps. Add authority only after measured trust. |
| Agentic email / QQ value ↔ operational and security fragility | PM §§2.10–2.11 and 7: high potential, experimental, non-blocking | UI onboarding/approval designs assume channel parity | QQ policy/login instability and email deliverability/abuse are continuing operations, not adapter-only work. Cross-channel approval adds identity and stale-action races. | Keep channel interfaces and semantic approval schema, but ship Web + Gmail + transactional digest first. Run QQ and agentic email behind independent go/no-go gates; never make them the sole approval or critical-reminder route. |
| Reminder urgency ↔ ADR-011 “never duplicate” | PM §§2.8 and 6.7 says a missed critical reminder is worse than a duplicate | UI scheduled-task history needs claimed/missed/failed states | End-to-end exactly-once is impossible; current at-most-once silently loses a firing. | Use unique firing + transactional outbox + at-least-once workers. Apply idempotency and delivery reconciliation. Digest may prefer no duplicate; critical reminders prefer eventual delivery. Show unknown/missed state. |
| “What the agent did” ↔ privacy/retention/engineering cost | PM trust layer requires traceability and deletion | UI §5.2 asks for detailed receipts and undo | Raw events may contain sensitive payloads and are unsuitable as a stable audit API. Duplicating all data increases retention risk. | Emit minimal redacted semantic receipts and keep a separate audit projection/table. Link to raw debug data only for authorized diagnostics. Define deletion/retention independently. |
| Cross-channel identical approval ↔ MLP scope | PM removes external writes and QQ/agentic email from v1 | UI §2 requires semantically identical approval everywhere | Full approval store, signed actions, channel identity, expiry, and resume logic are material work, but the public schema becomes hard to change after clients ship. | Freeze the versioned contract and correlation rules now. Do not implement approval surfaces until an `ask` action enters scope. Candidate confirmation remains a separate business workflow. |
| One-provider speed ↔ future portability | PM §§3.4 and 4.1 says one provider | UI expects stable run events despite retries/failover | Multiple adapters and failover multiply partial-stream and tool-ID cases; provider-specific data can leak into persisted contracts. | Implement one provider behind a canonical adapter; persist model/provider/prompt versions and canonical IDs. Defer failover, but keep provider metadata out of public event identity. |
| “One-click” self-hosting ↔ service correctness | PM §§2.2 and 5.1 warns Compose is not onboarding | UI onboarding requires recoverable, legible setup states | Collapsing PostgreSQL/Redis or running work in web saves containers but damages durability and operability. | Keep PostgreSQL, Redis, web, and worker boundaries; omit MinIO/sandbox/channels. Support one documented host profile with setup diagnostics, health checks, backup instructions, and demo data. Narrow the promise to supported Compose deployment. |

## 4. Revised pre-P0 checklist

This is the final minimum set of decisions to lock before P0 code. “Deferred” means an owned tracking issue with a feature-entry gate, not an undocumented assumption.

1. **[must-have-now] Lock the first release profile:** self-hosted/BYOK, one owner, one personal tenant, Web + read-only Gmail, no attachments, candidate-first todos, Web inbox + one digest route, one provider, and no generic side-effecting agent tools. Record explicit non-goals from PM §§1.6 and 3.4.
2. **[must-have-now] Amend ADR-003:** canonical identity and session keys include environment/tenant, channel installation, scope type, and external scope; group actor identity is separate from session identity.
3. **[must-have-now] Amend ADR-005:** PostgreSQL canonical run state and sequenced event journal + transactional outbox are the recovery source; Redis Streams accelerate delivery; pub/sub is never correctness-critical.
4. **[must-have-now] Amend ADR-009/ADR-010:** add a pipeline-specific structured extraction capability with no tools; candidates are automatic, formal todos require confirmation by default, and notifications require opt-in/policy.
5. **[must-have-now] Amend ADR-011:** durable unique firings and outbox, at-least-once processing, idempotent/reconciled delivery, and per-job missed/duplicate policy replace global at-most-once.
6. **[must-have-now] Define tenant enforcement:** PostgreSQL from day one, non-owner app role, `ENABLE/FORCE RLS`, `SET LOCAL app.tenant_id`, composite tenant foreign keys, tenant-filtered search, and API/worker/scheduler isolation tests.
7. **[must-have-now] Freeze run/event contracts:** run states, session/run sequences, event IDs, schema versions, bounded/redacted payloads, SSE cursor/reset behavior, outbox relay, retention, and projection replay.
8. **[must-have-now] Freeze ingress and candidate contracts:** provider installation/account namespace, external item/revision uniqueness, extraction-version uniqueness, candidate state machine, source provenance, thread-update reconciliation, feedback events, and deletion semantics.
9. **[must-have-now] Freeze effect contracts:** canonical invocation ID, idempotency key, effect class, `succeeded/failed/effect_unknown`, and “never blindly retry unknown effects.” Implement them on every P0 side effect; generic tooling can wait.
10. **[must-have-now] Select the worker model:** use one explicitly configured runtime (recommended: `arq` for P0), with job IDs, acknowledgement/retry, deadlines, cancellation, fencing, outbox dispatch, lost-job reconciliation, and no DB connection held during remote work.
11. **[must-have-now] Define Gmail credential and data boundaries:** exact scopes, callback profile, per-record AEAD metadata, KEK location/rotation, connector-only decrypt authority, refresh serialization, redaction, disconnect, retention, export, and deletion.
12. **[must-have-now] Define minimum audit/telemetry:** semantic action receipts, append-only audit access, run/generation/provider/prompt versions, tokens/cost, connector health, queue latency, candidate feedback, reminder outcome, and canary-secret redaction tests.
13. **[must-have-now] Freeze the semantic approval envelope:** correlation ID, tenant/run/invocation binding, normalized arguments hash, preview, policy version, expiry, nonce, decision actor/channel, and first-valid-response-wins. No P0 capability may return `ask` until the durable store/resume path exists.
14. **[must-have-now] Define the supported deployment profile:** service health/readiness, migration ownership, environment-key checks, backup/restore instructions, RPO/RTO claim, queue/event persistence, and graceful worker drain.
15. **[safe-to-defer-with-a-tracking-issue] Full cross-channel approval engine and renderers:** required before the first Web/QQ/email `ask` action, not for candidate confirmation.
16. **[safe-to-defer-with-a-tracking-issue] Token-delta streaming polish:** durable phase/status/final snapshots ship first; preserve event types and cursor semantics.
17. **[safe-to-defer-with-a-tracking-issue] Sandbox implementation:** before `run_code`, amend ADR-007 and require backend-neutral execution, gVisor or equivalent for unrelated tenants, dedicated nodes, immutable snapshots, egress policy, aggregate quotas, and orchestrator threat review.
18. **[safe-to-defer-with-a-tracking-issue] MinIO/files, memory, pgvector, and RAG:** add only with explicit visibility, generated object keys, versioning, tenant-filtered retrieval, lifecycle, and deletion contracts.
19. **[safe-to-defer-with-a-tracking-issue] Team product and full RBAC:** tenant schema remains; invitations, role matrix, explicit sharing, private-source rules, offboarding, comments/activity, and admin audit gate Team beta.
20. **[safe-to-defer-with-a-tracking-issue] GitHub, QQ, and agentic email:** each needs an independent owner, support matrix, threat model, health state, revoke/delete path, fallback, and go/no-go metric.
21. **[safe-to-defer-with-a-tracking-issue] Multi-provider failover, full billing/quotas, managed KMS, HA, and enterprise audit/export:** provider metadata and basic budget hooks exist now; these become release gates before managed or materially multi-tenant operation.

## 5. One-way doors

| Decision to get right now | Why reversal is expensive | Resolution |
|---|---|---|
| Tenant ownership and isolation | Every table, index, repository call, event, cache key, object, and test inherits it; retrofitting RLS exposes historical cross-tenant mistakes. | Tenant-qualified keys, composite FKs, forced RLS, least-privileged roles, and transaction-local tenant context from migration one. |
| Identity/session/UMO key | Sessions, channel links, history, approvals, and audit records become keyed to it; collisions cannot be safely disentangled later. | Internal UUID plus unique `(tenant_id, channel, channel_installation_id, scope_type, external_scope_id)`; retain raw provider ID only for audit. |
| Event identity, ordering, and recovery source | SSE clients, projections, audit links, retries, and operations consume it. Switching from lossy pub/sub after launch leaves irrecoverable history gaps. | PostgreSQL event journal is the replay/recovery source; business tables remain canonical current state. Use versioned envelopes, session/run sequences, outbox, and Redis Streams acceleration. |
| Run/effect/idempotency semantics | Every tool and external connector otherwise invents incompatible retry behavior; duplicate or unknown effects appear only after crashes. | Persist invocation identity before effect, classify retryability/effect, use idempotency keys, and stop for reconciliation on unknown outcomes. |
| Candidate/source provenance | Accepted todos, feedback, dedupe, source updates, and deletion all rely on it. Retrofitting loses user trust and training/evaluation integrity. | Stable connector item/revision, extraction version, generation, candidate, and accepted-todo links from the first candidate. |
| Approval correlation and authority | Once Web/email/QQ clients encode request fields, changing scope or one-use semantics risks approving the wrong action. | Versioned semantic payload bound to exact arguments, tenant, policy, expiry, nonce, and authorized decider; first valid response wins. |
| Secret cryptography and process boundary | Plaintext tokens leak into backups/logs/events, and later encryption does not erase copies. Broad decrypt privileges become embedded in services. | Per-record AEAD metadata, rotatable KEK version, immediate encryption, connector-only decrypt authority, and tested redaction from the first OAuth callback. |
| Reminder firing and delivery semantics | Users form expectations around missing versus duplicate reminders; historical jobs cannot be reconstructed from an advanced cursor. | Durable firing + outbox + delivery attempts, explicit unknown state, and per-notification retry/idempotency policy. |
| Audit versus debug-event boundary | Raw telemetry schemas and retention change frequently and may contain secrets; making them the public audit API freezes internals and increases privacy risk. | Stable, redacted semantic receipts in an append-only audit model linked to—but distinct from—debug/presentation events. |
| Future file/object and sandbox contracts | Persisted host paths, Docker options, or user-derived object keys create backend lock-in and cross-tenant risk. | Before those features ship, use generated tenant-owned object IDs and a backend-neutral execution request/result contract; never expose Docker socket semantics publicly. |

The central engineering judgment is therefore: accept the PM's product narrowing and the Designer's durable-state requirements together. The narrowed MLP removes most dangerous capabilities, while the remaining durability, tenant, event, secret, and provenance contracts are precisely the parts that must not be prototyped as throwaway code.
