# Implementation Plan

> Task-level backlog for building Sherpa **v1** (self-hosted, single-user, Gmail → Action; ADR-022). Ordered by dependency. Each task is issue-sized: implement → `uv run pytest`/`ruff`/`mypy` green → commit → tick [`STATUS.md`](STATUS.md). Specs are frozen in [`contracts/`](contracts/); rules in [`../AGENTS.md`](../AGENTS.md).
>
> **How to use:** pick the lowest-numbered task whose deps are all done. Don't build [deferred](decisions.md) features. Keep the core narrow (docs/01).

## Legend
`refs` = contract/ADR to implement against · `AC` = acceptance criteria (what proves it done) · deps in parentheses.

---

## Phase 0 — Walking skeleton ✅ DONE
- **S0** backend skeleton (`app.main` health/readyz), green `pytest`+`ruff`+`mypy` via `uv`. ✅
- **S1** frontend scaffold (Vite+React+TS), infra `docker-compose`, CI, `AGENTS.md`, `.env.example`. ✅

The architecture is proven bootable. M1 makes the durable spine real end-to-end.

---

## Phase M1 — Durable spine (contracts → code), end-to-end with a mock provider

| # | Task | refs | AC |
|---|---|---|---|
| **1** | **Persistence base**: async SQLAlchemy engine+session, settings wiring; `/readyz` checks DB+Redis | tech-stack, config-and-secrets | `/readyz` returns ready only when DB+Redis reachable; unit test with a test DB or mocked ping |
| **2** | **Alembic + initial migration**: identity/tenant/session/message/parts tables (`tenant_id`+composite keys) | data-model §identity/sessions; ADR-015 | `alembic upgrade head` builds schema; round-trip insert/select test |
| **3** | **Event journal + outbox** tables + `append_event()` + outbox relay primitive | data-model §events; events-and-effects; ADR-016 | append→journal row + outbox row in one txn; ordered `seq` per run; test |
| **4** | **Redis Streams + SSE**: publish journal events to a per-session stream; `GET /sessions/{id}/events?cursor=` with `Last-Event-ID` catch-up (replay from journal, then live) | events-and-effects §delivery; api §5 | reconnect replays missed events then resumes; no gaps; integration test |
| **5** | **Effect/idempotency**: `invocations` table + helper (persist-before-effect, idempotency key, outcome `succeeded/failed/effect_unknown`) | events-and-effects §effects; ADR-017 | duplicate call with same key is a no-op; `effect_unknown` blocks retry; test |
| **6** | **Provider layer**: `Provider` interface + **mock/echo provider** emitting normalized stream events (text-delta/tool-call/finish) | doc 08; tech-stack | mock streams a scripted reply incl. a tool call; unit test on normalization |
| **7** | **Tool interface + registry**: `Tool` shape, 4 gates, output bounding (2000 lines/50 KB spill), starter read-only tools (`read/glob/grep` workspace, `ask_user`, `todo_write`, `memory_*`) | api §7; ADR-009; doc 05 | schema-validate before exec; oversized output spills; per-tool tests |
| **8** | **Core loop**: bounded dual loop, stop-reason gate, per-turn persistence, named termination, grace call; runs in arq worker consuming a run job; emits events (task 3/4) | doc 04; ADR-006 | mock run: prompt→tool_use→tool result→final answer; every exit named; crash after a turn resumes from last completed turn; test |
| **9** | **Durable prompt admission**: `POST /sessions/{id}/prompt` persists input (admitted_seq) + enqueues run → `202` + `run_id` | ADR-005; api §4 | input row exists before any model call; crash pre-run leaves it pending/retryable; test |
| **10** | **REST sessions/messages + auth** (single-user session auth) | api §2,§4 | login → create session → prompt → list messages; authz test |
| **11** | **Config + secrets**: full `Settings`, AEAD (AES-GCM) + KEK helper, log redaction + canary-secret test | config-and-secrets; ADR-019 | encrypt/decrypt round-trip; secret never appears in logs (canary test) |
| **12** | **Observability**: project `traces`/`generations` from the event stream; token/cost rollups on session; redacted audit receipts | doc 07; ADR-021 | each run yields a trace w/ model/tokens/cost; audit receipt distinct from debug events; test |
| **13** | **Frontend chat**: subscribe to SSE, render event vocabulary (text/reasoning-hidden/tool-call/approval placeholder) per `design-bright/chat-session.html` | design-bright; api §5 | `npm run build` green; live run renders; reconnect shows catch-up state |

**M1 exit:** web prompt → durable admission → worker runs bounded loop (mock provider + a read-only tool) → events stream to the chat UI → transcript persisted; kill-worker-mid-run resumes from the last completed turn; full `pytest`/`ruff`/`mypy` + `npm build` green.

---

## Phase M2 — Personal Inbox-to-Action (Gmail → candidate → todo → reminder)

| # | Task | refs | AC |
|---|---|---|---|
| **14** | **Connector base + Gmail read-only OAuth** (mode = open §5 default), token AEAD storage, connect/disconnect | connectors; config-and-secrets; ADR-019 | OAuth round-trip stores encrypted token; disconnect deletes; test with mocked Google |
| **15** | **Gmail incremental sync** → `connector_items` (cursor, dedupe) | data-model §provenance; doc 06 | re-sync is idempotent; new messages captured; test |
| **16** | **`CONNECTOR_ANALYSIS` extraction** (no-tool structured output) → `candidates` + provenance chain, uncertainty | ADR-009/018; api §8 | email → candidate with source link + confidence; no tools/workspace access; golden test on fixtures |
| **17** | **Candidate lifecycle + Inbox UI**: accept/edit/dismiss → `todos`; reshape `todo-board` into a **personal Candidate Inbox** | api §candidates; design-bright README (v1 reshape) | accept creates a todo linked to source; dismiss feedback recorded; UI build green |
| **18** | **Scheduler**: arq cron leader (`SET NX`), at-least-once firing + outbox; periodic sync+analysis job | ADR-017; doc 06 | advance-cursor-then-run; no double-fire; missed firing visible; test |
| **19** | **Notifications**: web inbox + outbound digest/reminder email; opt-in, quiet hours, cap, idempotent send; missed/failed/unknown surfaced | ADR-017; settings | reminder delivered once; quiet-hours respected; duplicate suppressed; honest failure state; test |
| **20** | **Permission engine + approval envelope** (frozen contract; `POST /permissions/{id}/resolve`) — gate the first external action (send email) | api §6; ADR-020/008 | send-email requires approval; once/session/always/reject; first-valid-response-wins; test |
| **21** | **Run/activity receipt + data controls UI**: "what Sherpa did on my behalf" (audit), export/delete imported data | ADR-021; cross-ui §4 | receipt lists reads/inferences/actions; export+delete work; UI build green |
| **22** | **Compaction**: threshold trigger, preserve head+recent, verify-shrank, no orphan tool results | doc 06 | long transcript compacts; verified smaller; loop unaffected; test |

**M2 exit:** connect Gmail → receive useful candidates with provenance → accept → get a reliable reminder; zero cross-tenant/unauthorized actions; effect-replay + cross-tenant isolation tests pass (see reviews M2/M3 gates).

---

## Phase M-tools — Agent tool surface (agent can drive every UI capability)

> Goal (ADR-023): whatever the user can see/do in the UI, the agent can drive via **tools**, without duplicating business logic. Design + templates + capability matrix: [`11-agent-tool-surface.md`](11-agent-tool-surface.md). Pattern per capability: extract a **service** → thin REST + thin Tool adapter → permission-gated → 4-layer tests → browser E2E (agent really does it).

