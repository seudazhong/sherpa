# Sherpa pre-implementation architecture review

**Review date:** 2026-07-19  
**Scope:** `README.md`, design documents `00` through `09`, and ADR-001 through ADR-014 in `decisions.md`.

## Executive verdict

Sherpa has a strong high-level shape: the surface/gateway/core/capability boundaries are appropriate, asynchronous admission is the right default, the core loop is bounded, and treating tools as a narrow JSON interface should limit coupling. The design is not yet safe to implement as a multi-tenant service, however. Several statements currently describe aspirations rather than enforceable invariants.

The most important corrections are:

1. Use PostgreSQL RLS, tenant-qualified keys, and composite foreign keys from the first multi-tenant migration; application filtering alone is not an adequate security boundary.
2. Replace correctness-critical Redis pub/sub with a durable run/event journal plus transactional outbox. Redis Streams may accelerate delivery, but PostgreSQL should remain the recovery source.
3. Add durable tool-invocation and approval state. Turn-only recovery is unsafe for non-idempotent tools.
4. Do not present ordinary Docker as a hostile multi-tenant code boundary. Use gVisor or equivalent by P2, or explicitly restrict Docker-only deployments to trusted/single-tenant use.
5. Replace scheduler “at-most-once” with durable firing records, transactional enqueue, at-least-once processing, and idempotent effects. The current algorithm silently loses executions.

**Recommendation:** proceed with P0 only after amending ADR-003, ADR-005, ADR-006, ADR-007, ADR-009, and ADR-011 and defining the persistence/security contracts below. Observability, tenancy, idempotency, and audit primitives cannot safely be deferred to P6.

## 1. ADR-by-ADR assessment

| ADR | Assessment | Review | Reversibility |
|---|---|---|---|
| **ADR-001 — cloud agent** | **Sound** | The required long-lived channels, schedules, push delivery, and collaboration justify a service rather than a local process. It also means hostile tenancy, operations, and compliance are core requirements rather than later hardening. [`00-overview.md`; `01-architecture.md`] | **High-cost one-way door:** product/operating model and threat model. Justified. |
| **ADR-002 — Python + TypeScript** | **Sound** | Python fits the provider/RAG/connector ecosystem and TypeScript fits the UI. The design must choose one worker/queue model rather than leaving “Celery/arq” open: blocking Celery tasks and asyncio resource lifecycles behave differently. Define async provider/tool interfaces, cancellation, and process-level connection limits before implementation. [`01-architecture.md` § process topology] | Medium. Rewriting the core language is expensive; independent sandbox/channel adapters reduce lock-in. |
| **ADR-003 — tenant/user/identity + UMO** | **Needs-revisit** | The conceptual split is good, but `channel:type:external_id` lacks a channel installation/account namespace and is unsafe as a global natural key. External IDs can collide across QQ bots, email domains, webhook installations, environments, and tenants. Group sessions also require a separate actor/participant identity rather than `sessions.user_id`. `resolve_inbound()` must authorize tenant membership after workspace resolution, not merely find a verified identity. [`02-identity-session-memory.md` § UMO and `resolve_inbound()`] | **High-cost one-way door:** session identity and all foreign/event keys. Fix before data exists. |
| **ADR-004 — user + tenant memory** | **Risky** | The two scopes are useful, but injecting a user's “private” memory into a team session can disclose personal facts into a shared transcript, tool result, or model trace. Define memory visibility (`private`, `tenant-visible`, `session-only`), provenance, retention, and explicit promotion. Tenant blocks need optimistic concurrency/version history because session serialization does not serialize team-wide writes. [`02-identity-session-memory.md` § two-tier memory] | **High-cost one-way door:** ownership/privacy semantics and stored embeddings. |
| **ADR-005 — async jobs + streamed event bus** | **Needs-revisit** | Async-job-first and durable prompt admission are sound. Redis pub/sub is not a durable event bus: it has no acknowledgement, replay, or reconnect cursor, and therefore contradicts “durable-first.” Runs, events, queue publication, and recovery need a durable model and outbox; SSE needs resumable sequence IDs. [`03-runtime-async-jobs.md`; `04-core-loop.md` § streaming vocabulary] | **High-cost one-way door:** public event protocol and run lifecycle. |
| **ADR-006 — dual loop + turn persistence** | **Needs-revisit** | The bounded dual loop and structured stop-reason gate are sound. Turn-only persistence is not sufficient once a tool can mutate a workspace or external system: a crash after an effect but before its result is persisted causes re-execution. Keep a simple ReAct loop, but persist each tool invocation and idempotency key before execution. [`04-core-loop.md` § crash recovery] | **High-cost one-way door:** recovery semantics become embedded in every tool. |
| **ADR-007 — ephemeral Docker per run** | **Needs-revisit** | Ephemeral compute, read-only roots, resource limits, and network-off are good defaults. Docker containers share the host kernel and are not a strong boundary for malicious tenant code. The sandbox orchestrator's Docker socket is effectively root on its host, and its API is influenced by untrusted input. Multi-tenant P2 should use gVisor/Kata or stronger isolation; Docker-only must be labeled trusted beta/single-tenant. [`05-tools-permissions-sandbox.md` § sandbox; `07-observability-deployment.md` § compose] | **High-cost one-way door** if workspace mounts, images, and orchestration APIs assume Docker details. Preserve a backend-neutral execution contract. |
| **ADR-008 — asynchronous HITL** | **Risky** | Cross-surface approval is appropriate, but an ephemeral event and a correlation ID are insufficient. Persist an approval request bound to tenant, approver role, run, exact normalized arguments, policy version, expiry, and one-use nonce. Waiting must release the worker and session lease, then resume through a new job. [`05-tools-permissions-sandbox.md` § permissions] | Medium; the protocol is cheap to fix before clients ship. |
| **ADR-009 — SAFE/FULL toolsets** | **Needs-revisit** | Origin-based restriction is useful defense in depth, not a complete trust model. Authenticated users can paste hostile text, and a FULL run can retrieve hostile connector content. Conversely SAFE currently includes workspace reads, memory/todo writes, and arbitrary `web_fetch`, which can enable data exfiltration or durable poisoning. Apply trust to data and requested effect, not only to the entry channel; give egress and write tools narrower capabilities. [`05-tools-permissions-sandbox.md` § trust tiers; `06-connectors-autonomy.md` § email trust] | Medium-high because tool policy and prompt schemas spread across integrations. |
| **ADR-010 — autonomy boundary** | **Risky** | Requiring approval for actions representing a user is correct. “Notification is always low risk” is too broad: it may leak private content, spam a group, incur cost, or target an incorrectly linked identity. Automatic todos can also poison shared state. Add destination policy, content redaction, confidence thresholds, undo/history, and tenant budgets. [`06-connectors-autonomy.md` § autonomy ladder] | Low-medium; mostly policy, provided effects are audited and idempotent. |
| **ADR-011 — at-most-once scheduler** | **Needs-revisit** | Advancing `next_run_at` before durable enqueue loses a firing if the scheduler crashes between those operations. The shown `sent_log.exists → send → record` has a race and cannot atomically cover an external provider. Create a unique schedule-firing row and outbox record in one transaction, process at least once, and use provider/idempotency keys or explicit delivery reconciliation. [`06-connectors-autonomy.md` § scheduler/push] | **High-cost one-way door:** missed-versus-duplicate delivery semantics are user-visible. |
| **ADR-012 — Postgres/pgvector + Redis + MinIO** | **Sound** | This is a pragmatic initial stack. Do not treat the components as interchangeable merely because workers are stateless: queue semantics, vector indexes, object workspace materialization, and locks leak into behavior. Separate Redis correctness data from evictable cache traffic and define backup/RPO/RTO. [`07-observability-deployment.md` § storage] | Medium. pgvector is easy to start with; event/queue and object contracts require explicit adapters to move later. |
| **ADR-013 — agentic email vs Gmail** | **Sound** | The distinction between account trust and content trust is important, as are read-only OAuth scopes. Both inputs still need sender/authentication checks, inbound idempotency, MIME limits, malware handling, and prompt-injection controls. [`06-connectors-autonomy.md` § trust tiers] | Low. |
| **ADR-014 — name** | **Sound** | No architectural concern. | Low. |

