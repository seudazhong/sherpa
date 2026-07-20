# STATUS — living project status

> The resume anchor. A coding agent reads this first (per [`../AGENTS.md`](../AGENTS.md)), then picks the next ready task in [`IMPLEMENTATION.md`](IMPLEMENTATION.md). **Update this file at the end of every task** (tick the table, move "Next ready").
>
> Last updated: 2026-07-20 · Phase: **M2 in progress (Personal Inbox-to-Action)**. M1 complete.

## Real model wired ✅
The mock is no longer the only provider. `OpenAICompatibleProvider` (streaming) targets the user's **litellm proxy at host `:4000`** forwarding GitHub Copilot; default model `claude-sonnet-4.6`. Config-driven (`PROVIDER_KIND=openai_compatible` + `PROVIDER_API_KEY` in a gitignored `.env`; the worker reaches the host via `host.docker.internal`). `PROVIDER_KIND=mock` remains the default for offline dev/tests. **Live-verified in the browser** (real replies "Paris.", a real Sherpa definition). To run the stack with the real model:
`docker compose -f infra/docker-compose.yml --env-file .env up --build -d`.

## Where we are
The **design + contracts + runnable skeleton** are done, and the **v1 durable spine (M1) is complete and verified end-to-end**: persistence, event journal + outbox, Redis/SSE fan-out, effect idempotency, provider+tools, the bounded core loop, durable prompt admission, the REST auth + session/message surface, the credential vault (AEAD/KEK), run traces + structured logging, and the web chat client (#1–#13) are all implemented and green. A live smoke (login → session → prompt → real arq worker loop → outbox relay → SSE `run.settled` → persisted transcript) passes. Next: **M2 — Personal Inbox-to-Action (Gmail → candidate → todo → reminder)**.

## Verified state
- **Backend** (`backend/`, via `uv`): `uv sync` ok · `uv run pytest` → **34 passed** (needs Postgres+Redis up; vault/formatter tests need neither) · `ruff check`+`format --check` clean · `mypy app` clean. `uv.lock` committed. Schema at alembic `0005`.
- **Frontend** (`frontend/`): Vite+React+TS SPA (login + chat). `npm install` done, `npm run build` + `npm run lint` green. `package-lock.json` committed.
- **Runtime**: web (`uv run uvicorn app.main:app`) + worker (`uv run arq app.worker.WorkerSettings`, runs the run loop + outbox relay) verified live against docker Postgres+Redis.
- **Infra**: `infra/docker-compose.yml` (postgres+redis+web+worker+frontend) defined. **CI**: `.github/workflows/ci.yml` (backend uv lint/type/test + frontend build).

## Done ✅
- Architecture design docs (`docs/00–09`), 22 ADRs (`docs/decisions.md`), three-role review + action list (`docs/reviews/`).
- Confirmed v1 scope (ADR-022) + value/risk milestones (`docs/09-roadmap.md`).
- UI: bright "Daybreak" set (recommended) + v1 "Alpine" set + scope map (`docs/design-bright/README.md`).
- **Readiness kit**: tech-stack lock (`docs/10-tech-stack.md`), **frozen contracts** (`docs/contracts/`), `AGENTS.md`, runnable+green skeleton, infra, CI, this plan/status.
- **M1 durable spine (#1–#13)**: full web prompt → durable admission → worker bounded loop (mock provider + read-only tool) → events streamed to the chat UI via SSE → transcript persisted; per-run trace + rollups; single-owner auth; AEAD credential vault.

## ▶ Next ready task
**M2 #14 — Connector base + Gmail read-only OAuth**: `connectors`/`connector_items` tables, OAuth connect/disconnect endpoints, token sealed via the M1 AEAD vault; tested with a mocked Google. First real external connector. (Real model provider is already wired — see above.)

## In progress
**None — M1 is complete.** Ready to start M2 (Gmail → candidate → todo → reminder). M2 introduces the first real external connector, so confirm the open impl params first.
Dev DB: `docker compose -f infra/docker-compose.yml up -d postgres redis` (schema at alembic `0005`).

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
| M2 #14–22 Gmail→candidate→todo→reminder | ⬜ next |

## How to update
On finishing a task: set its row ✅, move "Next ready", note anything a future agent must know (schema changes, new commands, gotchas), bump "Last updated", and commit (the STATUS bump can ride with the task commit).