| # | Task | refs | AC |
|---|---|---|---|
| **T1** | **ToolContext + CallerContext + service scaffolding** (foundation): change `Tool.execute(self, ctx, args)` per api.md §7; add `app/services/{context,errors}.py` (`CallerContext`, `ServiceError` taxonomy → HTTP + `ToolError`); loop injects `ToolContext` (tenant/user/session/run/invocation) into tools; migrate `echo/get_time/send_email`; registry/loop updated | api §7; docs/11 §3,§5,§6 | tools receive `ctx`; `ServiceError` maps to both an HTTP status and a bounded tool observation; existing loop/tool tests green |
| **T2** | **ALLOWED policy engine** (4th gate): `permissions.evaluate(ctx, tool, scope) -> allow\|ask\|deny` (v1 table: read/own-write→allow, external/destructive→ask, else deny; last-match; deny>ask>allow); wire before `_run_tool` dispatch | api §7.1; ADR-008/020; docs/11 §7 | deny → bounded refusal (no exec); ask → approval envelope (existing #20 path); allow → executes; unit tests per branch |
| **T3** | **Candidate tools** (vertical-slice exemplar): extract `services/candidates.py` (accept/edit/dismiss/list); thin `api/candidates.py`; tools `list_candidates`/`accept_candidate`/`edit_candidate`/`dismiss_candidate` (FULL) | docs/11 §9,§10; ADR-018 | agent can list + accept/edit/dismiss; REST behavior unchanged; loop test drives accept→todo; browser: "accept the Q3 candidate" works |
| **T4** | **Todo tools** (+ missing REST): `services/todos.py` (create/update/complete/list); **add `POST /todos`** (parity); tools `todo_write`/`list_todos`/`update_todo`/`complete_todo` | docs/11 §9,§10 | agent creates + completes/reschedules a todo; version-conflict surfaced; tests |
| **T5** | **Connector tools**: `services/connectors.py` (list/sync/pause/resume); tools `list_connectors`/`sync_connector`/`pause_connector`/`resume_connector` | doc 06; docs/11 §9 | agent triggers sync → **read+inference receipts appear autonomously** (fixes the "who created these" gap); tests + browser |
| **T6** | **Schedule tools (+ missing REST)**: `services/schedules.py` + **add `/schedules` REST** (create/list/cancel; today none); tools `create_schedule`/`list_schedules`/`cancel_schedule` | doc 06; contracts/api.md §4.4 | agent creates a reminder that later fires + delivers; tests + browser |
| **T7** | **Read + settings tools**: `list_notifications`, `list_activity`, `update_settings` (+ services extract) | docs/11 §9 | agent can read notifications/activity and change notification prefs; tests |
| **T8** | **Output spill + DisplayPayload**: implement `ToolOutputSpillReference` (api §7.2, spill to `TOOL_OUTPUT_ROOT/{invocation_id}.txt`); upgrade `ToolResult.return_display` to `DisplayPayload{format,content}` | api §7.2 | oversized tool output spills to file + head/tail summary + spill ref; tests |

**M-tools exit:** in the browser, the agent (via chat) can list/accept/edit/dismiss candidates, create/complete todos, trigger a Gmail sync (candidates appear), create a reminder, and read activity — each permission-gated (own-data writes allowed, `send_email` still asks); REST unchanged; full `pytest`/`ruff`/`mypy` + `npm build` green. **Not agent tools (by design):** approval resolution, untrusted-content tool access, raw delete of imported data. — ✅ **DONE (T1–T8 shipped + browser-verified; pytest 88).**
> **Post-v1 update:** core-memory and manual semantic-note tools/RAG have shipped. A source-backed document Knowledge product is a separate capability, not an extension of `memory_passages`; research/design is in [`research/knowledge-base.md`](research/knowledge-base.md), with no implementation approved yet.

---

## Phase P0–P2 — Session Library, session search, personal Drive (post-v1, ADR-029/030) — ✅ **COMPLETE (2026-07-23), awaiting unified owner acceptance**

> Owner-approved 2026-07-23: implement through **P2** without mid-review, then unified acceptance. Prereqs done: ADR-029/030 + contract additions (data-model §"Post-v1 contract additions", api.md §10). Each task: implement → backend gate (`alembic upgrade head` · `ruff` · `mypy app` · `pytest`) + frontend (`npm run build`/`lint`) → commit → tick STATUS. Playwright human-lane per phase.
>
> **Status:** P0 ✅ (Sessions page at **`/history`**) · P1 ✅ (English + Chinese search verified) · P2 ✅ (Drive at **`/workspace`**; migrations 0019–0022; Playwright human-lane desktop + 390px all-pass). See STATUS.md "Active build".

### P0 — Session Library: browse + state-specific resume (ADR-029 Phase A)

| # | Task | refs | AC |
|---|---|---|---|
| **P0.1** | **Schema `0019`**: persist `sessions.title` (+CHECK); add `runs.heartbeat_at/lease_expires_at/worker_id` + `ix_runs_live_lease`; models updated | data-model §post-v1; ADR-029 | `alembic upgrade head` clean; round-trip test |
| **P0.2** | **Activity + lease maintenance**: write `last_activity_at` on message admission + run state change; worker refreshes run lease (15s) and sets it stale on settle; title derived from first user message when unset | ADR-029 | lease fresh while running, expired after crash; `last_activity_at` advances; test |
| **P0.3** | **Session service + resume-state**: `services/sessions.py` browse (filters + keyset snapshot cursor, tenant+user), `resume_state` computation (idle/running/stale/approval/expired/interrupted/effect_unknown/failed/archived), `recover` (recheck/verified/new_run), `rename`, `timeline` around typed anchor | api §10.1; ADR-017 | each state computed truthfully; approval past expiry → `approval_expired`; unit tests per branch |
| **P0.4** | **REST**: `GET /sessions` (extended), `PATCH /sessions/{id}/title`, `GET /sessions/{id}/resume-state`, `GET /sessions/{id}/timeline`, `POST /sessions/{id}/recover`; authz tenant+user | api §10.1 | endpoints return contract shapes; authz test |
| **P0.5** | **Frontend Sessions page** at `/sessions`: list + filters, detail restore (transcript + run state + approvals + activity), state-specific action buttons, responsive desktop+390px | design-session-library | `npm run build`/`lint` green; renders |
| **P0.6** | **Verify P0**: backend gate + frontend; Playwright human lane desktop+mobile | AGENTS §2 | states/actions verified in browser |

### P1 — Session content search (ADR-029 Phase B)

| # | Task | refs | AC |
|---|---|---|---|
| **P1.1** | **Schema `0020`** (search): `session_search_entries` (+generated `fts`/`cjk_fts`, trigram index), `search_projection_jobs`, `search_projection_checkpoints`; enable `pg_trgm`; models | data-model §post-v1 | migration clean; indexes present; test |
| **P1.2** | **Projector**: same-txn `search_projection_jobs` on message/title/delete/redaction; worker consuming jobs **and** `event_journal` (tool/run/audit) → upsert/tombstone entries with typed anchors + CJK bigrams; durable checkpoint; idempotent rebuild command | ADR-029; ADR-016 | user message + title indexed; delete tombstones ≤1min; rebuild reproduces entries; tests |
| **P1.3** | **Search service + API**: fused FTS + CJK-bigram + trigram, weighted + recency, session-grouped + escaped snippet + typed anchor; wire into `GET /sessions?query=`; retention/redaction filter | api §10.1 | English + Chinese queries return grouped matches; deep-link anchor opens correct turn; no cross-user leak; tests |
| **P1.4** | **Frontend search**: search box + grouped matches + snippet + deep-link to turn in Sessions page; responsive | design-session-library | build/lint green; search + open verified |
| **P1.5** | **Verify P1**: backend gate + frontend; Playwright search + deep-link human lane | AGENTS §2 | verified in browser |

### P2 — Personal Drive foundation (ADR-030 Workspace W1)

| # | Task | refs | AC |
|---|---|---|---|
| **P2.1** | **Schema `0021` + store correctness**: `storage_accounts`, `storage_blobs` (immutable, ref-counted), `drive_nodes`, `drive_versions`; object store gains streaming + content-addressed put; reconciliation/GC worker | data-model §post-v1; ADR-030 | migration clean; blob dedupe by hash; GC removes only ref_count=0 past retention; tests |
| **P2.2** | **Drive service + API + tools**: folders, upload (reserve→write→commit→usage), download, rename/move, versions + restore-version, trash/restore, quota accounting, storage summary; REST §10.2; agent tools (purge human-only) | api §10.2; ADR-023 | quota never double-counts unchanged bytes; crash leaves no orphan/lost bytes (reconcile converges); trash restorable; tests |
| **P2.3** | **Files → Drive migration**: migrate `files` rows into personal Drive root, preserve version/hash, no object-key exposure; legacy `/files` behavior preserved during transition | ADR-030 | existing files visible + downloadable post-migration; test |
| **P2.4** | **Frontend Workspace/Drive**: Drive browser (folders, breadcrumbs, upload, versions, trash, search/sort) + storage management (Active/History/Trash/Reserved/Available) at `/workspace`; responsive | workspace-product-report | build/lint green; renders |
| **P2.5** | **Verify P2 + unified**: backend gate + frontend; Playwright drive human lane; final check before owner acceptance | AGENTS §2 | verified; ready for owner acceptance |

**P0–P2 exit:** dedicated Sessions library (browse + truthful state-specific resume + content search with exact deep-links) and a Personal Drive (folders, versions, trash, quota, storage management) with the files→Drive migration; agent parity via tools; zero cross-tenant/user leakage; full backend gate + frontend build/lint green; Playwright human-lane per phase.

---

## Phase CRON — 通用定时任务 cron / Schedules 增强 (roadmap #6, ADR-031) — ✅ **COMPLETE + two-lane verified (2026-07-23)**

> Turns Schedules from a reminder/digest-only feature into a general recurring scheduler ("crontab for the agent"): general recurrence (cron/interval/weekly/monthly/once), a new `agent_task` action that runs the agent with a saved prompt, and generalized delivery routing. Prereq done: **ADR-031 accepted + contract deltas written** (data-model «Recurring schedules / general cron», api §4.5, migration `0023`). Per task: backend gate (`alembic upgrade head` · `ruff` · `mypy app` · `pytest`) + frontend (`build`/`lint`) → commit → tick STATUS. Two-lane Playwright per phase.
>
> **Status:** CRON.0–CRON.6 ✅. Cadence engine (croniter, DST-correct) · idempotent `agent_task` runs (slot-key admission, concurrency cap) · result delivery + firing settle · generalized service/REST/tools · scheduler-console UI at `/reminders`. Playwright human lane (create agent_task → Run now → history → pause/resume → 390px) + agent lane (real model created a cron task via `create_scheduled_task`) both pass. Migration `0023`.

| # | Task | refs | AC |
|---|---|---|---|
| **CRON.0** | **ADR-031 accept + contract**: finalize ADR-031; write frozen-contract deltas — relax `ck_schedules_kind` (+`agent_task`), add cadence cols (`cadence_kind`/`cron_expr`/`interval_seconds`/optional `rrule`) + `prompt`, adjust `ck_schedules_kind_target`, expand `ck_schedules_delivery_channel` (web/digest_email/email/qq), add `schedule_firings.run_id`; api.md §4.4 (create/edit general schedule, `run_now`, run history). Add `croniter` (or equiv) dep + `uv.lock`. | ADR-031; data-model; api §4.4; AGENTS §1 | contract updated before code; ADR marked accepted; dep pinned |
| **CRON.1** | **Schema `00xx` + recurrence engine**: migration for the schedules/firings changes + models; replace daily-only `scheduler/tick.py:_advance` with cadence-aware next-occurrence (cron via croniter, interval step, weekly/monthly calendar, once→completed), DST-correct via IANA tz; **guardrails**: min-frequency floor (deployment-config, e.g. ≥60s), cron-expression validation. | ADR-031; ADR-017 | migration clean; next-occurrence correct incl. DST + weekly/monthly; sub-floor cron rejected; unit tests per cadence |
| **CRON.2** | **`agent_task` execution + cost guardrails**: on fire, enqueue a `run_kind='scheduled_task'` run seeded with the saved prompt (dedicated scheduled session or per-fire session, configurable), **using the firing slot key as the idempotency key** (worker replay never double-runs); reuse the bounded loop/events/trace; external side effects still approval-gated; per-user concurrency + frequency caps. Deliver the result via firing→delivery to the target channel. | ADR-031; ADR-016/017/019/020/021 | agent_task runs exactly once per slot (idempotent); crash/replay no double-run; approval-gated effect pauses; caps enforced; tests |
| **CRON.3** | **Delivery routing generalization**: firing → route by `delivery_channel` to the channels/notifications layer (web inbox / email / qq); agent_task result body from the run output (not static text); honest `failed`/`missed`/`needs_reconciliation` settle + inbox visibility. | ADR-031; ADR-026/027 | each channel delivers; failures settle honestly + visible; test |
| **CRON.4** | **Service + REST + tools (generalized)**: `services/schedules.create_schedule` accepts cadence + `agent_task` + prompt + channel with validation; REST create/edit/`run_now`/history; generalize `schedule_*` agent tools so the agent creates recurring tasks (incl. `agent_task`); own-tenant writes allowed, no external grant. | api §4.4; ADR-023 | service/REST/tool three-way parity; validation errors typed; cross-user isolation; tests |
| **CRON.5** | **Frontend scheduler console**: upgrade the Schedules page (SPA route stays `/reminders`) from a reminder list into a scheduler console — new task (cadence picker + action: reminder / digest / **run agent task** + prompt + delivery channel), next-run time, **run history** (per-slot: what ran / success-fail / output), pause/resume, **Run now**; responsive desktop + 390px. | design; AGENTS §2 | build/lint green; renders; controls work |
| **CRON.6** | **Verify CRON**: backend gate + frontend; Playwright two lanes — human (create a cron `agent_task` via UI, Run now, see it fire + deliver) + agent (create via `schedule_*` tool). | AGENTS §2 | verified both lanes; ready for owner acceptance |

**CRON exit:** Schedules is a general recurring scheduler — cron/interval/weekly/monthly recurrence, a new `agent_task` action that autonomously runs the agent on schedule and delivers the result to web/email/qq, run history + Run-now, agent parity via tools; firing stays exactly-once/idempotent; external actions still approval-gated; frequency/concurrency guardrails; zero cross-tenant/user leakage; full gate + build/lint green; Playwright both lanes.

**Explicitly out of scope (later ADR):** multi-step workflow/DAG orchestration, webhook/event triggers, cross-task dependency chains, long-running/resident services.

---

## Phase APPROVALS — 待审批入口 + 可配置预授权 grants (ADR-034) — ✅ **COMPLETE + two-lane verified (2026-07-24)**

> Two features that make background/scheduled external actions both safe and automatable (driven by the scheduled-email use case): **(A)** a standalone pending-approvals surface so background/scheduled approvals can be resolved (today the nonce is delivered only via chat SSE, so they can't be); **(B)** configurable pre-authorization **grants** (e.g., an email-recipient allowlist) so the loop auto-allows matching actions without an approval. Prereq: **ADR-034 accepted + contract deltas** (data-model `permission_grants`, api §4.7 + grants). Per task: backend gate + frontend → commit → tick STATUS. Two-lane Playwright per phase. Security invariants from ADR-019/020/021 are non-negotiable (see ADR-034).
>
> **Status:** APR.0–APR.V ✅. Web resolution nonce-optional (session+CSRF+actor+binding; non-web keeps nonce) · Approvals page at `/approvals` (resolves background approvals, sidebar pending badge) · `permission_grants` (migration `0024`) + per-tool matcher + loop auto-allow (records effect + `auto_approved_by_grant` receipt) · `always` persists a grant · owner-only grants REST/UI. **Playwright two lanes pass with the real model:** a scheduled email to a whitelisted recipient **auto-sent** (no approval), a non-whitelisted one **asked** and was **approved from the `/approvals` UI (no nonce)**; add/remove trusted recipient; 390px no overflow.

| # | Task | refs | AC |
|---|---|---|---|
| **APR.0** | **ADR-034 + contracts**: finalize ADR-034; write `permission_grants` DDL (data-model, migration `0024`); api.md §4.7 — web resolution `nonce` optional (session+CSRF+authorized_actor+binding), and a new grants section (`GET/POST/DELETE /grants`). | ADR-034; ADR-020; data-model; api §4.7 | contract before code; ADR accepted |
| **APR.A1** | **Web resolution nonce-optional**: `permissions/service.resolve_approval` + `api/permissions.resolve` accept web owner resolution without a nonce (verify session actor==authorized_decider + CSRF + full binding); non-web channels still require nonce. `GET /permissions` unchanged (already lists pending). | ADR-034; api §4.7 | background approval resolvable via web w/o nonce; channel path unchanged; authz test |
| **APR.A2** | **Approvals UI**: standalone Approvals page at `/approvals` (list pending: tool, preview, source run/session, expiry; Approve `allow_once`/`always`, Reject → `/permissions/{id}/resolve`); sidebar entry + pending count; wire InboxView approvals to actually resolve. Responsive. | ADR-034; design | build/lint green; resolves a real pending approval in browser |
| **APR.B1** | **Grants schema + matcher + loop**: `permission_grants` table + model (migration `0024`); a per-tool matcher registry (send_email recipient allowlist first); `core/loop` checks matching grants on `ask` → auto-allow (record effect + audit receipt `auto_approved_by_grant`, no envelope, no pause). Grants service (owner-only). | ADR-034; core/loop; ADR-021 | matched action auto-executes + audited; unmatched still asks; no cross-user leak; tests |
| **APR.B2** | **`always` persists a grant**: resolving with `always` derives + persists a grant from the action scope (send_email → add recipient to allowlist); subsequent matching actions auto-allow. | ADR-034 | after `always`, the same recipient auto-sends next time; test |
| **APR.B3** | **Grants REST + UI**: `GET/POST/DELETE /grants` (owner + CSRF; NOT an agent tool); grants management UI (e.g., Settings: "Trusted email recipients" + general grants list, add/remove). Responsive. | ADR-034; api §4.7 | build/lint green; add/remove a grant in browser; agent has no grant tool |
| **APR.V** | **Verify APPROVALS**: backend gate + frontend; Playwright two lanes — human (approve a background approval; add an email allowlist; a scheduled email to a whitelisted address auto-sends, non-whitelisted still asks) + agent (scheduled_task to a whitelisted recipient auto-executes; agent cannot self-grant). | AGENTS §2 | verified both lanes; ready for owner acceptance |

**APPROVALS exit:** background/scheduled approvals are resolvable from a real UI; owner-configured grants auto-allow matching external actions (with audit) while everything else still asks; `always` persists a grant; non-web channels keep the nonce; zero cross-user/tenant leakage; full gate + build/lint green; Playwright both lanes.

**Explicitly out of scope (later ADR):** wildcard/regex grants, cross-user shared grants, time-window/quota-limited grants, per-session `always` persistence, agent-created grants.

---

## Phase SCHED-FIX — 定时任务修复 (ADR-031 amendment) — ✅ **COMPLETE + verified (2026-07-24)**

> Fixes found by owner testing the CRON phase + the R-SCHED-CONTEXT research: (P1) each cron firing must be a **fresh isolated context** — the shared per-schedule session accumulated history and caused a provider **400** on the 2nd run + polluted Chat/Sessions; (P2) **Run Now latency** — it waits up to 30s for the periodic tick; (P3) schedules have **no edit / no real delete** in the UI. Per task: backend gate + frontend → commit → tick STATUS. Playwright verify with the owner's real email.
>
> **Status:** SF.0–SF.V ✅. Fresh per-firing session (`scope_type='scheduled_task'`, excluded from Session Library browse+search) · Run Now enqueues an immediate one-shot dispatch job · edit (PATCH, revalidate+recompute) + hard delete. **Verified with the owner's real email:** run #1 and run #2 **both delivered (no 400 — fresh context)**, dispatch latency **~2s** (was ~30s), two runs isolated, scheduled runs **absent from Sessions** (API + UI), edit + delete work.

| # | Task | refs | AC |
|---|---|---|---|
| **SF.0** | **Docs**: amend ADR-031 (per-firing fresh session, Session Library exclusion, run-now immediate, edit/delete); record P4 pre-permission-hint simple design (deferred); this task table. | ADR-031; research/scheduled-permission-prehint | ADR amended; P4 noted |
| **SF.1** | **Fresh per-firing session (fixes the 400 + pollution)**: `_ensure_session` keyed per firing slot (`scheduled:{id}:{firing_key}`, lookup-then-create for idempotency), `scope_type='scheduled_task'`; exclude `scope_type='scheduled_task'` from Session Library browse (+ search). Each firing loads no prior transcript. Firing history still opens the run. | ADR-031 amend; R-SCHED-CONTEXT | 2nd run no 400 (fresh context); scheduled runs absent from Sessions; firing→run transcript still viewable; tests |
| **SF.2** | **Run Now immediate dispatch**: run-now enqueues an idempotent agent_task dispatch job (no ~30s tick wait); periodic tick still a safety net. | ADR-031 amend | run-now dispatches within ~1–2s; no double-run; test |
| **SF.3** | **Edit + hard delete**: `PATCH /schedules/{id}` (edit name/prompt/cadence/channel; revalidate cadence + recompute `next_fire_at`; optimistic `if_version`) + `DELETE /schedules/{id}` (hard: firings then schedule). Frontend: edit form + Delete button (distinct from Cancel). | api §4.5 | edit persists + reschedules; delete removes it; version conflict → 409; build/lint green; tests |
| **SF.V** | **Verify SCHED-FIX**: backend gate + frontend; Playwright with the owner's real email — a schedule runs twice with fresh context (no 400), Run Now is fast, edit + delete work, scheduled runs not in the Sessions library. | AGENTS §2 | verified; ready for owner acceptance |

**SCHED-FIX exit:** each cron firing runs in a fresh isolated session (no cross-run context, no 400), hidden from Chat/Sessions but inspectable via the schedule's firing history; Run Now dispatches immediately; schedules are editable + deletable in the UI; full gate + build/lint green; Playwright verified.

**Deferred:** P4 (create-time permission pre-hint) — simple design recorded in `research/scheduled-permission-prehint.md`; ADR-033 (observability) is the next phase.

---

## Phase OBS-A — Agent observability, Phase A: instrument (no backend) (ADR-033) — **ACCEPTED (2026-07-24)**

> Add a thin OpenTelemetry `gen_ai` span layer over the bounded loop — a **derived, ephemeral diagnostic surface** correlated to the ADR-016 journal (never a source of truth). Closes STATUS **item 0** (no per-LLM-call record: real assembled-input/token/finish_reason visibility — the exact blind spot hit debugging the memory bug). **Phase A = instrument only, no new infra** (Phoenix backend = Phase B; evals = Phase C, both deferred). Owner-locked decisions (ADR-033): Phoenix backend / span + durable event / content off by default / **hand-rolled** spans / independent of ADR-032. Contracts already written (config `OTEL_*`, events `model.request`/`model.response`). Per task: backend gate → commit → tick STATUS.

| # | Task | refs | AC | status |
|---|---|---|---|---|
| **OBS.0** | **Deps + config**: add `opentelemetry-api` + `opentelemetry-sdk` (+ optional `opentelemetry-exporter-otlp` for Phase B) to `pyproject`; add the frozen `OTEL_*` fields to `app/config.py` (`otel_enabled=False`, `otel_exporter_otlp_endpoint`, `otel_capture_message_content=False`, `otel_traces_sampler="always_on"`); `uv.lock`. | config-and-secrets §OTEL; ADR-033 | deps pinned; config matches the frozen contract; gate green | ✅ `cc642a2` |
| **OBS.1** | **OTel bootstrap + `gen_ai` wrapper**: `app/observability/otel.py` — build a `TracerProvider` gated by `OTEL_ENABLED` (exporter: OTLP when endpoint set, else in-memory/console; sampler `always_on`); a single `genai` wrapper module centralizing every `gen_ai.*`/`agent.*` attribute name (isolate Development-status semconv churn). **No-op + zero overhead when disabled.** Init from web + worker startup. | ADR-033 §护栏 | disabled → no tracer/no spans; enabled → active; wrapper unit test | ✅ `859fafc` |
| **OBS.2** | **Instrument the loop** (`core/loop.py`): a root `invoke_agent` span per `execute_run` (`run_id`/`session_id`, `agent.loop_count`, `agent.stop_reason`; `total_cost_usd` deferred — no price table); a `chat` span per `provider.stream` call (provider/model, `gen_ai.response.finish_reasons`=`stop_reason`, latency); a child `execute_tool` span per tool (`gen_ai.tool.name`/`call.id`; `status=ERROR`+`success=false` when the tool returns an error observation; approval-gated → success unset). Low-cardinality span names; values in attributes. | ADR-033 §决策1 | span tree = `invoke_agent > chat / execute_tool`; tool error → ERROR span; test | ✅ `9c6e75d` |
| **OBS.3** | **Real per-call tokens + generation record**: provider requests + parses usage (`stream_options={"include_usage": true}`), carries input/output tokens on `Finish` (extend the event); the loop puts real tokens on the `chat` span and writes a `generations` record per model call (real tokens replace the `projection.py` `chars/4` estimate for real providers; mock still deterministic); bounded, redacted `model.request`/`model.response` debug journal events (`input_digest` sha-256, **no content**; migration 0025 reconciles `durability='debug'`). Closes item 0. | ADR-033 §决策3; events §2.7; data-model `generations` | real tokens on span + generation; digest event bounded/no content; test | ✅ `1069cdf` |
| **OBS.4** | **Deterministic tests**: `InMemorySpanExporter` + mock provider — assert the span-tree structure, `gen_ai`/`agent` attributes, `finish_reason`, error status, and token attrs. Assert **content capture OFF** by default (no prompt/tool text in spans). | AGENTS §2; 铁律 | tests green; no content leak by default | ✅ `152386a` (in `test_obs_loop.py`) |
| **OBS.V** | **Verify OBS-A**: full backend gate; a run with `OTEL_ENABLED=true` + a console/in-memory exporter shows `invoke_agent > chat > execute_tool` with **real tokens + finish_reason** (item 0 closed). No Playwright (no user-facing UI in Phase A). | AGENTS §2 | verified; STATUS item 0 marked closed | ✅ gate 187 green; manual in-memory run prints the tree + real tokens + generations. Real-provider live-token check blocked by the proxy's own `No connected db` (proxy-side) — usage parsing unit-tested. |

**OBS-A exit:** every LLM call + tool execution is a `gen_ai` span with real tokens/finish_reason/latency, correlated to the journal; the journal stays the source of truth; content off by default, no secrets in spans; disabled = zero overhead; deterministic `InMemorySpanExporter` tests; STATUS item 0 closed. **Phase B** (optional Phoenix container reusing Postgres + OTLP exporter) and **Phase C** (evals/flywheel, evidence-gated) are deferred.

---

## Phase OBS-LOG — Agent observability, the Logs pillar: make stdout useful by default (ADR-033) — ✅ **COMPLETE + verified (2026-07-25)**

> OBS-A strengthened **traces** (DB / OTel spans); this closes the **Logs** pillar gap so default stdout is diagnostic **independent of OTEL and any UI** — directly answering the owner's "400 bad request, logs too terse" + "I can't see it by default".

| # | Task | refs | AC | status |
|---|---|---|---|---|
| **LOG.1** | **Provider errors surfaced**: `openai_compatible` reads the body on non-2xx (`await resp.aread()`) and raises `ProviderError(status_code, redacted bounded body)`; the worker's `run_job` stops swallowing the exception — logs a structured ERROR (run_id/provider_model/error_type/redacted detail/traceback) and threads the detail into `_settle_failed` → recorded on the `run.settled` journal event. | ADR-033 Logs; redaction §3.5 | provider raises with status+redacted body; failed run journals the reason; tests | ✅ `f382ead` |
| **LOG.2** | **Per-LLM-call log line**: one structured INFO `llm call` per `provider.stream` (provider/model/`input_tok`/`output_tok`/finish_reason/tool_calls/latency_ms; run/session-correlated). **Unconditional** — NOT gated on OTEL. No prompt/response content. | ADR-033 Logs | one line/call with real usage, default stdout; test | ✅ `84382d4` |
| **LOG.3** | **Per-tool-execution log line**: one `tool call` per tool (tool/call_id/outcome/latency_ms), at WARNING on error. | ADR-033 Logs | one line/tool; error → WARNING; test | ✅ `84382d4` |
| **LOG.4** | **Log↔trace correlation**: JsonFormatter injects `trace_id`/`span_id` from the active OTel span when tracing is on; nothing added when off. | ADR-033; docs/07 | trace ids present inside a span, absent when disabled; test | ✅ `84382d4` |
| **LOG.V** | **Verify OBS-LOG**: full gate; live docker run with **OTEL off (default)** shows `llm call` with real tokens on stdout + a bogus-model run shows `run failed` with the real provider reason. | AGENTS §2 | verified live | ✅ gate **192** green; live: `llm call ... input_tok=145 output_tok=2 finish=stop latency_ms=2262`; error: `run failed ... status=400 body={...Invalid model name...}` |

**OBS-LOG exit:** default stdout carries one structured line per LLM call (real tokens/finish/latency) and per tool (outcome/latency), provider HTTP failures show their real reason in both the log and the `run.settled` journal event, and logs correlate to the trace when OTEL is on — all with content off + secrets redacted, zero dependency on a UI or a trace backend.

---

## Phase OBS-B — full-prompt content capture into spans + self-hosted Phoenix UI (ADR-033 Phase B) — ✅ **COMPLETE + verified (2026-07-25)**

> The owner's chosen way to "see every LLM call's full assembled prompt (system + memory + tool list + all messages) + response". The core work — capturing the full assembled input + response at the single provider-call boundary, redacted + bounded — is the same one ADR-035 would need; here it lands as **OpenInference span attributes** exported to a **self-hosted Phoenix** container (reuses Postgres), so the owner reads it in Phoenix's waterfall/search UI **without a bespoke inspector**. Gated by `otel_capture_message_content` (default false), independent of the default metadata-only spans. Research: all 8 products capture at this same boundary (`files/debug-ui-research.md`).

| # | Task | refs | AC |
|---|---|---|---|
| **OBSB.0** | **deps + config**: add `opentelemetry-exporter-otlp-proto-grpc` to `pyproject` (`uv.lock`); verify `otel.py`'s OTLP branch loads it. `otel_exporter_otlp_endpoint` + `otel_capture_message_content` already exist — no new config. | ADR-033; config | dep pinned; OTLP exporter imports; gate green |
| **OBSB.1** | **Content capture into spans** (gated by `otel_capture_message_content`): on the `chat` span, attach the full assembled input (system+memory+transcript messages) + tool schemas + response as **OpenInference attributes** — `llm.input_messages.[N].message.role/content`, `llm.output_messages.[N]…`, `llm.tools.[N].tool.json_schema`, `input.value`/`output.value`. Structured parts (tool args/schemas) pass through `security/redaction` (key-masking); every field size-capped with a `…[truncated]` marker. `execute_tool` span gets tool args/result (gated). Off = metadata-only (today). Centralize OpenInference attr names in the `genai` wrapper. | ADR-033 §决策4; redaction §3.5 | flag on → span carries redacted, bounded messages/tools/response; off → none; secret-named fields masked; test |
| **OBSB.2** | **OpenInference span kind**: tag `invoke_agent`/`chat`/`execute_tool` with `openinference.span.kind` = `AGENT`/`LLM`/`TOOL` so Phoenix classifies + renders them correctly. | OpenInference semconv | correct span kinds; test asserts attr |
| **OBSB.3** | **Phoenix container**: add an optional `phoenix` service to `infra/docker-compose.yml` behind a **compose profile** (not started with the core stack), `PHOENIX_SQL_DATABASE_URL` → existing Postgres (separate schema/db), OTLP receiver `4317`; document `OTEL_ENABLED=true` + `OTEL_CAPTURE_MESSAGE_CONTENT=true` + `OTEL_EXPORTER_OTLP_ENDPOINT=http://phoenix:4317`. Default off. | ADR-033 Phase B | phoenix starts via profile; reuses Postgres; core stack unaffected |
| **OBSB.4** | **Deterministic tests**: `InMemorySpanExporter` — content on → span has redacted/bounded `input_messages`/`output_messages`/`tools` + span kind; off → none; secret masked; size-cap truncation. Mock provider. | AGENTS §2; 铁律 | tests green; no secret leak |
| **OBSB.V** | **Verify OBS-B**: full gate; bring up Phoenix (profile) + `OTEL_ENABLED` + `OTEL_CAPTURE_MESSAGE_CONTENT` + endpoint; run a real chat; in the **Phoenix UI** see the full assembled prompt (system+memory+tools+messages) + response + tokens/latency in the `invoke_agent > chat > execute_tool` waterfall. Screenshot + UX note. | AGENTS §2 | verified in Phoenix UI |

**OBS-B exit:** with `otel_capture_message_content=true` + Phoenix up, the owner sees every LLM call's full assembled prompt + response + tool steps in Phoenix's waterfall/search UI; off or no-Phoenix = metadata-only spans, zero content; content redacted (structured) + size-capped + secret-free; the core stack runs unaffected (Phoenix is an optional profile). **Supersedes the bespoke inspector** (Phase OBS-DEBUG / ADR-035 deferred).

**Done:** OBSB.0 `ef142ba` · OBSB.1+2+4 `0a9e5cd` · OBSB.3 `2b592cd`. **Verified live end-to-end (OBSB.V):** brought up Phoenix via `--profile observability` (reusing Postgres, `phoenix` schema), restarted web+worker with `OTEL_ENABLED=true` + `OTEL_CAPTURE_MESSAGE_CONTENT=true` + `OTEL_EXPORTER_OTLP_ENDPOINT=http://phoenix:4317`, ran a real `claude-sonnet-4.6` tool-using chat → Phoenix received the trace: `AGENT invoke_agent > LLM chat ×2 / TOOL execute_tool` (correct OpenInference span kinds), the `chat` span's `llm.input_messages` = the full assembled prompt (`system` + `user`, system carries the prompt + memory slot), `llm.tools` = the tool schemas, `llm.output_messages` = the assistant reply, and the flattened `input.value` contains the user's message; 1 trace with 3 linked child spans (waterfall). UI at http://localhost:6006. Gate green (195).

---

## Phase OBS-DEBUG — built-in inspector (ADR-035) — **DEFERRED (owner chose the Phoenix path OBS-B, 2026-07-25; kept as a fallback for a future embedded / container-free inspector)**

> A dev-only, owner-only inspector that shows, for a run, the **full assembled input of every LLM call** (system prompt + injected memory + tool schemas + all user/assistant/tool messages) + full response + tokens/latency + tool steps — Microsoft-Copilot-`/debug` depth, but **short-retention, off by default, redacted, owner-only**. Extends ADR-033 §决策3 with a built-in content store so the full prompt is inspectable **without standing up Phoenix**. Research: 8 products all capture at the single provider-call boundary (`files/debug-ui-research.md`). Content lives in a new `llm_call_debug` table (NOT the immutable journal — PII/ephemeral, TTL'd + deletable), gated by `debug_capture_enabled` (default false), independent of OTEL.

| # | Task | refs | AC |
|---|---|---|---|
| **DBG.0** | **ADR-035 accepted + contracts frozen + migration**: config (`debug_capture_enabled=false`, `debug_capture_ttl_hours=48`, `debug_capture_max_calls_per_session=200`, `debug_capture_max_bytes`), data-model (`llm_call_debug`: tenant_id+composite keys, `request_redacted`/`response_redacted` jsonb, tokens/finish/latency, `created_at`/`expires_at`, GC index), api (owner-only `/debug` section). events §2.7 **unchanged** (still digest-only). Migration (next free head). | ADR-035; contracts | contracts merged; migration head advances; gate green |
| **DBG.1** | **Capture at the provider-call boundary** (`core/loop.py`, gated by `debug_capture_enabled`): write one **redacted, bounded** `llm_call_debug` row per chat call — request (system+memory+transcript messages + tool schemas) + response (text/tool_calls/finish) + tokens + latency; `expires_at = now()+ttl`. Reuse the `model.request` site; content via `security/redaction`; per-field size cap + `…[truncated]`. **Off = zero rows / zero overhead.** | ADR-035; redaction §3.5 | flag on → one redacted bounded row/call; off → none; secret masked; test |
| **DBG.2** | **TTL GC + per-session cap**: background job (reuse the Drive-maintenance cron) deletes `expires_at < now()` rows; enforce `debug_capture_max_calls_per_session` (ring-buffer: drop oldest). | ADR-035 | expired rows purged; cap enforced; test |
| **DBG.3** | **Owner-only REST**: `GET /debug/sessions/{id}/llm-calls` + `GET /debug/runs/{id}/llm-calls` (list + detail of captured snapshots). Owner-only guard; empty when capture off. | api §debug; ADR-035 | owner-only; returns captured calls; 403 for non-owner; test |
| **DBG.4** | **Dev-only inspector UI** (**SPA route `/inspector`**, avoids API prefix `/debug`): per-run list of LLM calls; per-call expandable inspector — **System / Memory (highlighted) / Tools (schemas) / Messages (role+content) / Response / tokens / latency / tool-step waterfall**. Nav entry owner/dev-gated. Update capability matrix (`11-agent-tool-surface.md §9` UI column). | design-bright; ADR-035 | build/lint green; renders; matrix UI cell filled |
| **DBG.5** | **Deterministic tests**: capture off = no rows; on = redacted bounded rows (secret masked, size-cap truncation); GC deletes expired + cap enforced; API owner-only; content redaction. Mock provider. | AGENTS §2; 铁律 | tests green; no secret/content leak beyond redaction |
| **DBG.V** | **Verify OBS-DEBUG**: full backend gate; restart the stack; **two-lane Playwright** — agent lane (chat with capture on → `/inspector` shows the assembled prompt incl. system+memory+tools+messages+response); human lane (click through `/inspector`, UX acceptance review). Use `dazhongguo97@gmail.com` if a tool triggers email. | AGENTS §2; verification memory | verified two-lane; UX notes recorded |

**OBS-DEBUG exit:** with `debug_capture_enabled=true`, the owner can open `/inspector` and see, per run, every LLM call's full assembled prompt (system + memory + tools + messages) + response + tokens/latency + tool steps; off = zero capture/overhead; snapshots are redacted, size-capped, owner-only, TTL-GC'd, and never touch the immutable journal; two-lane Playwright verified. Deferred: span content capture (ADR-033 OTEL path), prompt playground/replay, cross-user debug sharing.

---

## Phase W2a — Workspace Projects (ADR-037) — ✅ **DESIGN/CONTRACT-FIRST + IMPLEMENTATION COMPLETE (2026-07-27)**

> Land the ADR-037 Workspace product model. The **contracts + design-first** batch (W2a-DESIGN.0) froze the W2a shape (**blank / template / archive** projects — **no GitHub**) and shipped the production-design static draft. The **implementation** phase (W2a.1…W2a.V, below) then shipped the production backend (migration 0028; `services/projects.py` + `services/archive.py` + `services/projects_import.py` durable job; REST §10.5; `project_*` agent tools; worker recovery tick) and the production `/work/projects` UI, two-lane verified. Owner-approved decisions: Workspace is the umbrella (Projects + Drive siblings); order **W2a→W2b→W3→W4**; **W3** mounts only a one-time scratch copy (never the source of truth) with the ADR-025 revision gated on an isolated review + `docker.sock`/multi-user hardening **before** W3 starts. **Non-goals (later ADRs):** GitHub one-time import (W2b, currently `POST /projects/imports kind=github → 501`); task working copy + scratch-copy sandbox + change review (W3); GitHub sync/push/PR (W4).

| # | Task | refs | AC | status |
|---|---|---|---|---|
| **W2a-DESIGN.0** | **ADR-037 accepted + contracts frozen + static draft**: ADR-037 (decisions.md) + decisions-log row; data-model (`projects`, `project_snapshots`, `project_snapshot_entries`, `sessions.project_id` immutable binding — canonical vs derived, immutable snapshots, `tenant_id` composite keys); api §10.5 (Projects REST + schemas + Open-in-Chat); events §2.9 (`project.lifecycle` + idempotency/outbox); config (`PROJECT_*` + §1.5 security boundary); capability matrix §9 (Projects rows, **UI ⬜**); W2a static draft `design-workspace/` (Quiet Work, desktop + 390px). **No code / no migration / nav not exposed.** | ADR-037; contracts | contracts merged; static draft renders desktop + 390px; docs validate; no production code/migration/nav | ✅ this batch |
| W2a.1 | **✅ (impl)** Schema + `services/projects.py`: `projects`/`project_snapshots`/`project_snapshot_entries` migration (0028); blank/template create + immutable-snapshot store reusing ADR-030 `storage_blobs`; `sessions.project_id` binding. Adds durable `project_import_jobs`. | data-model §Projects; ADR-037 | migration head 0028; blob-shared snapshots; binding immutable; tests | ✅ done |
| W2a.2 | **✅ (impl)** Archive import durable job (`services/archive.py` + `services/projects_import.py`): isolated bounded in-memory expand (size/count/ratio/depth/path-safety) → initial immutable snapshot → atomic activate; enqueue + recovery tick + idempotency (`import:<project_id>`); `project.lifecycle` via job stage/reason. | events §2.9; config §1.5 | crash/replay safe; unsafe archive rejected (failed, no snapshot); tests | ✅ done |
| W2a.3 | **✅ (impl)** REST §10.5 (`api/projects.py`) + W2a tools (`project_list`/`create`/`tree`/`read`; github→501); Open-in-Chat (`POST /projects/{id}/chats`, `GET /sessions/{id}/project-context`). ADR-023 dual adapter. | api §10.5; ADR-023 | REST+tools green; CSRF; 404/409/413/422/501/507; tests | ✅ done |
| W2a.4 | **✅ (impl)** Frontend `/work/projects` (`ProjectsView.tsx`): Projects list, new-project (blank/template/archive), detail (read-only tree + snapshots + activity), Open-in-Chat binding + chat project chip; Sidebar nav; Vite proxy. Capability-matrix UI cells → ✅. | design-workspace; matrix §9 | build/lint green; renders; matrix UI ✅ | ✅ done |
| W2a.V | **✅ (impl)** Verify: backend gate (alembic 0028 / ruff / mypy / pytest 243) + two-lane Playwright (agent: `project_*` tools; human: create blank/template + safe & unsafe archive import, detail, Open-in-Chat immutable binding, GitHub→501, 390px no overflow) + UX pass. | AGENTS §2 | verified two-lane; UX notes | ✅ done |

**W2a-DESIGN exit:** ADR-037 accepted; frozen contract deltas (data-model/api §10.5/events §2.9/config) + capability-matrix rows (UI ⬜) + the production static draft (`design-workspace/`, desktop + 390px) are in; **no production code, no migration, no exposed Projects navigation.** W2a implementation (W2a.1…W2a.V) starts after owner review. **Non-goals (each a later ADR):** GitHub one-time import (W2b); task working copy + scratch-copy sandbox + change review (W3, gated on the ADR-025 revision + `docker.sock`/multi-user isolation hardening); GitHub sync/push/PR (W4).

---

## Phase W2b-DESIGN — Workspace Projects GitHub one-time import: 研究收敛 + 契约与设计先行 (ADR-038) — ✅ **DESIGN/CONTRACT-FIRST COMPLETE (2026-07-27); production impl SHIPPED (2026-07-27, schema 0029, two-lane verified)**

> The ADR-037-预告 W2b "后续 ADR". **Contract + design first only** — no production code, no migration, no exposed W2b navigation. W2b = GitHub **one-time** import: select repo + ref (branch/tag/commit) → bounded **archive (tarball) fetch** of that ref's tree (no git history; reuses the W2a in-memory safe expander — no `git clone`/`.git`/working copy) → record source repo/ref/**OID** provenance → materialize an **immutable initial snapshot**. After import the project lives independently; **the remote is not authoritative**. Credentials reuse the connector/vault AEAD boundary and **never** enter a project tree/snapshot/prompt/log/tool result/sandbox. Read-only fetch ⇒ idempotent, **no `effect_unknown` remote reconciliation** (that is W4). GitHub import is **human-only** (not an agent tool). **Non-goals (later ADRs):** background fetch/sync, working copy, `git init/commit/branch`, merge, push, PR, force push, sandbox (W3/W4).

| # | Task | refs | AC | Status |
|---|---|---|---|---|
| **W2b-DESIGN.0** | **Research convergence + ADR-038 + contracts frozen + static draft**: (a) converged the first-version ref scope to **branch + tag + commit** (all three uniform via GitHub `tarball/{ref}`; resolve name→OID first, pin to OID) and the fetch mechanism to **bounded archive (tarball)** over `git clone`, with **fine-grained PAT `contents:read`** as the first-version credential (GitHub App installation token = forward path) — with GitHub-docs evidence. (b) **ADR-038** (decisions.md + decisions-log row). (c) **data-model**: `project_sources` (W2b provenance) + `github_connections` (AEAD credential) + `projects.source_status` widen (`importing/imported/import_failed`) + `project_import_jobs` github extension + `project_snapshots.source_oid` note. (d) **api §10.6**: `kind='github'` 501→**202** + repo/ref pickers (`GET /projects/github/repos`·`/refs`) + `GET/POST/DELETE /connections/github` + schemas; **no new agent tool**. (e) **events §2.10**: `project.lifecycle` `create_kind='github'` + durable job/idempotency/outbox + "read-only ⇒ no `effect_unknown`". (f) **config**: `GITHUB_*` + §1.6 GitHub source boundary. (g) **capability matrix §9**: GitHub import + connection rows, **UI ⬜**. (h) **W2b static draft** `design-workspace/github-import.html` (connection status / repo+ref select / import progress / success source metadata / failure+retry; Quiet Work; desktop + 390px). **No code / no migration / W2b nav not exposed.** | ADR-038; contracts; `research/workspace-product-report.md` §9 | ADR-038 accepted; contracts merged; static draft renders desktop + 390px (no overflow); HTML well-formed; no production code/migration/nav | ✅ this batch |
| W2b.1 | **✅ (impl)** Schema `0029` + models: `project_sources` + `github_connections` + `projects.source_status` widen + `project_import_jobs` github columns. `services/github_source.py` (ref→OID resolve, bounded tarball fetch via connection credential) + `security/github_token.py` (AEAD seal under the active KEK, connector-vault capability). | data-model §Projects W2b; ADR-019/038 | migration head 0029; credential AEAD round-trip; token never in project state; tests | ✅ done (5f85c09) |
| W2b.2 | **✅ (impl)** GitHub import durable job: `services/projects_import.py` `create_github_import` + the `create_kind='github'` branch in `process_import` (claim → resolve ref→OID → bounded tarball fetch → reuse W2a safe expander → strip top-level → initial immutable snapshot w/ `source_oid` → atomic activate; failed ⇒ status=active, no snapshot, visible+deletable; idempotent; `retry_github_import`; recovery tick). | events §2.10; config §1.6 | crash/replay safe; unsafe/oversized archive → import_failed; read-only retry idempotent by pinned OID; tests | ✅ done (5f85c09) |
| W2b.3 | **✅ (impl)** REST §10.6 (`kind='github'` 202 JSON body + repo/ref pickers + `/connections/github` endpoints; server-side credential proxy, token never to client; CSRF; 409/422/202/502/507; `/projects/{id}/imports/retry`). **No new agent tool** (human-only import). | api §10.6; ADR-023 | REST green; token never leaves server; authz/CSRF; tests | ✅ done (5f85c09) |
| W2b.4 | **✅ (impl)** Frontend on `/work/projects`: GitHub connection panel + repo/ref picker (branch/tag/commit) + import progress + source-metadata provenance detail + failure/retry; enabled the "GitHub" create path (was disabled→W2b); 390px OID wrap. Capability-matrix UI cells → ✅. | design-workspace/github-import.html; matrix §9 | build/lint green; renders; matrix UI ✅ | ✅ done (04cba5f) |
| W2b.V | **✅ (impl)** Verify: backend gate (alembic 0029 / ruff / mypy / pytest 257) + two-lane Playwright (human: connect → pick repo/ref → import → source metadata + failure/retry + 390px; agent: reads the imported project via existing `project_tree`/`project_read`) + UX pass. | AGENTS §2 | verified; UX notes | ✅ done |

**W2b implementation exit (✅ 2026-07-27):** migration `0029` at head; `github_connections`/`project_sources`/`source_status` widen/`project_import_jobs` github columns live; `security/github_token.py` (AEAD) + `services/github_source.py` (connection lifecycle + read-only REST proxy + resolve→OID/bounded tarball) + `services/projects_import.py` github branch (durable job · idempotent retry by pinned OID · no `effect_unknown`) + REST §10.6 (`/connections/github`, kind=github 202, retry, repos/refs) + production `/work/projects` GitHub UI. Credentials stay in the AEAD vault/connector boundary — never in a tree/snapshot/prompt/log/event/tool result. Backend gate green (ruff/mypy/pytest); frontend lint/build green. **Non-goals (each a later ADR):** task working copy + scratch-copy sandbox + change review (W3, gated on the ADR-025 revision + `docker.sock`/multi-user isolation hardening); GitHub sync/push/PR (W4).

---

## Phase W3-DESIGN/SECURITY — Project Chat task working copy + one-time scratch-copy sandbox + change review: 安全评审 + 契约与设计先行 (ADR-039 + ADR-040) — ✅ **DESIGN/CONTRACT-FIRST + SECURITY REVIEW COMPLETE (2026-07-27); production impl awaiting owner review**

> Owner approved entering **W3 in normal order** and executing the "security review + ADR/contract/design-first" phase. **No production code, no migration, no real sandbox mount, no exposed W3 navigation** this batch (AGENTS.md §1/§2). **First priority = an independent sandbox isolation security review** (`docker.sock` ≈ host-root threat model, single-user-now vs future-multi-user, comparing socket-proxy / rootless Docker / gVisor / Firecracker/Kata / dedicated sandbox service), producing a minimum W3 security architecture + explicit do-not-ship conditions — **unimplemented mitigations are never written up as already-safe**. W3 goal: a Project-bound Chat's first mutating action opens a **cross-turn durable working copy**; **Sherpa snapshot head is the source of truth**; the working-copy overlay is the **durable task state**; each execution materializes **only a one-time scratch copy** (the sandbox never mounts the project snapshot / blob store / credentials); built-in file/edit/run/test tools work on scratch; a bounded overlay is persisted after each batch; **Change Review** shows added/modified/deleted + artifacts; the user does **Save selected / Save + checkpoint / Discard**; a moved head **rejects a stale Save**; **single-writer lease/fence**; the container is a short-TTL disposable cache; a missing dependency ⇒ explicit `environment_missing_dependencies`; **no** package install, **no** embedded coding agent, **no** `git init/history/commit/branch`, **no** GitHub sync/push/PR (W4).

| # | Task | refs | AC | Status |
|---|---|---|---|---|
| **W3-SECURITY.0** | **Independent sandbox isolation security review** (first priority): confirmed `docker.sock` ≈ host root (OWASP Rule#1; read-only mount insufficient; CVE-2024-21626 shows shared-kernel runc escapes despite all hardening flags); the existing "socket only in the trusted orchestrator, untrusted code only in spawned containers that never touch the socket" **dedicated-sandbox** pattern is correct and must be kept; **socket-proxy is false security** for an orchestrator that must call `containers/create`/`exec`; **rootless Docker** = recommended single-user hardening; **gVisor(`runsc`)** = practical multi-user minimum (no known host-escape CVE); **Kata/Firecracker microVM** = required for genuinely untrusted third-party code; scratch **RW mounts must be a disposable copy only** (never the source of truth), creds stripped before copy, `nosuid,nodev`, orchestrator-managed atomic cleanup. Produced the W3 minimum architecture + explicit **do-not-ship** conditions. | ADR-039; `research/workspace-product-report.md` §10–§11; OWASP/gVisor/Firecracker/Docker docs | independent review with primary citations; comparison table; minimum architecture; do-not-ship conditions; no over-claiming | ✅ this batch |
| **W3-DESIGN.0** | **ADR-039 (isolation) + ADR-040 (product/data/tool/lifecycle) + ADR-025 formal revision + contracts frozen + static draft**: (a) **ADR-039** (isolation security architecture; decisions.md + decisions-log row). (b) **ADR-040** (W3 product model: durable working copy, single-writer lease/fence, head_generation CAS Save, change review, Save selected/checkpoint/discard human-only, built-in tools only, no embedded coding agent, `environment_missing_dependencies`; decisions.md + decisions-log row). (c) **ADR-025 formal revision** ("no workspace mount" → "mount only a one-time scratch, never the source of truth"; hardening retained; gated by ADR-039). (d) **data-model** §Projects W3: `project_working_copies` + `project_working_copy_entries` (overlay) + `project_change_sets` + `project_change_set_entries` + `project_artifacts` + `project_sandbox_runs` + `projects.head_generation` (canonical vs rebuildable-cache; lease/fence; CAS; tenant composite keys; bytes never in journal). (e) **api §10.7**: working-copy / sandbox-run / change-review / Save-selected·checkpoint·Discard / artifacts REST + Tool schema (`project_run`/`project_review_changes` allow; Save-series user-only). (f) **events §2.11**: sandbox has no external effect ⇒ no `effect_unknown`; fence-guarded idempotent overlay persist; head-generation CAS Save; crash recovery. (g) **config §1.7** + `SANDBOX_*`/`WORKING_COPY_*` settings: mount/lifecycle/resource/network/credential boundary. (h) **capability matrix §9**: W3 rows, **UI ⬜**. (i) **W3 static draft** `design-workspace/w3-change-review.html` (Project Chat execution state / diff change review / artifacts / Save·checkpoint·Discard / stale·conflict / isolation model; Quiet Work; desktop + 390px). **No code / no migration / no real mount / W3 nav not exposed.** | ADR-039/040; contracts; `research/workspace-product-report.md` §10 | ADRs accepted; ADR-025 revised; contracts merged; static draft renders desktop 1280 + 390px (no overflow); HTML well-formed; no production code/migration/mount/nav | ✅ this batch |
| W3.1 | Schema + models: the 6 `project_*` W3 tables + `projects.head_generation` (migration 0030); `services/project_workcopy.py` working-copy lifecycle (lazy open, lease/fence, materialize base+overlay, persist boundary, save CAS, discard, idle expiry). | data-model §Projects W3; ADR-040 | migration head 0030; lease/fence + CAS tests; isolated per-chat working copies | ✅ done (`70f0e57`; pytest test_project_workcopy 8) |
| W3.2 | Sandbox orchestration (ADR-039): hardened offline container + **one-time scratch** RW mount only (`nosuid,nodev`; creds stripped; path-validated; atomic cleanup + orphan sweep); materialize/run/persist/teardown; warm-TTL cache; `environment_missing_dependencies`. | ADR-039; config §1.7 | scratch-only mount; never snapshot/blob/creds; hardening retained; do-not-ship gate honored | ✅ done (`4b29aea`; app/sandbox/project_sandbox.py + services + worker sweep/tick; test_project_sandbox 7) |
| W3.3 | Change-set builder + REST §10.7 (working-copy/sandbox-run/change-review/apply/discard/artifacts) + W3 tools (`project_run`/`project_review_changes` allow; Save-series user-only). | api §10.7; events §2.11; ADR-023 | bounded/truncated change set; Save CAS `409 head_moved`; fence-guarded idempotent persist; tests | ✅ done (`ac0fb77`; services/project_changes.py + api §10.7 + tools; test_project_changes 8 + REST flow) |
| W3.4 | Frontend on `/work/projects`: Project-bound Chat execution state + Change Review (diff/artifacts) + Save selected/checkpoint/Discard + stale/conflict; 390px. Capability-matrix UI cells → ✅. | design-workspace/w3-change-review.html; matrix §9 | build/lint green; two-lane Playwright; matrix UI ✅ | ✅ done (`fa5a3c0`; components/ChangeReview.tsx + ChatView + api.ts; matrix §9 UI ✅) |
| W3.V | Verify: backend gate + two-lane Playwright (agent: `project_run` → change set; human: Change Review → Save selected/checkpoint/Discard + stale conflict) + UX pass. Restart the stack first. | AGENTS §2 | verified two lanes; UX notes | ✅ done (full pytest 297 green; stack rebuilt; two-lane Playwright with real claude-sonnet-4.6; `_wc_summary` discard bug fixed + regression test) |

**W3 production exit (✅ 2026-07-27):** all of W3.1→W3.V shipped. Schema at alembic **0030** (6 `project_*` W3 tables + `projects.head_generation`). Backend gate green (full **pytest 297**, ruff + `ruff format` + `mypy app` clean); frontend `npm run lint` + `build` green. **Two-lane Playwright with the real model (claude-sonnet-4.6):** agent — the model drove `project_run` (edit main.py) → durable working copy + change set; human — Change Review panel rendered the real unified diff, **Save + checkpoint** advanced the head (`head_generation` 0→1 + pinned `checkpoint` snapshot), **Discard** left the head byte-identical (working copy `discarded`, no snapshot), 390px overflow=0. **Bug found + fixed during verification:** the discard route's `_wc_summary` hit `MissingGreenlet` reading the flush-expired `updated_at` column → `await db.refresh(wc)` + a REST discard regression test. **UX notes:** (a) stale W2a "read/discuss only" copy on the Projects page updated to reflect the W3 working-copy flow; (b) change-review entry-row spacing at 390px could be tightened (minor); (c) a run that fails mid-loop (observed via the storage-contaminated leftover project) surfaces no chat-side error banner — a pre-existing observability gap, not W3. **Dev-stack limitation (honest):** ~~the worker shares the host `docker.sock`, so a sibling sandbox container's bind-mount source resolves on the host, not inside the worker container — a `project_run` **shell command** in this Docker-Desktop dev stack sees an empty `/work`~~ **CORRECTED 2026-07-30 (backlog B-8 triage):** the container **never starts at all**. The worker passes its own in-container path (`/app/.sherpa/scratch/<run>`) as the bind-mount `source` to the **host** daemon, which cannot resolve it, and `infra/docker-compose.yml` declares no shared scratch volume — so `containers.run` raises before execution and `services/project_sandbox.py` collapsed it to `sandbox_unavailable` with no log (**that collapse was fixed in Phase TR P0, 2026-07-30** — the same failure now reports `runtime_start_failed` with one worker log line and one redacted observation; the mount is still broken). `/work` is never mounted, let alone empty. Host-side **edits** (writes/deletes) + the whole change-review/save/discard loop are genuinely unaffected, which is why the W3 verification still passed. Also corrected: the **human Run lane never existed** — `frontend/src/api.ts::createSandboxRun` has no call site, and its route executed the sandbox synchronously inside the **web** process, where `SANDBOX_KIND` is unset. Fix = [ADR-047](decisions.md#adr-047) (tar transport, no host path at all) + [ADR-048](decisions.md#adr-048) (RuntimeSession + real Run control), planned as **Phase TR**. Per ADR-039 the shared-socket posture remains dev-single-user-only; a production runner (gVisor/microVM) is still required before multi-user. **Next: W4** = GitHub sync/push/PR (own ADR, ADR-020 approval).

**W3-DESIGN/SECURITY exit (✅ 2026-07-27):** independent security review done (ADR-039, primary-sourced); ADR-039 + ADR-040 accepted; **ADR-025 formally revised** ("only a one-time scratch, never the source of truth"); frozen contract deltas (data-model §Projects W3 / api §10.7 / events §2.11 / config §1.7 + `SANDBOX_*`/`WORKING_COPY_*`) + capability-matrix rows (UI ⬜); W3 static draft `design-workspace/w3-change-review.html` (desktop 1280 + 390px, no horizontal scroll, HTML well-formed). **No production code, no migration, no real sandbox mount, no exposed W3 navigation.** Design/contract-first review-only screenshots saved outside git (`.tmp-w3-design-screenshots`, not committed). **W3.1…W3.V production implementation awaits owner review.** **Non-goals (each later):** dependency installation; embedded coding-agent executors; `git init/commit/branch`, merge, push, PR (W4); long-running dev servers/previews; network-enabled environments.

---



## Cross-cutting (do continuously, not a separate phase)
- **Tests with every task** — deterministic, mock provider, `pytest-asyncio`. No real model calls in tests.
- **Migrations** — one Alembic head; every schema change is a migration.
- **Eval harness** (~~M3~~): extraction-precision goldens + regression dataset — **deferred out of v1 into post-v1 #11** (eval flywheel) per **ADR-024** (single-user self-hosted; owner is the eval loop; re-instate before external beta). A ~1-day deterministic mock regression lane on the extraction path is the optional minimum guard if that path changes.

## Open decisions that bind M2 (not M1) — see [reviews/README.md §5](reviews/README.md)
Initial real provider/model · Gmail OAuth operating mode · data-retention window · notification defaults. **Safe v1 defaults are in `contracts/config-and-secrets.md`** so M1 (and M2 scaffolding) proceed unblocked; confirm before M2 ships to real users.

## Phase MP — 多来源模型 provider（用户在设置里配置）(ADR-041) — ✅ **COMPLETE + two-lane verified (2026-07-28)**

roadmap #8 的「多 provider」那一半（failover/子 agent 后置）。研究先行 `research/model-provider.md`（深读 AstrBot `AstrBotDevs/AstrBot`、hermes-agent `NousResearch/hermes-agent`、PI-agent `earendil-works/pi` + provider landscape）→ ADR-041 + 契约先行（data-model §Model providers · api §10.8 · config · 能力矩阵 · 静态 `design-settings-models/`）→ 生产实现。Schema `0031`。

| # | Task | AC | Status |
|---|---|---|---|
| MP.1 | migration 0031 `model_providers`(AEAD 密钥) + `sessions.model_provider_id/model` · `security/model_provider_key.py`(KEK 直封) · `services/model_providers.py`(CRUD/默认/测试写回/会话选择/解析) | 密钥 seal/open roundtrip、首个设默认、改密钥重置、set_default 移旗、会话覆盖+解析 | ✅ `dad3b95` (pytest 7) |
| MP.2 | `providers/tools.py`(3 序列化器+Gemini 收敛) · 增强 `openai_compatible`(reasoning/per-choice usage/base_url 规范化) · 原生 `anthropic`+`gemini` · `factory.build_from_config` | 序列化+SSE 归一(text/reasoning/toolcall/finish)；anthropic 翻译(system/tool_result/merge)；gemini functionResponse 名解析 | ✅ `2afedce` (pytest 7; 修 f"******" 密钥头 bug) |
| MP.3 | `provider_for_session`(会话→默认→env) · `test_connection`(拉 /models) · worker chat loop 接线 · `api/model_providers.py` §10.8 + 注册 | 按 kind 建适配器；测试连接成功/失败；REST CRUD/默认/会话-model；密钥只入不出+CSRF+无 agent 工具 | ✅ `ba3d248` (pytest 10 + REST flow) |
| MP.4 | `components/ModelsPanel.tsx`→Settings · `components/ModelSwitcher.tsx`→ChatView · api.ts 客户端 · Vite proxy · styles · 能力矩阵 §9 UI ✅ | build/lint green；密钥 password 永不回显 | ✅ `bf9c1ad` |
| MP.V | full backend gate + 两栈 Playwright + UX pass；重启栈 | full pytest green；两栈验证；390px | ✅ `741ef7a` (真实 litellm 源测试连接拉 29 model；agent 用 DB 源 claude-sonnet-4.6→"Paris"；每会话切 gpt-4o-mini 生效) |

**Deferred（各自后续 ADR）：** 跨-provider failover、MoA/ensemble、成本 ledger、Bedrock/Vertex/OpenAI-Responses、子 agent、多 key 轮换。

---

## Phase TR — Tool catalog + coding RuntimeSession (clean break) — 🚧 **P0 · P1 · P3 · P4 SHIPPED · P2 DEFERRED · P5 NEXT**

> Closes backlog **B-2** (52 flat tools) and **B-8** (`project_run` always fails) as one program.
> Architecture approved by the owner 2026-07-30 ([ADR-045](decisions.md#adr-045) umbrella,
> [ADR-046](decisions.md#adr-046) tool catalog, [ADR-047](decisions.md#adr-047) tar transport,
> [ADR-048](decisions.md#adr-048) RuntimeSession). **Execution plan approved by the owner
> 2026-07-30.** **P0 (TR.5, honesty pass) and P1 (TR.6, baseline squash + legacy deletion) are
> shipped**, and the destructive baseline reset of TR.3 **has been run** (once, as designed —
> the schema is now the single `0001_baseline`).
>
> **Status as of 2026-08-03.** P0 · P1 · P3 · P4 and the P2 partials (P2.0a dead-tool
> sweep, P2.2 `domain_verb` rename) shipped. P3 is owner-accepted including its 128 MiB
> workspace/change-set cap; P2's catalog remains deferred; **P5 is next**.
> Neither backlog item is closed: **B-2** waits on the P2 catalog, **B-8** on the P5 human
> Run/Stop lane.

### TR.0 Owner approval checklist — defaults already approved (2026-07-30)

Do **not** re-litigate these. They are settled inputs, not open questions.

| # | Decision | Approved value |
|---|---|---|
| — | Compatibility posture | **Clean break.** No alias table, no deprecation window, no legacy tool names, no shims. |
| — | Data migration | **None.** All existing data is disposable test data. |
| — | Alembic | **Squash `0001`…`0032` into one new `0001_baseline`.** Destructive dev rebuild accepted. |
| O-1 | Tool naming | **`domain.verb`** (`^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$`), api.md §7 regex updated. |
| O-2 | Verb mega-tools (`drive(op=…)`) | **No.** Grouping + on-demand loading only. |
| O-3 | `sh_exec` policy | **`ask`**, auto-released by a **platform safe-command allowlist grant**. |
| O-4 | `fs_write`/`fs_edit`/`fs_delete` policy | **`allow`** (writes the reviewable overlay), **except** sensitive paths → `ask`. |
| O-5 | v1 workspace transport | **tar ingress/egress** (`put_archive`/`get_archive`); no bind mount, no host path. |
| O-6 | Runner image v1 | **Python + `pytest` + `ruff`**, first-party, pinned by digest. **Node is later/optional.** |
| O-7 | `tools_load` | **Model may load autonomously** (audited). External/MCP tools never enter core. |
| O-8 | `/Project` UI | **Three-column workspace** for Project-bound chat. |
| O-9 | Execution REST | **Async `202` + SSE + cancel**, executed by the **worker**. |
| O-10 | Plan object | **Deferred** to a later slice; reserve a `ui.*` namespace only. |
| O-11 | Legacy `/files` stack + `run_code` | **Deleted.** |
| O-12 | `run_code` | **Deleted**, replaced by `runtime_open(scope="ephemeral")` + `sh_exec`. |
| O-13 | Route inventory | **Generated file + CI diff**, replacing the hand-frozen api.md §9 count. |
| O-14 | `app/files/` package | **Rename to `app/objectstore/`** (it is the object-store adapter, not the deleted files stack). |

### TR.1 Orientation for a fresh process (no conversational memory required)

Read in this order: [`AGENTS.md`](../AGENTS.md) → [`STATUS.md`](STATUS.md) → ADR-045/046/047/048 in
[`decisions.md`](decisions.md) → `contracts/api.md` §7 + §10.7 → `contracts/events-and-effects.md`
§2.2 + §2.11 → `contracts/config-and-secrets.md` §1.4 + §1.7 + §1.10 → `contracts/data-model.md`
§Projects. Contract sections are marked **`[shipped]`** / **`[target]`** / **`[deleted]`** — implement
the `[target]` ones; do not assume a `[target]` section describes existing code.

**Measured ground truth (verify before changing anything, so you can prove the delta):**

```powershell
cd C:\src\sherpa\backend
@'
import json
from app.tools.builtin import build_default_registry
s = build_default_registry().schemas("full")
print("tools:", len(s), "json_bytes:", len(json.dumps(s)))
'@ | uv run python -
# Expected baseline before Phase TR: tools: 52  json_bytes: 19848
```

**The three facts that explain B-8** (do not re-derive them, but do re-confirm the first one after
any change): (a) `backend/app/sandbox/project_sandbox.py` passes a **worker-container** path as a
bind-mount `source` to the **host** daemon, and `infra/docker-compose.yml` declares no shared scratch
volume, so container creation always fails; (b) ~~`backend/app/services/project_sandbox.py` collapses
every error into `sandbox_unavailable` with no log~~ **✅ fixed in P0** — each failure now carries its
own contract name plus one worker log line and one redacted observation; (c)
`backend/tests/test_project_sandbox.py` and `test_sandbox.py` monkeypatch the executor, so **no test
ever starts a container** — which is why 297 green tests did not catch it. P0 narrowed this with a
fake-docker-client lane that exercises the real classification branches of `_run_docker`, but P3 must
still add the real-container lane or (a) will happen again.

### TR.2 Canonical commands

```powershell
# backend gate (run in C:\src\sherpa\backend)
uv sync
uv run pytest                                   # isolated data plane per ADR-044; safe with the stack up
uv run pytest -m docker                         # real-Docker lane (new in P3; skipped by default)
uv run pytest tests/test_tool_catalog.py -q     # targeted
uv run ruff check . ; uv run ruff format --check . ; uv run mypy app
uv run alembic upgrade head

# frontend gate (run in C:\src\sherpa\frontend)
npm ci ; npm run lint ; npm run build

# stack
docker compose -f infra/docker-compose.yml --env-file .env up --build -d
docker compose -f infra/docker-compose.yml logs -f worker
docker compose -f infra/docker-compose.yml down

# sandbox runner image (new in P3)
docker build -t sherpa-sandbox-runner:dev sandbox-runner/
# NOTE: the image is built locally and never pushed, so it has NO RepoDigests. Pin it by
# IMAGE ID digest instead (owner decision D-1):
docker image inspect sherpa-sandbox-runner:dev --format '{{.Id}}'
```

### TR.3 Destructive reset procedure (baseline squash) — **P1 only, and only once** — ✅ **RUN 2026-07-30**

This **destroys the dev database, Redis and MinIO volumes**. It is approved, but it is irreversible,
so run it deliberately.

1. Confirm with the owner that the dev stack holds nothing they want. (Approved in principle
   2026-07-30; still announce it in the P1 commit body.)
2. `docker compose -f infra/docker-compose.yml down -v` — the `-v` is what removes `pgdata`,
   `redisdata`, `miniodata`.
3. Delete `backend/migrations/versions/0001_initial_core.py` … `0032_chat_attachments.py`
   (all 32 files). Do **not** leave any orphaned revision; the chain must stay linear.
4. Author a single `backend/migrations/versions/0001_baseline.py` that creates the **target** schema:
   no `files` table, no `project_sandbox_runs`, plus `project_runtime_sessions` and
   `project_exec_runs` (`contracts/data-model.md` §Projects). Generate it from `Base.metadata` after
   the model deletions land, then hand-audit it against the contract DDL — `--autogenerate` will not
   produce the partial indexes, CHECKs, triggers or `uq_prs_live`.
5. `docker compose -f infra/docker-compose.yml --env-file .env up --build -d`
6. Verify: `uv run alembic upgrade head` on an empty database succeeds and
   `uv run alembic heads` shows exactly one head.
7. Verify the pytest harness is unaffected: `uv run pytest` — ADR-044 creates `<app_db>_test` from
   scratch and runs `upgrade head`, so it needs no special handling. If it complains about a missing
   marker on a pre-existing test DB, use `SHERPA_TEST_DB_RESET=1` once.
8. **Never** run `alembic revision --autogenerate` against the test database (ADR-044).

### TR.4 Phase graph and independence

```
P0 honesty ──► P1 baseline + deletions ──┬──► P2 tool catalog ──┐
                                         └──► P3 tar transport ─┴──► P4 runtime + fs/sh ──► P5 UI
                                                                          │
                                                     roadmap:  P6 production runner
                                                               P7 in-sandbox coding agent
```

**P2 and P3 are deliberately disjoint and MUST be developable in parallel by two processes:**

| | P2 touches | P3 touches |
|---|---|---|
| owns | `backend/app/tools/**`, `backend/app/permissions/**`, `backend/app/core/loop.py` (the `registry.schemas(tier)` call site only), `backend/tests/test_tool_*.py` | `backend/app/sandbox/**`, `sandbox-runner/**`, `infra/docker-compose.yml`, `backend/tests/test_runtime_transport.py`, `backend/tests/conftest.py` (docker marker) |
| must not touch | anything under `app/sandbox/`, `sandbox-runner/`, `infra/` | anything under `app/tools/` or `app/permissions/` |

The only shared file is `backend/app/config.py` (P2 adds `tool_catalog_core_max_bytes`; P3 removes
`sandbox_scratch_root`/`sandbox_warm_ttl_seconds` and adds `sandbox_runtime_idle_ttl_seconds`). Land
them as two separate stanzas to keep the merge trivial. **Both must be merged before P4 starts** —
P4 registers `fs.*`/`sh.*` through P2's descriptor API against P3's transport.

### TR.5 P0 — Honesty pass (no architecture change) — ✅ **SHIPPED 2026-07-30**

Doable immediately after plan approval; unblocks everything by removing false statements.

| # | Task | Paths | AC |
|---|---|---|---|
| P0.1 | ✅ Split the `sandbox_unavailable` collapse into the named-exit list (events §2.11); emit one structured worker log line and one redacted tool observation per failure | `backend/app/sandbox/runner.py` (shared reason vocabulary), `backend/app/sandbox/project_sandbox.py`, `backend/app/services/project_sandbox.py`, `backend/app/tools/project_tools.py`, `backend/app/tools/sandbox_tools.py` | A forced daemon-unreachable, a forced image-missing and a disabled sandbox produce **three different** `termination_reason`s and three log lines; regression test asserts each |
| P0.2 | ~~Correct the stale W3 exit note~~ **✅ already corrected in the design batch** ("a `project_run` shell command sees an empty `/work`" — the container never started) | this file, Phase W3 exit block | Note states the container never starts and points at ADR-047 |
| P0.3 | ~~Correct the capability matrix~~ **✅ already corrected in the design batch**: the W3 human lane never existed (`api.ts::createSandboxRun` has no call site) | `docs/11-agent-tool-surface.md` §9 | The Run/UI cell is ⬜ with the blocker named, not ✅ |
| P0.V | ✅ `uv run pytest tests/test_project_sandbox.py -q` + full gate | — | green; **commit P0 separately** |

**P0 exit:** every sandbox failure is distinguishable in the log and in the model's observation; no
document claims a capability that does not exist.

**P0 exit result (2026-07-30 — met).** `sandbox_unavailable` no longer exists in any sandbox code
path. The named reasons now live **once**, in `app/sandbox/runner.py`, and are shared by both
entry points. `_run_docker` (both of them) classifies into `runtime_daemon_unreachable` (client
construction fails) / `runtime_image_missing` (`ImageNotFound`) / `runtime_start_failed` (any other
create-time `DockerException`/`APIError`) / `runtime_transport_failed` (container ran, output
unreadable) / `error:<class>` (unmodelled); `run_in_scratch` and `run_code` keep `sandbox_disabled`
distinct. The named reason travels on `RunResult.error` while the **raw** failure text travels
separately on the new `RunResult.error_detail`, which reaches the **worker log only** — the model's
observation is a static, reason-specific sentence (`runner.runtime_failure_note` /
`services/project_sandbox.failure_note`) carrying no host path, image reference or exception text
(ADR-019). This also closed a real leak: `run_code` previously returned
`f"sandbox error: {result.error}"`, i.e. the **raw docker exception string**, straight to the model.
`run_sandbox` emits exactly **one** `logger.warning("project sandbox run failed", …)` per failing
exit — including the pre-existing `wall_timeout` / `environment_missing_dependencies` /
`changeset_bounds` / `fence_lost` / scratch exits — and returns `SandboxOutcome.failure_note`, which
`project_run` appends to its observation; `run_code` logs one line of its own. Error-is-observation
is preserved: a runtime failure still persists the host-side edits. Gate: full `uv run pytest`
**397 passed**, ruff + `ruff format --check` + `mypy app` clean; no frontend change.
**Not done in P0 (by design):** the bind mount itself (P3), the async worker-executed REST lane and
the human Run control (P4/P5). B-8 stays **open**.

### TR.6 P1 — Baseline squash + legacy deletion — ✅ **SHIPPED 2026-07-30**

| # | Task | Paths | AC |
|---|---|---|---|
| P1.1 | Delete the legacy files stack | `backend/app/services/files.py`, `backend/app/api/files.py`, `backend/app/tools/file_tools.py`, the `File` model, `include_router(files_router)` in `backend/app/main.py`, `backend/tests/test_file*.py` | No import of `app.services.files` remains; `uv run mypy app` clean |
| P1.2 | Delete `run_code` | `backend/app/tools/sandbox_tools.py`, `backend/tests/test_sandbox.py` | Registry no longer exposes `run_code` |
| P1.3 | Rename `app/files/` → `app/objectstore/` (O-14) and update Drive/Projects imports | `backend/app/files/**` → `backend/app/objectstore/**`, `app/services/{drive,projects,projects_import,project_workcopy}.py` | Full gate green; no `app.files` import remains |
| P1.4 | Delete `WORKSPACE_ROOT` (setting, `.env.example` line, compose read-only mount, role requirement) | `backend/app/config.py`, `.env.example`, `infra/docker-compose.yml` | Worker starts with no `WORKSPACE_ROOT`; config §1.4 matches |
| P1.5 | Baseline squash per **TR.3** | `backend/migrations/versions/**` | `alembic upgrade head` on an empty DB; exactly one head; `uv run pytest` green |
| P1.V | Full gate + stack rebuild | — | backend gate + `npm run build` green; stack healthy; **one commit per task** |

**P1.4 correction (2026-07-30, measured during execution).** `WORKSPACE_ROOT` was
**contract-only and never existed in code**: no `workspace_root` field in
`backend/app/config.py`, no line in `.env.example`, no mount in
`infra/docker-compose.yml` (the `worker` service mounts only `/var/run/docker.sock`), and
no `read`/`glob`/`grep` tools were ever registered. `config-and-secrets.md` §1.4 already
records it as **DELETED**, so the contract and the code now agree — but the deletion was of
a *planned* setting, not a shipped one. The task row above is kept verbatim for audit;
this note is the honest result. The only remaining occurrences repo-wide are the deletion
records themselves plus historical ADR-025/ADR-039 "never mount" lists and the
`design-workspace` mockup, which are immutable history and correctly left alone.

**P1 exit:** one Alembic revision; no legacy files stack; no `run_code`; no `WORKSPACE_ROOT`; the
dev stack is rebuilt from empty and healthy.

**P1 exit result (2026-07-30 — met).** Five commits, one per task. The reset was executed
deliberately: `docker compose down -v` destroyed `pgdata`/`redisdata`/`miniodata`, and the rebuilt
stack's `migrate` service ran `-> 0001` on an empty database. `alembic heads` shows **exactly one
head**. The baseline was audited **mechanically, not by eye**: a normalized statement-set diff of
`pg_dump` at 0032 vs `pg_dump` after the rebuild shows **15 statements removed — every one owned by
`files` or `project_sandbox_runs` — and 17 added — every one owned by `project_runtime_sessions` or
`project_exec_runs` — with no other difference**. (Postgres flattens `((A AND B) AND C)` to
`(A AND B AND C)` when it re-parses a dumped CHECK; the audit normalizes parentheses/whitespace and
nothing else.) Live schema vs `Base.metadata`: 52 = 52, only `alembic_version` extra.

**Sequencing tension, resolved explicitly.** The baseline must remain the *only* revision and must
never require a follow-up migration, so the ORM had to move with it: `ProjectSandboxRun` is replaced
by `ProjectRuntimeSession` + `ProjectExecRun` (declarative definitions), and the existing W3
bookkeeping was re-pointed at them — `run_sandbox` opens one runtime session per boundary, records a
command as one exec run, and **closes the session on every exit** so the `uq_prs_live` partial unique
index can never block the next boundary. This is schema/persistence alignment only. **P4 behavior is
not implemented**: no `runtime_open`/`runtime_close`, no `sh_exec`, no tar transport, no async 202
REST, no three-column UI, and `project_run`/`project_tree`/`project_read` still exist (their deletion
is TR.9 P4). `project_run` still bind-mounts and still fails identically — it now records its named
exit on the right rows. `SandboxRunState.warm` was dropped (ADR-047 §7: never implemented).

**Deliberately not added to the baseline:** the immutability triggers and PL/pgSQL function sketched
in `contracts/data-model.md` §Alembic item 1. They exist in no shipped migration and in no database;
a squash is not the place to introduce new enforcement, which needs its own ADR and tests.

Gate: `uv run pytest` **385 passed** (the ADR-044 harness created `sherpa_test` from scratch; 397 →
385 is exactly the 12 tests deleted with the dead code), ruff + `ruff format --check` + `mypy app`
clean, `npm run lint` + `npm run build` green, `/health` + `/readyz` ok on the rebuilt stack.
Measured tool surface **52 → 47 tools / 19,848 → 18,397 B** — deletion, **not** the B-2 fix.
**B-2 and B-8 both stay open.**

### TR.7 P2 — Tool catalog, resolver, discovery (closes B-2)

TDD order per task: write the failing test first, then the implementation.

> **P2.0 added 2026-07-30** by the owner's design review — slim the surface **before** indexing it, so the
> catalog hides nothing that should simply be deleted. See [`backlog.md` B-10](backlog.md#b-10-tool-surface-slimming-dead-tools-prose-diet-and-vertical-workflow-consolidation)
> and [ADR-046 修订 A](decisions.md#adr-046). **Measured baseline is 47 tools / 17,432 B (compact) — not the
> 19,848 B quoted earlier, which was the pre-P1 52-tool figure.** Ideally run
> [B-11](backlog.md#b-11-no-tool-use-evaluation-harness-decisions-are-argued-not-measured) **E0** (mine
> existing Phoenix traces for per-tool call frequency) first: it is nearly free and turns the four "owner
> decision" deletions below into measurements.

| # | Task | Paths | TDD / AC |
|---|---|---|---|
| **P2.0a** ✅ **SHIPPED 2026-07-31** | **Dead-tool sweep (5 confirmed deletions)** | `backend/app/tools/{builtin,candidate_tools,todo_tools,drive_tools,memory_tools}.py`, `app/tools/__init__.py`, `app/services/todos.py`, `app/core/loop.py`, 6 test files, `docs/contracts/api.md` §7.3 | Deleted `echo` (SAFE-tier dev leftover; SAFE is now `{get_time}`) and **`drive_restore`** (structurally uncallable — needed a `node_id` no tool emits); deleted `complete_todo` **and its one-line service alias** (`todos.complete_todo`, which had no REST caller); folded `edit_candidate` into `accept_candidate` (optional `title`/`description`/`due_at`/`priority` patch; **REST keeps both endpoints** for the Inbox UI's two buttons); folded `memory_user_list` into `memory_user_get` (`key` now optional). Regression guard `test_deleted_tools_are_gone` asserts none reappear. **Measured 47 → 42 tools, 17,432 → 16,153 B compact.** Gate: 386 passed, ruff + format + mypy clean. |
| P2.0b | **Prose diet + description byte cap** (the other half of P2.0 — **owner skipped 2026-07-31**: *"P2.0跳过了，一个个删工具没意义"*) | `backend/app/tools/*.py`, `backend/app/config.py`, new startup assertion | Descriptions are still **39% of the surface** (6,336 B of 16,161). Would add `TOOL_DESCRIPTION_MAX_BYTES` (proposal: 160) enforced at startup **next to the name regex**; trim the offenders (`project_run` 641 chars, `project_tree` 507, `knowledge_search` 398, `schedule_create_task` 276). |
| P2.0c | **Owner-decision deletions** (**skipped with P2.0b**) | — | `drive_make_folder` · `notify_list` · `knowledge_reindex` · `schedule_create_digest`; plus whether memory keeps **two** systems (KV + archival). Parked in [B-10](backlog.md). |
| P2.1 | `ToolDescriptor` + startup validation (name regex, uniqueness, unknown `requires`, version monotonicity) | new `backend/app/tools/catalog.py`; `backend/app/tools/builtin.py` | Test first: a bad name, a dupe and an unknown `requires` each raise at startup |
| ~~P2.2~~ | ~~Rename every tool to `domain.verb` **and register with a descriptor**~~ | — | **The rename half shipped 2026-07-31** (row below). The descriptor half moves to P2.1, which owns `ToolDescriptor`. |
| P2.2 ✅ **SHIPPED 2026-07-31** | Rename every tool to `domain.verb` (hard rename, no aliases) | `backend/app/tools/*.py` + their tests + `app/api/schemas.py` + `app/permissions/grants.py` + `app/core/{loop,session_context,attachments}.py` + `frontend/src/views/{ApprovalsView,ChatView}.tsx` + new `migrations/versions/0002_tool_name_domain_verb.py` | All **42** tools now match `^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$` across 11 namespaces, matching §7.3's table exactly. Two dot-less enforcement points widened: `ApprovalAction.tool_name` and the **`ck_pg_tool` CHECK** on `permission_grants` (found only by running the suite — 6 failures — and given migration `0002`, the first revision after the baseline squash). Model-facing prose renamed with the tools. `memory_recall` covers the old get/list; `todo_update` covers complete. Gate: pytest green, one alembic head, ruff/format/mypy clean, frontend lint+build green. |
| P2.3 | `ToolsetResolver` + profiles | new `backend/app/tools/resolver.py`; `backend/app/tools/registry.py` | Tests: (a) `resolve(general).core` is a **byte-true prefix** of the project-bound array; (b) two calls with the same profile are byte-identical; (c) `connector_analysis` → empty array |
| P2.4 | `tools_search` / `tools_load` + catalog digest in the system message | `backend/app/tools/meta_tools.py`, `backend/app/core/loop.py` (system-message assembly + the `registry.schemas(tier)` call site) | Mock-provider script: search → load → **next turn** exposes the toolset and the call succeeds; a same-turn load does **not** change that turn |
| P2.5 | Byte budget | `backend/app/config.py` (`tool_catalog_core_max_bytes=6144`), `backend/app/tools/resolver.py` | Startup fails above the cap; `test_tool_catalog.py` asserts `core_bytes <= 6144` and records the measured value |
| P2.6 | Args-aware policy + structured `PermissionScope` + grants matchers | `backend/app/permissions/{policy,grants,service}.py` | Tests: read-only → allow; overlay write → allow; sensitive path → ask; safe-command grant flips `sh_exec` ask→allow; non-allowlisted stays ask |
| P2.7 | `toolset.resolved` telemetry event | `backend/app/events/journal.py` consumer, `backend/app/core/loop.py` | Event carries `core_toolsets`/`loaded_toolsets`/`tools_offered`/`core_bytes`/`total_bytes` |
| P2.8 | Typed `ToolOutputSpillReference` + retention janitor (api §7.2 debt) | `backend/app/tools/bounding.py`, worker cron | Oversized output yields the typed object in `return_display`; janitor deletes past `TOOL_OUTPUT_RETENTION_HOURS` |
| P2.V | Full gate + agent-lane Playwright | — | Chat: "what can you do about my knowledge base?" → model calls `tools_search`, then `tools_load`, then `knowledge_search` |

**P2 exit (this closes B-2):** general-chat tool JSON **≤ 6,144 bytes** (measured baseline **17,432 B**
compact / 18,303 B default separators for 47 tools — the older 19,848 B figure was the pre-P1 52-tool
number and must not be reused); core is a byte-true prefix; discovery works end-to-end in the agent lane;
`CONNECTOR_ANALYSIS` still receives zero tools; every tool name matches `domain.verb`; **P2.0's slimming is
measured and recorded, and no tool ships with a description over the byte cap.**

> **Not in P2** (ADR-046 §决策10): horizontal `domain(action, …)` merging stays rejected — see the amended
> §决策5 (the surviving grounds are `validate.py`'s missing conditional-`required` support and model
> weakness at discriminated unions, **not** the effect-class/approval-scope grounds, which §决策6 undoes).
> Vertical (workflow) consolidation candidates are parked in [B-10](backlog.md#b-10-tool-surface-slimming-dead-tools-prose-diet-and-vertical-workflow-consolidation)
> and need B-11 evidence first.

### TR.8 P3 — tar transport + first-party runner image (mechanical half of B-8) — ✅ **COMPLETE — owner-accepted 2026-08-01**

| # | Task | Paths | TDD / AC |
|---|---|---|---|
| P3.1 ✅ | Merge the two sandbox modules into one | `backend/app/sandbox/{runner,project_sandbox}.py` → `backend/app/sandbox/runtime.py` | Pure move, no behaviour change; 386 passed. `scope` still distinguishes project vs ephemeral at the model level — the `ephemeral` product path itself is P4. |
| P3.2 ✅ | `WorkspaceTransport` + `TarTransport` (`put_archive`/`get_archive`), **delete every `Mount(type="bind")`**; `/work` becomes an anonymous volume ~~with `nosuid,nodev`~~ | `backend/app/sandbox/transport.py`, `backend/app/sandbox/runtime.py` | Round trip preserves content, mode bits and the executable flag; the no-bind canary greps **token-stripped** source (so the module may still *explain* the deleted mount). ⚠️ **`nosuid,nodev` NOT set** — docker exposes those flags for tmpfs/bind only, never for an image-declared anonymous volume. Equivalent protection = `cap_drop=ALL` + `no-new-privileges` + non-root. Recorded, not claimed (owner decision D-2). |
| P3.3 ✅ | Credential strip + assert before the tar is built | `backend/app/sandbox/transport.py` | Canary runs **end to end through the orchestration boundary** with the secret in the base snapshot: absent from tar / overlay / change set / artifact / log / observation, **and not reported as deleted** (held-back paths are merged back before the delta). |
| P3.4 ✅ | Egress tar treated as untrusted | `backend/app/sandbox/transport.py` reusing `backend/app/services/archive.py` semantics | Absolute, `..`, NUL, device/FIFO, hard link, escaping symlink → `path_escape`, and the boundary persists **nothing the container produced** while keeping explicit host-side edits. |
| P3.5 ✅ | First-party runner image | new `sandbox-runner/Dockerfile`, `sandbox-runner/capabilities.json`, `.dockerignore`, rewritten `README.md` | Non-root (uid 10001), read-only-rootfs friendly, pinned python 3.11.9 + pytest 8.3.3 + ruff 0.6.9, no git/curl/wget. **`SANDBOX_IMAGE` is pinned by *image ID* digest, not a registry digest** — the image is never pushed, so it has no `RepoDigests` (owner decision D-1; the TR.2 command is corrected below). **Review fix (2026-07-31):** pinning is now **enforced fail-closed** in the docker path (`verify_runner_image`: immutable reference **and** first-party title label, else `runtime_image_untrusted`), there is **no default image**, and the Dockerfile base is pinned by registry digest. |
| P3.6 ✅ | Config: drop `sandbox_scratch_root`/`sandbox_warm_ttl_seconds`, add `sandbox_runtime_idle_ttl_seconds` | `backend/app/config.py`, `.env.example`, `infra/docker-compose.yml`, `backend/tests/db_guard.py` | No scratch path anywhere; `SANDBOX_MEM_MB` 256 → **1024** (pytest+ruff do not fit in 256 MiB, D-3); orphan sweep now removes label-filtered **containers**. **Review fix:** `SANDBOX_SCRATCH_MAX_BYTES` **2 GiB → 512 MiB** — 2 GiB was incoherent with `WORKING_COPY_MAX_CHANGED_BYTES` (500 MiB) and sized a worker-side buffer against a number the worker cannot hold. |
| P3.7 ✅ | **Real-Docker test lane** | `backend/tests/conftest.py` (`docker_runner_image` fixture + auto-skip), new `backend/tests/test_runtime_docker.py`, `backend/pyproject.toml` marker + `addopts = "-q -m 'not docker'"` | `uv run pytest -m docker` → **30 passed**: real exit codes, real pytest fail→fix→pass, ruff, no network, uid 10001 + read-only rootfs, no secret env / no `docker.sock`, real OOM → `mem_limit`, real wall-clock kill, credential canary, container+volume removal, **ownership-scoped + liveness-guarded sweep incl. a live run surviving a concurrent sweeper**, real `chmod +x/-x` deltas, and a real mutable tag / foreign digest both refused. **It found a real bug on first run** (implicit parent dirs created root-owned ⇒ the non-root runner could not write in them), and a later run **caught a confirmed sweep race** (the dev worker's cron deleting a live test container → `409 dead or marked for removal`). |
| P3.V ✅ | Failure injection (TR.11) + topology matrix (TR.12) | — | See the two tables' status columns below. Windows + Docker Desktop / DooD green; Linux DooD / DinD / rootless **NOT verified** (D-7). |

**P3 exit — met (2026-07-31), owner-accepted (2026-08-01).** A real command executes in a real
container on the Windows dev
stack and returns a real exit code and stdout, driven both from `uv run pytest -m docker` and,
live, from the worker container over DooD and from chat with the real provider
(`pytest -q` → exit 1 → fix → exit 0; `ruff --version` → 0.6.9; `git --version` → 127). No bind
mount and no host path reaches the daemon anywhere. The canary passes. `uv run pytest -m docker`
exists and is green locally.

**Owner decision (2026-08-01): the 128 MiB workspace / change-set cap is ACCEPTED** as the
intentional product trade-off for the current 1 GiB worker memory budget. It is no longer a
pending question. The trade-off is explicit: a Project whose *changed* bytes in one boundary
exceed 128 MiB is refused with `changeset_bounds` rather than risking the worker. Raising it is
a deliberate act that **requires raising the compose worker's `mem_limit` by twice the delta**
(peak ≈ 2 × cap + ~40 MiB — see config §1.7), not a number to nudge.

**Still open after P3 — B-8 does NOT close here** (it closes at the end of P5):
- no `runtime_open`/`sh_exec`/`fs_*` tools, no async `202`+SSE, no cancel (**P4**);
- no human Run control / streaming log / Stop — `frontend/src/api.ts::createSandboxRun` is still
  dead code (**P5**);
- `cancelled` and `output_limit` (+ typed spill reference) are unimplemented; `pids_limit` has no
  reliable daemon-side signal and is **not** mapped — a fork bomb surfaces as a plain non-zero
  exit. All three are recorded in TR.11 rather than quietly assumed.

**Correction to TR.2:** `docker image inspect ... --format '{{index .RepoDigests 0}}'` **cannot
work** for this image — it is built locally and never pushed, so `RepoDigests` is empty. Use
`--format '{{.Id}}'`.

### TR.9 P4 — RuntimeSession + `fs`/`sh` (product half of B-8) — 🚧 **AUTHORIZED 2026-08-03**

> **Owner sequencing decision (2026-08-03):** P2 remains deferred. Register the P4 tools in
> today's FULL flat registry (`safe=False`) and let P2 wrap them in descriptors later. This resolves
> TR.4's former hard dependency without changing the target catalog architecture. The temporary
> byte/visibility debt is measured and B-2 remains open.

> **P4.5 dropped 2026-07-30** ([ADR-046 §决策10](decisions.md#adr-046), from the owner's P2 design review):
> `run_test`/`run_lint` are pure sugar over `sh_exec("pytest")` / `sh_exec("ruff check")` and CLI agents
> ship no `run_test` tool — **−2 tools**. The capability probe that backed them stays (it is what turns a
> missing binary into `environment_missing_dependencies` instead of a bare exit 127); it now reports through
> `runtime_open` and `sh_exec`.

| # | Task | Paths | TDD / AC |
|---|---|---|---|
| P4.0 | Reconcile ADR/contracts/plan before code: flat-registry sequencing, complete fs schemas (`fs_list`/`fs_delete`/`if_hash`), remove stale `run_test`/`run_lint`, runtime liveness + sweeper protection + committed dispatch | `docs/decisions.md`, `docs/contracts/*`, this file, `STATUS.md` | No target/current fork remains |
| P4.1 ✅ | Extend the already-shipped runtime tables with exec invocation/output/cancel fields using a normal forward migration | `backend/app/models/projects.py`, `backend/migrations/versions/0004_runtime_exec_dispatch.py` | unique non-null `invocation_id`; bounded stdout/stderr; committed cancel signal; one Alembic head |
| P4.1b ✅ | Persist bounded async exec request inputs | `backend/migrations/versions/0005_runtime_exec_request.py` | Worker reconstructs command+timeout from Postgres row id only; preview remains the sole API/audit projection |
| P4.1c ✅ | Add cross-worker open/close operation claim | `backend/migrations/versions/0006_runtime_operation_claim.py` | Duplicate/recovery delivery cannot overlap Docker I/O; runtime arq jobs use app-owned retry only |
| P4.2 ✅ | `runtime_open` / `runtime_close` service + tools | `backend/app/services/project_runtime.py`, `backend/app/tools/runtime_tools.py` | Open acquires lease + bumps fence, probes capabilities, records `ingress_bytes`; close persists the boundary **before** teardown |
| P4.3 ✅ | `fs.*` host-side tools over the working-copy effective tree | `backend/app/services/project_fs.py`, `backend/app/tools/fs_tools.py`, `backend/app/services/project_workcopy.py` | With `SANDBOX_KIND=disabled`, every `fs.*` works; `if_hash`/anchored edit conflict is zero-write; running runtime ⇒ `runtime_busy`; ready runtime is invalidated atomically and rematerialized on next exec |
| P4.3a ✅ | Temporary args-aware policy without ToolDescriptor | `backend/app/permissions/{policy,grants,service}.py`, `backend/app/core/loop.py` | Normal fs writes allow; sensitive/recursive delete asks; `sh_exec` asks unless the strict platform command matcher allows it; previews exclude file contents |
| P4.4 ✅ | `sh_exec` with hot-container reuse, bounded transient output, cancel and crash recovery | `backend/app/tools/sh_tools.py`, `backend/app/services/project_runtime.py`, `backend/app/sandbox/runtime.py`, `backend/app/api/projects.py`, `backend/app/worker.py` | Worker-owned REST `202`; agent dispatch committed before Docker; cancel persists boundary; app-owned recovery; no arq double retry |
| ~~P4.5~~ | ~~`run_test` / `run_lint` over probed capabilities~~ | ~~`backend/app/tools/run_tools.py`~~ | **DROPPED**. The AC moves to `sh_exec`: a missing tool → `environment_missing_dependencies` **naming what the image does have**, never a bare exit 127 |
| P4.6 ✅ | Delete `project_run` / `project_tree` / `project_read`; rewrite REST to the runtime routes; delete `POST /projects/{id}/sandbox-runs` | `backend/app/tools/project_tools.py`, `backend/app/api/projects.py` | Route inventory shows the new routes and none of the old |
| P4.7 ✅ | Route-inventory generator + CI step (O-13) | `backend/scripts/route_inventory.py`, `docs/contracts/route-inventory.md`, `.github/workflows/ci.yml` | CI fails on undeclared route drift; `/files/*` and `sandbox-runs` cannot reappear |
| P4.V ✅ | Full gate + real-Docker + agent/human acceptance | — | Live chat drove fs writes → runtime open → pytest exit 1 → fs edit → rematerialize → exit 0; human clicked diff + Save selected; 390 px overflow 0; Run/log/Stop remains explicitly P5 |

**P4 exit:** the agent completes a real edit/test loop; `fs.*` provably survives a disabled sandbox;
`sh_exec` approval preview shows the exact command and paths; the old tools and route are gone and
CI enforces it.

**P4 exit met 2026-08-03.** Full backend: 553 passed (+32 docker deselected); real-Docker:
32 passed; ruff/format/mypy/Alembic head `0006`/route inventory and frontend lint/build green.
Live real-provider agent lane called exactly `fs_write`×2 → `runtime_open` → `sh_exec`
(pytest exit 1) → `fs_edit` → `sh_exec` (exit 0, `1 passed`). Human lane opened the existing
Change Review, inspected the final `return a + b` diff and clicked Save selected; mobile 390 px
overflow = 0. UX acceptance: change review remains clear, but there is intentionally still no
human Run button, streaming log, Stop or Runs tab — those are P5, so B-8 remains open.

### TR.10 P5 — `/Project` three-column UI (human lane)

| # | Task | Paths | AC |
|---|---|---|---|
| P5.1 | Three-column Project-bound chat layout | `frontend/src/views/ChatView.tsx`, `frontend/src/styles.css` | Tree / conversation / right panel; 390 px overflow = 0 |
| P5.2 | Editable file tree writing the same overlay as the agent | new `frontend/src/components/ProjectTree.tsx`, `frontend/src/api.ts` | A human edit and an agent edit appear in **one** change set |
| P5.3 | Run control + streaming log panel + Stop | new `frontend/src/components/RunPanel.tsx` | Real streaming output; Stop cancels; the dead `createSandboxRun` client is replaced |
| P5.4 | `Changes / Runs / Artifacts` tabs; Runs shows history with named termination reasons | `frontend/src/components/ChangeReview.tsx` + new `RunsPanel.tsx` | A failed run shows an explicit banner (the pre-existing observability gap noted in the W3 exit) |
| P5.5 | One-click rebase-review on `409 head_moved` | `frontend/src/components/ChangeReview.tsx` | Conflict offers a path forward, not only a message |
| P5.V | **Two-lane Playwright + UX acceptance** (restart the stack first) | — | Agent lane and human lane both pass; matrix UI cells flip to ✅ only after a **real click**; UX notes recorded |

**P5 exit (this closes B-8):** the human lane exists and works end-to-end; `docs/11` §9 has no
non-❌ ⬜ cell for this program; UX review recorded.

### TR.11 Failure injection matrix (every row must map to exactly one named reason)

**Status after P3 (measured 2026-07-31, real Docker unless noted).** ✅ = mapped and verified ·
⬜ = not implemented yet, with the owning phase named. Nothing here is assumed.

| Injection | How | Expected | Status |
|---|---|---|---|
| Daemon unreachable | point `DOCKER_HOST` at a dead socket | `runtime_daemon_unreachable` | ✅ real (`DOCKER_HOST=tcp://127.0.0.1:1`); **also** covered mid-run — a daemon that dies during `container.wait` is `runtime_daemon_unreachable`, **not** `wall_timeout` (P3 review fix) |
| Image missing | `SANDBOX_IMAGE` = unknown digest | `runtime_image_missing` | ✅ real (a **pinned** but absent digest; an unpinned tag is a different reason, below) |
| Image unpinned / foreign | `SANDBOX_IMAGE` = a tag, empty, or a digest for another image | `runtime_image_untrusted` | ✅ real + unit (P3 review fix — enforced before any container is created) |
| Container create fails | invalid resource limit | `runtime_start_failed` | ✅ fake client (a real invalid limit is rejected client-side before the API call) |
| tar put/get fails | fake client raising mid-stream | `runtime_transport_failed` | ✅ fake client |
| Sandbox disabled | `SANDBOX_KIND=disabled` | `sandbox_disabled`, **and edits still persist** | ✅ (the `fs.*` half of this row is P4; the *degradation* is verified today) |
| Wall timeout | `sleep 999` | `wall_timeout` | ✅ real. **Only** a genuine read timeout qualifies (P3 review fix): the runtime library reports a wait timeout with the *same exception class* as an unreachable daemon, so the chain is inspected rather than the class |
| OOM | allocate past `SANDBOX_MEM_MB` | `mem_limit` | ✅ real (docker `State.OOMKilled`, exit 137) |
| pids | fork bomb | `pids_limit` | ⬜ **NOT mapped.** Measured: the limit *is* enforced (fork fails) but docker exposes no pids-kill signal, so it surfaces as a plain non-zero exit. Naming it would be a guess dressed as a fact. Needs a decision in **P4**. |
| Output flood | 10⁶ lines | `output_limit` + spill reference | ⬜ **partial.** Output is capped at 1 MiB with `output_truncated=true`; there is no `output_limit` reason and no typed spill reference — that is api §7.2 debt, **P2.8**. |
| Change-set overflow | write past `WORKING_COPY_MAX_*` | `changeset_bounds` + `truncated=true` | ✅ unit (DB) |
| Escaping symlink in egress | craft in `/work` | `path_escape` | ✅ unit + end-to-end (persists nothing the container produced) |
| Stale fence | bump the lease behind a live session | `fence_lost`, overlay **not** published | ✅ unit (pre-existing) |
| Head moved | Save from a second chat | `409 head_moved`, nothing applied | ✅ unit (pre-existing) |
| Missing blob | delete a MinIO object | named error (`blob_missing`), no partial tar | ✅ unit |
| Cancel | press Stop mid-run | `cancelled`, boundary still persisted | ⬜ **P4** (no cancel path and no Stop control exist yet) |
| Credential at the boundary | KEK-shaped `.env` in the project | never in tar/overlay/change set/artifact/log/observation | ✅ unit + end-to-end + real container |

### TR.12 Docker topology matrix (P3 gate)

**Status after P3 (2026-07-31).** Only the dev topology is verified. The rest are recorded as
unverified rather than assumed green (owner decision D-7).

| Topology | Requirement | Status |
|---|---|---|
| Windows + Docker Desktop + DooD (**the dev stack**) | `uv run pytest -m docker` green; real exit codes | ✅ **verified** — 17 passed, plus a live run *from inside the worker container* over the mounted socket, plus the chat lane with the real provider |
| Linux + DooD | green | ⬜ **not verified** (no Linux host in this environment) |
| Linux + DinD | green | ⬜ **not verified** |
| No Docker (CI default) | all docker-marked tests skip; `sandbox_disabled` observation is named and actionable | ✅ verified — `addopts = "-q -m 'not docker'"` deselects 17; the `docker_runner_image` fixture skips with an actionable message when the daemon or image is missing |
| rootless Docker | verified manually at least once, recorded in the phase exit note | ⬜ **not verified** |

### TR.13 Telemetry, budgets and canaries

- **Telemetry:** `toolset.resolved` per turn (`core_bytes`, `total_bytes`, `tools_offered`, loaded
  toolsets); `runtime.state`/`runtime.output` frames; OTel `execute_tool` spans gain
  `sherpa.toolset` and `sherpa.runtime_session_id`; a `termination_reason` histogram.
- **Budget gate:** `core_bytes <= TOOL_CATALOG_CORE_MAX_BYTES` (6,144) asserted in tests **and**
  enforced at startup. Record the measured value in the P2 commit body so the 19,848 → ≤6,144 delta
  is auditable.
- **Security canaries (must fail the build if broken):** (1) synthetic KEK never leaves the vault
  boundary (existing); (2) **new** — a secret-shaped file in a project tree never appears in the tar,
  overlay, change set, artifact, log, prompt or tool result; (3) `CONNECTOR_ANALYSIS` resolves to
  zero tools; (4) no `type="bind"` in `app/sandbox/`; (5) route inventory contains no `/files/*` and
  no `sandbox-runs`.

### TR.14 Definition of Done per phase

Every phase, in addition to its own exit criteria: `uv run pytest` green · `ruff check` +
`ruff format --check` clean · `mypy app` clean · `npm run lint` + `npm run build` green (when the
frontend changed) · contracts/ADRs honoured and status markers updated · capability matrix updated ·
two-lane Playwright after the phase (restart the stack first; the human lane is also a **UX
acceptance review** with concrete suggestions) · `docs/STATUS.md` updated · small focused commits
(one per task, per AGENTS.md §3).

### TR.15 Explicitly roadmap, not this program

**P6 production runner** — gVisor (`runsc`) or microVM, no shared `docker.sock`, per-tenant
isolation/egress/quota; this is ADR-039's do-not-ship gate and is unchanged by ADR-047.
**P7 in-sandbox coding agent** — a `SubAgentProvider` exposing `delegate.code_task(runtime_session_id,
goal, budget)` against the **same** RuntimeSession, overlay, budget and audit path. Also roadmap:
MCP/plugin providers, hosted dev-server preview, W4 GitHub sync/push/PR, and the Plan object.

**What closes what:** **B-2 closes at the end of P2** (plus the P1 deletions it depends on).
**B-8 closes at the end of P5** (P0 honesty + P3 transport + P4 runtime + P5 human lane). Anything in
TR.15 is roadmap and must not be used to justify leaving B-2 or B-8 open.