## 2. Multi-tenant isolation

### RLS is required for the first multi-tenant PostgreSQL release

`08-data-model.md` says both “every table has `tenant_id`” and that filtering is performed by the application, with RLS optional. Application filtering is not enough for a system with HTTP handlers, background workers, schedulers, connectors, vector queries, migrations, and future raw SQL. A single omitted predicate becomes a data breach.

Use defense in depth:

* A non-owner application role without `BYPASSRLS`; table owners are reserved for migrations.
* `ENABLE ROW LEVEL SECURITY` and `FORCE ROW LEVEL SECURITY` on every tenant-owned table.
* At transaction start, `SET LOCAL app.tenant_id = :tenant_id`; fail closed if absent.
* Repository methods still require `tenant_id`. RLS is a backstop, not a reason to hide tenant context.
* Composite primary/unique keys and foreign keys that include `tenant_id`, preventing a child row from pointing across tenants.
* Separate, narrowly privileged code paths for global user login and exact inbound-identity resolution.

Illustrative pattern:

```sql
ALTER TABLE messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE messages FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON messages
USING (
  tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
)
WITH CHECK (
  tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
);

ALTER TABLE messages
  ADD CONSTRAINT messages_session_tenant_fk
  FOREIGN KEY (tenant_id, session_id)
  REFERENCES sessions (tenant_id, id);
```

`users` can legitimately be global because a user belongs to several tenants; therefore “every table” is not literally correct. `memberships`, `messages`, `parts`, `todo_deps`, and other tenant-owned descendants should nevertheless carry or enforce tenant context. The schema currently does not show that. Test RLS with a matrix of API, worker, scheduler, and connector roles, including attempts to use guessed UUIDs.

With async SQLAlchemy, set tenant context inside every transaction, not on a pooled connection outside the transaction. PgBouncer transaction pooling is compatible with `SET LOCAL`; session-level settings are not. Never hold a database transaction or connection during an LLM call, tool run, or HITL wait.

### Identity/session isolation

The UMO key should be an opaque canonical tuple, not a delimiter-concatenated security identifier:

```text
(tenant_id, channel, channel_installation_id, scope_type, external_scope_id)
```

Enforce `UNIQUE` on that tuple and expose an internal session UUID. `channel_installation_id` distinguishes two bots/apps seeing the same external identifier. Normalize external values once, retain the raw value for audit, and do not silently re-key sessions if providers change identifiers.

