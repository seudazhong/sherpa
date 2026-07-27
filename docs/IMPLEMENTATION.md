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


## Cross-cutting (do continuously, not a separate phase)
- **Tests with every task** — deterministic, mock provider, `pytest-asyncio`. No real model calls in tests.
- **Migrations** — one Alembic head; every schema change is a migration.
- **Eval harness** (~~M3~~): extraction-precision goldens + regression dataset — **deferred out of v1 into post-v1 #11** (eval flywheel) per **ADR-024** (single-user self-hosted; owner is the eval loop; re-instate before external beta). A ~1-day deterministic mock regression lane on the extraction path is the optional minimum guard if that path changes.

## Open decisions that bind M2 (not M1) — see [reviews/README.md §5](reviews/README.md)
Initial real provider/model · Gmail OAuth operating mode · data-retention window · notification defaults. **Safe v1 defaults are in `contracts/config-and-secrets.md`** so M1 (and M2 scaffolding) proceed unblocked; confirm before M2 ships to real users.
