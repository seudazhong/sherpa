# AGENTS.md — working agreement for coding agents

You are building **Sherpa**, a multi-tenant cloud agent runtime. This file is the contract for how to work here. Read it fully before touching code.

## 0. Start here (resume protocol)
1. Read [`docs/STATUS.md`](docs/STATUS.md) — current phase, what's done, what's next-ready, blockers.
2. Open [`docs/IMPLEMENTATION.md`](docs/IMPLEMENTATION.md) — pick the **next ready task** (dependencies satisfied).
3. Implement it against the **frozen contracts** in [`docs/contracts/`](docs/contracts/) and the ADRs in [`docs/decisions.md`](docs/decisions.md).
4. Verify (see §2), **commit** (see §3), **update `docs/STATUS.md`**. Repeat.

## 1. Source-of-truth hierarchy (when in doubt, higher wins)
1. `docs/decisions.md` — ADRs (esp. **ADR-022 v1 scope**). Decisions are locked; don't silently deviate.
2. `docs/contracts/` — frozen data-model / events-effects / api / config-secrets. Implement to these exactly.
3. `docs/reviews/README.md` — consolidated review + action list.
4. `docs/0x-*.md` — architecture design docs.
5. `docs/design-bright/` — UI mockups (the *vision*; see its README for v1 vs later).

If a contract is wrong/insufficient, **update the contract + an ADR first**, then code. Never fork reality between code and contract.

## 2. Canonical commands & Definition of Done
**Backend** (run in `backend/`, uses **uv**):
```bash
uv sync                                   # install
uv run uvicorn app.main:app --reload      # web
uv run arq app.worker.WorkerSettings      # worker
uv run pytest                             # tests
uv run ruff check . && uv run ruff format --check .   # lint+format
uv run mypy app                           # types
uv run alembic upgrade head               # migrations
```
**Frontend** (run in `frontend/`, uses **npm**): `npm ci` · `npm run dev|build|lint`
**Infra**: `docker compose -f infra/docker-compose.yml --env-file .env up --build`

A task is **Done** only when, for the code you touched:
- [ ] `uv run pytest` green (you added/updated tests — happy path **and** edge cases).
- [ ] `uv run ruff check .` + `ruff format --check .` clean; `uv run mypy app` clean (frontend: `npm run lint` + `npm run build`).
- [ ] Contracts/ADRs honored; docs updated if behavior changed.
- [ ] **User-facing capability ⇒ UI shipped too.** If a capability is something a user can see/do, it is NOT done until it has a UI page/control — not just a REST endpoint and/or agent tool. Update the capability matrix (`docs/11-agent-tool-surface.md §9`, which has a **UI** column); a row is unfinished while its UI cell is ⬜. Never leave a nav item as a "placeholder" for a backend that already ships (either build the page or mark it explicitly deferred with the blocker).
- [ ] **Two Playwright verification lanes after each phase** (restart the stack first, per §0):
      **(a) agent lane** — drive the capability via chat (model → tool); **(b) human lane** — drive the *actual UI control* (click the button/form/page), not only chat. A capability "verified" only through chat while its UI page is missing is a classic miss — check the human lane too.
      Reminder: SPA route names must not collide with an API proxy prefix (e.g. Activity UI is `/data`, Schedules `/reminders`, Settings `/preferences`).
- [ ] `docs/STATUS.md` updated; task committed (§3).

Only run/extend **existing** tooling; don't introduce new frameworks without an ADR.

## 3. Commit convention (IMPORTANT)
**Commit after every completed task** — small, focused commits, not big batches (user preference).
- Identity is set locally: `seudazhong <seudazhong@163.com>`.
- Message: concise imperative subject; body explains what/why; reference task id + ADR/contract.
- Include trailers:
  ```
  Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>
  Copilot-Session: 44048257-622a-4ee7-be67-57691e3c8f8b
  ```
- Never commit secrets or `.env`. `.venv/`, `node_modules/`, `dist/` are gitignored. **Do** commit `backend/uv.lock`.

## 4. Guardrails & invariants (non-negotiable)
**v1 scope (ADR-022):** self-hosted, single-user, **Gmail → Action**. Do **NOT** build deferred features: sandbox/code-exec, Files/MinIO, GitHub, QQ/IM, agentic email, teams/shared-memory, memory/RAG/pgvector, general cron, multi-provider. If a task seems to need one, stop and flag it.

**Loop invariants (docs/04):** ① bound every loop/recovery; every exit has a named reason ② execute tools only on structured `stop_reason == tool_use` ③ persist input before the first model call ④ per-call copy of history ⑤ layered, byte-stable cached prefix; dynamic data on the tail ⑥ bound tool output, spill to disk.

**Data-plane/durability (ADR-016/017):** Postgres event journal + outbox is the recovery source of truth; Redis Streams accelerate; pub/sub is never correctness-critical. Every side effect: persist invocation + idempotency key **before** executing; on `effect_unknown` stop & reconcile, never blind-retry.

**Security (ADR-019):** secrets from env only, AEAD-encrypted at rest, connector-only decrypt, **never logged, never in a sandbox env**. Untrusted content (email) → `CONNECTOR_ANALYSIS` no-tool extraction (ADR-009); candidate-first (ADR-010).

**Tenancy (ADR-015):** every table carries `tenant_id` + composite keys even though v1 is single-owner (forward-compat; don't remove it).

## 5. Conventions
**Python:** 3.11+, full type hints (`mypy` enforced), `async` throughout the request/worker path, `from __future__ import annotations`. Module layout follows `backend/app/<subsystem>/` mirroring the design docs (gateway, core, providers, tools, connectors, scheduler, memory, persistence, observability, workers, api). Errors from tools are **observations** fed back to the model, not exceptions that crash the loop. Keep the core small (narrow-waist): built-ins/MCP/sub-agents present as one tool interface.
**TypeScript:** the UI is a **client of the core event stream** — no agent logic in the frontend. eslint + prettier. Port design tokens from `docs/design-bright/base.css`.
**Tests:** deterministic. Use the **mock provider** (no real model calls in tests). `pytest-asyncio` (asyncio_mode=auto). Test the contract, not the implementation detail.

## 6. Repo map
```
backend/   Python core+workers+api (uv). app/ = code, tests/ = pytest.
frontend/  Vite+React+TS SPA (npm).
infra/     docker-compose + deploy.
docs/      decisions.md (ADRs) · contracts/ (frozen) · 0x-*.md (design) ·
           reviews/ · design-bright/ (UI vision) · IMPLEMENTATION.md · STATUS.md · 10-tech-stack.md
```
When you add a subsystem, keep its dir README (`backend/<x>/README.md`) accurate.