Inbound identity lookup is a privileged bootstrap operation. First verify webhook/channel authenticity and identify the channel installation; then look up the exact identity/group binding; then verify membership and role in the resolved tenant. A verified user identity alone must not authorize an event to choose an arbitrary tenant. Messages need `actor_user_id`/`external_actor_id`, because one group session has multiple users as acknowledged in `02-identity-session-memory.md`.

### Redis

No browser or channel client should access Redis directly. Every key and delivery topic must be environment- and tenant-qualified, for example:

```text
sherpa:{env}:tenant:{tenant_id}:session:{session_id}:events
sherpa:{env}:tenant:{tenant_id}:session:{session_id}:interrupt
```

Opaque IDs are not authorization. The SSE/WS server must authenticate the user and check current tenant membership before it reads a stream, and again on reconnect. Avoid a global pub/sub topic followed by application filtering; one filtering bug exposes all tenants. Events must carry immutable `tenant_id`, `session_id`, `run_id`, and sequence, and publishers should validate these against the run record rather than trusting job payloads.

Use separate Redis instances, or at minimum separate service roles and `noeviction` deployments, for durable queue/stream/lock data versus evictable caches. Logical Redis databases are not a security boundary. Redis ACLs should isolate service classes, although per-tenant authorization remains in Sherpa.

### MinIO

Use a dedicated environment bucket with policy-enforced prefixes such as `tenants/<tenant_uuid>/users/<user_uuid>/...`, or a bucket per tenant where regulatory isolation justifies the operational cost. Never derive object keys directly from user paths. Store the display path separately, reject traversal and ambiguous Unicode, and map it to a generated object key.

Issue only short-lived, object-specific presigned operations after tenant authorization. Workers should not receive broad MinIO credentials. Validate object key, tenant, size, content type, checksum, and ownership against the `files` row on every operation. Add encryption at rest, versioning/retention policy, upload limits, archive/zip-bomb protection, and malware scanning where uploaded artifacts can reach other users.

### pgvector

Every lexical and vector query must be tenant- and visibility-filtered before ranking; RLS must also apply to `memory_passages`. Approximate HNSW/IVFFlat indexes can under-return when a post-filter removes other tenants' nearest neighbors. At small scale use exact tenant-filtered search, or partition by tenant/cohort and tune iterative scans before adopting approximate indexes. Store embedding model, dimensions, version, content hash, and visibility with each passage.

Use a B-tree index beginning with `tenant_id` for metadata filtering, a GIN index on a generated `tsvector`, and an HNSW index for embeddings. Hybrid retrieval should fuse separately tenant-filtered FTS and vector result sets. Never retrieve globally and filter in Python.

## 3. Sandbox security — highest-risk area

### Threat model

Once a tenant can ask the model to create and execute code, Sherpa is running attacker-controlled code even if the tenant is “authenticated.” Prompt injection can also influence generated code. The distinction in ADR-007 between user code and “untrusted third-party code” is therefore not a safe deployment boundary.

The sandbox must defend against:

* host/kernel/container escape;
* cross-tenant filesystem and cache leakage;
* access to Docker, cloud metadata, Redis, PostgreSQL, MinIO, or internal services;
* CPU, memory, PID, disk, inode, log, network, and container-count exhaustion;
* malicious images and supply-chain artifacts;
* exfiltration through network, logs, tool output, or shared workspaces.

### Docker socket and orchestrator

Mounting the Docker socket gives `sandbox-orch` root-equivalent control of the Docker host. Although tenant code does not directly see the socket, untrusted job arguments cross the orchestrator's API. A command-injection, mount-selection, image-selection, or path-validation defect can convert that influence into host control.

Required controls:

1. Run sandbox nodes separately from web, database, Redis, and connector-secret workloads. A sandbox-node compromise must not reveal production credentials.
2. Prefer a dedicated rootless daemon and gVisor runtime. A socket-filter proxy may reduce API surface but is not a substitute for isolation.
3. Authenticate worker-to-orchestrator calls with short-lived workload identity/mTLS and authorize tenant/run IDs.
4. Construct container options entirely server-side. Permit only images pinned by digest from an allowlist; never accept caller-supplied mounts, devices, capabilities, network modes, entrypoints, privileged flags, or host paths.
5. Apply `no-new-privileges`, seccomp, AppArmor/SELinux, all-capability drop, non-root UID, read-only root, bounded tmpfs, PID/CPU/memory/I/O quotas, ulimits, output limits, and hard wall-clock termination.
6. Enforce aggregate per-tenant concurrency and compute/storage budgets in addition to per-container limits.
7. Reconcile and reap orphan containers after orchestrator/host restarts.

### Docker versus gVisor/Firecracker

Ordinary Docker-per-run is acceptable for P0 local development and a clearly labeled trusted single-tenant deployment. It is not enough for the advertised hostile multi-tenant P2. gVisor is the pragmatic minimum before allowing unrelated tenants to execute code; Firecracker/Kata can be added for higher assurance or regulated workloads. Keep an `ExecutionBackend` contract covering image, command, immutable input snapshot, output delta, limits, and egress policy so the backend is genuinely replaceable.

If gVisor is not available on a supported host, fail closed or disable `run_code`; do not silently fall back to weaker isolation for a tenant expecting hardened execution.

