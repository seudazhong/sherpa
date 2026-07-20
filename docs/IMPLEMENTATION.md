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
> **Deferred:** `memory_*` tools need the two-tier memory tables first (separate task, not in M-tools).

---


## Cross-cutting (do continuously, not a separate phase)
- **Tests with every task** — deterministic, mock provider, `pytest-asyncio`. No real model calls in tests.
- **Migrations** — one Alembic head; every schema change is a migration.
- **Eval harness** (M3): goldens for extraction precision; regression dataset; deferred until M2 lands.

## Open decisions that bind M2 (not M1) — see [reviews/README.md §5](reviews/README.md)
Initial real provider/model · Gmail OAuth operating mode · data-retention window · notification defaults. **Safe v1 defaults are in `contracts/config-and-secrets.md`** so M1 (and M2 scaffolding) proceed unblocked; confirm before M2 ships to real users.
