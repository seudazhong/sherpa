# STATUS — living project status

> The resume anchor. A coding agent reads this first (per [`../AGENTS.md`](../AGENTS.md)), then picks the next ready task in [`IMPLEMENTATION.md`](IMPLEMENTATION.md). **Update this file at the end of every task** (tick the table, move "Next ready").
>
> Unscheduled findings from manual testing live in [`backlog.md`](backlog.md) (B-1…B-12; **B-1 + B-3 + B-4 + B-7 fixed 2026-07-28**, **B-5 + B-6 shipped 2026-07-29**, **B-9 fixed 2026-07-29**, **B-12 fixed 2026-07-31**; **B-2 still open** — the tool catalog is P2, which the owner deferred; **B-8's reported symptom is fixed by Phase TR P3 (2026-07-31) but B-8 stays OPEN** until the human Run/Stop lane exists in P5; **B-10 / B-11 opened 2026-07-30** by the owner's P2 design review — see the "P2 design review" note below). ✅ **B-9 is fixed ([ADR-044](decisions.md))**: `uv run pytest` now provisions and uses a dedicated `<app_db>_test` database, Redis logical db 15 and a synthetic owner, so it is safe to run **with the stack and worker up** and can no longer touch dev data. The old "stop the worker / point `DATABASE_URL` at `sherpa_test` by hand" workaround is retired.
>
> 🏗 **B-2 + B-8 are one program now.** Triage on 2026-07-30 established that the oversized flat tool surface and the always-failing `project_run` are two faces of the same defect, and the owner approved a **clean-break** unified architecture: [ADR-045](decisions.md#adr-045) (umbrella) · [ADR-046](decisions.md#adr-046) (tool catalog + resolver + progressive disclosure) · [ADR-047](decisions.md#adr-047) (tar workspace transport) · [ADR-048](decisions.md#adr-048) (RuntimeSession + `fs`/`sh`/`run`). Contracts are updated and marked `[shipped]`/`[target]`/`[deleted]`; the execution plan is [`IMPLEMENTATION.md` **Phase TR**](IMPLEMENTATION.md) (P0–P5), **approved by the owner 2026-07-30**. 🚧 **P0 (honesty pass), P1 (baseline squash + legacy deletion), the P2 partials (P2.0a dead-tool sweep + P2.2 `domain_verb` rename) and the whole of P3 (tar transport + first-party runner image + real-Docker lane) are shipped — ✅ P3 is COMPLETE and owner-accepted 2026-08-01, including its 128 MiB workspace cap.** **P2's catalog is deferred by owner decision (2026-07-31); P4 and P5 have not started.** B-2 remains open (the surface is still flat and statically injected) and B-8 remains open (no human Run/Stop lane).
>
> Last updated: 2026-08-03 · Phase: **Phase TR P4 authorized and in progress**. P0/P1/P3 and the P2 partials remain shipped; P2's catalog remains deferred. On 2026-08-03 the owner explicitly chose the P4 sequencing path: register `fs_*`/`runtime_*`/`sh_exec` temporarily in the existing FULL flat registry, then let P2 wrap the unchanged tools later. The contract pass also pins RuntimeSession liveness recovery, DB-live container protection, host-edit/runtime serialization, committed agent dispatch, complete fs schemas and persisted exec output/cancel fields. P5 remains the human Run/Stop lane that closes B-8.

## Real model wired ✅
The mock is no longer the only provider. `OpenAICompatibleProvider` (streaming) targets the user's **litellm proxy at host `:4000`** forwarding GitHub Copilot; default model `claude-sonnet-4.6`. Config-driven (`PROVIDER_KIND=openai_compatible` + `PROVIDER_API_KEY` in a gitignored `.env`; the worker reaches the host via `host.docker.internal`). `PROVIDER_KIND=mock` remains the default for offline dev/tests. **Live-verified in the browser** (real replies "Paris.", a real Sherpa definition). To run the stack with the real model:
`docker compose -f infra/docker-compose.yml --env-file .env up --build -d`.

## Where we are
The **design + contracts + runnable skeleton** are done, and the **v1 durable spine (M1) is complete and verified end-to-end**: persistence, event journal + outbox, Redis/SSE fan-out, effect idempotency, provider+tools, the bounded core loop, durable prompt admission, the REST auth + session/message surface, the credential vault (AEAD/KEK), run traces + structured logging, and the web chat client (#1–#13) are all implemented and green. A live smoke (login → session → prompt → real arq worker loop → outbox relay → SSE `run.settled` → persisted transcript) passes. Next: **M2 — Personal Inbox-to-Action (Gmail → candidate → todo → reminder)**.

## Verified state
- **Backend** (`backend/`, via `uv`): `uv sync` ok · `uv run pytest` green (needs Postgres+Redis up; MinIO for file/drive tests) · `ruff check`+`format --check` clean · `mypy app` clean. `uv.lock` committed. **New in P3: a second, opt-in test lane — `uv run pytest -m docker`** starts real containers from the `sherpa-sandbox-runner` image and is **deselected by default** (`addopts = "-q -m 'not docker'"`) because CI has no daemon; it skips with an actionable message if the daemon is down or the image is not built (`docker build -t sherpa-sandbox-runner:dev sandbox-runner`). Schema is the **single `0001_baseline`** revision (Phase TR P1.5, ADR-045) plus the two tool-rename revisions `0002`/`0003`: the 32 accumulated revisions `0001_initial_core`…`0032_chat_attachments` were squashed and deleted, and every environment rebuilds with `docker compose ... down -v` then `up --build`. The baseline creates the **target** schema — everything the 32 revisions produced, **minus** the legacy `files` table and `project_sandbox_runs`, **plus** `project_runtime_sessions` + `project_exec_runs` (ADR-047/048), which is why **P3 needed no migration at all**.
- **Frontend** (`frontend/`): Vite+React+TS SPA with the responsive `Quiet Work` design system across login, chat, inbox, activity, schedules, settings, memory, **sessions (/history)**, **drive (/workspace)**, messaging, and connectors. `npm run build` + `npm run lint` green. `package-lock.json` committed.
- **Runtime**: web (`uv run uvicorn app.main:app`) + worker (`uv run arq app.worker.WorkerSettings`, runs the run loop + outbox relay) verified live against docker Postgres+Redis.
- **Infra**: `infra/docker-compose.yml` (postgres+redis+web+worker+frontend) defined. **CI**: `.github/workflows/ci.yml` (backend uv lint/type/test + frontend build).

## Done ✅
- Architecture design docs (`docs/00–09`), 22 ADRs (`docs/decisions.md`), three-role review + action list (`docs/reviews/`).
- Confirmed v1 scope (ADR-022) + value/risk milestones (`docs/09-roadmap.md`).
- UI: original "Daybreak" / "Alpine" concept sets plus the production, Notion-inspired `Quiet Work` system (`docs/design-refined/README.md`).
- **Readiness kit**: tech-stack lock (`docs/10-tech-stack.md`), **frozen contracts** (`docs/contracts/`), `AGENTS.md`, runnable+green skeleton, infra, CI, this plan/status.
- **M1 durable spine (#1–#13)**: full web prompt → durable admission → worker bounded loop (mock provider + read-only tool) → events streamed to the chat UI via SSE → transcript persisted; per-run trace + rollups; single-owner auth; AEAD credential vault.

## ▶ Next ready task
**Phase TR P4 is in progress.** The former P2 dependency is resolved by the owner's
2026-08-03 decision: register the new tools in today's FULL flat registry (`safe=False`), with
binding/ownership checks inside each adapter; P2 later adds descriptors without redesign. Start with
P4.0 contracts, then P4.1 forward migration, P4.3 host-side `fs_*`, P4.2/P4.4 RuntimeSession +
`sh_exec` + worker REST, P4.6 cutover and P4.7 route inventory. Then P5 supplies the human
three-column Run/Stop lane and closes B-8.

**✅ Phase TR P3 is CLOSED (owner-accepted 2026-08-01).** The transport half of B-8 is done and
the 128 MiB workspace/change-set cap is an accepted product trade-off (see the P3 block below).
**B-8 itself stays OPEN** — its close criterion is the *human* Run/Stop lane, which is **P5**.
Nothing else in the exclusion list moved: `cancelled` (P4), `output_limit` + typed spill (P2.8)
and `pids_limit` (no daemon signal) are still unmapped; Linux DooD/DinD/rootless are still
unverified; `/work` still cannot carry `nosuid,nodev` on an anonymous volume; cryptographic
image provenance is still out of scope for v1; **B-12's diff-spill retention is still unfixed**
(`project-diff/` grows without bound).

**✅ Phase TR P3 shipped (2026-07-31) — tar transport + first-party runner image. `project_run` really runs now.**
Seven commits, one per task, plus one separate commit for an unrelated bug the verification uncovered.

The B-8 root cause was structural, not a coding slip: `app/sandbox` handed a **worker-container**
path to `Mount(type="bind", source=…)`, which the **host** daemon resolved, so container creation
could never succeed. [ADR-047](decisions.md#adr-047) removes the whole class of failure instead of
configuring around it — there is no `src=` to get wrong.

- **P3.1** `app/sandbox/{runner,project_sandbox}.py` → **`app/sandbox/runtime.py`** (pure move, one code path).
- **P3.2** new **`app/sandbox/transport.py`** (`WorkspaceTransport` + `TarTransport`). The disposable
  copy is now an **in-memory `Workspace`**, not a host directory; `_run_docker` is
  create → `put_archive` → start → wait → logs → `get_archive` → `remove(v=True)`. `/work` is the
  runner image's **anonymous volume**; the create call passes **no mount and no host path**.
  ADR-025 hardening unchanged. `mem_limit` is now named (docker `State.OOMKilled`), output is
  bounded at 1 MiB with an explicit `output_truncated`, and the orphan sweep removes
  **ownership-scoped, liveness-guarded** containers (there is no scratch tree left to sweep).
- **P3.3 / P3.4** credential strip + assert, and the egress tar treated as untrusted input, both
  proven **end to end through the orchestration boundary**, not just in the transport.
- **P3.5** **`sandbox-runner/`** is now a real image: non-root uid 10001, read-only-rootfs friendly,
  pinned python 3.11.9 + pytest 8.3.3 + ruff 0.6.9, **no git / curl / wget**, `capabilities.json`.
  `VOLUME /work` is load-bearing — it is what makes `/work` writable under a read-only rootfs
  *without* any host path, and it gives the volume the runner's ownership.
- **P3.6** `SANDBOX_SCRATCH_ROOT` and `SANDBOX_WARM_TTL_SECONDS` **deleted**;
  `SANDBOX_RUNTIME_IDLE_TTL_SECONDS` added (unread until P4); `SANDBOX_MEM_MB` 256 → **1024**;
  `SANDBOX_IMAGE` defaults to the first-party image.
- **P3.7** **`uv run pytest -m docker`** — the first lane in this repo that starts a real container.

**Owner decisions applied:** D-1 pin by **image ID** digest (`--format '{{.Id}}'`; the image is never
pushed so it has **no** `RepoDigests` — the TR.2 command was wrong and is corrected) · D-2 anonymous
`/work` volume, `nosuid,nodev` recorded honestly as **NOT set** (docker exposes those flags for
tmpfs/bind only; `cap_drop=ALL` + `no-new-privileges` + non-root carry the equivalent protection) ·
D-3 memory 1 GiB · D-6 the single approved `project_tools.py` import line, no other P2 work ·
D-7 Windows/DooD verified, Linux DooD/DinD/rootless **explicitly unverified**.

**Gate (after the second review's fixes, 2026-07-31):** `uv run pytest` **516 passed**
(+24 deselected) · `uv run pytest -m docker` **24 passed** ·
ruff + `ruff format --check` + `mypy app` clean · `alembic heads` single head `0003` (**no migration
in P3** — `project_runtime_sessions` already carried `image_digest`/`capabilities`/`ingress_bytes`
from the 0001 baseline) · `npm run lint` + `npm run build` green.

**Live verification on the rebuilt stack** (`SANDBOX_IMAGE` pinned to the runner's image ID
digest), three independent ways:
1. **from inside the worker container** over the mounted socket (DooD): exit 0, real stdout, real delta;
2. **agent lane, real provider** (`claude-sonnet-4.6` via litellm): the model wrote `calc.py` +
   `test_calc.py`, ran `pytest -q` → **real exit 1** with the real failure text, then fixed the bug and
   got **real exit 0**; `ruff --version` → `ruff 0.6.9`; `git --version` → **127** (the
   `environment_missing_dependencies` mapping). Re-verified after the fixes: create `deploy.sh` →
   `chmod +x` → `./deploy.sh` prints **deployed**, exit 0;
3. **human lane** on the existing Change Review panel: real diff → **Save selected** → head advanced.
   After the fixes the change set shows `deploy.sh` with an **`exec`** badge and the snapshot at
   `head_generation=2` persists `executable=t`. No `.pytest_cache` or `__pycache__` noise in the
   change set — the runner image's cache settings hold.

**⚠️ Two things the real-Docker lane caught that no fake ever could.** (a) `TarTransport.build`
emitted only the directories it was handed, so an implicit parent (`src/` for `src/app.py`) was
created by docker as **root** and the non-root runner could not write in it. (b) The first build of
the runner image had `PYTEST_ADDOPTS=-p_no:cacheprovider` (missing space) and pytest refused to start.
Both are the same lesson as the P2.2 dotted-name incident, one layer down: **the fake and the mock can
only ever confirm what we already believed.**

**🔧 Four blocking defects found by an independent review of P3 HEAD, fixed 2026-07-31
(commit `66ce8e6`).** Each fix has a regression test verified to **fail** against the old behaviour:
1. **Egress could exhaust worker memory** — it buffered every chunk, joined a second full copy, then
   read each untrusted member whole *before* checking the cap. Now streamed (`mode="r|"`, constant
   32 KiB read-ahead), members refused on declared size before allocation, one exactly-sized buffer
   per member, compression and sparse members rejected. Measured with `tracemalloc`: **12.9 MB → under
   8.9 MB** peak for a ~6 MiB workspace. `SANDBOX_SCRATCH_MAX_BYTES` **2 GiB → 512 MiB**, because
   2 GiB was incoherent with `WORKING_COPY_MAX_CHANGED_BYTES` (500 MiB); a test now pins the ordering.
2. **Digest pinning was comments only** — config/`.env.example`/compose all defaulted to the mutable
   tag `sherpa-sandbox-runner:dev` and the runtime ran anything. Now enforced **fail-closed before
   the container is created**: immutable reference **and** the first-party title label, new named
   reason **`runtime_image_untrusted`**, **no default at all**, and the Dockerfile base pinned by
   registry digest.
3. **`chmod +x` with identical bytes vanished** — the baseline stored only content hashes. It now
   carries the executable bit (`BaselineEntry`), verified in a real container and end to end.
4. **Every `container.wait` failure was reported as `wall_timeout`** — a dead daemon told the user
   their command was slow. docker-py surfaces a real timeout as
   `ConnectionError(ReadTimeoutError(...))`, the *same class* as an unreachable daemon, so the fix
   walks the exception chain; everything else maps to its own named reason.

**🔧 A second independent review still blocked P3; four more fixes landed (commit `8810852`).**
Each was reproduced before being changed, and each fix's test was verified to fail against the old
behaviour:
1. **Egress still allocated each member three times.** The first fix was not enough: `bytearray(size)`
   + the same-size bytes object `tarfile.readinto` allocates when handed a whole-member view +
   a final `bytes(buf)` copy. Reproduced at **3.01x** for a single 8 MiB member — the earlier
   24×256 KiB test could not see it, because transients scale with the largest **member**, not the
   archive. Fixed by chunk-sized `readinto` views plus **ownership transfer** of the filled buffer
   (`transport.ByteBuffer`; the blob store converts once per file at its own boundary).
   Measured after: **1.08x** at 8 MiB, **1.02x** at 32 MiB, and **1.13x for a real 8 MiB round trip
   through a real container**. The test now uses the single-large-member shape and asserts the
   *overhead does not grow* when the member quadruples — a ratio alone passes for any N once the
   member is big enough. **PAX sparse** members were also walking through (only `GNUTYPE_SPARSE` was
   checked); writing that test surfaced an unreported crash path — `tarfile` raises `ValueError`,
   not `TarError`, on some malformed sparse maps, and it was escaping the sandbox boundary.
2. **Connect timeouts were being reported as `wall_timeout`.** `requests.ConnectTimeout` subclasses
   `Timeout` and `urllib3.ConnectTimeoutError` subclasses urllib3's `TimeoutError`, so both passed
   the timeout test. A connect marker anywhere in the chain now wins and yields
   `runtime_daemon_unreachable`.
3. **The image-trust claim was overstated.** The docs said the label "proves they are the right
   bytes". OCI labels are ordinary, **forgeable** metadata. Corrected everywhere: the
   operator-chosen **immutable digest is the trust root** (an allowlist of one); the label is a
   **misconfiguration guard**. **No signature or attestation is verified and none is claimed** —
   cryptographic provenance is now recorded as explicitly **out of scope for v1** rather than
   implied. Registry ports are accepted in digests without weakening the `sha256` half.
4. **The executable-bit result is now protected by a DB test** through
   delta → overlay → change set → Save/CAS → head-snapshot row, not just a browser screenshot.

**🔧 A fourth review confirmed a sweep race *by execution*, now fixed (commit pending below).**
`uv run pytest -m docker` failed with `409 container is dead or marked for removal` while the
live worker logged `containers_swept=1` at the same moment. The orphan sweeper filtered on the
generic `sherpa.runtime` label and removed **every** match unconditionally, so the dev worker
deleted a concurrently running test lane's container mid-run. Two independent guards now apply,
and neither alone is sufficient:
- **Ownership** — containers carry `sherpa.owner=<deployment id>` and the sweeper filters on it.
  The id is **derived** from the data-plane identity (database URL + bucket) when
  `SANDBOX_OWNER_ID` is unset, so the ADR-044 test harness is automatically distinct from the
  dev worker. A configuration step nobody has to remember is the only kind that survives.
- **Liveness** — within an owned deployment a container is reclaimed only if it is not
  in-flight in this process **and** is older than `SANDBOX_RUN_TIMEOUT_SECONDS + 300 s`. The
  orchestrator provably cannot hold one longer than the enforced wall clock plus the bounded
  post-exit tail. The in-flight registry covers the `created` state, where a container waits
  while a large workspace uploads — the exact window the 409s came from.
Running orphans are still reclaimed (a crashed worker can leave one executing) but *eventually*,
on the age rule, so recovery never races an active run.
**Verified by execution, not argument:** reverting to the old sweep makes the new tests fail with
the identical `409 ... dead or marked for removal`; with the fix, the worker's real sweeper run
**193 times during a live lane run swept 0 containers** and the lane stayed clean, and 9
consecutive lane runs (270 tests) spanning a live 00:57 cron tick produced zero 409/404 flakes.

**🔧 A third independent review found the last P3 blocker: the *end-to-end* worker peak
(commit `90e744b`).** The earlier fixes bounded egress in isolation; the whole persist path
still held roughly four copies of one file, so one 500 MiB modified file could legally reach
**~2 GiB** of worker RSS. Reproduced first (**3.18x** at 64 MiB for the staging half alone),
then fixed at each of its four sources:
1. `project_sandbox` did `bytes(d.data)` before `ensure_blob` — a full duplicate of a project
   file purely to satisfy a `bytes` annotation.
2. `ensure_blob` hashed/uploaded `bytes`, and `MinioObjectStore._put_sync` wrapped it in
   `io.BytesIO(data)` — another full duplicate. The store now takes a **buffer**
   (`bytes`/`bytearray`/`memoryview`), streams it through a `RawIOBase` view, and hashes in
   chunks. Content-addressed **immutability is preserved, not weakened**: the one required
   snapshot moved to the storage boundary instead of being forced on every caller.
3. `build_change_set` full-read **both** sides of every entry *before* checking the diff cap,
   so a 500 MiB file was pulled in twice to conclude it was too big to diff. It now decides
   from **recorded sizes** and classifies with an **8 KiB prefix read**
   (`ObjectStore.get_prefix`, a ranged GET on MinIO); over-cap entries are recorded truthfully
   as `diff_truncated`.
4. Both trees stayed pinned for the whole staging pass; staging is now destructive and
   releases each file's bytes as soon as they are durable. Failure fallback is untouched.

**Measured peak model, now a contract (config §1.7):** `peak ≈ 2 × workspace + C`, `C ≈ 40 MiB`.
The 2× is inherent — a delta needs the old tree *and* the new tree. Measured **2.56x** at
64 MiB synthetically and **2.15x live inside the memory-limited worker** with a real container.
**The caps are therefore a memory budget and were re-derived**: `SANDBOX_SCRATCH_MAX_BYTES`
and `WORKING_COPY_MAX_CHANGED_BYTES` are now **128 MiB** (budgeting ~296 MiB), and the compose
worker declares `mem_limit: 1g` so the claim is checkable rather than aspirational — raising
the cap requires raising that limit by twice the delta. History: 2 GiB → 512 MiB → 128 MiB,
each step forced by a measurement contradicting the previous claim.
✅ **Owner decision (2026-08-01): the 128 MiB cap is ACCEPTED** as the intentional product
trade-off for the current 1 GiB worker budget — settled, not pending. The trade-off is
explicit and worth restating for whoever hits it: a boundary whose changed bytes exceed
128 MiB is refused with `changeset_bounds` and **nothing is persisted**, rather than the
worker being put at risk. Changing the number is a deliberate act that must move the worker's
`mem_limit` with it.
Guards: `tests/test_sandbox_memory_e2e.py` (subprocess RSS bound + a **marginal-cost** bound at
two sizes, because a ratio alone passes for any constant factor once the file is large enough),
`tests/test_blob_nocopy.py` (the no-copy mechanism pinned by identity — a `bytes()` copy is
functionally invisible, which is exactly why it survived two reviews), and
`test_build_change_set_never_full_reads_an_oversized_file` (verified to fail with the size gate
disabled).

**❗ B-8 is still OPEN, and P3 did not move its close criterion.** No `runtime_open`/`sh_exec`/`fs_*`
tools, no async `202`+SSE, no cancel (**P4**); **no human Run control, no streaming log, no Stop** —
`frontend/src/api.ts::createSandboxRun` is *still* dead code and the "web executes the sandbox
synchronously with `SANDBOX_KIND` unset" blocker is *still* true (**P5**). Three failure-matrix rows
are also unimplemented and are recorded as such, not assumed: `cancelled` (P4), `output_limit` +
typed spill reference (P2.8), and `pids_limit` — the cap is enforced but docker exposes no
pids-kill signal, so a fork bomb surfaces as a plain non-zero exit and naming it would be a guess.

**🐛 B-12 found and fixed during P3's human lane (separate commit, not P3's fault).** The Drive
orphan GC deleted change-set diff spills: `build_change_set` writes them under `project-diff/…`
with no `storage_blobs` row, and `sweep_orphan_objects` deleted every key without a row, exempting
only `project-import/`. Live evidence: `cron:drive_maintenance ● 'gc=0 orphans=6'` at 13:40:00,
Change Review 500 `NoSuchKey` at 13:40:26. Fixed by an explicit exempt-prefix tuple + a regression
test **verified to fail without the fix**. **Still open in B-12:** those spills are now never
reclaimed — recommend folding retention into the same janitor as the api §7.2 tool-output debt
(P2.8) rather than adding a second sweeper.

**Minor observation, not fixed (pre-existing, cosmetic).** A file first created in one boundary and
edited in a later one is labelled `~ modified` in Change Review even though it is `+ added` relative
to the project head: `build_change_set` trusts `overlay.change_kind`, which `persist_overlay` records
relative to the *effective tree*, not the head. Apply/Save are unaffected (they key on content
hashes), so this is a label, not a correctness bug.

The program is [`IMPLEMENTATION.md` **Phase TR**](IMPLEMENTATION.md): the unified,
clean-break fix for backlog **B-2** (the 52-tool flat surface) and **B-8** (`project_run` always
fails). Triage on 2026-07-30 showed these are **one architecture problem** — fixing B-8 alone would
grow the flat surface to ~66 tools, and fixing B-2 alone would block the tools B-8 needs.

**Approved (2026-07-30):** the architecture — [ADR-045](decisions.md#adr-045) umbrella ·
[ADR-046](decisions.md#adr-046) tool catalog / `ToolsetResolver` / `tools_search`+`tools_load` ·
[ADR-047](decisions.md#adr-047) tar workspace transport (a **narrowing** of the ADR-025/039 mount
wording, not a relaxation) · [ADR-048](decisions.md#adr-048) explicit `RuntimeSession` + host-side
`fs.*` + sandbox-routed `sh.*`/`run.*` — **and the P0–P5 execution plan itself**. Clean break: no
compatibility layer, no aliases, **no data migration**; the 32 Alembic revisions squash into one
`0001_baseline` and the dev database/volumes are rebuilt from empty (all current data is disposable
test data).

**Design batch (documentation only):** ADR-045…048; contract updates in `api.md` (§7 rewritten,
§7.3 and §9 deleted/replaced, §10.7 replaced), `events-and-effects.md` (§2.2 `toolset.resolved`,
§2.11 RuntimeSession + expanded named exits), `config-and-secrets.md` (§1.4 `WORKSPACE_ROOT` deleted,
§1.7 tar boundary, §1.10 catalog byte budget), `data-model.md` (`project_runtime_sessions` +
`project_exec_runs`, baseline-squash plan); `docs/11` capability-matrix corrections; Phase TR P0–P5.

**✅ Phase TR P0 shipped (2026-07-30) — the honesty pass, backend only, no schema/frontend/infra
change.** `sandbox_unavailable` is gone from every sandbox code path. The named reasons now live
once, in `app/sandbox/runner.py`, shared by both entry points: `_run_docker` classifies into
`runtime_daemon_unreachable` / `runtime_image_missing` / `runtime_start_failed` /
`runtime_transport_failed` / `error:<class>`, and `sandbox_disabled` stays distinct. The raw failure
text rides on the new `RunResult.error_detail` into the **worker log only**, while the model gets one
static, redacted, reason-specific observation (`runtime_failure_note` /
`SandboxOutcome.failure_note`, surfaced by `project_run` and `run_code`). That also closed a real
leak: `run_code` used to hand the **raw docker exception string** to the model. `run_sandbox` emits
exactly **one** structured `logger.warning` per failing exit, including the pre-existing
`wall_timeout` / `environment_missing_dependencies` / `changeset_bounds` / `fence_lost` / scratch
exits. Error-is-observation preserved: a runtime failure still persists the host-side edits. Gate:
full `uv run pytest` **397 passed**, ruff + `ruff format --check` + `mypy app` clean. **The bind
mount is still structurally broken (P3) and no human Run control exists (P5) — B-8 remains open.**

**✅ Phase TR P1 shipped (2026-07-30) — baseline squash + legacy deletion. THE DESTRUCTIVE RESET
HAS BEEN RUN.** `docker compose down -v` destroyed `pgdata`/`redisdata`/`miniodata`; the stack was
rebuilt from empty and the `migrate` service ran `-> 0001` on a fresh database. Five commits, one
per task:
- **P1.1** the legacy `/files` stack is gone — `app/services/files.py`, `app/api/files.py`,
  `app/tools/file_tools.py`, the `File` model, `include_router(files_router)`, its tests, and the
  dead frontend client (`listFiles`/`uploadFile`/`deleteFile`/`fileDownloadUrl`, which had no call
  site). Drive is the only file world the model sees.
- **P1.2** `run_code` is gone (tool, `app/sandbox/runner.py` snippet runner, `app.sandbox`
  re-export, tests). `runner.py` keeps only what P0 put there: the shared named-exit vocabulary.
  `SANDBOX_TIMEOUT_SECONDS` went with it — it bounded nothing else.
- **P1.3** `app/files/` → `app/objectstore/` (ADR-046 O-14): it is the object-store adapter under
  Drive/Knowledge/Projects, and sharing a name with the deleted stack was misleading.
- **P1.4** `WORKSPACE_ROOT` — **measured correction: it never existed in code.** No setting, no
  `.env.example` line, no compose mount, and the `read`/`glob`/`grep` tools it was to back were never
  registered. The contract already recorded it as DELETED; the plan row implied a shipped setting.
- **P1.5** one revision. `0001_baseline` creates the target schema; `0001`…`0032` are deleted.
  Verified by a normalized statement-set diff of `pg_dump` before vs after: **15 statements removed
  (all owned by `files`/`project_sandbox_runs`), 17 added (all owned by `project_runtime_sessions`/
  `project_exec_runs`), nothing else changed.** The ORM moved with it (`ProjectSandboxRun` →
  `ProjectRuntimeSession` + `ProjectExecRun`) so the baseline can stay the only revision and never
  need a follow-up migration; `run_sandbox` now opens/closes one runtime session per boundary and
  records a command as one exec run. **That is persistence alignment, not P4 behavior** — no
  `runtime_open`/`sh_exec`, no tar transport, no async REST, no UI. `SandboxRunState.warm` is gone
  (ADR-047 §7: never implemented anywhere).

Gate: `alembic heads` = **one head (0001)**; `uv run pytest` **385 passed** (the ADR-044 harness
recreated `sherpa_test` from scratch; 397 → 385 is exactly the 12 tests deleted with the dead code);
ruff + `ruff format --check` + `mypy app` clean; `npm run lint` + `npm run build` green; web
`/health` + `/readyz` ok and the worker ticking on the rebuilt stack; live schema vs ORM metadata
52 = 52. Measured tool surface **52 → 47 tools / 19,848 → 18,397 B** — that is deletion, **not** the
B-2 fix; the catalog in P2 is.
**B-2 and B-8 both remain OPEN.** The bind mount is still structurally broken (P3), no human Run
control exists (P5), and the tool surface is still flat and statically injected (P2).
**Scope note:** an earlier draft of this file listed `project_run`/`project_tree`/`project_read`
among P1's deletions. They are **P4** ([TR.9](IMPLEMENTATION.md)), not P1, and they still exist —
`project_run` still runs (and, until P3, still failed at the container).

**~~What P2/P3 do next~~ — superseded 2026-07-31.** **P3 is shipped** (see the P3 block above) and
**P2's catalog is deferred by owner decision**; only P2.0a (dead-tool sweep) and P2.2 (`domain_verb`
rename) landed. TR.4's "both must merge before P4" therefore no longer holds as written — see the
warning under "Next ready task".

**⚠ P2 design review (2026-07-30, owner-led) — P2 gains a slimming step before the catalog.**
The owner challenged the plan: *progressive disclosure is the classic answer to an oversized toolset,
but we never did the cheap compression that should come first.* Measurement agreed, so the plan
changed. Recorded in [`backlog.md` **B-10**](backlog.md#b-10-tool-surface-slimming-dead-tools-prose-diet-and-vertical-workflow-consolidation)
and [**B-11**](backlog.md#b-11-no-tool-use-evaluation-harness-decisions-are-argued-not-measured);
[ADR-046](decisions.md#adr-046) amended (修订 A). Key measured findings:
- **Baseline correction.** 19,848 B was the **pre-P1, 52-tool** number. Today: **47 tools / 17,432 B**
  (compact) / **18,303 B** (default separators) — recompute every "−70%" claim against these. The
  general-chat core measures **3,627 B** today, so `TOOL_CATALOG_CORE_MAX_BYTES=6144` is a **ratchet with
  2,517 B of headroom**, not a stretch goal; the win is *not sending the other 37 tools*.
- **Descriptions are 38%** of the surface (6,625 B; worst: `project_run` 641 chars) and unbudgeted →
  add a per-tool description byte cap at startup, beside the name regex.
- **Never-audited dead tools**: `echo` (dev leftover, and SAFE-tier), `accept_candidate` (a strict subset
  of `edit_candidate`), plus 4 owner decisions. **`drive_restore` is structurally uncallable** — it needs
  `node_id` and **no tool ever emits one** (`drive_tools.py:105-110`, `:129-133`), so the model can only
  call it with a hallucinated id.
- **But slimming cannot replace the catalog**: fully slimmed is ~12,137 B (still 2× the budget), and P4's
  11 new tools eat the saving back. Compression shifts the curve once; the catalog changes its slope.
- **Horizontal `domain(action, …)` merging stays rejected — with corrected reasons.** ADR-046's grounds ②
  (effect-class) and ③ (approval scope) are **undone by its own §决策6** (args-aware policy). What remains
  is ① — `app/tools/validate.py` is not a JSON-Schema engine, so merging `update_todo` would make
  `if_version` un-requirable and **delete the optimistic-concurrency guard** — plus ④ model weakness at
  discriminated unions.
- **New ADR-046 §决策10: consolidate only on the *vertical* (workflow) axis**, never the horizontal (CRUD)
  one; cross-domain `list(kind)` is forbidden. Vertical candidates (`todo_create(…, remind_at?)`,
  `inbox_accept(…, patch?, remind_at?)`, `today()`, `knowledge_add(query_or_path)`) are parked in B-10.
  Borrowed from CLI practice: **drop `run_test`/`run_lint` from P4** (pure sugar over `sh_exec`), and keep
  `fs.*` separate (CLI agents keep Read/Edit out of Bash for exactly Sherpa's reasons).
- **Methodology gap (B-11).** All of the above is empirically decidable and we decided it by argument.
  Phoenix is already up with `OTEL_CAPTURE_MESSAGE_CONTENT=true` and already records
  `llm.tools[].tool.json_schema` + every `tool_call` name, so a **zero-cost baseline (E0: mine existing
  traces for per-tool call frequency, never-called tools, error rates, bytes/call)** is available now and
  should land **before** the B-10 deletion decisions. E1–E3 (Phoenix dataset → experiment → A/B the tool
  surface) run parallel to Phase TR and need their own ADR. **⚠ Running E0 on 2026-07-30 corrected three
  assumptions**: (a) P1's `down -v` destroyed the trace corpus (Phoenix shares the `pgdata` volume) — only
  18 spans exist, so E0 must *generate* a corpus, not mine one; (b) `agent.tool.success` does **not** catch
  semantic failure (`project_run`, the always-fails tool, records `status_code=UNSET`), because
  error-is-observation is by design — an eval built on that flag would have scored B-8 as passing;
  (c) `execute_tool` spans carry no result content. Details in B-11.

**✅ Phase TR P2.2 shipped (2026-07-31) — naming unification only.** All **42** tools renamed to a
single `domain_verb` namespace (ADR-046 §决策1 as amended by **修订 B**), hard rename, no aliases. The
measured starting point was **28 `action_domain` · 15 `domain_action` · 4 neither**, mixed *inside*
single domains (`todo_write` ↔ `list_todos`; `project_read` ↔ `list_projects`), so the model could not
even pattern-match locally. 11 namespaces now: `core` `inbox` `todo` `connector` `schedule` `notify`
`memory` `drive` `knowledge` `project` `email`.

> **⚠️ This shipped WRONG once, and the test suite structurally could not see it.** ADR-046 originally
> specified `domain.verb` **with a dot**, on the unverified claim that a dot is legal in the OpenAI /
> Anthropic / Gemini wire formats. The dotted rename was implemented and committed, **`uv run pytest`
> stayed fully green** — the suite runs the mock provider, which can never observe a wire-format
> rejection — and the live stack was then unable to make **any** tool call: GitHub Copilot behind the
> litellm proxy answers `400 Invalid 'tools[0].name': ... Expected a string that matches the pattern
> '^[a-zA-Z0-9_-]+$'`. **Only a live smoke against the real provider caught it.** Fixed by switching to
> underscores — which is also what Anthropic's own examples use (`asana_search`, `jira_search`) — and
> re-verified end to end on the rebuilt stack: the event journal now shows `tool-call` → `tool-result`
> for `core_get_time`, with zero provider errors.
> **Standing lesson: tool-name grammar is a wire contract that `pytest` can never validate.** Any future
> change to it MUST be smoke-tested against a real provider. Gap tracked in B-11.

Two enforcement points spelled the old dot-less grammar: `app/api/schemas.py::ApprovalAction.tool_name`,
and the **`ck_pg_tool` CHECK** on `permission_grants.tool_name` — the latter is reachable only at
runtime and surfaced as 6 test failures. Migrations **`0002`** (dotted, wrong) and **`0003`**
(underscore, correct) are both kept: existing databases, including the retained ADR-044 test database,
are migrated forward rather than rebuilt, and the history records the wrong turn honestly.
Model-facing prose was renamed with the tools (`SYSTEM_PROMPT`, `session_context`, the
`if_version from …` argument descriptions, the approval-card text) so no description advertises a name
that no longer exists. Byte-neutral by design: 16,153 → **16,161 B**, still 42 tools.
Gate: `uv run pytest` green, `alembic heads` = one head (`0003`), ruff + format + mypy clean,
`npm run lint` + `npm run build` green, **plus a live agent-lane smoke on the rebuilt stack**.
**No merging, no deletion, no redesign** — those remain open in B-10.

**✅ Phase TR P2.0a shipped (2026-07-31) — the dead-tool sweep.**
Five tools deleted that the catalog would otherwise have indexed instead of removed:
`echo` (SAFE-tier dev leftover — SAFE is now `{get_time}` alone), **`drive_restore`** (structurally
uncallable: it required a `node_id` and **no tool ever emitted one**, so the model could only call it with a
hallucinated id), `complete_todo` (exactly `update_todo(status="completed")` — its one-line service alias
`todos.complete_todo` had no REST caller and went with it), `edit_candidate` (folded into
`accept_candidate`, which now takes an optional `title`/`description`/`due_at`/`priority` patch — **REST
keeps both endpoints**, since the Inbox UI has two buttons and only the *tool* surface merges), and
`memory_user_list` (folded into `memory_user_get`, `key` now optional — both become `memory_recall` in
P2.2). `SYSTEM_PROMPT` updated to stop advertising a deleted tool.
**Measured 47 → 42 tools / 17,432 → 16,153 B compact** (18,303 → 16,948 B with default separators).
Guard: `tests/test_tools.py::test_deleted_tools_are_gone`. Contract `api.md` §7.3 updated.
Gate: `uv run pytest` **386 passed** (385 baseline + 2 new − 1 deleted), ruff + `ruff format --check` +
`mypy app` clean. **This is deletion, not the catalog — B-2 still closes at the end of P2.**
**Still open in P2.0:** the **prose diet (P2.0b)** — descriptions are still **39%** of the surface
(6,336 B of 16,153), because the offenders (`project_run` 641 chars, `project_tree` 507,
`search_knowledge` 398) all survived this pass; it needs `TOOL_DESCRIPTION_MAX_BYTES` enforced at startup
or it refills. And **P2.0c**, the 4 deletions awaiting an owner decision.

**Close criteria:** B-2 closes at the end of **P2** (general-chat tool JSON ≤ 6,144 bytes, down from
the measured 19,848; core is a byte-true cache prefix; discovery verified in the agent lane) —
**P2's catalog is deferred, so B-2 stays open**. B-8
closes at the end of **P5**: "a real command runs in a real container on the Windows dev stack"
✅ **done in P3 (2026-07-31)**; "every failure injection maps to one named reason" ✅ for 13 of 16
rows with the three gaps named in [TR.11](IMPLEMENTATION.md); "the human Run/Stop lane exists and is
click-verified" ⬜ **not started** — so **B-8 stays open**.
Production runner (gVisor/microVM) and the in-sandbox coding agent stay **roadmap** and may not be
used to justify leaving either item open.

## ▶ Earlier direction notes (superseded by Phase TR for the tool/sandbox areas)
**M-tools is complete** (T1–T8: ToolContext + capability layer, ALLOWED policy engine, and candidate/todo/connector/schedule/read-settings tools + output spill). The agent can drive every own-data UI capability via chat, permission-gated. The **UI-completion** pass then added session mgmt (new chat) + Schedules + Settings pages. Ready directions (⚠️ item 0 is a **newly-found correctness bug** — do before the polish items):
0. **✅ Observability shipped (Phase OBS-A, ADR-033, 2026-07-24) — item 0 CLOSED:** the loop now emits OTel `gen_ai` spans (`invoke_agent > chat / execute_tool`) with real per-call tokens (provider `stream_options.include_usage` → `Finish` → span), `finish_reason`, latency, and tool success/ERROR; gated on `OTEL_ENABLED`, zero overhead when off, content off by default. Durable redacted `model.request`/`model.response` debug events (sha-256 `input_digest`, **no content**, events §2.7) give a replayable per-call record; `project_run_trace` derives real trace token totals + one `generations` row per model call from them. See the Phase OBS-A block below. **Bug:** every prompt starts a *new run*, and a run rebuilds provider history **text-only** from `messages`/`message_parts` (`core/loop._load_transcript`); prior `tool_use`/`tool_result` live only in the event journal, so **across runs the model loses all evidence it called a tool** → it "forgets", apologizes, and re-does/denies work (observed: created a todo, then on a follow-up claimed it never called the tool and re-created it). Same root cause also weakens mid-run crash resume. **Fix — Option B (✅ done, commit `de1eb91`):** `app/core/history.assemble_provider_history()` reconstructs the OpenAI-protocol window (assistant + `tool_calls`, `role:tool` results, `permission.asked`/deny placeholders, crash-halfway backfill) from the event journal (the declared tool-history source of truth), replacing the text-only reload — no contract change; regression tests in `test_history.py` prove run2's provider **receives** run1's `tool_use` (pytest 90 green). *(Option A = persist tool steps as `messages`/`parts` — rejected for now: needs a frozen-contract change + ADR + migration + a wide message-consumer audit.)* **⏸️ Deferred — observability (synergistic, the 2nd ask; owner deferred 2026-07-21):** persist **each LLM call's exact assembled input** as a redacted `model.request` journal event and/or a `generations` row, and emit chat-loop generation records (model / prompt-version / tokens / `stop_reason`), so "what each LLM call sent + every internal step" is inspectable for human debugging. refs: `core/loop.py`, `events/journal.py`, ADR-016/017, docs/07-observability.
1. **UI/UX backlog** — ✅ **UX-1…UX-16 cleared + browser-verified** ([`ui-backlog.md`](ui-backlog.md)): the earlier functional gaps remain fixed; the 2026-07-22 full-product pass also shipped the responsive `Quiet Work` redesign, mobile drawer, rendered Markdown, clearer empty states, progressive disclosure for connector internals, and safer data-control hierarchy.
2. **v1 approval closure** — ✅ **done + browser-verified** (commit `c29b86f`): `POST /permissions/{id}/resolve` → resume job (`core/resume.py`) executes the gated `send_email` end-to-end (recovering approved args from the bound `tool-call` event); ChatView renders Approve/Reject, capturing the single-use nonce from the `permission.asked` SSE event + envelope fields from `GET /permissions`. Browser E2E: model called `send_email` → approval card → **Approve** → activity showed "email sent to test@example.com". `send_email` stays a v1 stub; `allow_session`/`always` grant persistence deferred (static policy engine). **Follow-up (contract reconciliation, own ADR):** `permission.asked` carries the nonce+preview beyond its frozen minimal schema (events-and-effects §2.3) — nonce-in-journal delivery for web needs a decision; a `permission.resolved` event (already in the catalog) could drive UI card removal.
3. ~~**M3 eval harness**~~ — **deferred out of v1** into post-v1 #11 (eval flywheel) per **ADR-024**: v1 is single-user self-hosted, the owner *is* the eval loop, so no external-user quality gate now; re-instate before onboarding external beta users. Optional cheap insurance: a ~1-day deterministic mock regression lane on the extraction path.
4. **R-SESSION-SEARCH research** — ✅ complete, awaiting owner decision: surveyed Copilot CLI, Hermes, Codex, Claude Code, and Gemini CLI; recommends a Postgres canonical + rebuildable `session_search_entries` projection, lexical/CJK/trigram first, typed anchors, and state-specific Resume/Reconnect/Recover. Report: [`research/session-search-report.md`](research/session-search-report.md); static prototype: [`design-session-library/index.html`](design-session-library/index.html).
5. **R-WORKSPACE-PRODUCT research** — ✅ complete; Project Chat/sandbox lifecycle direction owner-confirmed: Personal workspace contains Projects + Drive; Chat is General or immutably Project-bound; a durable task working copy spans turns while scratch volume/warm container remain rebuildable caches; the initial executor is Sherpa built-in tools only (no embedded coding agent). Storage remains Postgres canonical metadata + immutable tenant-scoped MinIO blobs with configurable 5 GiB personal quota plus tenant/deployment ceilings; Git remote is optional and external writes stay approval-gated. Report: [`research/workspace-product-report.md`](research/workspace-product-report.md); static prototype: [`research/workspace-product-prototype/index.html`](research/workspace-product-prototype/index.html).
6. **R-KNOWLEDGE-BASE research** — ✅ complete: audited the shipped manual-note RAG, researched production KB patterns, and designed a separate file-backed Knowledge vertical slice with async source/version ingestion, hybrid retrieval, citations, multilingual handling, UI, tools, and release gates. Recommendation: GO for the narrow slice after owner approval and ADR/contract review; no implementation has started. Report: [`research/knowledge-base.md`](research/knowledge-base.md).
7. **R-MEMORY research** — ✅ complete, awaiting owner decision. Triggered by a **live memory bug**: `user_memory` is an exact-match, free-form-key KV whose whole table is injected into the system prompt; a fact stored under key `personal.email` missed a later `memory_user_get('personal_email')` and the model then denied having it — **even though the fact was injected** (proven from the journal + DB: failed session `f04f8b3f` / run `c31b69b3` started 02:48:05Z with all 3 `user_memory` rows, incl. `personal.email` @ 02:46:58Z, present; the model did a redundant wrong-key lookup and trusted the miss). Surveyed Letta/MemGPT, Hermes, Sydney, Mem0, Zep/Graphiti, Generative Agents, LangMem, Anthropic/OpenAI; proposes **tiered memory** (named, bounded, always-in-context core **blocks** + on-demand auto-formed semantic tier + session-search as episodic recall), a **deterministic ADD/UPDATE/INVALIDATE/NOOP write-merge**, **bi-temporal soft-invalidation**, and **cache-stable injection** (memory currently lives in the cached system-prompt prefix → violates docs/04 loop-invariant ⑤). Phased A/B/C + an evidence gate. **Owner approved the direction (2026-07-23): tiered memory + bundled `ollama` embedding (bge-m3, 1024-d); `ADR-032` + contract diffs (data-model/api/events/config) drafted this batch, implementation order TBD.** **Observability follow-up (backlog):** emit a `core_memory.loaded` signal — the injected block labels/char-count in a journal event — so each run self-documents what memory it saw (today it must be reconstructed from timestamps); dovetails with item 0's deferred `model.request`/`generations` capture. Report: [`research/memory.md`](research/memory.md); ADR: [`decisions.md` ADR-032](decisions.md).
8. **R-OBSERVABILITY research** — ✅ complete, awaiting owner decision. Surveyed OpenTelemetry GenAI semconv, Langfuse, Arize Phoenix/OpenInference, OpenLLMetry, and the agent-observability landscape. Recommends adopting **OTel `gen_ai.*` spans as a thin *derived diagnostic* layer** over the ADR-016 journal (journal stays source of truth; spans correlated by `run_id`, never a substitute), instrumenting the loop's `invoke_agent`/`chat`/`execute_tool` spans with real per-call tokens + `finish_reason`/`stop_reason` + loop/cost ceilings — this **closes item 0**'s deferred per-LLM-call gap (the exact blind spot hit while debugging the memory bug: no record of the assembled prompt). Content capture **off by default** (ADR-019); `InMemorySpanExporter` + mock provider keeps tests deterministic. Backend: prefer **Arize Phoenix** (single container, reuse the existing Postgres, OTLP-native, auto-converts `gen_ai.*`) over the docs/07-earmarked **Langfuse** (now 6 services incl. ClickHouse ≥4 GB) — OTLP keeps the backend swappable; ship it optional + off by default. Phased A (instrument, no infra) / B (optional Phoenix) / C (evals, evidence-gated). **ADR-033 + config/events contract diffs drafted this batch; ✅ Phase OBS-A shipped 2026-07-24 (see block below).** Report: [`research/observability.md`](research/observability.md); ADR: [`decisions.md` ADR-033](decisions.md).
Then the remaining **post-v1 milestones** in `09-roadmap.md` (cron → GitHub → provider/sub-agent → plugins → teams → eval), in the owner's chosen order.

## ▶ Backlog batch B-5 + B-6 (2026-07-29) — ✅ **complete + two-lane verified**

Owner picked the two `backlog.md` items about getting files into Sherpa; both were contract-first.

- **B-5 Drive folder / multi-file upload** ([ADR-042](decisions.md)) — **client-side bounded expansion** over the
  existing endpoints (no batch/archive endpoint, **zero server change**): `frontend/src/lib/driveUpload.ts`
  walks the picked directory or the dropped entry tree, rejects an over-budget batch up front (≤ 200 files /
  ≤ 200 MiB), mirrors the tree with `POST /drive/folders` (409 ⇒ reuse), uploads at concurrency 3, and
  reports **per-file** outcomes (a `507` stops the queue rather than repeating itself). Human lane found a
  real bug — a directory-picked upload posts its *relative path* as the multipart filename, which
  `_validate_name` rejected (`422`) — fixed on both sides (client sends the base name; the endpoint reduces
  any client filename to its base name) with a regression test.
- **B-6 Chat attachments** ([ADR-043](decisions.md), schema **0032**) — attachments are **references to Drive
  nodes**, never a second byte store. Pasted/uploaded images land in `Chat uploads/` before admission (quota
  `507` / cap `413` / versioning / trash / GC inherited); `parts.kind` gains `image`/`file_ref` carrying
  `{drive_node_id, version, name, content_type, size_bytes}`; `assemble_provider_history` expands them per
  run under a byte budget (≤ 5 MiB/image, ≤ 15 MiB/assembly) while a **text-only turn keeps its plain-string
  shape** so cached prefixes stay byte-stable; the three wire adapters translate the content array
  (Anthropic image block / Gemini `inlineData` / OpenAI pass-through); and every failure mode degrades
  **honestly** (no vision, budget spent, oversized, purged node) instead of provoking a provider error. New
  per-source `supports_vision` flag (Settings → Models toggle) drives that. UI: composer Attach / Drive
  picker / clipboard paste / removable chips / transcript thumbnails + file chips.

**Verified:** backend gate green (ruff · mypy · 355 pytest, incl. `test_chat_attachments*` and the new Drive
regression) run against a dedicated `sherpa_test` database so dev data survived; frontend `lint` + `build`
green. **Agent lane** (real litellm `claude-sonnet-4.6`): the model described the attached PNG exactly
("SHERPA TEST", red circle, blue triangle) and a **follow-up run** still answered about it, proving
cross-run replay; a Drive-picked text file was quoted verbatim. **Human lane**: nested folder upload → 3/3
with the tree rebuilt; paste → chip; Drive picker → attach; reload → transcript re-renders attachments;
`supports_vision=false` → honest composer warning; 390 px overflow = 0.

## ▶ Backlog B-9 (2026-07-29) — ✅ **complete**

The suite shared one Postgres/Redis with the running dev stack **and** got its clean slate by deleting the
*configured* owner tenant, so a single `uv run pytest` cascaded away the developer's model sources, projects
and chat sessions, and raced the worker's `project_workcopy_maintenance` cron into a `DeadlockDetectedError`
that failed a random API test. Fixed by isolating the **data plane**, not by tidying the 20 call sites
([ADR-044](decisions.md); no migration, no `app/` change, no CI change).

- `backend/tests/__init__.py` — the first module Python executes for the package, hence the only place that
  runs *before* `app.config` builds its `Settings` singleton — rewrites `DATABASE_URL` → `<app_db>_test`,
  `REDIS_URL` → logical db **15**, `OWNER_EMAIL` → a **synthetic** owner (`owner_ids()` derives the tenant
  uuid5 from it, so the deleted tenant provably cannot be the real one), plus temp scratch roots.
  `TEST_DATABASE_URL` / `TEST_REDIS_URL` override; resolving to the app database aborts at import.
- `backend/tests/db_guard.py` — creates the database, runs `alembic upgrade head`, stamps
  `_sherpa_test_marker`, and treats that marker as the **only** evidence that destructive writes are allowed
  (fail-closed; an unreachable Postgres still only warns, so the existing `ping_db()` skips keep CI green).
  Escape hatches: `SHERPA_TEST_DB_ADOPT=1`, `SHERPA_TEST_DB_RESET=1`; the database is retained between runs.
- All 20 cleanup sites now call one guarded `drop_tenant()` (bounded by `lock_timeout` + a single retry).

**Verified:** `uv run pytest` green **with the dev worker running** — the exact scenario that used to fail —
**370 passed, twice** (idempotent); `ruff check` · `ruff format --check` · `mypy app` clean; pointing
`TEST_DATABASE_URL` at the app database aborts before opening a connection; dev-database row counts
(`tenants/users/model_providers/projects/sessions/messages/runs` = 1/1/1/0/5/13/7) **identical** before and
after. Docs that claimed the suite destroys dev data (README, AGENTS §2, this file) are corrected.

## ▶▶ Active build (owner-approved 2026-07-23): P0 → P2, no mid-review — ✅ **P0–P2 complete, awaiting unified owner acceptance**

Sequenced implementation of the two completed research lines, through **P2**, then unified owner acceptance. Prereqs done: **ADR-029** (Session Library + search) + **ADR-030** (Personal Drive/W1); contract additions in `contracts/data-model.md §"Post-v1 contract additions"` and `contracts/api.md §10`; task breakdown in [`IMPLEMENTATION.md` Phase P0–P2](IMPLEMENTATION.md). Order:
- **P0 — Session Library** ✅: persisted title, `last_activity_at` + run lease/heartbeat (migration 0019), browse+filters, truthful state-specific Resume/Reconnect/Recover, dedicated Sessions page at **`/history`** (route `/sessions` collides with the API proxy). Browser-verified.
- **P1 — Session search** ✅: `session_search_entries` projection (migration 0020; inline per-session `reindex_session` from canonical rows in admission + loop settle), FTS + CJK bigram + trigram, grouped results + typed deep-link anchors. Browser-verified English + Chinese.
- **P2 — Personal Drive (W1)** ✅: storage accounts/quota (5 GiB default, configurable), immutable content-addressed ref-counted blobs, drive nodes/versions/trash, cross-store commit fix + GC/orphan-sweep worker (migrations 0021+0022), files→Drive migration (legacy `/files` kept during transition), REST `/drive/*` + agent tools (`drive_*`, purge human-only), Drive browser UI at **`/workspace`** (folders/breadcrumbs/upload/versions/trash/storage; responsive). Playwright human-lane verified (desktop + 390px). **Trash-view fix:** `GET /drive/nodes?trashed=true` (backend was filtering trashed nodes out).

**Verified P2:** backend gate green (ruff/mypy/pytest, incl. `test_drive*`), frontend build/lint green, Playwright human lane all-pass (create folder → upload → rename → versions → trash → restore → 390px no-overflow). **→ Ready for unified owner acceptance.**

Deferred within these lines: session semantic search + branch/lineage (Phase C); Projects/sandbox/GitHub (W2–W4).

## ▶▶▶ Phase CRON (owner-approved 2026-07-23): 通用定时任务 cron — ✅ **COMPLETE + two-lane verified**

**ADR-031** upgrades Schedules from reminder/digest-only into a general recurring scheduler ("crontab for the agent"). Schema at **`0023`**. Shipped:
- **Cadence engine** (`app/scheduler/cadence.py`, croniter): cron/interval/weekly/monthly/daily/once, DST-correct via IANA tz; min-frequency floor + validation. Replaces the daily-only tick `_advance`; `once` → completed.
- **`agent_task` action**: a schedule saves a prompt; on fire the worker admits an idempotent `run_kind='scheduled_task'` run (firing slot key → admission id, so replay never double-runs) into a dedicated per-schedule session; per-user concurrency cap. External effects inside the run stay approval-gated.
- **Result delivery**: on run settle, output is delivered (always visible in the schedule's session + web inbox; best-effort email/qq push) and the firing settles delivered/failed.
- **Service/REST/tools**: general `create_schedule` (cadence + agent_task), `run-now`, `status` (pause/resume), `firings` history; agent tool `create_scheduled_task`.
- **Scheduler-console UI** at `/reminders`: Scheduled-task card (prompt + cadence picker + channel), Run now, Pause/Resume, run history; responsive.
- **Verify**: full backend gate green (ruff/mypy/pytest), frontend build/lint green, Playwright human lane (create → Run now → history → pause/resume → 390px) + agent lane (real model created a cron task via the tool) both pass.

Deferred (later ADR): multi-step workflow/DAG orchestration, webhook/event triggers, cross-task dependency chains.

## ▶▶▶▶ Phase APPROVALS (owner-approved 2026-07-23): 待审批入口 + 预授权 grants — ✅ **COMPLETE + two-lane verified**

**ADR-034**, driven by the scheduled-email use case: make background/scheduled external actions both safe and automatable. Schema at **`0024`**. Shipped:
- **Web resolution nonce-optional** (APR.A1): the single-use nonce is delivered only on the `permission.asked` SSE event, so background/scheduled approvals couldn't be resolved. For `channel=web` owner resolution the nonce is now optional — authorized by session cookie + CSRF + authorized-actor + full binding (replay prevented by `pending→decided`); non-web channels still require it. Reconciles ADR-020.
- **Approvals page** at `/approvals` (APR.A2): lists pending approvals (incl. from scheduled tasks), Approve-once / Always-allow / Reject with no nonce; sidebar pending-count badge; Inbox links here.
- **Pre-authorization grants** (APR.B1): `permission_grants` (owner-only, tool_name + bounded `match_json`, soft-revoke); per-tool matcher registry (send_email exact recipient allowlist); the loop auto-allows on a grant match — still records the effect + an audit receipt `auto_approved_by_grant`; unmatched actions still ask. Agent has **no** grant path.
- **`always` persists a grant** (APR.B2): resolving with Always-allow derives + merges a grant from the action.
- **Grants REST + UI** (APR.B3): `GET/POST/DELETE /grants`; Approvals page "Pre-authorized" section to add/remove trusted email recipients.
- **Verify** (APR.V): full backend gate green (ruff/mypy/pytest 177), frontend build/lint green. **Playwright two lanes with the real model:** a scheduled email to a whitelisted recipient **auto-sent (no approval)**; a non-whitelisted one **asked** and was **approved from the `/approvals` UI (no nonce)**; add/remove trusted recipient; 390px no overflow.

Deferred (later ADR): wildcard/regex grants, cross-user shared grants, time-window/quota-limited grants, per-session `always`, agent-created grants.

## ▶▶▶▶▶ Phase SCHED-FIX (owner testing feedback 2026-07-24): 定时任务修复 — ✅ **COMPLETE + verified**

**ADR-031 amendment** from owner testing + R-SCHED-CONTEXT research (AstrBot/ChatGPT Tasks/OpenHands/n8n/CrewAI/Hermes all use fresh context per fire). Fixed:
- **Fresh per-firing session** (P1): each cron firing runs in its own session (`scope_type='scheduled_task'`), so history never accumulates → the **2nd-run provider 400 disappears structurally** (root cause was a shared session replaying a prior run's `tool_calls` + duplicate user messages). Scheduled sessions are excluded from the Session Library (browse + search); still inspectable via `/reminders` firing history.
- **Run Now immediate dispatch** (P2): run-now enqueues a one-shot dispatch job (idempotent) instead of waiting ~30s for `agent_task_tick`.
- **Edit + hard delete** (P3): `PATCH /schedules/{id}` (revalidate cadence + recompute `next_fire_at`, optimistic version) + `DELETE /schedules/{id}` (firings then schedule); UI edit form + Delete button.
- **Verify** (SF.V): full backend gate green (179), frontend green. **Playwright with the owner's real email:** run #1 + run #2 **both delivered (no 400)**, dispatch latency **~2s**, isolated runs, scheduled runs absent from Sessions (API + UI), edit + delete work.

**Deferred:** P4 (create-time permission pre-hint) — simple design in `research/scheduled-permission-prehint.md`.

## ▶▶▶▶▶ Phase OBS-A (ADR-033): agent observability, Phase A — instrument (no infra) — ✅ **COMPLETE + verified**

OTel `gen_ai` spans as a **derived, ephemeral diagnostic layer** over the ADR-016 journal (journal stays source of truth). Owner-locked decisions: Phoenix backend (Phase B) / span + durable event / content off by default / **hand-rolled** spans (the provider uses raw httpx streaming, so auto-instrumentation doesn't apply) / independent of ADR-032. Closes **item 0** (the per-LLM-call token/finish blind spot hit debugging the memory bug).
- **OBS.0** deps + config: `opentelemetry-api`/`-sdk` (1.44); frozen `OTEL_*` fields (`otel_enabled=False`, endpoint, `otel_capture_message_content=False`, `otel_traces_sampler=always_on`). commit `cc642a2`.
- **OBS.1** `app/observability/otel.py` TracerProvider gated by `OTEL_ENABLED` (OTLP when endpoint set → Phase B, else console/in-memory; no-op + zero overhead when off) + `genai.py` single source of truth for every `gen_ai.*`/`agent.*` name. Wired into web + worker startup. commit `859fafc`.
- **OBS.2** loop instrumentation: `invoke_agent` root (ids, `agent.loop_count`, `agent.stop_reason`) > `chat` per `provider.stream` (system/model, `finish_reasons`, latency) / `execute_tool` per tool (name/call.id; error → ERROR + `agent.tool.success=false`; approval-gated → success unset). commit `9c6e75d`.
- **OBS.3** real per-call tokens + generation record: provider requests `stream_options.include_usage`, `Finish` carries tokens; loop puts real tokens on the `chat` span + emits durable redacted `model.request`/`model.response` debug events (sha-256 `input_digest`, **no content**, events §2.7 — migration 0025 + data-model.md reconcile `durability='debug'`); `project_run_trace` derives real trace token totals + one `generations` row/call (purpose=web_chat), else the chars/4 estimate. commit `1069cdf`.
- **OBS.4** deterministic `InMemorySpanExporter` tests: span tree, real token attrs, finish_reasons, tool success + tool-error ERROR span, content capture OFF (every attr key `gen_ai.*`/`agent.*`, no prompt leak), debug events digest-only, generations rows. commit `152386a`.
- **Verify** (OBS.V): full backend gate green (**187 passed**, ruff + `mypy app` clean, migration head **0025**). Manual `OTEL_ENABLED=true` in-memory-exporter run prints the tree `invoke_agent(loops=2, stop=completed) > chat(in/out/finish=tool_use) · execute_tool(get_time, success) · chat(in/out/finish=stop)` with real tokens, durable digest-only `model.*` events, 2 generations rows, and real trace totals. **Later verified live in docker** with a real `claude-sonnet-4.6` chat (the earlier `No connected db` was a wrong proxy key; `sk-litellm-local` works): worker stdout showed the `invoke_agent > chat` span tree with real tokens (150/27), matching `model.request/response` debug events + a `generations` row + real trace totals in Postgres. No Playwright (no user-facing UI in Phase A). Compose wires `OTEL_*` as overridable env (commit `1b780f8`).

**Deferred:** Phase B (optional Phoenix container, reuse Postgres + OTLP exporter) · Phase C (evals/flywheel, evidence-gated).

## ▶▶▶▶▶ Phase OBS-LOG (ADR-033): the Logs pillar — make stdout useful by default — ✅ **COMPLETE + verified live**

OBS-A strengthened **traces** (in the DB / OTel spans), but the **Logs** pillar was untouched — so default stdout showed nothing per-call and provider failures were opaque ("400 bad request, logs too terse"). OBS-LOG fixes that **independent of OTEL and of any UI**.
- **LOG.1** provider errors surfaced: `openai_compatible` reads the body on non-2xx and raises `ProviderError(status, redacted body)`; the worker's `run_job` no longer swallows the exception — it logs a structured ERROR (run_id/model/error_type/redacted detail/traceback) and journals the reason on `run.settled`. commit `f382ead`.
- **LOG.2** one structured INFO `llm call` line per model call (provider/model/`input_tok`/`output_tok`/finish/tool_calls/latency, run/session-correlated), **unconditional** (not OTEL-gated). commit `84382d4`.
- **LOG.3** one line per tool execution (`tool call`: tool/call_id/outcome/latency; WARNING on error). commit `84382d4`.
- **LOG.4** JsonFormatter injects `trace_id`/`span_id` from the active span so logs↔trace correlate when OTEL is on; nothing added when off. commit `84382d4`.
- **Redaction fix:** the secret-redactor masks any key containing `token`, so `input_tokens`/`output_tokens` came out `***REDACTED***`; renamed the log keys to `input_tok`/`output_tok` (counts, not secrets). Regression test added.
- **Verify** (LOG.V): gate green (**192 passed**, `mypy app` clean). Live in docker with **OTEL off (default)**: a real `claude-sonnet-4.6` chat logged `llm call ... input_tok=145 output_tok=2 finish_reason=stop latency_ms=2262` to worker stdout (no span dump, OTEL off); pointing the worker at a bogus model logged `run failed ... error_type=ProviderError error_detail="... status=400 body={...Invalid model name...}"` + traceback — the real reason, previously invisible.

**Next: Phase OBS-B (optional Phoenix) or the next roadmap milestone (owner's choice).**

## ▶▶▶▶▶ Phase OBS-B (ADR-033 Phase B): full-prompt content capture → self-hosted Phoenix UI — ✅ **COMPLETE + verified live**

The owner-chosen way to "see every LLM call's full assembled prompt (system + memory + tool list + all messages) + response" — Microsoft-Copilot-`/debug` depth, via the ecosystem-standard pattern (all 8 researched products capture at the single provider-call boundary; `files/debug-ui-research.md`). Chosen over the bespoke in-app inspector (ADR-035, deferred) because the core capture work is shared and Phoenix's UI (waterfall/search/aggregate/replay) is stronger + zero UI to maintain; cost = one optional container.
- **OBSB.0** `opentelemetry-exporter-otlp-proto-grpc` so `otel.py`'s OTLP branch resolves a real exporter. commit `ef142ba`.
- **OBSB.1+2** `genai.capture_llm_io()` attaches the full assembled input (system+memory+transcript messages) + tool schemas + response as **OpenInference** span attrs (`llm.input_messages`/`output_messages`/`tools`, `input.value`) gated by `otel_capture_message_content` (default off); message text preserved (the debug value), structured parts key-redacted, every field size-capped; spans tagged `openinference.span.kind` = AGENT/LLM/TOOL. commit `0a9e5cd`.
- **OBSB.3** optional `phoenix` service behind the `observability` compose profile (reuses Postgres via a separate `phoenix` schema; 6006 UI + 4317 OTLP gRPC); not started with the core stack. commit `2b592cd`.
- **Verify** (OBSB.V): gate green (**195**). Live end-to-end — Phoenix up (`--profile observability`), web+worker with `OTEL_ENABLED`+`OTEL_CAPTURE_MESSAGE_CONTENT`+`OTEL_EXPORTER_OTLP_ENDPOINT=http://phoenix:4317`, a real `claude-sonnet-4.6` tool-using chat → Phoenix trace `AGENT invoke_agent > LLM chat×2 / TOOL execute_tool`, the `chat` span's `llm.input_messages` = full assembled prompt (system+user), `llm.tools` = schemas, `llm.output_messages` = reply, `input.value` contains the user's message; 1 trace, 3 linked child spans (waterfall). **UI: http://localhost:6006.**

**Deferred:** Phase C (evals/flywheel, evidence-gated) · ADR-035 bespoke inspector (fallback if a Sherpa-embedded / container-free viewer is ever needed).

## ▶▶▶▶▶ Phase KB (ADR-036): source-backed Knowledge base — ✅ **KB0→KB5 COMPLETE** (UI two-lane verified) · **+ KB-PERF ingest-throughput pass**

Owner approved the R-KNOWLEDGE-BASE narrow slice (2026-07-26) with 5 locked gates: **private file-backed only** · **local ollama/bge-m3 (1024-d) embedding** (reuses ADR-032) · **zhparser CJK** behind a stable `sherpa_text` config (app-jieba fallback) · **static UI + golden set before backend** · **enters the roadmap**. Separate `knowledge_*` subsystem (not an extension of `memory_passages`). KB0 = contract-first, no business code.
- **Static UI** ✅ ([`design-knowledge/index.html`](design-knowledge/index.html)): 4 surfaces (Knowledge home / source detail / search test / chat citations), Quiet Work tokens, bilingual, Playwright-rendered + owner-approved. SPA route `/library` (avoids the REST `/knowledge/*` prefix).
- **ADR-036** ✅ (design anchor) + **contract deltas** ✅ — data-model (`embedding_profiles`, `knowledge_sources`, `knowledge_source_versions`, `knowledge_chunks`, `knowledge_ingestion_jobs`, `knowledge_retrieval_evidence`; canonical vs derived; immutable snapshots; generation-fenced activation), api §10.4 (`/knowledge/*` + schemas), events §2.8 (`knowledge.ingest`/`knowledge.searched` — refs+counts in journal, excerpts in the retention-scoped evidence table), config (`KNOWLEDGE_*`).
- **Capability-matrix rows** ✅ (docs/11 §9, UI cells ⬜ until KB5) + **retrieval golden-set spec** ✅ ([`research/knowledge-golden-set.md`](research/knowledge-golden-set.md): ≥30 CN/EN/mixed queries, Recall@5 ≥ 0.85, no-answer precision).
- **Remaining KB0:** the timeboxed **zhparser/embedding spike** (does zhparser build on `pgvector:pg16` + beat vector-only on CN exact terms; else app-jieba fallback). Then **KB1→KB5** (schema+lifecycle → parsers/chunking/async index → hybrid retrieval+citations+golden tests → services+REST+tools → Knowledge UI + Playwright two lanes).
- **zhparser spike** ✅ **PASS** (2026-07-26, [`infra/postgres-zhparser/`](../infra/postgres-zhparser/README.md)): combined **pgvector+zhparser image builds** on `pgvector:pg16` (SCWS 1.2.3 + zhparser compile clean); `sherpa_text` config **segments Chinese into words**; a mini-corpus hybrid demo with **real bge-m3 embeddings** (via the live ollama) + RRF showed each branch contributing — lexical disambiguated an exact code (`6602-05` vs `6602-03`), vector rescued a zero-overlap paraphrase. **Decision: `KNOWLEDGE_LEXICAL_BACKEND=zhparser`** (jieba fallback documented, unused). **KB0 complete.**
- **KB1** ✅ (schema + source lifecycle): migration **0027** creates the 6 `knowledge_*`/`embedding_profiles` tables + best-effort `zhparser`/`sherpa_text` (portable: CI on a vanilla image still migrates, config skipped); `app/models/knowledge.py` + `app/services/knowledge.py` (create-from-Drive-file / list / get / reindex / remove-tombstone-cascade / mark-stale-on-file-change, idempotent, tenant-scoped); `docker-compose` postgres now **builds the combined image**. Gate green (ruff/mypy/**pytest 197**); migration applied live on the combined image (zhparser + `sherpa_text` + tables verified).
- **KB2** ✅ (ingestion pipeline): **KB2a** parsers (`pypdf`/`python-docx`/MD/TXT → sections+locators, named `ParseError`) + structural sentence-overlap chunking (language-aware token estimate). **KB2b** durable worker `services/knowledge_ingest.py`: claim(lease+generation-fence) → snapshot(verify file unchanged→immutable object) → parse → chunk → embed(bge-m3)+best-effort `sherpa_text` fts → generation-fenced activate; `knowledge_ingest_job` + recovery tick; every exit named. **KB2c** Drive hooks (overwrite→`mark_stale`+auto-reindex, trash/purge→tombstone) + `knowledge_maintenance` GC (evidence TTL, tombstoned-source purge, orphan-snapshot sweep). Gate green (ruff/mypy/**pytest 212**).
- **KB3** ✅ (hybrid retrieval + citations): `services/knowledge_search.py` fuses a **zhparser lexical** branch (OR-fused, best-effort) + a **pgvector cosine** branch (with a similarity floor so nearest-neighbour doesn't always "match") via RRF, filtered by tenant/user/active-version/not-tombstoned **before ranking**; per-source cap; structured hits with `K:<tool_call_id>:N` refs + page/heading locators; persists excerpts to `knowledge_retrieval_evidence` (not the journal); `sufficient=False` = explicit no-answer. Deterministic CI tests + live bge-m3 smoke. Gate green (**pytest 215**).
- **KB4** ✅ (REST + tools + policy): `api/knowledge.py` (`GET/POST /knowledge/sources`, `GET /…/{id}`, `POST /…/{id}/reindex`, `DELETE /…/{id}`, `POST /knowledge/search`; enqueue best-effort after commit, recovery-tick backstop) + `tools/knowledge_tools.py` (5 tools: `search`/`list`/`add`(by Drive path)/`reindex` → policy **allow**, `remove` destructive → policy **ask**). Retrieved excerpts are untrusted evidence; agent has no grant path. Tests: policy classification, add→ingest→cited search, REST CRUD + CSRF (403 without) + 404 after delete. Gate green (ruff/mypy/**pytest 220**). Capability matrix §9 REST+Tool cells now ✅ (UI ⬜ until KB5).
- **KB5** ✅ (Knowledge UI + chat citations): SPA route **`/library`** → `frontend/src/views/LibraryView.tsx` (Sidebar item in *Organize*, between Memory & Drive; `/knowledge` added to the Vite proxy; typed `api.ts` client). Three surfaces from the static稿: **Sources** home (status pills queued/parsing/chunking/embedding/ready/stale/failed/deleting; per-source Rebuild/Remove; **Rebuild all**; polls while ingesting), an **Add-from-Drive** picker (browse/search Drive → `POST /knowledge/sources`), a **Source detail** panel (status + active version + chunk count + language + failure_code + embedding-profile disclosure + fail-safe note), and a **Search test** tab (`POST /knowledge/search` → hits grouped by source with lexical/vector/both badges, `K:…` citation refs, page/heading locators, RRF scores, explicit no-evidence state). **Chat citation chips (R1 = Option A, no contract change):** ChatView parses the existing `search_knowledge` tool text output (live SSE `tool-result` + a cursor-0 journal backfill for reload) into a `ref→{title,page,heading,excerpt}` map, renders `[K:…]` tokens in the assistant answer as citation chips + a Sources line, and shows a no-evidence banner when a search returned insufficient evidence and the answer cites nothing. Capability matrix §9 UI cells now ✅. Frontend `npm run build` + `npm run lint` green. **KB5 complete → ADR-036 slice done end-to-end (schema → ingest → hybrid retrieval → REST/tools → UI + citations).**
- **KB-PERF** ✅ (ingest throughput + honest progress + bulk add — 2026-07-28, prompted by an AstrBot comparison where building a KB felt "much faster"): four stacked causes were found and fixed. **(1) Embedding was one giant request** — a whole document rode on a single `/api/embed` call with no batching, no concurrency and no retry, bounded by the *chat* provider's 60 s timeout, so large PDFs were slow and past 60 s failed outright. `embed_texts` now splits into `EMBEDDING_BATCH_SIZE` (32) batches, keeps `EMBEDDING_CONCURRENCY` (3) in flight over one shared pool, retries each batch up to `EMBEDDING_MAX_RETRIES` (3) with backoff, and uses its own `EMBEDDING_TIMEOUT_SECONDS` (120) **per batch**; an arity guard rejects short responses so a vector can never attach to the wrong chunk. **(2) Embed failure was not a named exit** (violating the loop invariant): it bubbled into arq and retried into the same timeout — now `_fail(code="embedding_failed")`, with the previous `ready` version left active and searchable. **(3) The UI could only add one file at a time** and disabled every Add button during the request — new `POST /knowledge/sources/batch` (≤50 files, per-file idempotent, **named per-file failures instead of losing the batch**) behind a multi-select Drive picker. **(4) Two-step flow** (upload in Drive → come back to `/library` → add) — `/library` now takes a **drag-drop/choose-files** upload that saves to Drive first (a source is still always backed by a real Drive file, ADR-036) and then indexes. Plus **honest progress**: the live stage and embed counts surface on the source row and detail (`Embedding 28/60`) instead of a source stuck on "queued" for the whole run. Both are published to **Redis, never Postgres** — the human lane caught why this matters: `knowledge_ingestion_jobs.stage` only becomes visible when the worker's transaction commits (i.e. after the run), so a reader outside it sees `queued` for the *entire* job, and an autonomous write would deadlock against the job row that transaction already holds. Telemetry only, never correctness-critical (ADR-016/017); the durable job row is the fallback. UI poll tightened 3500 ms → 1500 ms while ingesting. Contracts updated (api §10.4 batch route + `stage`/`progress_*` + per-file failure semantics; config `EMBEDDING_BATCH_SIZE`/`CONCURRENCY`/`MAX_RETRIES`/`TIMEOUT_SECONDS`; `.env.example`). **No schema change.** New `tests/test_embeddings.py` (order preserved across batches, concurrency cap, progress, retry, exhaustion, arity guard, offline mock) + ingest named-exit + fail-safe-keeps-v1 + Redis progress round-trip + batch-REST tests. Gate green (ruff/format/mypy/**pytest 334**; frontend lint+build). **Two-lane verified** on the live stack: *human* — dropped 3 files (43 KB CN/EN policy + 2 small) straight onto `/library` in one gesture, watched `Queued → Embedding 0/60 → Embedding 28/60 → Ready` (60 chunks ≈ 20 s on CPU ollama, the two batches finishing ~2 s apart = concurrency working), multi-select picker showed "2 selected / Add 2 sources", Search test returned `both · 词法+向量` hits with `K:` refs and `§` locators, zero console errors; *agent* — chat asked for clause SEC-6605 and got a grounded answer with a citation chip resolving to `§差旅与报销制度 2026 / 5. 条款 SEC-6605`. UX review also fixed an a11y defect found in that pass ("choose files" was a `<label>`, not keyboard-focusable → now a real button). Deliberately **not** copied from AstrBot: passive always-on injection (would pollute the stable cached prefix and, with no score floor, always inject), per-KB embedding-model choice (one audited profile, ADR-036 gate 2), and multiple named KBs (gate 1). Remaining levers: the bundled ollama container is **CPU-only** — a host GPU (`.env.example` layout (a)) still dominates; and exact-code lexical matching is imprecise (a search for `SEC-6605` ranked `SEC-6606` first, though the agent recovered by re-querying) — a retrieval-quality follow-up against the golden set, not part of this pass.
- **KB-INGEST-DURABILITY** ✅ (book-length source ⇒ infinite retry loop — found 2026-07-29 by uploading 三国演义.txt, ~1.8 MB / 620k CJK chars / **1406 chunks**): the job died at `299.98s ! knowledge_ingest_job failed, TimeoutError` and then **retried forever**. Four stacked defects, each fixed with a named exit:
  1. **arq's 300 s default job timeout** was never overridden, so any source needing more than 5 minutes was killed mid-embed. `WorkerSettings.functions` now registers `func(knowledge_ingest_job, timeout=KNOWLEDGE_INGEST_JOB_TIMEOUT_SECONDS (3600), max_tries=1)` — `max_tries=1` because retries are *ours*: arq retrying on top would multiply the work.
  2. **The claim was not durable.** `process_ingestion` runs as one transaction that commits only at the end, so a killed job rolled back its `attempt += 1` *and* its lease. The row went back to `stage='queued'`, `lease_expires_at IS NULL` — exactly what `recover_stuck_jobs` selects — so the cron re-dispatched it **every 30 s while the previous 300 s run was still going**, stacking concurrent duplicates (two job ids in the same log window) and pinning ollama at ~790 % CPU. New `claim_job()` runs in its **own committed transaction** before the long work (worker does claim→commit→process→commit), making the attempt counter monotonic and the lease real. Verified live: gen 2 showed `claiming / attempt=1` from a *different* connection while the job ran.
  3. **No bound on attempts** → new `KNOWLEDGE_INGEST_MAX_ATTEMPTS` (3) with the named exit `too_many_attempts`; lease = job timeout + `KNOWLEDGE_INGEST_LEASE_MARGIN_SECONDS` (300) so a killed job is not instantly re-dispatched on top of itself.
  4. **No bound on document size** → `KNOWLEDGE_MAX_CHUNKS` (8000) with the named exit `document_too_large`, so a pathological source fails fast instead of re-burning the timeout.
  Plus **two real bugs in the KB-PERF embedding code itself**, both surfaced by this stress test: (a) a bare `asyncio.gather` propagated the first batch failure immediately, exiting the `async with httpx.AsyncClient` and **closing the client under the batches still in flight** — they then died with `Cannot send a request, as the client has been closed` and burned their retries on a corpse; now `return_exceptions=True` + an abort flag lets every task settle, stops starting new work after a hard failure, and re-raises the *original* error. (b) `str(httpx.ReadTimeout)` is empty, so the retry log read `error: ""` — it now logs the exception **type**. Measured on CPU ollama: one 32-chunk CJK batch takes **81.6 s**, so the old 120 s per-batch timeout was the trigger; defaults retuned to `EMBEDDING_BATCH_SIZE=16` (parallelism comes from concurrent *requests*, not batch size, so a smaller batch costs ~nothing and buys 2× finer progress + cheaper retries) and `EMBEDDING_TIMEOUT_SECONDS=300`. Tests: `claim_job` durability + bounded attempts, leased job is **not** re-dispatched (the dogpile guard), `document_too_large`, and a regression test for the closed-client cascade **proven non-vacuous** by re-running it against the reverted implementation (fails with `started=7`). **No schema change** (`claiming` was already in the `ck_kij_stage` CHECK). Gate green; end-to-end re-verified on a regenerated 1.86 MB / 620k-char CJK book: **1406 chunks, single attempt, zero retries, zero duplicate jobs, monotonic progress, `ready` in ~25 min** (was: never). Retrieval on it returns lexical+vector hits with `sufficient=true`. Two follow-ups recorded, not fixed here: (a) a plain `.txt` has no headings or pages, so its citations carry only an excerpt — no `§`/page locator (structured formats are unaffected); (b) CPU ollama is the wall at ~0.87 chunks/s — a host GPU (`.env.example` layout (a)) is worth more than any remaining knob.

## ▶▶▶▶▶ Phase W2a-DESIGN (ADR-037, owner-approved 2026-07-27): Workspace Projects — 契约与设计先行 — ✅ **COMPLETE; W2a 实现待负责人审核后开始**

**ADR-037** lands the Workspace product model as a **contracts + design-first** batch (no production code, no migration, **Projects navigation not exposed**). Owner-approved: Workspace is the umbrella (**Projects + Drive siblings**); order **W2a→W2b→W3→W4**; **W2a = blank/template/archive import (no GitHub)**; GitHub one-time import = **W2b**; **W3** mounts only a one-time scratch copy (never the source of truth), with the **ADR-025 revision gated on an isolated review + `docker.sock`/multi-user isolation hardening before W3 starts**; W4 = GitHub sync/push/PR (ADR-020 approval). Shipped this batch:
- **ADR-037** ✅ (decisions.md + decisions-log row) — Workspace IA, W2a/W2b/W3/W4 sequencing, non-goals + later-ADR boundaries, security boundary; extends ADR-030, reuses ADR-012/015/016/017/023, previews an ADR-025 revision for W3.
- **Contract deltas** ✅ — **data-model** (`projects`, immutable `project_snapshots`, `project_snapshot_entries` → ADR-030 `storage_blobs`; `sessions.project_id` immutable binding; canonical vs derived; `tenant_id` composite keys), **api §10.5** (Projects REST + schemas + Open-in-Chat; `github`→`501` in W2a), **events §2.9** (`project.lifecycle` + durable archive-import job idempotency/outbox), **config** (`PROJECT_*` bounds + §1.5 Projects security boundary).
- **Capability-matrix rows** ✅ (docs/11 §9) — 4 W2a rows (list/create · archive import · detail/tree · Open-in-Chat) + W2b/W3/W4 boundary rows; **every Projects UI cell ⬜** (static draft only, production nav not exposed).
- **W2a static draft** ✅ ([`design-workspace/index.html`](design-workspace/index.html) + README) — production **Quiet Work** design system, 4 surfaces (Projects list / new project (blank·template·archive, GitHub→W2b disabled) / project detail (read-only tree + snapshots + activity) / Open-in-Chat (project-bound, **read/discuss only** — no working copy/sandbox)); desktop + 390px Playwright-checked.
- **IMPLEMENTATION.md** ✅ — Phase W2a section: `W2a-DESIGN.0` ✅ this batch; `W2a.1…W2a.V` impl tasks **⬜ awaiting owner review**.
- **Verify (W2a-DESIGN.V):** static draft renders desktop **1280px** + mobile **390px** with no horizontal scroll (Playwright, review-only screenshots saved outside git); **no production code, no migration, no exposed Projects navigation**; working tree clean apart from the temp screenshot dir.

**Deferred (each a later ADR, contract-first first):** W2b GitHub one-time import (`project_sources`); W3 task working copy + scratch-copy sandbox + change review (`project_working_copies`/`project_change_sets`/`project_artifacts` + **ADR-025 revision** + docker.sock/multi-user hardening); W4 GitHub sync/push/PR.

## ▶▶▶▶▶▶ Phase W2a (ADR-037, owner-approved 2026-07-27): Workspace Projects 生产实现 — ✅ **COMPLETE + two-lane verified**

Implements the ADR-037 W2a Projects slice (blank/template/archive; **no GitHub**). Schema at **`0028`**. Shipped:
- **Schema/models (W2a.1):** migration `0028` — `projects` + immutable `project_snapshots` + `project_snapshot_entries` (→ ADR-030 `storage_blobs`, shared dedup/quota) + durable `project_import_jobs` + `sessions.project_id` (immutable Project-bound Chat binding). `tenant_id` composite keys (ADR-015). `services/projects.py`: blank/template create, shared immutable-snapshot materializer (dir synthesis, dedup, per-user quota reuse, project `used_bytes` rollup), list/get/tree/read, Open-in-Chat + project-context (bound after first admitted message). Drive blob ref-count + orphan sweep extended to count project entries and skip the `project-import/` prefix.
- **Archive import (W2a.2):** `services/archive.py` — bounded, **in-memory (never-to-disk)** ZIP/TAR expander; rejects absolute/traversal/NUL/device/FIFO/hardlink/escaping-symlink and enforces `PROJECT_MAX_*` (size/count/ratio/depth); format sniffed, not trusted. `services/projects_import.py` — durable stage machine (claim/lease → stage → expand → materialize snapshot → atomic activate; named `termination_reason`; **failed ⇒ no snapshot, visible+deletable**; idempotent per project) + `project_import_tick` recovery. Realizes events §2.9's durable job as `project_import_jobs` (not the run-scoped journal).
- **REST + tools (W2a.3):** `api/projects.py` (§10.5) — list/create/imports(github→**501**, archive→**202**)/get/tree/snapshots/templates/chats + `/sessions/{id}/project-context`; CSRF on writes; 404/409/413/422/501/507. Agent tools (ADR-023 dual adapter): `list_projects`/`create_project`/`project_tree`/`project_read`, all policy **allow**; no destructive/run/push in W2a.
- **UI (W2a.4):** SPA route **`/work/projects`** → `frontend/src/views/ProjectsView.tsx` (Sidebar «Projects» beside Drive; `/projects` added to the Vite proxy). Projects list (status pills importing/ready/failed + storage + unbound source), new-project (blank/template/archive with **GitHub disabled → W2b**, archive safety notes), read-only detail (file tree + snapshots + activity + storage facts + W3 read-only note), Open-in-Chat → project-bound session; ChatView shows a project chip when bound. Capability-matrix §9 UI cells → ✅.
- **Contract sync:** added `project_import_jobs` to data-model §Projects (durable-job realization) + dropped the non-executable NUL CHECK; events §2.9 realization note; api §10.5 additive `import_status`/`import_failure_reason` + `GET /projects/templates`·`/snapshots`; ADR-037 implementation amendment.
- **Verify (W2a.V):** backend gate green (ruff/mypy/**pytest 243**, incl. `test_archive`/`test_projects`/`test_projects_import`/`test_projects_api`/`test_project_tools`); frontend build/lint green; alembic `0028` applied live. **Playwright two lanes with the real model (claude-sonnet-4.6):** human — create blank/template + **safe archive import → ready** + **unsafe (path-traversal) archive → Import Failed·unsafe_archive (0 B, no snapshot)** + detail tree/snapshots/activity + **Open in Chat → immutable binding (`bound:true` after first message)** + GitHub→501 + 390px no overflow (list + detail); agent — the model drove `list_projects`/`project_tree`/`project_read` and reported exact tool output. UX pass: clean Quiet Work pages; list «Open in Chat» disabled for non-ready projects. **Follow-up (W-later):** no project-delete route in the frozen §10.5 (failed projects stay visible but not yet UI-deletable).

**Deferred (each a later ADR):** W2b GitHub one-time import; W3 working copy + scratch-copy sandbox + change review (+ ADR-025 revision + docker.sock/multi-user hardening); W4 GitHub sync/push/PR.

## ▶▶▶▶▶▶ Phase W2b-DESIGN (ADR-038, owner-approved 2026-07-27): Workspace Projects GitHub 一次性导入 — 研究收敛 + 契约与设计先行 — ✅ **COMPLETE; W2b 生产实现待负责人审核后开始**

**ADR-038** 落地 ADR-037 §决策2 预告的 W2b「后续 ADR」，把 **GitHub 一次性导入**冻结为**契约 + 设计先行**批次（**无生产代码/迁移/不暴露 W2b 导航**）。研究收敛（有 GitHub 官方文档证据）：

- **首版 ref 范围 = branch + tag + commit（三者皆首版）**——GitHub `tarball/{ref}` 对三者统一接受，先 `git/ref/...`/`commits/{sha}` **解析成具体 OID 再获取并钉住**。
- **获取 = 有界归档（tarball），非 `git clone`**——只含内容、无 git 历史，复用 W2a 内存内有界安全解压器（不落 `.git`/不建工作副本）。
- **凭据 = AEAD vault 内 GitHub connection**（首版 fine-grained PAT `contents:read`，可扩展 GitHub App 安装令牌，**无需改表**），复用 connector/credential 边界，**绝不**进树/快照/prompt/日志/工具结果/沙箱。
- **只读拉取 ⇒ 幂等，无 `effect_unknown` 远端对账**（那是 W4 push）；GitHub 导入**不给 agent**（人工·跨凭据+不可信外部内容）。**导入后项目独立存活、远端非权威**。

本批次交付：

- **ADR-038** ✅（decisions.md + decisions-log 行）——延伸 ADR-037；复用 ADR-019/030；决策 6 条 + 数据模型 + 能力面 + 事件/幂等 + 安全边界 + W3/W4 非目标。
- **契约增量** ✅ — **data-model**（新增 `project_sources` provenance + `github_connections` AEAD 凭据表；`projects.source_status` 扩展 `importing/imported/import_failed`；`project_import_jobs` 增 `github` create_kind + source/连接列；`project_snapshots.source_oid` 起用），**api §10.6**（`kind='github'` **501→202** + `GET /projects/github/repos`·`/refs` + `GET/POST/DELETE /connections/github` + schema；**不新增 agent 工具**），**events §2.10**（`project.lifecycle` `create_kind='github'` + durable job/幂等/outbox + 只读拉取无 `effect_unknown`），**config**（`GITHUB_*` + §1.6 GitHub 源安全边界）。
- **能力矩阵行** ✅（docs/11 §9）——GitHub 一次性导入 + GitHub 连接两行，**UI 列 ⬜**（设计态，生产实现待审核，W2b 导航未暴露）。
- **W2b 静态稿** ✅（[`design-workspace/github-import.html`](design-workspace/github-import.html) + README）——生产 **Quiet Work** 设计系统，4 面（① 连接状态 / ② repo·ref 选择（branch/tag/commit）/ ③ 导入进度（durable job 阶段 + 失败·重试态）/ ④ 成功·来源元数据（provider/repo id/ref/**source OID**/imported-at/connection + 「远端非权威」+ 只读树 + 初始快照））；明标设计稿、不冒充已实现。
- **IMPLEMENTATION.md** ✅ — Phase W2b-DESIGN：`W2b-DESIGN.0` ✅ 本批；`W2b.1…W2b.V` 实现任务 **⬜ 待负责人审核**。
- **验证（W2b-DESIGN.V）：** 两个 HTML 稿（W2a + W2b）well-formed（无未闭合标签）；W2b 静态稿 Playwright 桌面 **1280px** + 移动 **390px** 全 4 面**无横向滚动**（初测发现 40 位 OID 不换行致 390px 溢出，已加 `overflow-wrap:anywhere` 修复并复测 4 面 scrollW=clientW）；仅供本会话审核的截图存 `C:\src\sherpa\.tmp-w2b-design-screenshots`（**未提交**，非 gitignore 但只按路径提交 docs）；**无生产代码/迁移/新导航暴露**；工作树除临时截图目录外干净。

**Deferred（各自后续 ADR，契约先行）：** W3 任务工作副本 + 一次性 scratch 沙箱 + 变更评审（+ **ADR-025 修订** + docker.sock/多用户隔离加固前置）；W4 GitHub 同步 / push / PR（走 ADR-020，带期望远程 OID，首版不 force push）。

## ▶▶▶▶▶▶▶ Phase W2b (ADR-038, 2026-07-27): Workspace Projects GitHub 一次性导入 — 生产实现 — ✅ **COMPLETE + two-lane verified**

**ADR-038** 的 W2b **生产实现**（承接 W2b-DESIGN 契约批次）。Schema 升至 **`0029`**。落地：
- **Schema/model** ✅ — migration `0029`：`github_connections`（AEAD，复用 connectors 列形态，`ck_ghc_active_has_token`/`ck_ghc_aead_all_or_none`/`uq_ghc_owner_active`）+ `project_sources`（provenance：repo id/owner/repo/ref/`source_oid`/status）+ `projects.source_status` 扩展（`unbound|importing|imported|import_failed`）+ `project_import_jobs` github 列（`create_kind='github'`/`connection_id`/`source_ref_type`/`source_ref`/`resolved_oid`）。
- **凭据 AEAD** ✅ — `security/github_token.py`：token 直接在活动 KEK 下 AES-256-GCM 封装，AAD 由行身份重算，解封受 connector-vault capability 门控；**只**在连接边界解密，**绝不**进树/快照/prompt/日志/事件/工具结果/沙箱。
- **service** ✅ — `services/github_source.py`：连接生命周期（create 先 `GET /user` 校验再封装；delete = 软撤销 + 擦除 token）+ 只读 REST 代理（repos/refs 选择器）+ 导入原语（ref→OID 解析、有界 tarball 获取，所有 GitHub 失败 **redacted**）。
- **durable job** ✅ — `services/projects_import.py` 的 github 分支：认领 → resolve ref→OID（钉住）→ 有界 tarball 获取 → 复用 W2a 内存安全解压器 → 剥离 tarball 顶层目录 → 不可变初始快照（`source_oid`）→ 原子激活 + `source_status='imported'` + 冻结 provenance。只读拉取 ⇒ 幂等、**无 `effect_unknown`**；失败保持 `status='active'` 无快照（可见可删），`retry_github_import` 按钉住 OID 重取 → 相同字节。
- **REST** ✅ — `POST /projects/imports kind=github` **501→202**（JSON body）、`POST /projects/{id}/imports/retry`、`GET /projects/github/repos`·`/refs`、`GET/POST/DELETE /connections/github`；token 只服务端、绝不下发；`ProjectSummary.source_status` 扩展 + `GET /projects/{id}` 带 `source` provenance。**不新增 agent 工具**（人工导入）。Vite proxy 加 `/connections`。
- **UI** ✅ — 生产 `/work/projects`：GitHub 连接面板（fine-grained PAT，password 输入，永不回显 token）+ repo 选择（搜索/选中）+ ref 选择（branch/tag/commit）+ 导入进度 + 详情来源元数据卡（provider/repo/repo id/ref/**source OID**/imported/connection + 「远端非权威」）+ 失败重试；40 位 OID `overflow-wrap:anywhere` 保 390px 无横向溢出。能力矩阵 §9 两行 service/REST/UI 单元格 → ✅。
- **验证（W2b.V）：** alembic `upgrade head → 0029`；ruff + `ruff format --check`（改动文件）+ mypy 全清；**full pytest 257 green**（新增 `test_github_token`/`test_github_source`/`test_projects_import_github`/`test_connections_api` + `test_projects_api` 501→409 更新）；前端 `npm run lint` + `build` green。Playwright 两栈（human：连接 → 选 repo/ref → 导入 → 来源元数据 + 失败/重试 + 390px；agent：既有 `project_tree`/`project_read` 读已导入项目）+ 真实 GitHub.com 导入验证（见下）。关键截图存 `C:\src\sherpa\.tmp-w2b-implementation-screenshots`（**未提交**）。
- **收尾修复（scope 偏差）：** 独立审查发现 `create_connection` 只查 `auth_kind=='pat'` + `GET /user`，未校验 token 形态，故 classic/OAuth/App token 也可能被接受，偏离「首版仅 fine-grained PAT」的批准范围。修复：新增 `security/github_token.py::classify_github_token`（纯前缀分类，只返回类别标签，绝不回显 token/长度/片段/hash）+ `FINE_GRAINED_PAT_PREFIX`；`create_connection` 在**任何网络调用之前**强制只接受 `github_pat_` 前缀，拒绝 `ghp_/gho_/ghs_/ghu_/空/other`（稳定、不回显的 `422`）；补 `test_github_token`（分类矩阵 + 不泄漏）与 `test_github_source`（拒绝非 fine-grained + 接受 fine-grained）；`security/github_token.py` 顶部把「或 GitHub App 安装令牌」的过时注释改为「首版仅 fine-grained PAT，App 令牌为未建的 forward `auth_kind`」；同步 api.md/data-model.md 输入边界文案（不扩展功能）。当次 live demo 连接经连接器边界内存判定为 **OAuth（`gho_`）** 越界凭据，已走正式 `delete_connection` 软撤销 + 擦除密文（`token_enc=NULL`、`status=revoked`，剩余 live=0），既有 project provenance/snapshot 不删。
- **QA 收尾修复（W2b 截图审查，Agent 正确性）：** 最终截图审查发现 `project_tree` **把截断页误当完整树**——真实导入项目快照 412 entries，但 `ProjectTreeTool` 用 `svc.get_tree` 默认 `limit=200`，响应又不带任何「还有更多」标记，模型因此误报 `frontend/`/`infra/`/`docs/` 不存在。**通用最小修复（契约先行）：** ① api.md §10.5 `ProjectTree` 增 `truncated: bool` + `returned_count: int`（path 过滤无廉价 total，**不虚构** `total`）；② `services/projects.py::get_tree` 改为取 `limit+1` 行判断截断并回切到 `limit`，`ProjectTree` dataclass 携带 `truncated`，空树 `truncated=False`；③ REST `GET /projects/{id}/tree` 回传 `truncated` + `returned_count`；④ `ProjectTreeTool` 请求上限 **500** 条，`description` 与 `llm_content` 在截断时明确「PARTIAL result / 缺失不等于不存在 / 用 `path` 前缀再查」，未截断时标「complete listing」，绝不让模型把截断当完整；⑤ 新增 service/API/tool 测试覆盖 `>limit` 截断与提示文案，保持既有 UI 兼容（前端 `ProjectTree` 类型加两字段 + 详情树「partial」提示）。**验证：** ruff+`format --check`+mypy 全清；targeted `test_projects`/`test_projects_api`/`test_project_tools` **13 passed**；前端 `npm run lint`+`build` green。真实模型 Agent lane 复跑（见下）确认已纠正；**未新建 GitHub 连接**（live=0）。

## ▶▶▶▶▶▶▶▶ Phase W3-DESIGN/SECURITY (ADR-039 + ADR-040, owner-approved 2026-07-27): Workspace Projects 任务工作副本 + 一次性 scratch 沙箱变更评审 — 安全评审 + 契约与设计先行 — ✅ **COMPLETE; W3 生产实现待负责人审核后开始**

负责人批准按正常顺序进入 W3 并先执行「安全评审 + ADR/契约/设计先行」。本批次**不写生产代码/不做迁移/无真实 sandbox 挂载/不暴露 W3 导航**（AGENTS.md §1/§2）。

- **第一优先：独立沙箱隔离安全评审 → ADR-039** ✅（decisions.md + decisions-log 行）：一手来源确证 **worker 挂 `docker.sock` ≈ 宿主 root**（OWASP Docker Cheat Sheet Rule#1；只读挂 socket 无用；CVE-2024-21626 表明 shared-kernel runc 即便全硬化 flag 也可逃逸）；现有「**socket 只给可信编排进程、不可信代码只在其派生容器里、容器绝不碰 socket**」的**专用 sandbox 编排**模式正确、须保持；**socket-proxy 对本编排角色是假安全**（需放行 `containers/create`/`exec`=放行逃逸），不采用；**rootless Docker** = 单用户推荐加固；**gVisor(`runsc`)** = 多用户实用最低标准（无已知宿主逃逸 CVE）；**Kata/Firecracker microVM** = 真不可信第三方代码必需；scratch **RW 挂载只挂一次性拷贝**（拷贝前剔除凭据 + `nosuid,nodev` + 编排原子清理/孤儿扫除）。产出 **W3 首版最小安全架构 + 明确禁止上线条件**（多用户前必须 gVisor/microVM + 不共享 socket + 每租户 scratch/出口/配额隔离 + 威胁评审）；**未实施缓解绝不写成已安全**。
- **产品/数据/工具/生命周期 → ADR-040** ✅（decisions.md + decisions-log 行）：Project 绑定 Chat 首次变更动作**惰性开跨 turn 持久工作副本**（base=当前 head 快照）；**真相源=Sherpa 快照 head**，工作副本 overlay=**持久任务态**，scratch 卷/热容器=**可丢缓存**；每次执行**物化一次性 scratch 拷贝**、有界批次后**持久化 overlay**；**Change Review** 展示 added/modified/deleted + artifacts；用户 **Save selected / Save+checkpoint / Discard**（**Save 不给 agent**，人工评审闸）；head 移动 → **stale Save 用 `head_generation` CAS 拒绝**（`409 head_moved`，须重评审 rebase）；**single-writer lease + fence**（stale fence 不能发布 overlay）；缺依赖 → 显式 `environment_missing_dependencies`（绝不联网装包）；内置 file/edit/run/test 工具在 scratch 上工作，**不嵌 coding agent**；**不做 git init/history/commit/branch、不做 GitHub sync/push/PR（W4）**。
- **正式修订 ADR-025** ✅：把「不挂 workspace（纯计算）」收窄为「**仅挂一次性 scratch 副本、永不挂真相源**」（保留断网/掉权/非 root/只读 rootfs+tmpfs/资源+时限/`--rm`/无密钥注入；scratch 额外 `nosuid,nodev`），**受 ADR-039 门控**、仅自托管单用户生效。
- **冻结契约增量** ✅ — **data-model** §Projects W3（`project_working_copies`/`project_working_copy_entries`(overlay)/`project_change_sets`/`project_change_set_entries`/`project_artifacts`/`project_sandbox_runs` + `projects.head_generation`；canonical vs 可重建缓存、lease/fence、CAS、`tenant_id` 复合键、字节不入 journal），**api §10.7**（working-copy/sandbox-run/change-review/Save-selected·checkpoint·Discard/artifacts REST + Tool schema：`project_run`/`project_review_changes` allow、Save 系列 user-only），**events §2.11**（沙箱无对外副作用⇒无 `effect_unknown`、fence 守护幂等持久、head-gen CAS、crash recovery），**config §1.7** + `SANDBOX_*`/`WORKING_COPY_*`（mount/lifecycle/resource/network/credential 边界）。
- **能力矩阵行** ✅（docs/11 §9）——W3 工作副本/变更评审/Save·checkpoint·Discard 三行，**UI 列 ⬜**（静态稿；生产 W3 导航未暴露）。
- **W3 静态稿** ✅（[`design-workspace/w3-change-review.html`](design-workspace/w3-change-review.html) + README）——生产 **Quiet Work** 设计系统，4 面（Project Chat 执行态 / 变更评审 diff+artifacts / Stale·冲突 / 隔离与真相源）；明标「设计稿·未实现」；桌面 1280px + 移动 390px 均无横向滚动。
- **IMPLEMENTATION.md** ✅ — Phase W3-DESIGN/SECURITY：`W3-SECURITY.0` + `W3-DESIGN.0` ✅ 本批；`W3.1…W3.V` 实现任务 **⬜ 待负责人审核**。
- **验证（W3-DESIGN.V）：** 三个 workspace HTML 稿（W2a/W2b/W3）well-formed（标签平衡）；W3 静态稿 Playwright 桌面 **1280px** + 移动 **390px** 全 4 面 `scrollWidth==clientWidth`（初测 review/arch 因 grid 子项 `min-width:auto` + arch 内联 2 列溢出，改 mobile-first grid + `min-width:0` + `.review-grid.even` 修复并复测 4 面 overflow=0）；仅供审核截图存 `C:\src\sherpa\.tmp-w3-design-screenshots`（**未提交**，先删除了旧 `.tmp-w2b-implementation-screenshots`）；**无生产代码/迁移/真实挂载/W3 导航暴露**；工作树除临时截图目录外干净。

**Deferred（W3 生产实现，待负责人审核 ADR-039 + ADR-040 + ADR-025 修订后开始）：** W3.1 schema+working-copy 生命周期 · W3.2 沙箱编排（硬化 + 仅一次性 scratch 挂载）· W3.3 change-set + REST §10.7 + 工具 · W3.4 `/work/projects` W3 UI · W3.V 两栈验证。**W4** = GitHub 同步/push/PR（走 ADR-020，另带自己的 ADR）。

## ▶▶▶▶▶▶▶▶▶ Phase W3 (ADR-039 + ADR-040, owner-approved 2026-07-27): Workspace Projects 任务工作副本 + 一次性 scratch 沙箱变更评审 — 生产实现 — ✅ **COMPLETE + two-lane verified**

W3.1→W3.V 全部落地。Schema 升至 **`0030`**。

- **W3.1 schema + working-copy 生命周期** ✅ (`70f0e57`)：migration `0030` = 6 张 `project_*` W3 表（`project_working_copies` + `_working_copy_entries` overlay · `project_change_sets` + `_entries` · `project_artifacts` · `project_sandbox_runs`）+ `projects.head_generation` CAS 令牌；`tenant_id` 复合键（ADR-015）；`uq_pwc_live_session` 保证每个 Project 绑定 Chat 至多一个活工作副本。`services/project_workcopy.py`：惰性 open（幂等·按会话隔离）· single-writer lease + 单调 fence（stale fence 绝不能发布）· fence 守护幂等 overlay 持久 + 对共享 ADR-030 账户的配额预留 · Save/Save-selected/checkpoint = head_generation **CAS**（head 移动→`conflicted`/`head_moved`）· Discard（head 字节相同）· idle-expiry 一次原子事务释放预留。config §1.7 `SANDBOX_*`/`WORKING_COPY_*` 冻结。test_project_workcopy 8 passed。
- **W3.2 沙箱编排** ✅ (`4b29aea`)：`app/sandbox/project_sandbox.py`（host 侧 scratch 机制，离线全可测）——把「base 快照 + 持久 overlay」物化进 `SANDBOX_SCRATCH_ROOT/<run>` 一次性 scratch（只写项目字节·每个路径校验在 scratch 根内）· host 侧 edit（write/delete）· scratch↔base delta（`WORKING_COPY_MAX_*` 有界）· cleanup/孤儿扫除；硬化容器仅**单个** RW 绑定挂载 scratch（`network_disabled`/`cap_drop ALL`/非 root/只读 rootfs+tmpfs nosuid,nodev/mem·pids·cpu·wall 上限/`--rm`），受 `SANDBOX_KIND` 门控。`services/project_sandbox.py` 编排：取 lease/fence → `project_sandbox_runs` 行 → 物化 → edit → 命令 → 有界 delta → 暂存 blob → fence 守护幂等 overlay 持久（唯一持久副作用；events §2.11②）→ 拆除。具名终止原因；缺依赖(exit 127)= 显式 `environment_missing_dependencies`（仍持久已应用的 edit）；越界 delta 绝不持久（无静默半量）；无对外副作用 ⇒ 无 `effect_unknown`。worker 启动孤儿 scratch 扫除 + `project_workcopy_maintenance` cron（idle 过期 + scratch 扫除）。test_project_sandbox 7 passed。
- **W3.3 change-set + REST §10.7 + 工具** ✅ (`ac0fb77`)：`services/project_changes.py` = overlay↔base 的有界可评审投影（added/modified/deleted 计数 · 逐文件 unified diff 溢出到 MinIO 按 `WORKING_COPY_MAX_DIFF_BYTES` 有界 · 二进制检测 · `WORKING_COPY_MAX_CHANGED_FILES` 截断=显式 partial）；Save selected / Save+checkpoint 经 working-copy head-gen CAS（移动→`Conflict('head_moved')`→`409 SaveConflict`）· 部分保存重建剩余 change set · Discard · artifacts（ephemeral→Keep 计配额 / Export 拷进 Drive）。`drive._recompute_blob` 计入 **retained** artifact（Keep 一致计配额）。api §10.7 全 REST（GET working-copy · POST sandbox-runs · GET change-sets/{cs}·/entries/{e}/diff · POST apply(409 SaveConflict head_moved 信封)/discard · working-copies/{id}/discard · artifacts list/keep/export；写需 CSRF）。工具 `project_run`（writes/deletes/command→沙箱边界→change set，policy **allow**）+ `project_review_changes`（read_only·allow）；Save/checkpoint/push/delete **不给 agent**（人工评审闸）。test_project_changes 8 + REST flow。
- **W3.4 前端** ✅ (`fa5a3c0`)：`components/ChangeReview.tsx` = Project 绑定 Chat 里的人工评审面（added/modified/deleted 逐条勾选 + 按需 unified diff · binary/exec 徽章 · artifacts Keep/Export→Drive · Save selected/Save+checkpoint/Discard；head 移动→冲突横幅；截断→显式 partial）；ChatView 跟踪活工作副本（run settle 后刷新）+ 「Review changes(N)」开关；project chip 反映 working-copy 状态。api.ts W3 类型/客户端 + `reqText`（纯文本 diff）。styles.css Quiet Work 样式（响应式；<640px 动作栏换行）。能力矩阵 §9 三行 service/UI 单元格 → ✅。
- **验证（W3.V）** ✅：**full pytest 297 green**、ruff+`ruff format`+`mypy app` 全清、前端 lint+build green。栈重建（web/worker/frontend 镜像）。**真实模型 claude-sonnet-4.6 两栈 Playwright：** agent — 模型驱动 `project_run` 改 main.py → 持久工作副本 + change set（`list_projects`/`project_read`/`project_run` tool 调用见 worker 日志）；human — Change Review 面渲染真实 unified diff、**Save + checkpoint** 推进 head（`head_generation` 0→1 + pinned `checkpoint` 快照）、**Discard** 保持 head 字节相同（工作副本 `discarded`、无快照）、390px overflow=0。**验证中发现并修复的 bug：** discard 路由的 `_wc_summary` 读到 flush 后过期的 `updated_at` 列触发 `MissingGreenlet` → `await db.refresh(wc)` + REST discard 回归测试。
- **UX 记录：** (a) Projects 页残留的 W2a「read/discuss only」文案已更新为 W3 工作副本流程；(b) change-review 条目行在 390px 的间距可再收紧（小）；(c) 中途失败的 run（观察到的存储污染遗留项目）在 Chat 侧无错误横幅——既有 observability 缺口，非 W3。
- **开发栈已知限制（如实记录）：** worker 共享宿主 `docker.sock`，故兄弟 sandbox 容器的绑定挂载 source 在**宿主**而非 worker 容器内解析——本 Docker-Desktop 开发栈里 `project_run` 的 **shell 命令** 看到空 `/work`；host 侧 **edit**（写/删）+ 整个 change-review/save/discard 环路不受影响。按 ADR-039，此共享 socket 姿态仅限开发单用户；多用户前需生产 runner（gVisor/microVM 或宿主对齐的 scratch 路径）。

**Deferred:** W4 = GitHub 同步/push/PR（走 ADR-020，另带自己的 ADR，带期望远程 OID，首版不 force push）。

## ▶▶▶▶▶▶▶▶▶▶ Phase MP (ADR-041, owner-approved 2026-07-28): 多来源模型 provider（用户在设置里配置）— ✅ **COMPLETE + two-lane verified**

roadmap #8 的「多 provider」那一半（failover/子 agent 后置）。把 env 单一 provider 升级为 **DB 支持、用户可配的多 provider 注册表**。研究先行 [`research/model-provider.md`](research/model-provider.md)（深读 AstrBot/hermes-agent/PI-agent + landscape），ADR-041 + 契约先行（data-model §Model providers · api §10.8 · config · 静态 `design-settings-models/`），再生产实现。Schema 升至 **`0031`**。

- **MP.1 schema + AEAD 密钥 + service** ✅（`dad3b95`）：migration `0031` = `model_providers`（kind[openai_compatible/anthropic/gemini] + base_url + AEAD 密钥列[github_connections 同形] + models[]/default_model/is_default/status；唯一默认+唯一名，`tenant_id` 复合键）+ `sessions.model_provider_id/model`。`security/model_provider_key.py`（KEK 直封，AAD 由行身份重算，connector-vault capability 门控）。`services/model_providers.py`：CRUD（create 封密钥+首个设默认；改密钥→status pending；set_default 原子清旧）、record_test_result、per-session get/set、resolve_for_session。
- **MP.2 适配器** ✅（`2afedce`）：`providers/tools.py`（openai/anthropic/gemini 三序列化器 + Gemini schema 收敛）；增强 `openai_compatible`（reasoning_content/reasoning→ReasoningDelta、per-choice usage、base_url 版本规范化）；原生 `anthropic`（Messages API：system 顶层/tool_result 入 user/连续同角色合并/max_tokens/block SSE/thinking 签名）；原生 `gemini`（generateContent：functionDeclarations/functionResponse 名解析/parts 流/thought_signature/单块 tool 参数）；`factory.build_from_config(kind,...)`。**修复**：原生适配器初版把 `f"******"` 当密钥头发出（ruff 抓到），已改真实密钥。
- **MP.3 DB 解析 + 测试连接 + REST §10.8** ✅（`ba3d248`）：`provider_for_session`（会话覆盖→全局默认→env 兜底，仅此边界解密）；`test_connection`（服务端拉 /models）；worker chat loop 接 DB 解析；`api/model_providers.py` §10.8（providers CRUD + test/default/models + session-model，**密钥只入不出**、写需 CSRF、**无 agent 工具**）+ 注册。
- **MP.4 Settings「Models」UI + chat 切换器** ✅（`bf9c1ad`）：`components/ModelsPanel.tsx`（来源卡片 + 加来源表单[password 密钥永不回显] + 测试连接 + 选默认 + 每源默认 model）→ SettingsView；`components/ModelSwitcher.tsx`（chat 顶栏每会话切 model）→ ChatView；api.ts 客户端 + Vite proxy `/providers` + styles；能力矩阵 §9 两行 service/UI ✅。
- **验证（MP.V）** ✅（`741ef7a` 修 test_observability patch 点）：**full pytest green**（唯一失败是 observability 测试 patch 了旧 `build_provider`——run_job 现经 `provider_for_session`，已改 patch 点）、ruff/mypy 全清、前端 lint/build green。栈重建。**真实两栈 Playwright**：human — Settings「Models」加 `openai_compatible` 源（litellm 代理）→ **测试连接成功**（Active、key ✓、拉到 **29 个 model**）→ 设默认 + 选默认 model；agent — chat 发消息，worker 日志确认 `provider=openai_compatible model=claude-sonnet-4.6`（**DB 配置的源**）→ 真实回复「Paris」；**每会话切换**到 `gpt-4o-mini` → 下条消息 worker 用 `gpt-4o-mini`（同会话 turn2）。密钥全程 write-only、AEAD 封存、绝不回显。Settings 面 390px overflow=0。

**Deferred（各自后续 ADR）：** 跨-provider failover、MoA/ensemble、成本 ledger、Bedrock/Vertex/OpenAI-Responses、子 agent、多 key 轮换。

## In progress
_Nothing in progress._ **M-tools shipped** (ADR-023, [`11-agent-tool-surface.md`](11-agent-tool-surface.md)): app/services/ capability layer + REST/Tool dual adapters; the agent tools = list/accept/edit/dismiss candidates, create/update/complete/list todos (+ POST /todos, migration 0014 for standalone agent todos), list/sync connectors, create/list/cancel reminders + digests (+ /schedules REST), list notifications/activity + get/update settings; ALLOWED policy engine (own-data writes allowed, external actions ask); tool output spill.
_Nothing in progress._ **M-tools shipped** (ADR-023, [`11-agent-tool-surface.md`](11-agent-tool-surface.md)): app/services/ capability layer + REST/Tool dual adapters; the agent tools = list/accept/edit/dismiss candidates, create/update/complete/list todos (+ POST /todos, migration 0014 for standalone agent todos), list/sync connectors, create/list/cancel reminders + digests (+ /schedules REST), list notifications/activity + get/update settings; ALLOWED policy engine (own-data writes allowed, external actions ask); tool output spill.
Dev DB: `docker compose -f infra/docker-compose.yml --env-file .env up --build -d` (schema at alembic `0014`; `--env-file .env` enables the real model). Note: `uv run pytest` used to wipe the owner tenant → re-login; **no longer true since B-9/[ADR-044](decisions.md)** — the suite runs against its own database. **SPA routes must not collide with an API proxy prefix** (Activity UI lives at `/data`).

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
| **ADR-032 (partial) — embedding → bundled ollama** (dim 1536→1024, `EMBEDDING_*` decoupled from provider, `ollama` compose service behind `embeddings` profile, migration 0026) | ✅ done (gate green: ruff/mypy/pytest 195; live ollama bge-m3 smoke) |
| **KB0 (ADR-036) — Knowledge base design/contract batch** (static UI + ADR + data-model/api/events/config deltas + capability matrix + golden-set spec + zhparser spike) | ✅ done (spike PASS: pgvector+zhparser image builds, CN segmentation + hybrid RRF verified with live bge-m3; `KNOWLEDGE_LEXICAL_BACKEND=zhparser`) |
| **KB1 (ADR-036) — Knowledge schema + source lifecycle** (migration 0027 + models + service create/list/get/reindex/remove/stale + combined image wiring) | ✅ done (gate green: ruff/mypy/pytest 197; 0027 applied live on the combined image) |
| **KB2 (ADR-036) — Knowledge ingestion pipeline** (KB2a parsers+chunking · KB2b durable snapshot→parse→chunk→embed+fts→fenced-activate worker · KB2c Drive hooks + GC) | ✅ done (gate green: ruff/mypy/pytest 212; pypdf/python-docx deps) |
| **KB3 (ADR-036) — Hybrid retrieval + citations** (`search_knowledge`: zhparser+pgvector RRF, similarity floor, K:refs + evidence table, no-answer) | ✅ done (gate green: ruff/mypy/pytest 215; live bge-m3 semantic smoke passed) |
| **KB4 (ADR-036) — Knowledge REST + tools + policy** (`/knowledge/*` REST · 5 agent tools search/list/add/reindex/remove · remove→ask) | ✅ done (gate green: ruff/mypy/pytest 220; capability matrix §9 REST+Tool ✅, UI ⬜) |
| **KB-PERF (ADR-036 follow-up) — ingest throughput + honest progress + bulk add** (batched/concurrent/retried embedding w/ its own per-batch timeout · `embedding_failed` named exit · `POST /knowledge/sources/batch` · multi-select picker · drag-drop upload in `/library` · Redis-backed stage/progress) | ✅ done (gate green: ruff/format/mypy/pytest 334; frontend lint+build; no schema change) |
| **KB-INGEST-DURABILITY (ADR-036 follow-up) — book-length source no longer loops forever** (explicit arq `job_timeout` 3600s + `max_tries=1` · durable committed `claim_job` so attempt/lease survive a kill · bounded attempts → `too_many_attempts` · chunk cap → `document_too_large` · fixed the closed-client cascade + empty retry-log diagnostics in `embed_texts`) | ✅ done (found by a 1.8 MB / 1406-chunk CJK upload; gate green; no schema change) |
| **W2a-DESIGN (ADR-037) — Workspace Projects 契约与设计先行** (ADR-037 + data-model `projects`/`project_snapshots`/`project_snapshot_entries` + `sessions.project_id` + api §10.5 + events §2.9 + config `PROJECT_*` + 能力矩阵行 UI ⬜ + `design-workspace/` 静态稿) | ✅ done (契约先行；无生产代码/迁移/导航；静态稿桌面+390px Playwright；W2a 实现待负责人审核) |
| **W2a (ADR-037) — Workspace Projects 生产实现** (migration 0028 projects/snapshots/entries/import_jobs + sessions.project_id · services/projects+archive+projects_import durable job · REST §10.5 · `project_*` 工具 · `/work/projects` UI · 能力矩阵 UI ✅) | ✅ done (gate green ruff/mypy/pytest 243; 两栈 Playwright: 空/模板/安全+不安全归档导入、详情、Open-in-Chat 不可变绑定、GitHub 501、390px; agent 驱动 project_* 工具) |
| **W2b-DESIGN (ADR-038) — Workspace Projects GitHub 一次性导入 契约与设计先行** (ADR-038 + data-model `project_sources`/`github_connections`/`source_status`/`project_import_jobs(github)` + api §10.6 501→202 + repo/ref/connection 端点 + events §2.10 create_kind=github + config `GITHUB_*`/§1.6 + 能力矩阵行 UI ⬜ + `design-workspace/github-import.html` 静态稿) | ✅ done (契约先行；研究收敛 branch/tag/commit + tarball 有界获取 + PAT 凭据 + 只读幂等无 effect_unknown；HTML well-formed；静态稿桌面+390px Playwright 无溢出；无生产代码/迁移/W2b 导航；W2b 实现待负责人审核) |
| **W3-DESIGN/SECURITY (ADR-039 + ADR-040) — Workspace Projects 任务工作副本 + 一次性 scratch 沙箱变更评审 安全评审 + 契约与设计先行** (独立 docker.sock/隔离威胁模型 → ADR-039 + W3 产品/数据/工具/生命周期 → ADR-040 + **正式修订 ADR-025** + data-model §Projects W3 `project_working_copies`/`overlay`/`change_sets`/`entries`/`artifacts`/`sandbox_runs`/`head_generation` + api §10.7 + events §2.11 + config §1.7/`SANDBOX_*`/`WORKING_COPY_*` + 能力矩阵行 UI ⬜ + `design-workspace/w3-change-review.html` 静态稿) | ✅ done (安全评审 + 契约先行；一手来源确证 docker.sock≈宿主 root、socket-proxy 假安全、rootless/gVisor/microVM 阶梯 + 禁止上线条件、未实施缓解不写成已安全；lease/fence + head_generation CAS + 沙箱无 effect_unknown + 仅挂一次性 scratch；HTML well-formed；静态稿桌面+390px Playwright overflow=0；无生产代码/迁移/真实挂载/W3 导航；W3 实现待负责人审核) |
| **W3 (ADR-039 + ADR-040) — Workspace Projects 任务工作副本 + 一次性 scratch 沙箱变更评审 生产实现** (migration 0030 6 张 project_* W3 表 + projects.head_generation · services/project_workcopy(lease/fence/CAS/持久/discard/expire) · app/sandbox+services/project_sandbox(硬化仅一次性 scratch 挂载·delta·孤儿扫除) · services/project_changes(有界 diff/artifacts/apply CAS/discard) · api §10.7 + project_run/project_review_changes 工具 · ChangeReview UI · worker 扫除/维护 cron · 能力矩阵 §9 UI ✅) | ✅ done (full pytest 297 green ruff/mypy 清；栈重建；真实 claude-sonnet-4.6 两栈 Playwright：agent project_run→working copy+change set；human Change Review→真实 diff→Save+checkpoint 推进 head+pinned checkpoint、Discard head 不变、390px overflow=0；验证中修复 `_wc_summary` discard MissingGreenlet + 回归测试)。⚠️ **事后更正**：其中的 agent 泳道断言只到"working copy + change set 产生"，**容器从未真正启动过**(B-8)——真正跑起来是 2026-07-31 的 P3。 |
| **Phase TR P3 (ADR-047) — tar workspace transport + first-party runner image** (`app/sandbox/runtime.py` 合并 · 新 `app/sandbox/transport.py` **流式有界** tar ingress/egress · **删除全部 bind mount 与宿主路径** · 凭据剔除+断言+回填(不误判为删除) · egress 不可信解包 → `path_escape` · `mem_limit`/输出有界 · 容器 label 孤儿扫除 · `sandbox-runner/` Dockerfile(基础镜像按 registry digest 固定)+capabilities.json(非 root/只读 rootfs/pin python+pytest+ruff/无 git 无网络工具) · 删 `SANDBOX_SCRATCH_ROOT`+`SANDBOX_WARM_TTL_SECONDS`、`SANDBOX_MEM_MB`→1024、`SANDBOX_SCRATCH_MAX_BYTES`→512 MiB · **镜像固定 fail-closed 强制**(`runtime_image_untrusted`) · **新增真 Docker 泳道 `uv run pytest -m docker`**) | ✅ done (**无迁移**；pytest **516 passed** + `-m docker` **24 passed**，ruff/format/mypy 清，前端 lint+build 绿；栈重建 + `SANDBOX_IMAGE` 按 image ID digest 固定；三路实测：worker 容器内 DooD、真实 claude-sonnet-4.6 agent 泳道(`pytest -q` 真 exit 1 → 修复 → 真 exit 0 · `chmod +x` → `./deploy.sh` 输出 deployed · `git` 127)、human Change Review 真实 diff→Save→head_generation=2 且 `executable=t` 落库。真 Docker 泳道首跑即抓到隐式父目录 root 属主的真 bug；独立评审又抓出 4 个阻断缺陷，已于 `66ce8e6` 修复并各配"去掉修复即失败"的回归测试。**B-8 仍 OPEN**：Run 控件/流式/Stop 属 P4+P5) |
| **backlog B-12 — Drive 孤儿 GC 删掉 change-set diff 溢出对象** (P3 人工泳道发现；`sweep_orphan_objects` 只豁免 `project-import/`，把 `project-diff/` 全删；实证 `orphans=6` 后 26 秒 Change Review 500 NoSuchKey) | ✅ fixed (显式豁免前缀元组 + 回归测试**已验证去掉修复就会失败**；**保留期问题仍开放**——建议并入 api §7.2 spill janitor) |
| **backlog B-12 — Drive 孤儿 GC 删掉 change-set diff 溢出对象** (P3 人工泳道发现；`sweep_orphan_objects` 只豁免 `project-import/`，把 `project-diff/` 全删；实证 `orphans=6` 后 26 秒 Change Review 500 NoSuchKey) | ✅ fixed (显式豁免前缀元组 + 回归测试**已验证去掉修复就会失败**；**保留期问题仍开放**——建议并入 api §7.2 spill janitor) |

**Milestones 1 (memory+manual-note RAG), 2 (files/MinIO), 3 (sandbox), 4 (QQ/IM), 5 (agentic email) all DONE + browser-verified**, on top of v1 + v1 wrap-up + UI/UX backlog. A full source-backed document Knowledge product is separately designed but not implemented. **Milestone 5**: ADR-027 + the roadmap unify-note — `send_email` (was a stub) + notification digests now share the single `build_email_sender()` seam; `email_kind='agentmail'` sends for real via `AgentMailClient`. Inbound agentic email (`POST /channels/email/webhook`, Svix-verified + owner-email allowlist) reuses the SAME generic channel path as QQ (session `channel='email'` → loop); email-side approval reuses the v1 base. Multi-channel Messaging UI (QQ + email sections). **Real send verified with the owner's AgentMail key** (email landed in inbox). Deferred (manual, needs a public webhook URL): real AgentMail→webhook inbound; open-sender SAFE-tier (ADR-013).

Milestone 4: **ADR-028 — migrated QQ from the OneBot bridge to Tencent's OFFICIAL platform** (api-v2, `qq-botpy` WebSocket; OneBot transport removed). Config is runtime/DB-backed on the revived **Connectors page** (`/integrations`): manual AppID/Secret **or** the official QR one-click bind (endpoint verified OPEN to third parties — the live `q.qq.com` create/poll works from our pure-Python port; the scan returns `user_openid` = owner). AppSecret sealed in the AEAD vault (`channel_configs`); the botpy WS client runs as a leader-gated reconnecting worker task; replies are passive C2C replies keyed by the stored inbound `msg_id`; approvals reuse the v1 base (`channel='qq'`). **Deferred / manual acceptance:** completing a real QR scan + real inbound over the WS needs a live bot account (owner); IP whitelist on NAT; group messages; scheduled firings → QQ (frozen schedules CHECK).

**▶ All requested post-v1 milestones (memory → files → sandbox → QQ/IM → agentic email) are complete.** Next candidates (roadmap `09-roadmap.md`): #6 general cron, #7 GitHub connector, #8 multi-provider failover + sub-agents, #9 plugins/MCP, #10 multi-user/teams, #11 eval flywheel.

## How to update
On finishing a task: set its row ✅, move "Next ready", note anything a future agent must know (schema changes, new commands, gotchas), bump "Last updated", and commit (the STATUS bump can ride with the task commit).