### Workspace and prewarming

A persistent read/write workspace mounted directly into an at-least-once tool execution makes crash recovery and isolation difficult. Prefer a per-run snapshot/overlay, then validate and atomically commit the output delta under a workspace version. This prevents a killed container from leaving partially written state and permits conflict detection.

Prewarm image layers, runtime sandboxes, or clean one-use containers. Do not return a live container to a cross-tenant pool. If pre-created containers are used, they must never have mounted tenant storage, must receive a single run, and must be destroyed afterward. Pools need per-image/trust-class bounds; otherwise prewarming itself is a DoS vector. Measure cold-start SLO before accepting the security complexity of a pool.

### Network and SSRF

`--network none` is the correct default. Most “web fetch” needs should remain an out-of-sandbox tool: the controlled service fetches bytes and passes a bounded artifact into the sandbox. That preserves a truly disconnected execution environment.

If a run genuinely needs package/network access, it cannot simultaneously use Docker's `none` network. Attach it to a dedicated egress-only network whose firewall permits only an authenticated proxy. The proxy must:

* deny loopback, link-local, RFC1918/ULA, multicast, metadata endpoints, internal DNS zones, and all non-proxy egress for both IPv4 and IPv6;
* resolve and validate every address, pin the connection target, and revalidate every redirect to prevent DNS rebinding;
* enforce method, scheme, port, domain/category, byte, time, and request-count limits;
* strip credentials and dangerous headers, avoid forwarding cookies, and audit the decision;
* use a signed per-run policy rather than trusting a URL supplied by the container.

An allowlist is preferable for package registries. The proxy itself must have no route to control-plane services unless specifically required.

### Secrets

The enforceable guarantee is: **Sherpa-managed connector/provider credentials are never decrypted in, mounted into, or forwarded to a sandbox.** It is not possible to promise that a sandbox sees no secret at all: users may store secrets in their own workspace or paste them into code.

The orchestrator must not possess connector decryption rights, inherit the worker's environment, or accept arbitrary environment variables. Use delegated operations outside the sandbox rather than handing it OAuth tokens. Scrub environment, Docker metadata, errors, logs, event payloads, and tool output. Network-off limits exfiltration but does not prevent a malicious program from printing workspace secrets back to the user/model.

## 4. Durable jobs, event bus, and streaming

Redis pub/sub in `03-runtime-async-jobs.md` and `04-core-loop.md` is fire-and-forget. A web replica restart, network interruption, or slow subscriber loses deltas and possibly a permission request. This conflicts with durable prompt admission and with `07-observability-deployment.md`, which promises an `events` table that is absent from `08-data-model.md`.

Use three distinct concepts:

1. **PostgreSQL canonical state:** admitted messages, runs, tool/approval state, final messages, and a sequenced append-only event journal.
2. **Transactional outbox:** publishes queue and stream notifications only after the state transaction commits. A relay retries safely.
3. **Redis delivery acceleration:** a durable queue and optionally Redis Streams for low-latency fan-out/replay. Pub/sub may remain only as a lossy wake-up hint.

Minimum schema:

```sql
runs(
  id, tenant_id, session_id, admitted_seq, status,
  attempt, fence_token, prompt_version, created_at, settled_at
)
run_events(
  tenant_id, run_id, seq, event_id, type, schema_version,
  payload_json, created_at,
  PRIMARY KEY (run_id, seq),
  UNIQUE (event_id)
)
outbox(
  id, tenant_id, aggregate_type, aggregate_id, event_type,
  payload_json, available_at, attempts, published_at
)
```

Persist every state transition and correctness event (`run.started/settled`, tool invocation/result, approval request/resolution, compaction epoch). Token deltas may be batched or treated as short-retention presentation events, provided a reconnect can always obtain the authoritative assistant snapshot/final message. Do not make every token a separate PostgreSQL transaction.

SSE event IDs should be `(run_id, seq)` or an opaque cursor encoding them. On `Last-Event-ID`, the API:

1. authenticates and authorizes tenant/session access;
2. reads persisted events after the cursor;
3. begins stream consumption;
4. reads PostgreSQL once more to close the subscribe/read race;
5. emits heartbeats and supports a bounded retention/reset response.

Redis Streams can supply recent events with `XREAD` by ID, but PostgreSQL remains the source when a stream expires. Consumer groups are useful for worker work distribution, not for broadcasting a single event to every SSE client.

Durable admission also needs a reconciler: scan admitted prompts/runs without a corresponding queued outbox publication and repair them. Define precisely how `admitted_seq` becomes `promoted_seq` when several prompts arrive while a session is active.

## 5. Core-loop correctness

### Turn-granular crash gaps

In the pseudocode in `04-core-loop.md`, the assistant tool request is persisted, tools execute, and results are then persisted. A crash in the middle leaves ambiguous effects. Parallel tool calls make this worse: some may have succeeded while others have not.

Before execution, insert one `tool_invocations` row per normalized call:

```text
(tenant_id, run_id, turn_seq, invocation_id, tool_name,
 args_hash, idempotency_key, state, attempt, result_ref, effect_class)
```

Use a deterministic invocation identity within the durable run, a unique constraint, and states such as `pending/running/succeeded/failed/effect_unknown`. On recovery:

