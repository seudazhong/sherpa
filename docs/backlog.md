# BACKLOG — findings from manual testing

> Raw, unscheduled work items. Each entry is a **reported observation plus the evidence found while triaging it** — none of them are fixed, and none have been promoted into [`IMPLEMENTATION.md`](IMPLEMENTATION.md) yet. Promote an item (give it a task id + phase) before writing code; anything that changes behaviour covered by a frozen contract needs the contract/ADR update first (per [`../AGENTS.md`](../AGENTS.md) §1).
>
> Status values: `open` (not started) · `promoted` (now a task in `IMPLEMENTATION.md`) · `done` · `wontfix`.

## Index

| # | Kind | Item | Status |
| --- | --- | --- | --- |
| B-1 | bug | [Chat header shows a stale hard-coded model](#b-1-chat-header-shows-a-stale-hard-coded-model) | ✅ done |
| B-2 | design | [Built-in tool surface is too large (53 tools)](#b-2-built-in-tool-surface-is-too-large-53-tools) | open |
| B-3 | bug | [The model cannot see the chat's bound project](#b-3-the-model-cannot-see-the-chats-bound-project) | ✅ done |
| B-4 | bug/dx | [OTel tracing silently off after a stack restart](#b-4-otel-tracing-silently-off-after-a-stack-restart) | ✅ done |
| B-5 | gap | [Drive cannot upload a folder](#b-5-drive-cannot-upload-a-folder) | ✅ done |
| B-6 | feature | [Chat attachments: image upload/paste + attach from Drive](#b-6-chat-attachments-image-uploadpaste--attach-from-drive) | ✅ done |
| B-6 | feature | [Chat attachments: image upload/paste + attach from Drive](#b-6-chat-attachments-image-uploadpaste--attach-from-drive) | open |
| B-7 | ux | [`Inbox` nav label collides with the email inbox](#b-7-inbox-nav-label-collides-with-the-email-inbox) | ✅ done |
| B-8 | bug | [`project_run` always fails with `sandbox_unavailable`](#b-8-project_run-always-fails-with-sandbox_unavailable) | open |
| B-9 | bug/dx | [The test suite deletes the owner tenant in the dev database](#b-9-the-test-suite-deletes-the-owner-tenant-in-the-dev-database) | ✅ done |

Suggested order: ~~**B-4 → B-1 → B-7 → B-3**~~ (done 2026-07-28) → ~~**B-5, B-6**~~ (done 2026-07-29) → ~~**B-9**~~ (done 2026-07-29, [ADR-044](decisions.md)) → **B-2** (largest design question, own ADR) → **B-8** (sequence after B-2, which may remove the tool).

---

## B-1 Chat header shows a stale hard-coded model

*Reported 2026-07-28 (manual test) · kind: bug · status: ✅ done 2026-07-28*

**Observed.** The Chat page header always rendered `claude-sonnet-4.6` next to the `Web chat` chip, while the session actually runs a different model (the session model switcher showed `Local litellm · gpt-5.5`).

**Evidence.** `frontend/src/views/ChatView.tsx:695-703` rendered `meta.model` from `GET /meta`; `backend/app/main.py:89-98` returns `settings.provider_model`, i.e. the **process-level env default** (`PROVIDER_MODEL`, default `claude-sonnet-4.6`). It reflected neither the DB-configured model source (ADR-041) nor the per-session override (`sessions.model_provider_id` / `sessions.model`).

**Fixed by** making the *effective* model a first-class read instead of a client guess:
`GET`/`POST /sessions/{id}/model` now answer `SessionModelState` (selection + `effective_source` ∈ `session|default|env` + provider id/name/kind + model), resolved with the run's own precedence in
`services/model_providers.get_session_model_state()`. The chat header no longer uses `/meta` when a session
exists — `ModelSwitcher` owns the label and hides it when the select already spells the pair out. `/meta`
keeps reporting the process env provider (it is the honest answer to "what is this server's default"), and
the contract now says so (`docs/contracts/api.md` §10.8).

**Verified.** Backend gate green (full pytest, ruff, `mypy app`); new service + REST tests cover
session-override / global-default / env fallback / disabled-override. Live in the browser: with no source
configured → `Server default · claude-sonnet-4.6` ("no source configured"); after adding the litellm source
(29 models, default `gpt-5.5`) → `Local litellm · gpt-5.5` ("your default source"); with a per-chat override
→ `Local litellm · gpt-4o-mini` and the worker log for that message reads `provider=openai_compatible
model=gpt-4o-mini`. 390 px overflow = 0.

---

## B-2 Built-in tool surface is too large (53 tools)

*Reported 2026-07-28 (manual test) · kind: design · status: open*

**Observed.** Asked to list its tools, the assistant enumerated **53** built-ins in one flat namespace. Every chat pays the full schema cost in the cached prefix, and the model has to disambiguate near-duplicates.

**Inventory** (`backend/app/tools/`): builtin 3 (`echo`, `get_time`, `send_email`) · candidate 4 · connector 2 · schedule 5 · todo 4 · insight 4 · memory 6 · knowledge 5 · **drive 8** · file 4 · project 6 · sandbox 1.

**Problems.**
- CRUD-per-entity explosion, with real overlap: `drive_*` vs `file_*`, `memory_user_get` / `memory_user_list` / `memory_search`, todo vs candidate.
- Naming is inconsistent: `todo_write` vs `update_todo` vs `complete_todo`; `memory_user_set` vs `memory_note`.
- No scoping: a plain chat is offered the project / drive / knowledge tools it can never use.
- Token + prefix-cache cost (docs/04 invariant ⑤), and scope drift versus ADR-022 v1 (many are post-v1 surfaces).

**Direction (undecided, needs an ADR).** Options to weigh: verb-parameterised namespaced tools (`drive{op}`, `memory{op}`, `todo{op}`); progressive disclosure via a tool-search meta-tool; context-scoped tool sets (a project-bound session gets project tools, a plain chat does not); merging `drive_*` and `file_*`. Must preserve the narrow-waist rule (built-ins / MCP / sub-agents present as one tool interface). Deliverables: ADR → `docs/11-agent-tool-surface.md` (incl. the §9 capability matrix) → contracts → code.

---

## B-3 The model cannot see the chat's bound project

*Reported 2026-07-28 (manual test) · kind: bug · status: ✅ done 2026-07-28*

**Observed.** In a project-bound chat (the UI chip showed `hello-world-py · project`), asking *"which project is this chat in?"* produced "I can't see an explicit binding" — the model only guessed from `list_projects`. **Confirmed** with tracing restored (B-4): the assembled prompt in the Phoenix trace carried **no project context at all**, so the model's answer was the honest consequence, not a formatting slip.

**Evidence.** The binding is real server-side: `sessions.project_id` (`backend/app/models/core.py:124`), set by `services/projects.py` `open_in_chat` and relied on by `services/project_workcopy.py:118-130`; the frontend renders it as a chip. But the assembled prompt never carried it — `backend/app/core/loop.py:474` built `system_content = SYSTEM_PROMPT [+ core_memory]` and nothing else.

**Fixed by** adding the missing **session-stable layer** that docs/04 already prescribed:
`app/core/session_context.py` renders a bounded ambient block — today's date (**day granularity**, so a
wall-clock stamp never churns the cacheable prefix; `get_time` still gives the exact time), a human surface
label (`Web chat`, never a raw UMO key), and the bound project's name + id with a note that project tools
default to it. The loop composes the system message as **global prefix → per-user core memory → per-session
ambient**, i.e. most-shared layer first, so the prefix stays reusable across a user's sessions.
`project_tree` / `project_read` now take `project_id` as **optional** and fall back to the binding; a
general chat that omits it gets an actionable observation instead of a schema error, and `list_projects`
marks which project the chat is on.

**Verified.** 7 new tests (`tests/test_session_context.py`) cover the bound/unbound blocks, the date-only
rule, the unknown-session case, tool defaulting, the unbound-chat observation, and an `execute_run`
assertion that the provider really receives the binding. Full backend gate green. Live agent lane: in a
bound chat, *"which project is this? don't use tools"* → **"当前 chat 绑定在项目 helloworld (id c0a48df2…)"**
with `tool_calls: 0` in the worker log, and *"read main.py"* → `project_read` succeeded first try with no
`list_projects` round-trip. Human lane: project chip unchanged, 390 px overflow = 0.

---

## B-4 OTel tracing silently off after a stack restart

*Reported 2026-07-28 (manual test) · kind: bug/dx · status: ✅ done 2026-07-28*

**Observed.** Phoenix (`localhost:6006`) showed the `default` project *last updated 2 days ago*, 12 traces — no new traces, so the assembled prompt for B-3 could not be inspected.

**Evidence (checked live).** `infra-phoenix-1` was up, but `infra-worker-1` / `infra-web-1` (rebuilt ~1 h earlier) ran with `OTEL_ENABLED=false` and an empty `OTEL_EXPORTER_OTLP_ENDPOINT`. `infra/docker-compose.yml:99-101,146-149` default these to `false`/empty, so tracing depended on shell env at `up` time and was **silently lost on every rebuild/restart** that did not export them. Neither the app nor Phoenix indicated that collection was off.

**Fixed by** making the state audible and the config durable:
`configure_tracing()` now logs exactly one startup line either way — `tracing disabled` (with how to enable
it) or `tracing enabled` with exporter kind / endpoint / sampler / content-capture — and `.env.example` +
the compose comments say these belong in the `--env-file`, not a shell export. Defaults stay off (ADR-033),
so the config contract is unchanged.

**Verified.** `pytest tests/test_otel.py` green (two new tests assert both startup lines). Live: web and
worker log `tracing enabled … exporter=otlp endpoint=http://phoenix:4317`, and a real chat produced a new
Phoenix trace (12 → 13, "last updated 1 minute ago") with `trace_id`/`span_id` on the worker's `llm call`
log line.

---

## B-5 Drive cannot upload a folder

*Reported 2026-07-28 (manual test) · kind: gap · status: ✅ done 2026-07-29*

**Observed.** Drive uploads exactly one file at a time; a folder cannot be uploaded.

**Evidence.** `frontend/src/views/WorkspaceView.tsx:248-257` rendered a bare `<input type="file">` with neither `multiple` nor `webkitdirectory`, and the handler took `files?.[0]` only. The contract offers just `POST /drive/files` (single multipart `path` + file) beside `POST /drive/folders` (`docs/contracts/api.md:1513-1523`). There was no directory drag-and-drop either.

**Fixed by** option (a) — **client-side bounded expansion** ([ADR-042](decisions.md)), so the server and the
contract are unchanged: `frontend/src/lib/driveUpload.ts` collects the selection (`multiple` /
`webkitdirectory`) or walks a dropped `DataTransferItem` entry tree, **rejects an over-budget batch before
sending anything** (≤ 200 files, ≤ 200 MiB), mirrors the folder tree with `POST /drive/folders` (409 ⇒
"already exists, reuse", resolved by listing the parent), then uploads each file at concurrency 3. A batch
is **not** a transaction and does not pretend to be: each file carries its own status with the server's own
reason (`413` too large, `409` name taken, `507` out of space), and a `507` **stops** the remaining queue
instead of repeating the same error per file. `WorkspaceView` gained *Upload folder* + multi-select *Upload
files*, a page drop zone, and a dismissable per-file result panel. Option (b) (archive upload) was
explicitly rejected in the ADR.

**Bug found *by* the human lane (and fixed).** The first live run failed every file with `422 invalid`: a
directory-picked upload sends its **relative path** as the multipart filename (`upload-demo/notes/a.md`),
and `services/drive._validate_name` rejects any name containing `/`. Fixed on both sides — the client now
sends the base name explicitly, and `POST /drive/files` reduces a client-supplied filename to its base name
(`PurePosixPath(...).name`, also handling `\`), since a client filename is untrusted input. Regression test:
`tests/test_drive_api.py::test_upload_filename_with_path_is_stored_as_base_name`.

**Verified.** Backend gate green (ruff/mypy/full pytest); frontend `lint` + `build` green. Human lane
(Playwright, real stack): uploading a nested `upload-demo/{readme.txt, notes/a.md, notes/deep/b.txt}` →
"Upload results · 3/3" with every row ✓, and the tree is rebuilt in Drive (`upload-demo` → `notes` →
`deep`). 390 px overflow = 0. Known-benign noise: a reused folder logs a handled `409` in the browser
console.

---

## B-6 Chat attachments: image upload/paste + attach from Drive

*Requested 2026-07-28 · kind: feature · status: ✅ done 2026-07-29*

**Ask.** In Chat, (a) upload or paste an image straight into the composer, and (b) attach an existing file from Drive.

**Prior state.** The whole path was text-only: the composer had no attach/paste handling, admission stored a single text part, and `backend/app/core/history.py:57-74,202` collapsed each user turn into a plain string `content` — nothing multimodal could reach the provider.

**Shipped as [ADR-043](decisions.md)** — attachments are **references to Drive nodes**, never a second byte
store:
1. **Storage.** A pasted/uploaded image is written to Drive under `Chat uploads/` *before* admission
   (`frontend/src/lib/chatAttachments.ts`), so it inherits quota (`507`), the per-file cap (`413`),
   versioning, trash and GC. Picking an existing Drive file skips the upload entirely.
2. **Message model.** Migration **0032** widens `ck_parts_kind` to `text|status|image|file_ref`; an
   attachment part stores `{drive_node_id, version, name, content_type, size_bytes}` — a reference, never
   bytes, so nothing large enters `parts`, the journal, or an event payload.
3. **Admission.** `PromptRequest.attachments` (≤ 8) is resolved in the same transaction as the text part
   (`app/core/attachments.py`): ownership via the Drive service's tenant+user scoping, not trashed, real
   file, pinned `version`, per-image cap. Idempotency now compares text **and** the attachment set.
4. **Assembly.** `assemble_provider_history` expands attachments **per run** under a shared byte budget
   (≤ 5 MiB per image, ≤ 15 MiB per assembly). A turn **without** attachments keeps the plain-string
   `content`, so existing sessions' cached prefixes stay byte-stable (docs/04 invariant ⑤).
5. **Honest degradation** instead of provider errors, in four cases: the source has
   `supports_vision = false` (new `model_providers` column + Settings→Models toggle), the byte budget is
   spent, the image is oversized, or the node was purged. Non-image text-like files are inlined as a
   **bounded** extract (≤ 32 KiB, truncation stated); binary files become a pointer that names `drive_read`.
6. **Providers.** `app/providers/content.py` parses the OpenAI-shape content array once; `anthropic` emits
   base64 `image` blocks, `gemini` emits `inlineData`, `openai_compatible` passes through, `mock` renders
   text.
7. **UI.** Composer *Attach* / *From Drive* picker / clipboard paste / removable chips with size, the
   no-vision warning, and transcript rendering (image thumbnail, file chip with download).

**Verified.** Backend gate green (ruff/mypy + 355 pytest incl. `test_chat_attachments*`); frontend `lint` +
`build` green. **Agent lane** (real model, litellm `claude-sonnet-4.6`): attached a generated PNG and asked
"这张图片里有哪些形状和文字？" → the model answered exactly *"SHERPA TEST" 文字、红色圆形、蓝色三角形、浅灰背景、黑色边框* — the
image genuinely reached the provider; a **follow-up run** ("三角形是什么颜色") answered *蓝色*, proving attachments
replay across runs. A `file_ref` picked from Drive was quoted verbatim (`nested b`). **Human lane**: paste
→ chip; *From Drive* search → pick; reload → the transcript re-renders the thumbnail + chip from
`GET /sessions/{id}/messages`; with `supports_vision` off the composer shows the honest warning; 390 px
overflow = 0.

**Deliberately not done** (ADR-043 §8): model-*produced* images, audio/video, attachment OCR/vectorisation
(that is Knowledge, ADR-036), attachment-level sharing, and non-Drive external links as attachments.

---

## B-7 `Inbox` nav label collides with the email inbox

*Reported 2026-07-28 (manual test) · kind: ux/naming · status: ✅ done 2026-07-28*

**Observed.** The sidebar item **Inbox** read as "my email inbox", but the page is the agent triage surface — its own subtitle said *"Review suggestions, approvals, and follow-through in one place"* (candidates extracted from Gmail per ADR-009/ADR-010, not raw mail).

**Why it was worse here.** Gmail is *the* v1 connector (ADR-022), and other views already show an **Inbox** chip meaning the mail folder (`frontend/src/views/ConnectorsView.tsx:332`, `views/MessagingView.tsx:185`) — one word, two meanings. Secondary IA problem: the subtitle claimed approvals live here while **Approvals** is a separate nav item.

**Fixed by** renaming the surface to **Today** (the eyebrow already said "Today"): nav label + route
`/inbox` → `/today` (free of any API proxy prefix), `InboxView.tsx` → `TodayView.tsx`, heading/aria updated,
and the subtitle rewritten to *"What needs you today — suggestions, follow-through, and updates"* — it no
longer claims approvals. `/inbox` now redirects to `/today` so old links keep working, the Approvals
section is explicitly a read-only roll-up with an **Open Approvals** link to the page that owns the
decision, and the `list_notifications` tool description says "shown on the Today page" instead of "the web
inbox". Capability matrix (docs/11 §9) and the design-bright README note the rename. The mail-source
"Inbox" chips stay — that is exactly the disambiguation.

**Verified.** Frontend lint + build green; backend ruff/mypy + tool tests green. Human lane: `/inbox`
redirects to `/today`, nav highlights **Today**, heading/subtitle correct, 390 px overflow = 0. Agent lane:
the model called `list_notifications` and answered "可以在界面的 Today 页面查看".

**Left open (deliberately).** Whether Approvals should merge into Today (the "Decisions" option) is an IA
change, not a rename — not done here.

---

## B-8 `project_run` always fails with `sandbox_unavailable`

*Reported 2026-07-28 (manual test) · kind: bug · status: open · sequence after B-2 (the redesign may remove/merge this tool)*

**Observed.** In a project-bound chat, "run the helloworld code" → the model calls `project_run({"command": "python main.py"})` and gets back
`Sandbox run sandbox_unavailable (exit -1, state persisted). No file changes were produced.`
It then fell back to *describing* what the code would print. The plain `run_code` tool works in the same stack (computed `1²+…+100²` = 338350, exit 0), so code execution as such is fine — only the **project** sandbox path fails.

**Root cause (reproduced from inside the worker).** The worker shares the host `docker.sock`, so the daemon resolves a sibling container's bind **source** on the *host*, where the worker-local scratch path does not exist:

```
APIError 400 ... invalid mount config for type "bind":
bind source path does not exist: /app/.sherpa/scratch/<run>
```

`app/sandbox/project_sandbox.py:252` therefore raises `DockerException` → `RunResult(error="sandbox_start_failed")`. Two follow-on problems make it opaque:
1. `services/project_sandbox.py:141-144` collapses **every** error into `termination_reason="sandbox_unavailable"` — a start failure, a missing daemon and an unknown error are indistinguishable — and nothing is logged, so the worker log has no trace of it (DB row: `state=persisted`, `termination_reason=sandbox_unavailable`, `exit_code=-1`, empty `scratch_ref`).
2. `docs/IMPLEMENTATION.md` (W3 exit note) describes this dev-stack limitation as "a `project_run` shell command sees an empty `/work`" — in reality it never starts. The doc needs correcting either way.

**Direction (undecided).**
- (a) Make the scratch path resolve identically on host and worker — bind a host directory at the *same absolute path* into the worker (or use a named volume shared with the sandbox container) so sibling mounts work.
- (b) Skip the bind entirely: `docker cp` the materialized tree into the container and copy the delta back.
- (c) The ADR-039 production posture (gVisor/microVM runner), which removes the shared-socket assumption.
- (d) Regardless of the above: keep `sandbox_start_failed` distinct from `sandbox_unavailable`, attach a redacted detail to the tool observation, and log one worker line — the model and the user should never have to guess which failure happened.

---

## B-9 The test suite deletes the owner tenant in the dev database

*Found 2026-07-28 while verifying B-3 · kind: bug/dx · status: ✅ done 2026-07-29 ([ADR-044](decisions.md))*

**Observed.** Running `uv run pytest` against the default dev configuration **destroyed the owner workspace**: after one full-suite run, `model_providers` = 0 (the configured litellm source gone), `projects` = 0 (the `helloworld` project gone), and the chat sessions were wiped. Verified directly in Postgres.

**Evidence.** API tests got their clean slate by deleting the *real* owner tenant:

```python
async def _drop_owner() -> None:            # tests/test_connections_api.py:28-32
    tid, _ = owner_ids()
    await s.execute(text("DELETE FROM tenants WHERE tenant_id = :t"), {"t": tid})
```

`owner_ids()` resolves the configured owner (the same identity the running stack logs in as), and the default `DATABASE_URL` is the same Postgres the dev stack uses — so the suite and the app shared one database. 20 test files used this pattern, and every tenant-scoped table cascades from `tenants` (`ondelete="CASCADE"`, ADR-015), which is what turned one `DELETE` into a wiped workspace.

**Second symptom.** With the dev worker running, that `DELETE` also **deadlocked** against the worker's cron (`project_workcopy_maintenance` holds locks on the same tenant's rows): `DeadlockDetectedError ... DELETE FROM tenants`, failing a *random* API test each run. The earlier "flaky" `test_gmail_oauth_round_trip_and_disconnect` failure was the same cause. Stopping the worker made the full suite green — a workaround, not a fix.

**Fixed (ADR-044).** The data plane is isolated at the process level rather than the 20 call sites being tidied:

- `tests/__init__.py` rewrites `DATABASE_URL` → `<app_db>_test`, `REDIS_URL` → logical db 15, and `OWNER_EMAIL` → a **synthetic** owner *before* `app.config` builds its `Settings` singleton (so `app.db`'s engine is born isolated).
- A session hook creates the database, runs `alembic upgrade head`, and stamps `_sherpa_test_marker`; that marker is the **only** accepted evidence that destructive writes are allowed. `SHERPA_TEST_DB_ADOPT=1` / `SHERPA_TEST_DB_RESET=1` are the explicit escape hatches; the database is retained between runs.
- All 20 cleanup sites now go through one guarded `drop_tenant()` with a `lock_timeout` and a single retry.
- Result: `uv run pytest` is green **with the dev worker running** (370 passed, twice), and the dev database's row counts are unchanged across runs. The "stop the worker first" workaround is retired.
