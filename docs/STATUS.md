# STATUS — living project status

> The resume anchor. A coding agent reads this first (per [`../AGENTS.md`](../AGENTS.md)), then picks the next ready task in [`IMPLEMENTATION.md`](IMPLEMENTATION.md). **Update this file at the end of every task** (tick the table, move "Next ready").
>
> Last updated: 2026-07-22 · Phase: **Milestones 1–5 (memory+manual-note RAG, files/MinIO, sandbox, QQ/IM, agentic email) all complete + browser-verified**. The shipped RAG is archival semantic notes, not a source-backed document knowledge base. v1 + v1 wrap-up + UI/UX backlog + the responsive **Quiet Work** redesign are done. All requested post-v1 milestones done; next candidates in `09-roadmap.md` (#6 cron … #11 eval).

## Real model wired ✅
The mock is no longer the only provider. `OpenAICompatibleProvider` (streaming) targets the user's **litellm proxy at host `:4000`** forwarding GitHub Copilot; default model `claude-sonnet-4.6`. Config-driven (`PROVIDER_KIND=openai_compatible` + `PROVIDER_API_KEY` in a gitignored `.env`; the worker reaches the host via `host.docker.internal`). `PROVIDER_KIND=mock` remains the default for offline dev/tests. **Live-verified in the browser** (real replies "Paris.", a real Sherpa definition). To run the stack with the real model:
`docker compose -f infra/docker-compose.yml --env-file .env up --build -d`.

## Where we are
The **design + contracts + runnable skeleton** are done, and the **v1 durable spine (M1) is complete and verified end-to-end**: persistence, event journal + outbox, Redis/SSE fan-out, effect idempotency, provider+tools, the bounded core loop, durable prompt admission, the REST auth + session/message surface, the credential vault (AEAD/KEK), run traces + structured logging, and the web chat client (#1–#13) are all implemented and green. A live smoke (login → session → prompt → real arq worker loop → outbox relay → SSE `run.settled` → persisted transcript) passes. Next: **M2 — Personal Inbox-to-Action (Gmail → candidate → todo → reminder)**.

## Verified state
- **Backend** (`backend/`, via `uv`): `uv sync` ok · `uv run pytest` → **129 passed** (needs Postgres+Redis up; MinIO for file tests; vault/formatter/compaction/spill-unit tests need none) · `ruff check`+`format --check` clean · `mypy app` clean (125 files). `uv.lock` committed. Schema at alembic `0018`.
- **Frontend** (`frontend/`): Vite+React+TS SPA with the responsive `Quiet Work` design system across login, chat, inbox, activity, schedules, settings, memory, files, messaging, and connectors. `npm run build` + `npm run lint` green. `package-lock.json` committed.
- **Runtime**: web (`uv run uvicorn app.main:app`) + worker (`uv run arq app.worker.WorkerSettings`, runs the run loop + outbox relay) verified live against docker Postgres+Redis.
- **Infra**: `infra/docker-compose.yml` (postgres+redis+web+worker+frontend) defined. **CI**: `.github/workflows/ci.yml` (backend uv lint/type/test + frontend build).

## Done ✅
- Architecture design docs (`docs/00–09`), 22 ADRs (`docs/decisions.md`), three-role review + action list (`docs/reviews/`).
- Confirmed v1 scope (ADR-022) + value/risk milestones (`docs/09-roadmap.md`).
- UI: original "Daybreak" / "Alpine" concept sets plus the production, Notion-inspired `Quiet Work` system (`docs/design-refined/README.md`).
- **Readiness kit**: tech-stack lock (`docs/10-tech-stack.md`), **frozen contracts** (`docs/contracts/`), `AGENTS.md`, runnable+green skeleton, infra, CI, this plan/status.
- **M1 durable spine (#1–#13)**: full web prompt → durable admission → worker bounded loop (mock provider + read-only tool) → events streamed to the chat UI via SSE → transcript persisted; per-run trace + rollups; single-owner auth; AEAD credential vault.

## ▶ Next ready task
**M-tools is complete** (T1–T8: ToolContext + capability layer, ALLOWED policy engine, and candidate/todo/connector/schedule/read-settings tools + output spill). The agent can drive every own-data UI capability via chat, permission-gated. The **UI-completion** pass then added session mgmt (new chat) + Schedules + Settings pages. Ready directions (⚠️ item 0 is a **newly-found correctness bug** — do before the polish items):
0. **Context-fidelity fix + agent observability (v1 wrap-up · correctness, found 2026-07-21).** **Bug:** every prompt starts a *new run*, and a run rebuilds provider history **text-only** from `messages`/`message_parts` (`core/loop._load_transcript`); prior `tool_use`/`tool_result` live only in the event journal, so **across runs the model loses all evidence it called a tool** → it "forgets", apologizes, and re-does/denies work (observed: created a todo, then on a follow-up claimed it never called the tool and re-created it). Same root cause also weakens mid-run crash resume. **Fix — Option B (✅ done, commit `de1eb91`):** `app/core/history.assemble_provider_history()` reconstructs the OpenAI-protocol window (assistant + `tool_calls`, `role:tool` results, `permission.asked`/deny placeholders, crash-halfway backfill) from the event journal (the declared tool-history source of truth), replacing the text-only reload — no contract change; regression tests in `test_history.py` prove run2's provider **receives** run1's `tool_use` (pytest 90 green). *(Option A = persist tool steps as `messages`/`parts` — rejected for now: needs a frozen-contract change + ADR + migration + a wide message-consumer audit.)* **⏸️ Deferred — observability (synergistic, the 2nd ask; owner deferred 2026-07-21):** persist **each LLM call's exact assembled input** as a redacted `model.request` journal event and/or a `generations` row, and emit chat-loop generation records (model / prompt-version / tokens / `stop_reason`), so "what each LLM call sent + every internal step" is inspectable for human debugging. refs: `core/loop.py`, `events/journal.py`, ADR-016/017, docs/07-observability.
1. **UI/UX backlog** — ✅ **UX-1…UX-16 cleared + browser-verified** ([`ui-backlog.md`](ui-backlog.md)): the earlier functional gaps remain fixed; the 2026-07-22 full-product pass also shipped the responsive `Quiet Work` redesign, mobile drawer, rendered Markdown, clearer empty states, progressive disclosure for connector internals, and safer data-control hierarchy.
2. **v1 approval closure** — ✅ **done + browser-verified** (commit `c29b86f`): `POST /permissions/{id}/resolve` → resume job (`core/resume.py`) executes the gated `send_email` end-to-end (recovering approved args from the bound `tool-call` event); ChatView renders Approve/Reject, capturing the single-use nonce from the `permission.asked` SSE event + envelope fields from `GET /permissions`. Browser E2E: model called `send_email` → approval card → **Approve** → activity showed "email sent to test@example.com". `send_email` stays a v1 stub; `allow_session`/`always` grant persistence deferred (static policy engine). **Follow-up (contract reconciliation, own ADR):** `permission.asked` carries the nonce+preview beyond its frozen minimal schema (events-and-effects §2.3) — nonce-in-journal delivery for web needs a decision; a `permission.resolved` event (already in the catalog) could drive UI card removal.
3. ~~**M3 eval harness**~~ — **deferred out of v1** into post-v1 #11 (eval flywheel) per **ADR-024**: v1 is single-user self-hosted, the owner *is* the eval loop, so no external-user quality gate now; re-instate before onboarding external beta users. Optional cheap insurance: a ~1-day deterministic mock regression lane on the extraction path.
4. **R-SESSION-SEARCH research** — ✅ complete, awaiting owner decision: surveyed Copilot CLI, Hermes, Codex, Claude Code, and Gemini CLI; recommends a Postgres canonical + rebuildable `session_search_entries` projection, lexical/CJK/trigram first, typed anchors, and state-specific Resume/Reconnect/Recover. Report: [`research/session-search-report.md`](research/session-search-report.md); static prototype: [`design-session-library/index.html`](design-session-library/index.html).
5. **R-WORKSPACE-PRODUCT research** — ✅ complete; Project Chat/sandbox lifecycle direction owner-confirmed: Personal workspace contains Projects + Drive; Chat is General or immutably Project-bound; a durable task working copy spans turns while scratch volume/warm container remain rebuildable caches; the initial executor is Sherpa built-in tools only (no embedded coding agent). Storage remains Postgres canonical metadata + immutable tenant-scoped MinIO blobs with configurable 5 GiB personal quota plus tenant/deployment ceilings; Git remote is optional and external writes stay approval-gated. Report: [`research/workspace-product-report.md`](research/workspace-product-report.md); static prototype: [`research/workspace-product-prototype/index.html`](research/workspace-product-prototype/index.html).
6. **R-KNOWLEDGE-BASE research** — ✅ complete: audited the shipped manual-note RAG, researched production KB patterns, and designed a separate file-backed Knowledge vertical slice with async source/version ingestion, hybrid retrieval, citations, multilingual handling, UI, tools, and release gates. Recommendation: GO for the narrow slice after owner approval and ADR/contract review; no implementation has started. Report: [`research/knowledge-base.md`](research/knowledge-base.md).
Then the remaining **post-v1 milestones** in `09-roadmap.md` (cron → GitHub → provider/sub-agent → plugins → teams → eval), in the owner's chosen order.

## In progress
_Nothing in progress._ **M-tools shipped** (ADR-023, [`11-agent-tool-surface.md`](11-agent-tool-surface.md)): app/services/ capability layer + REST/Tool dual adapters; the agent tools = list/accept/edit/dismiss candidates, create/update/complete/list todos (+ POST /todos, migration 0014 for standalone agent todos), list/sync connectors, create/list/cancel reminders + digests (+ /schedules REST), list notifications/activity + get/update settings; ALLOWED policy engine (own-data writes allowed, external actions ask); tool output spill.
Dev DB: `docker compose -f infra/docker-compose.yml --env-file .env up --build -d` (schema at alembic `0014`; `--env-file .env` enables the real model). Note: `uv run pytest` wipes the owner tenant → re-login in the browser. **SPA routes must not collide with an API proxy prefix** (Activity UI lives at `/data`).

## Blockers
- **None for M1.** M1 runs on the **mock provider** and needs no external accounts.
- Open impl params bind **M2** (initial real provider, Gmail OAuth mode, retention, notification defaults). Safe v1 defaults are set in `docs/contracts/config-and-secrets.md`; confirm before M2 ships to real users (see `docs/reviews/README.md §5`).

## Task tracker (mirror of IMPLEMENTATION.md)
| Task | Status |
|---|---|
| S0/S1 walking skeleton + tooling | ✅ done |
| M1 #1 persistence base | ✅ done |
| M1 #2 alembic + initial migration | ✅ done |
| M1 #3 event journal + outbox | ✅ done |
| M1 #4 redis streams + SSE catch-up | ✅ done |
| M1 #5 effect/idempotency | ✅ done |
| M1 #6 provider + mock | ✅ done |
| M1 #7 tool interface + starter tools | ✅ done |
| M1 #8 core loop (worker) | ✅ done |
| M1 #9 durable prompt admission | ✅ done |
| M1 #10 REST sessions/messages + auth | ✅ done |
| M1 #11 config + secrets (AEAD/KEK) | ✅ done |
| M1 #12 observability | ✅ done |
| M1 #13 frontend chat | ✅ done |
| **M1 exit** (web prompt → loop → SSE → transcript; live-verified) | ✅ **met** |
| M2 provider — real OpenAI-compatible (litellm/Copilot) | ✅ done |
| M2 #14 connector base + Gmail read-only OAuth | ✅ done |
| M2 #15 Gmail incremental sync → connector_items | ✅ done |
| M2 #16 CONNECTOR_ANALYSIS extraction → candidates | ✅ done |
| M2 #17 candidate lifecycle + Inbox UI | ✅ done |
| M2 #18 scheduler + periodic sync/analyze | ✅ done |
| M2 #19 notifications (delivery + web inbox + settings) | ✅ done |
| M2 #20 permission engine + approval envelope (gate send_email) | ✅ done |
| M2 #21 activity receipts + data controls (export/delete) | ✅ done |
| M2 #22 transcript compaction | ✅ done |
| M-tools T1–T8 agent tool surface (candidates/todos/connectors/schedules/settings + spill) | ✅ done |
| v1 wrap-up: context-fidelity fix (cross-run tool history) | ✅ done (`de1eb91`, browser-verified) |
| v1 wrap-up: approval closure (resume + web renderer) | ✅ done (`c29b86f`, browser-verified) |
| UI/UX backlog P1–P3 (UX-1…UX-11) | ✅ done (browser-verified; +2 infra fixes) |
| **Quiet Work full UI redesign** (UX-12…UX-16; desktop + 390 px mobile) | ✅ done (Playwright browser-verified) |
| **R-SESSION-SEARCH** cloud session persistence/search/resume research | ✅ complete; owner decision pending ([report](research/session-search-report.md), [static UI](design-session-library/index.html)) |
| **R-WORKSPACE-PRODUCT** personal drive/project workspace research | ✅ complete; Project Chat + durable working-copy lifecycle owner-confirmed, implementation pending ([report](research/workspace-product-report.md), [static UI](research/workspace-product-prototype/index.html)) |
| **Milestone 1 — core memory** (storage + tools + context injection + REST + UI) | ✅ done (browser-verified: agent stores + recalls cross-session) |
| **Milestone 1 — pgvector archival-note RAG** (manual passages + hybrid retrieval + tools + REST + UI) | ✅ done (browser-verified: agent notes + searches cross-session with real embeddings; not a document KB) |
| **R-KNOWLEDGE-BASE** source-backed personal knowledge research/design | ✅ complete; narrow file-backed slice recommended, implementation not approved (`docs/research/knowledge-base.md`) |
| **Milestone 2 — personal files / MinIO** (object store + tools + REST + UI) | ✅ done (browser-verified: agent write→MinIO, Files page list, agent read cross-session) |
| **Milestone 3 — code execution / sandbox** (hardened Docker container + run_code) | ✅ done (browser-verified with real Docker: agent computed 15!=1307674368000, exit 0) |
| **Milestone 4 — QQ/IM inbound + IM approval renderer** — **now on the OFFICIAL QQ platform** (ADR-028: qq-botpy WebSocket; OneBot removed) | ✅ done + **REAL-ACCOUNT VERIFIED** (owner 2026-07-22): QR scan bind captured owner openid, the worker WS gateway connected, and a real inbound C2C message (`msg_id ROBOT1.0_…`) created a session. Also browser-verified: Connectors manual-config→Connected + sealed secret; live `q.qq.com` QR endpoint. |
| **Milestone 5 — agentic email** (AgentMail: unified send seam + inbound email → loop + email approval) | ✅ done (real AgentMail send verified: agent's approved send_email landed in the inbox as "Sherpa milestone 5 verified"; inbound + approve-over-email via browser) |

**Milestones 1 (memory+manual-note RAG), 2 (files/MinIO), 3 (sandbox), 4 (QQ/IM), 5 (agentic email) all DONE + browser-verified**, on top of v1 + v1 wrap-up + UI/UX backlog. A full source-backed document Knowledge product is separately designed but not implemented. **Milestone 5**: ADR-027 + the roadmap unify-note — `send_email` (was a stub) + notification digests now share the single `build_email_sender()` seam; `email_kind='agentmail'` sends for real via `AgentMailClient`. Inbound agentic email (`POST /channels/email/webhook`, Svix-verified + owner-email allowlist) reuses the SAME generic channel path as QQ (session `channel='email'` → loop); email-side approval reuses the v1 base. Multi-channel Messaging UI (QQ + email sections). **Real send verified with the owner's AgentMail key** (email landed in inbox). Deferred (manual, needs a public webhook URL): real AgentMail→webhook inbound; open-sender SAFE-tier (ADR-013).

Milestone 4: **ADR-028 — migrated QQ from the OneBot bridge to Tencent's OFFICIAL platform** (api-v2, `qq-botpy` WebSocket; OneBot transport removed). Config is runtime/DB-backed on the revived **Connectors page** (`/integrations`): manual AppID/Secret **or** the official QR one-click bind (endpoint verified OPEN to third parties — the live `q.qq.com` create/poll works from our pure-Python port; the scan returns `user_openid` = owner). AppSecret sealed in the AEAD vault (`channel_configs`); the botpy WS client runs as a leader-gated reconnecting worker task; replies are passive C2C replies keyed by the stored inbound `msg_id`; approvals reuse the v1 base (`channel='qq'`). **Deferred / manual acceptance:** completing a real QR scan + real inbound over the WS needs a live bot account (owner); IP whitelist on NAT; group messages; scheduled firings → QQ (frozen schedules CHECK).

**▶ All requested post-v1 milestones (memory → files → sandbox → QQ/IM → agentic email) are complete.** Next candidates (roadmap `09-roadmap.md`): #6 general cron, #7 GitHub connector, #8 multi-provider failover + sub-agents, #9 plugins/MCP, #10 multi-user/teams, #11 eval flywheel.

## How to update
On finishing a task: set its row ✅, move "Next ready", note anything a future agent must know (schema changes, new commands, gotchas), bump "Last updated", and commit (the STATUS bump can ride with the task commit).