* reuse a persisted result for a succeeded invocation;
* retry only tools that explicitly support the same idempotency key;
* reconcile an external provider where possible;
* stop for operator/user resolution when the effect is unknown;
* never blindly re-run a non-idempotent write.

Tool authors must declare retry and effect semantics, not merely `is_read_only`. External APIs should receive Sherpa's idempotency key. Workspace writes should use snapshot/versioned commit. Multiple tools may run in parallel only when their declared conflict keys do not overlap.

This is a small durable state machine around tools, not necessarily a wholesale adoption of ADR-006 option B.

### Stop-reason gate

The structured gate is a strong invariant. Execute no tool until the provider response is complete, its normalized finish reason is exactly `tool_use`, all tool calls validate against the schema, and the call IDs are unique. Text resembling a call is inert. If streaming fails after partial tool arguments, mark the provider attempt aborted and execute nothing.

Provider adapters must map provider-specific finish reasons into a closed internal enum. Unknown/mixed reasons fail closed. A final “grace call” must have a reserved token/cost budget and must not be allowed to initiate new tools, otherwise the allegedly bounded loop has another side-effect path.

### Compaction

“Verify it got smaller” should mean token count under the selected provider tokenizer, not byte length. Verification should also ensure:

* preserved system mandates, user intent, recent turns, citations, and complete tool-call/result pairs;
* no new instructions or unsupported facts in the summary;
* a bounded number of attempts and a fallback truncation strategy;
* immutable original transcript plus a versioned compaction epoch, summary prompt/model, source range, token counts, and digest.

Compaction writes must be transactional and fenced against another worker. Evaluate compaction quality with regression cases before it becomes automatic.

### Prompt-cache stability across workers

Byte stability requires more than sorted JSON:

* content-address and version the system prompt, tool schemas, context files, tokenizer, provider adapter, and serialization format;
* canonicalize Unicode, newlines, numeric encoding, map and tool ordering;
* freeze date/time/environment values for a run and keep them after retry;
* deploy immutable prompt assets so all workers with a given version produce the same prefix;
* namespace cache identity by provider, model revision, tenant policy, toolset, and prefix hash;
* record the actual request/prefix hash and cache hit metrics in each generation.

Provider failover necessarily changes cache namespace and often tokenization/tool schema. Do not assume a cached prefix is portable. Provider-generated tool IDs should be preserved where required by that provider rather than rewritten merely to appear deterministic.

## 6. Concurrency, locks, and scheduling

### Session serialization

A Redis TTL lock without fencing is unsafe. A long model/tool run can outlive the TTL; another worker then acquires the lock while the original worker continues writing. Renewal reduces frequency but cannot prevent a paused process from resuming after lease loss.

Use:

* a unique lock token and compare-and-delete/renew script;
* heartbeat renewal shorter than the TTL;
* a monotonic fencing token stored on the run/session;
* conditional PostgreSQL writes that reject stale fencing tokens;
* optimistic transcript sequence/version checks;
* cancellation when renewal fails and a reconciler for abandoned runs.

Do not hold a worker, Redis lock, or database connection while awaiting HITL. Persist `waiting_approval`, release resources, and resume with a new fenced job. New user messages can be admitted and ordered without starting a competing loop.

Redis failover can violate intuitive lock timing, so PostgreSQL version/fence enforcement is the final correctness barrier. An alternative is session-partitioned queue routing, but rebalancing still needs durable versions.

### Scheduler

`FOR UPDATE SKIP LOCKED` already permits multiple safe claimers. Redis leadership can reduce polling load, but it should be an optimization rather than the correctness mechanism. If retained, use a random owner token, compare-and-renew, fencing, and database time. A stopped leader should delay work only until TTL expiry.

In one PostgreSQL transaction:

1. lock the schedule row;
2. calculate a concrete `scheduled_for` and next occurrence, with timezone/DST/misfire policy;
3. insert `schedule_firings(schedule_id, scheduled_for, status)` under a unique constraint;
4. insert an outbox entry;
5. advance the cursor.

Workers process a firing at least once. This prevents both duplicate internal firings and loss between claim and enqueue. “Exactly once” cannot be guaranteed across an external email/IM API; use provider idempotency where supported and otherwise track `pending/sent/unknown`, reconcile, and state the duplicate-versus-loss policy.

### Shared memory contention

Session locks do not serialize two sessions editing the same tenant memory block. Give every block a version and perform compare-and-swap updates (`UPDATE ... WHERE version=:expected`). Retain revisions and actor/run provenance. Conflicts should be merged, retried from fresh state, or presented for review rather than last-write-wins.

Invalidate prompt caches by memory version and rebuild lazily; eagerly rebuilding every member's prompt turns one hot shared block into fan-out work. Consider append/propose-and-promote semantics for high-contention team memory.

## 7. Data model and migrations

`08-data-model.md` is a useful inventory, not yet an implementable schema. Important additions and corrections include:

* **Execution:** `runs`, `run_attempts`, `tool_invocations`, `approval_requests`, `run_events`, `outbox`, dead-letter/reconciliation state.
* **Ingress:** provider/channel installation, verified external identity claims, group/workspace bindings, inbound deliveries with a unique provider event/message ID and payload digest.
* **Connectors:** normalized connector items, sync attempts, cursor history, OAuth key version, external account identity, and unique item IDs.
* **Scheduling/delivery:** `schedule_firings`, `delivery_attempts`; `sent_log` alone cannot model an unknown outcome.
* **Authorization/audit:** role/permission assignments and an append-only security audit log distinct from debug telemetry.
* **Quotas:** tenant/user plan, concurrent-run limits, storage/compute/token budgets, and an immutable usage ledger.
* **Files/memory:** file versions/checksums/scan state; memory visibility, owner foreign keys, revisions, embedding model/version.
* **Observability:** a unified observation/span model for tools and retrieval, not only `generations`.

Key constraints/indexes:

* `memberships`: primary key `(tenant_id, user_id)` and constrained roles.
* identities: unique canonical `(channel, installation_id, external_subject_id)`, with verification method/time; avoid bare `(channel, external_id)`.
* sessions: unique `(tenant_id, channel, installation_id, scope_type, external_scope_id)`.
* messages: unique `(tenant_id, session_id, seq)` plus source/inbound idempotency; actor attribution.
* permissions: deterministic precedence/specificity and policy version; check valid effect/scope.
* connectors: unique external account per intended scope; token algorithm/key version and refresh concurrency version.
* schedules: partial index on `(next_run_at)` where active, plus timezone/misfire policy.
* deliveries: unique `(tenant_id, idempotency_key)` and indexed unresolved states.
* memory blocks: exactly one valid owner, unique owner/label, optimistic `version`.
* files: unique logical path within owner scope and globally unique generated object key.
* todo dependencies: tenant-qualified composite foreign keys, no self-edge, and application/trigger validation for cycles.
* all rows: explicit foreign-key delete policy, timestamps, bounded JSON/text sizes, and schema versions for event payloads.

### Event sourcing terminology

The system as designed is CRUD state plus an event journal; it is not event sourced unless business state is rebuilt from events. That is a reasonable choice. Rename “event sourcing as observability” to “durable event journal plus projections” and keep tables such as sessions/todos as canonical state. The event journal should be append-only, partitioned/retained by time if needed, and sufficient for audit and stream recovery. Projection failure must be replayable.

### FTS and pgvector

PostgreSQL can support both at the intended scale. Add a generated `tsvector` with a GIN index and versioned embedding column with HNSW only after measurement. Hybrid ranking should be explicit (for example reciprocal-rank fusion), tenant-filtered in each branch, and reproducible enough to evaluate. An embedding dimension/model change needs a parallel column/table and backfill rather than an in-place flag day.

### Migration strategy

The single Alembic owner in `07-observability-deployment.md` is correct if it means one migration job, not that every app starts by racing to migrate. Use an advisory migration lock, a privileged migration role, least-privileged app roles, and expand/migrate/contract changes compatible with the current and next application version. Backups are not sufficient without automated restore drills, migration rollback/forward-fix procedures, and recorded RPO/RTO. RLS policies and grants belong in migrations and tests.

P0's SQLite prototype (`09-roadmap.md`) must not create a divergent schema/repository contract. Carry tenant IDs, run IDs, event sequences, and tool invocation IDs even in single-user P0; enable PostgreSQL RLS when P1 introduces PostgreSQL.

## 8. Provider layer

The provider layer needs a canonical request/response/usage/error contract and explicit capability matrix: tool calling, parallel calls, context length, cache controls, streaming semantics, tokenizer, data residency, and safety policy. Failover is allowed only to a provider/model compatible with the run's tenant policy and required features.

### Partial streams and identity reconciliation

If provider A emits visible text or a tool call and then fails, silently continuing with provider B can duplicate prose, change intent, or produce incompatible tool IDs. Persist `generation_attempts` and their provider response IDs. Before any effect, require one complete, validated response. For visible partial prose, emit an `attempt.aborted/reset` semantic event or retain it explicitly as non-canonical; provider B starts from the last durable canonical transcript, not an invented concatenation.

Tool results must be attached to Sherpa's canonical invocation ID while preserving provider-specific IDs in adapter metadata. Failover after an external effect must reuse the durable result; it must not ask the new model to repeat the effect.

### Prompt cache and rate limits

Prompt caches are provider/model/request-byte specific. A fallback gets a new cache key and may require a differently serialized prompt. Record provider/model revision and prefix hash on every attempt.

“Process-wide” guards from `09-roadmap.md` are insufficient across workers. Implement distributed token/request buckets and concurrency semaphores keyed by provider credential and tenant, plus:

* provider-wide and per-tenant limits;
* admission estimates and final usage reconciliation;
* circuit breakers, jittered `Retry-After` handling, and bounded failover;
* fair scheduling so one tenant cannot consume all credentials;
* daily/monthly cost budgets with a hard stop and auditable override;
* a defined fail-closed/degraded policy when Redis is unavailable.

Never use failover to evade a provider's rate limit indefinitely; it must remain within tenant policy and global spend limits.

## 9. Scalability and failure modes

### Around 10 active users

The chosen stack is sufficient, but correctness issues will appear before capacity issues:

* missed SSE events and approval requests under pub/sub reconnects;
* Docker cold-start latency and orphaned containers;
* duplicate/lost side effects after worker crashes;
* session lock expiry during long tools;
* too many database connections if each web/worker process uses default pools;
* provider rate-limit bursts and unbounded connector polling.

