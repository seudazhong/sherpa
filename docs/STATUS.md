# STATUS — living project status

> The resume anchor. A coding agent reads this first (per [`../AGENTS.md`](../AGENTS.md)), then picks the next ready task in [`IMPLEMENTATION.md`](IMPLEMENTATION.md). **Update this file at the end of every task** (tick the table, move "Next ready").
>
> Last updated: 2026-07-20 · Phase: **M1 in progress (durable spine)**.

## Where we are
The **design + contracts + runnable skeleton** are done. The walking skeleton boots and is green. Implementation of the v1 durable spine (M1) has **not started**.

## Verified state
- **Backend** (`backend/`, via `uv`): `uv sync` ok · `uv run pytest` → **2 passed** · `ruff check`+`format --check` clean · `mypy app` clean. `uv.lock` committed.
- **Frontend** (`frontend/`): Vite+React+TS scaffold present. ⚠️ Needs `npm ci` before `npm run build` (not yet installed/verified in this env).
- **Infra**: `infra/docker-compose.yml` (postgres+redis+web+worker+frontend) defined; not yet brought up.
- **CI**: `.github/workflows/ci.yml` (backend uv lint/type/test + frontend build).

## Done ✅
- Architecture design docs (`docs/00–09`), 22 ADRs (`docs/decisions.md`), three-role review + action list (`docs/reviews/`).
- Confirmed v1 scope (ADR-022) + value/risk milestones (`docs/09-roadmap.md`).
- UI: bright "Daybreak" set (recommended) + v1 "Alpine" set + scope map (`docs/design-bright/README.md`).
- **Readiness kit**: tech-stack lock (`docs/10-tech-stack.md`), **frozen contracts** (`docs/contracts/` — data-model, events-and-effects, api, config-and-secrets), `AGENTS.md`, runnable+green skeleton, infra, CI, this plan/status.

## ▶ Next ready task
**M1 #3 — Event journal + outbox** (append-only events + transactional outbox; per-run `seq`). See [`IMPLEMENTATION.md`](IMPLEMENTATION.md) and `contracts/events-and-effects.md`.

## In progress
**M1 — durable spine.** #1 (persistence base) + #2 (alembic + initial migration) done; #3 next.
Dev DB: `docker compose -f infra/docker-compose.yml up -d postgres redis` (schema at alembic `0001`).

## Blockers
- **None for M1.** M1 runs on the **mock provider** and needs no external accounts.
- Open impl params bind **M2** (initial real provider, Gmail OAuth mode, retention, notification defaults). Safe v1 defaults are set in `docs/contracts/config-and-secrets.md`; confirm before M2 ships to real users (see `docs/reviews/README.md §5`).

## Task tracker (mirror of IMPLEMENTATION.md)
| Task | Status |
|---|---|
| S0/S1 walking skeleton + tooling | ✅ done |
| M1 #1 persistence base | ✅ done |
| M1 #2 alembic + initial migration | ✅ done |
| M1 #3 event journal + outbox | ⬜ next |
| M1 #4 redis streams + SSE catch-up | ⬜ |
| M1 #5 effect/idempotency | ⬜ |
| M1 #6 provider + mock | ⬜ |
| M1 #7 tool interface + starter tools | ⬜ |
| M1 #8 core loop (worker) | ⬜ |
| M1 #9 durable prompt admission | ⬜ |
| M1 #10 REST sessions/messages + auth | ⬜ |
| M1 #11 config + secrets (AEAD/KEK) | ⬜ |
| M1 #12 observability | ⬜ |
| M1 #13 frontend chat | ⬜ |
| M2 #14–22 Gmail→candidate→todo→reminder | ⬜ |

## How to update
On finishing a task: set its row ✅, move "Next ready", note anything a future agent must know (schema changes, new commands, gotchas), bump "Last updated", and commit (the STATUS bump can ride with the task commit).
