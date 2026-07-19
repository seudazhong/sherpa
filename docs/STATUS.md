# STATUS — living project status

> The resume anchor. A coding agent reads this first (per [`../AGENTS.md`](../AGENTS.md)), then picks the next ready task in [`IMPLEMENTATION.md`](IMPLEMENTATION.md). **Update this file at the end of every task** (tick the table, move "Next ready").
>
> Last updated: 2026-07-20 · Phase: **M1 in progress (durable spine)**.

## Where we are
The **design + contracts + runnable skeleton** are done. The v1 durable spine (M1) is **nearly complete**: persistence, event journal + outbox, Redis/SSE fan-out, effect idempotency, provider+tools, the bounded core loop, durable prompt admission, the REST auth + session/message surface, the credential vault (AEAD/KEK), and **run traces + structured logging** (#1–#12) are implemented and green. Last M1 task: the frontend chat client (#13).

## Verified state
- **Backend** (`backend/`, via `uv`): `uv sync` ok · `uv run pytest` → **34 passed** (needs Postgres+Redis up; vault/formatter tests need neither) · `ruff check`+`format --check` clean · `mypy app` clean. `uv.lock` committed.
- **Frontend** (`frontend/`): Vite+React+TS scaffold present. ⚠️ Needs `npm ci` before `npm run build` (not yet installed/verified in this env).
- **Infra**: `infra/docker-compose.yml` (postgres+redis+web+worker+frontend) defined; not yet brought up.
- **CI**: `.github/workflows/ci.yml` (backend uv lint/type/test + frontend build).

## Done ✅
- Architecture design docs (`docs/00–09`), 22 ADRs (`docs/decisions.md`), three-role review + action list (`docs/reviews/`).
- Confirmed v1 scope (ADR-022) + value/risk milestones (`docs/09-roadmap.md`).
- UI: bright "Daybreak" set (recommended) + v1 "Alpine" set + scope map (`docs/design-bright/README.md`).
- **Readiness kit**: tech-stack lock (`docs/10-tech-stack.md`), **frozen contracts** (`docs/contracts/` — data-model, events-and-effects, api, config-and-secrets), `AGENTS.md`, runnable+green skeleton, infra, CI, this plan/status.

## ▶ Next ready task
**M1 #13 — Frontend chat** (Vite+React+TS): a login view + chat session view that creates/uses a session, POSTs prompts (CSRF), subscribes to `GET /sessions/{id}/events` via SSE, and renders the event vocabulary (text-delta / tool-call / turn.end / run.settled) per `docs/design-bright/chat-session.html`. AC: `npm run build` green; live run renders; reconnect shows catch-up.

## In progress
**M1 — durable spine.** #1–#12 done (persistence, migrations, journal+outbox, Redis/SSE, effect idempotency, provider+mock, tools, core loop, durable prompt admission, REST auth+sessions+messages, credential vault AEAD/KEK, run traces + structured logging). #13 next (final M1) — the web chat client wiring the whole vertical: login → session → prompt → SSE render.
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
| M1 #13 frontend chat | ⬜ next |
| M2 #14–22 Gmail→candidate→todo→reminder | ⬜ |

## How to update
On finishing a task: set its row ✅, move "Next ready", note anything a future agent must know (schema changes, new commands, gotchas), bump "Last updated", and commit (the STATUS bump can ride with the task commit).