Use small explicit async pools, release connections before remote work, cap worker concurrency by resource class, and instrument queue delay, run duration, event lag, lock renewals, sandbox startup, and provider limits.

### Around 100 active users

The first bottlenecks are likely provider quotas/cost, sandbox CPU/memory, noisy-neighbor behavior, queue backlog, and PostgreSQL connections—not the Python loop itself. pgvector filtering and connector/scheduler scans then need measured indexes. A single Redis instance becomes a shared blast radius for queue, streams, and locks.

“Add machines directly” in `03-runtime-async-jobs.md` is incomplete: local Docker sockets and named workspace volumes do not span machines. Scale-out requires sandbox nodes, object-backed snapshot materialization, routing, image distribution, and no dependence on local volume identity.

### Backpressure

Define bounded queues and admission policy:

* per-tenant/user rate limits, concurrent-run caps, token/compute/storage budgets;
* weighted-fair tenant queues and separate interactive, scheduled, connector, and sandbox priorities;
* maximum queue age/deadline, cancellation, and stale-job rejection;
* `429`/`503` plus `Retry-After` for HTTP, and bounded channel acknowledgements;
* connector batch/page limits and coalescing;
* dead-letter queues with operator-visible replay/reconcile tools;
* load shedding before PostgreSQL/Redis/provider saturation.

Queue depth alone is insufficient; alert on oldest-job age and estimated drain time. Avoid retry storms with exponential backoff and a retry budget.

### Single points of failure

Compose intentionally starts with single PostgreSQL, Redis, MinIO, scheduler, and likely sandbox host. Document this as a development/small-installation profile with explicit RPO/RTO:

* PostgreSQL: backups, WAL/point-in-time recovery, restore tests; HA when required.
* Redis: AOF policy, `noeviction` for correctness workloads, persistence/replication caveats, and recovery reconciliation from PostgreSQL.
* MinIO: versioning and backed-up object data/metadata; avoid assuming a single disk is durable.
* Scheduler: no correctness dependence on one leader; firing/outbox reconciliation.
* Sandbox: reschedule only invocations known safe; mark ambiguous effects.

Health endpoints must distinguish liveness from readiness and verify required dependencies without causing cascades. During shutdown, stop claiming work, renew/finish or safely checkpoint current leases, flush events/outbox, and only then exit. Long-lived channels need reconnect and ownership handoff.

## 10. Secrets and key management

The DEK-wrapped token direction in `06-connectors-autonomy.md` is sound but incomplete:

* Use random per-record DEKs with an AEAD mode such as AES-256-GCM; unique nonce and authenticated associated data should include tenant, connector, record ID, and key version.
* In production, keep the KEK in KMS/HSM and grant decrypt only to connector/provider workloads. An environment master key is acceptable only for local development and must not appear in compose files, images, backups, crash dumps, or process listings.
* Store algorithm/KEK version, support lazy/batch rotation, revoke on disconnect, and serialize OAuth refresh to prevent token races.
* Never decrypt in web, scheduler, generic core worker, sandbox orchestrator, telemetry, or the model prompt unless a narrowly defined connector operation requires data from the credential.
* Redact request/response headers, URLs, exception bodies, traces, events, tool output, and support bundles. Test redaction with canary secrets.
* Audit decrypt/use events without logging plaintext. Restrict backup and database administrator access because ciphertext plus environment KEK defeats envelope encryption.

The “never in sandbox” guarantee is enforceable only through process and credential separation, an orchestrator API that cannot inject arbitrary environment/mounts, and no network route to secret-bearing control-plane services. It does not cover secrets that users intentionally place in their workspace; document that distinction.

## 11. Missing or under-specified subsystems

| Gap | Required design decision |
|---|---|
| **Authentication/RBAC** | Session/token lifecycle, CSRF, MFA/recovery, OAuth state/PKCE, tenant invitation/removal, channel/group roles, and authorization for files, memory, approvals, schedules, connectors, and audit access. |
| **Tenant quotas/billing** | Requests, tokens/cost, concurrent runs/sandboxes, CPU time, storage, connector sync, notifications, and sub-agent budget; enforcement must precede expensive work. |
| **Security audit log** | Append-only records for login, identity linking, policy/role changes, approvals, secret use, connector action, file access, sandbox runs, and administrator action. Telemetry is not an audit log. |
| **Inbound authenticity/idempotency** | Verify webhook signatures and timestamp windows before parsing; unique `(installation, provider_delivery_id)`; deduplicate channel retries; MIME/payload/attachment limits. |
| **Prompt injection/data taint** | SAFE/FULL is insufficient. Track untrusted provenance, isolate connector content, require approval for data movement/effect escalation, and prevent `web_fetch` from becoming an exfiltration tool. |
| **Run lifecycle/reconciliation** | Explicit run states, cancellation, timeout, lost-worker detection, DLQ, operator replay, and repair of admitted-but-not-enqueued/outbox-not-published work. |
| **Health and draining** | Liveness/readiness/startup probes, dependency health, graceful SIGTERM, lease handling, SSE/channel drain, and deploy compatibility. |
| **Async DB capacity** | Pool size per process and global connection budget, PgBouncer mode, transaction boundaries, statement timeouts, slow-query/index monitoring, and no connections held across awaits to external systems. |
| **Data lifecycle/privacy** | Retention, tenant export/deletion, backup deletion limits, user departure, legal hold, embedding/object/event cleanup, and telemetry PII controls. |
| **Supply-chain/file safety** | Pinned images/dependencies, image signing/SBOM/scanning, MCP/tool provenance, upload malware/archive controls, and sandbox base-image patching. |
| **Operational ownership** | SLOs, RPO/RTO, alerting, incident/audit access, KMS/credential rotation, restore drills, and capacity/runbook ownership. |
| **Clock/time semantics** | UTC storage, tenant timezone, DST, misfire/catch-up rules, clock skew, and use of database time for schedule claims. |

The `last-match` permission algebra also needs deterministic ordering and administrative constraints: a user must not be able to create a later `allow` that overrides a tenant administrator's `deny`. Model policy layers separately (platform deny, tenant policy, user grants, one-time approval), with non-overridable higher-layer denies.

## 12. Prioritized top risks

Scores use likelihood and impact from 1 (low) to 5 (very high).

| Priority | Risk | L × I | Mitigation |
|---:|---|---:|---|
| 1 | Cross-tenant disclosure through omitted SQL/vector predicate, identity collision, Redis topic, or object key | 4 × 5 = **20** | RLS/FORCE RLS, composite tenant FKs, qualified UMO, tenant-scoped streams/keys, MinIO policy/presigned objects, isolation tests. |
| 2 | Container/host escape or noisy-neighbor DoS from tenant-controlled code | 4 × 5 = **20** | gVisor by multi-tenant P2, dedicated rootless sandbox nodes, strict orchestrator contract, aggregate quotas, egress isolation, one-use snapshots. |
| 3 | Duplicate destructive tool/external action after a mid-turn crash | 4 × 5 = **20** | Durable tool invocations, idempotency keys, effect reconciliation, fenced writes, versioned workspace commits; never blindly retry unknown effects. |
| 4 | Lost approvals/UI state and unrecoverable streams due to Redis pub/sub | 4 × 4 = **16** | PostgreSQL run-event journal/outbox, Redis Streams for acceleration, SSE cursors and authoritative replay. |
| 5 | Silent loss of scheduled work or duplicate notification around claim/send crashes | 4 × 4 = **16** | Unique schedule firings + outbox, at-least-once workers, provider idempotency/delivery state and reconciliation. |
| 6 | Split-brain session writers after Redis lock expiry/failover | 3 × 5 = **15** | Renewable leases plus monotonic fencing enforced by PostgreSQL and optimistic sequence checks. |
| 7 | Credential exposure through broad process privileges, logs, sandbox parameters, or workspace | 3 × 5 = **15** | KMS envelope encryption, workload separation, narrow decrypt grants, strict sandbox API, redaction/canary tests, audited use. |
| 8 | Queue/provider/sandbox exhaustion by one tenant | 4 × 4 = **16** | Fair queues, per-tenant budgets/concurrency, bounded admission, distributed provider guards, priority and load shedding. |
| 9 | Prompt injection escalates via “SAFE” fetch/read/write or hostile data retrieved in a FULL run | 4 × 4 = **16** | Data provenance/taint, narrower capabilities, destination-aware approval, egress controls, no origin-only trust assumption. |
| 10 | Data corruption/privacy ambiguity in team memory and shared workspaces | 3 × 4 = **12** | Visibility semantics, CAS/version history, atomic workspace snapshots, conflict resolution and audit. |
| 11 | Incorrect provider failover after partial streams/tool calls | 3 × 4 = **12** | Durable generation attempts, canonical IDs, abort/reset events, no effect until complete response, capability/policy checks. |
| 12 | Single-node PostgreSQL/Redis/MinIO or sandbox failure exceeds unstated durability expectations | 3 × 4 = **12** | Declare deployment profile and RPO/RTO, persistence/backups/restore drills, reconciliation, then HA based on SLO. |

## What to change before P0 coding

1. **Amend ADRs:** qualify ADR-003's key, replace ADR-005 pub/sub correctness path, add invocation state to ADR-006, require hardened isolation or trusted-only Docker in ADR-007, make ADR-009 data/effect-aware, and replace ADR-011's loss-prone at-most-once algorithm.
2. **Freeze core contracts:** tenant context; canonical identity/session tuple; run/message/event sequences; tool invocation/idempotency/effect states; approval lifecycle; provider attempt model; execution-backend contract.
3. **Add the minimum durable schema:** runs, run events, outbox, tool invocations, approval requests, inbound deliveries, schedule firings, delivery attempts, audit and quotas. Carry these IDs through SQLite P0 so PostgreSQL P1 is not a rewrite.
4. **Write the threat model and deployment profiles:** local/trusted Docker versus hostile multi-tenant gVisor; control-plane/sandbox network separation; secret trust boundaries; explicit RPO/RTO.
5. **Choose queue/runtime semantics:** select Celery or an asyncio-native worker model, define acknowledgement/visibility/cancellation/retry behavior, and separate queue/stream/lock correctness from evictable Redis caching.
6. **Move foundational telemetry/security earlier:** event journal, usage ledger, audit log, queue/provider/sandbox limits, health/draining, and failure-injection tests begin in P0/P1, not P6.

With those changes, the four-layer/narrow-waist architecture can remain intact while the implementation gains enforceable tenant isolation, recoverability, and a credible sandbox boundary.
