# BACKLOG — findings from manual testing

> Raw, unscheduled work items. Each entry is a **reported observation plus the evidence found while triaging it** — none of them are fixed, and none have been promoted into [`IMPLEMENTATION.md`](IMPLEMENTATION.md) yet. Promote an item (give it a task id + phase) before writing code; anything that changes behaviour covered by a frozen contract needs the contract/ADR update first (per [`../AGENTS.md`](../AGENTS.md) §1).
>
> Status values: `open` (not started) · `promoted` (now a task in `IMPLEMENTATION.md`) · `done` · `wontfix`.

## Index

| # | Kind | Item | Status |
| --- | --- | --- | --- |
| B-1 | bug | [Chat header shows a stale hard-coded model](#b-1-chat-header-shows-a-stale-hard-coded-model) | ✅ done |
| B-2 | design | [Built-in tool surface is too large (53 tools)](#b-2-built-in-tool-surface-is-too-large-53-tools) | open · 🚧 Phase TR **P0 + P1 shipped** (52 → 47 by deletion); catalog is P2 |
| B-3 | bug | [The model cannot see the chat's bound project](#b-3-the-model-cannot-see-the-chats-bound-project) | ✅ done |
| B-4 | bug/dx | [OTel tracing silently off after a stack restart](#b-4-otel-tracing-silently-off-after-a-stack-restart) | ✅ done |
| B-5 | gap | [Drive cannot upload a folder](#b-5-drive-cannot-upload-a-folder) | ✅ done |
| B-6 | feature | [Chat attachments: image upload/paste + attach from Drive](#b-6-chat-attachments-image-uploadpaste--attach-from-drive) | ✅ done |
| B-7 | ux | [`Inbox` nav label collides with the email inbox](#b-7-inbox-nav-label-collides-with-the-email-inbox) | ✅ done |
| B-8 | bug | [`project_run` always fails with `sandbox_unavailable`](#b-8-project_run-always-fails-with-sandbox_unavailable) | **open** · ✅ symptom fixed/P3 accepted; 🚧 **P4 authorized 2026-08-03 and in progress** with temporary flat-registry registration; still closes only after P5 human Run/Stop |
| B-9 | bug/dx | [The test suite deletes the owner tenant in the dev database](#b-9-the-test-suite-deletes-the-owner-tenant-in-the-dev-database) | ✅ done |
| B-10 | design | [Tool-surface slimming: dead tools, prose diet, and *vertical* (workflow) consolidation](#b-10-tool-surface-slimming-dead-tools-prose-diet-and-vertical-workflow-consolidation) | open — feeds [Phase TR](IMPLEMENTATION.md) **P2** |
| B-11 | gap | [No tool-use evaluation harness (decisions are argued, not measured)](#b-11-no-tool-use-evaluation-harness-decisions-are-argued-not-measured) | open |
| B-12 | bug | [The Drive orphan GC deletes change-set diff spills](#b-12-the-drive-orphan-gc-deletes-change-set-diff-spills) | ✅ fixed 2026-07-31 (retention question still open) |

Suggested order: ~~**B-4 → B-1 → B-7 → B-3**~~ (done 2026-07-28) → ~~**B-5, B-6**~~ (done 2026-07-29) → ~~**B-9**~~ (done 2026-07-29, [ADR-044](decisions.md)) → **B-2 + B-8 together** — triaged 2026-07-30 and found to be **one architecture problem, not two** (see both entries below). The owner approved the unified **clean-break** architecture ([ADR-045](decisions.md#adr-045) umbrella · [ADR-046](decisions.md#adr-046) tool catalog · [ADR-047](decisions.md#adr-047) tar transport · [ADR-048](decisions.md#adr-048) RuntimeSession); the execution plan is [`IMPLEMENTATION.md` Phase TR](IMPLEMENTATION.md). **Neither item is fixed**: B-2 closes at the end of Phase TR **P2**, B-8 at the end of **P5**. The owner approved the Phase TR execution plan on 2026-07-30. **Status as of 2026-08-01: P0, P1 and the P2 partials (P2.0a + P2.2) are shipped; ✅ P3 is COMPLETE and owner-accepted (including its 128 MiB workspace cap); P2's catalog is deferred by owner decision; P4 + P5 have not started.** P1 removed the duplicate `file_*` stack and `run_code` (**52 → 47 tools / 19,848 → 18,397 B — deletion, not the catalog**) and moved the sandbox bookkeeping onto `project_runtime_sessions`/`project_exec_runs` without changing any execution path; **P3 then replaced the bind mount with tar transport, so `project_run` really runs**. Neither backlog item is closed: B-2 still waits on the P2 catalog, B-8 on the P5 human Run/Stop lane.

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

*Reported 2026-07-28 (manual test) · kind: design · status: **open** — architecture approved 2026-07-30 ([ADR-045](decisions.md#adr-045)/[ADR-046](decisions.md#adr-046)); Phase TR **P1 deleted the duplicate `file_*` stack and `run_code` (52 → 47 tools / 19,848 → 18,397 B)**, which is deletion, not the fix; closes at the end of [Phase TR](IMPLEMENTATION.md) **P2***

**Observed.** Asked to list its tools, the assistant enumerated **53** built-ins in one flat namespace. Every chat pays the full schema cost in the cached prefix, and the model has to disambiguate near-duplicates.

**Measured 2026-07-30 (correcting the report).** The registry actually holds **52** tools, not 53:

```
build_default_registry().schemas("full")  →  tools: 52   json_bytes: 19848   ≈ 4,962 tokens
largest: project_run 1142 B · create_scheduled_task 889 B · project_tree 873 B
```

That whole 19,848-byte array is rebuilt at `backend/app/core/loop.py:527` **inside** the turn loop and sent on **every** provider call.

**Inventory** (`backend/app/tools/`): builtin 3 (`echo`, `get_time`, `send_email`) · candidate 4 · connector 2 · schedule 5 · todo 4 · insight 4 · memory 6 · knowledge 5 · **drive 8** · file 4 · project 6 · sandbox 1.

**Problems.**
- CRUD-per-entity explosion, with real overlap: `drive_*` vs `file_*`, `memory_user_get` / `memory_user_list` / `memory_search`, todo vs candidate.
- Naming is inconsistent: `todo_write` vs `update_todo` vs `complete_todo`; `memory_user_set` vs `memory_note`.
- No scoping: a plain chat is offered the project / drive / knowledge tools it can never use.
- Token + prefix-cache cost (docs/04 invariant ⑤), and scope drift versus ADR-022 v1 (many are post-v1 surfaces).
- **Added during triage:** the VISIBLE gate (api.md §7.1 step 2) is **not actually implemented** — `backend/app/tools/registry.py` only has a SAFE/FULL binary and `tier` is hard-coded to `FULL` at `core/loop.py:412/437/449`, so "no scoping" is structural, not an oversight. Also `file_*` is backed by a **legacy `files` table** whose UI was replaced by Drive, yet `files_router` is still registered in `app/main.py`.

**Direction (~~undecided, needs an ADR~~ ✅ decided 2026-07-30).** Options weighed: verb-parameterised namespaced tools (`drive{op}`, `memory{op}`, `todo{op}`); progressive disclosure via a tool-search meta-tool; context-scoped tool sets; merging `drive_*` and `file_*`. **Resolved in [ADR-046](decisions.md#adr-046)**: unified `domain.verb` naming + a `ToolDescriptor` beside the **unchanged** `Tool` protocol + a `ToolsetResolver` that finally implements the VISIBLE gate + `tools_search`/`tools_load` progressive disclosure + an args-aware policy engine. **Verb mega-tools were rejected** — collapsing eight precise schemas into one `oneOf` weakens argument validation, destroys per-tool effect classification, and coarsens the approval scope. `file_*` and the whole legacy `files` stack are **deleted** (Drive is the only personal byte store), as is `run_code`. The narrow waist is untouched: built-ins / MCP / sub-agents / runtime providers still present one `Tool` interface through the same four gates. **Sequencing note from triage: this and B-8 are one problem** — fixing B-8 alone would grow the flat surface from 52 to ~66 tools. Deliverables landed: ADR-045/046/047/048 → `docs/11-agent-tool-surface.md` (incl. the §9 capability matrix) → contracts (`api.md` §7.0/§7.3/§7.5/§7.6, `events` §2.2, `config` §1.10) → [`IMPLEMENTATION.md` Phase TR](IMPLEMENTATION.md). **Code not started** — Phase TR needs its own owner approval.

**Close criterion (Phase TR P2).** General-chat tool JSON **≤ 6,144 bytes** (from the measured 19,848), `core` is a byte-true prefix of the project-bound array, discovery works end-to-end in the agent lane, and `CONNECTOR_ANALYSIS` still receives zero tools.

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

*Reported 2026-07-28 (manual test) · kind: bug · status: **open** — architecture approved 2026-07-30 ([ADR-045](decisions.md#adr-045)/[ADR-047](decisions.md#adr-047)/[ADR-048](decisions.md#adr-048)); **P0 (named exits + logging) + P1 (baseline squash) shipped 2026-07-30; P3 (tar transport + first-party runner image + real-Docker lane) shipped 2026-07-31 — the original symptom is GONE and `project_run` really runs**; still **open** because the close criterion is the *human* Run lane, which is [Phase TR](IMPLEMENTATION.md) **P5***

> **P4 started 2026-08-03.** The owner resolved the deferred-P2 sequencing question:
> `fs_*` / `runtime_*` / `sh_exec` temporarily register in the existing FULL flat registry and
> self-enforce binding/ownership; P2 later wraps them in descriptors. Runtime liveness recovery,
> DB-live container sweep protection, host-edit/runtime serialization and committed exec dispatch
> were added to the contract before code.

> **✅ Phase TR P3 shipped 2026-07-31 — the reported symptom is fixed, the item is not closed.**
> The bind mount is gone; the disposable copy is tar-injected into an anonymous `/work` volume,
> so **no host path reaches the daemon at all**. Measured on the Windows + Docker Desktop dev
> stack, three independent ways: `uv run pytest -m docker` (17 passed), a run driven *from
> inside the worker container* over the mounted socket, and the chat lane against the real
> provider — `pytest -q` → **real exit 1** → model fixes `calc.py` → **real exit 0**;
> `ruff --version` → 0.6.9; `git --version` → **127** (mapped to
> `environment_missing_dependencies`). The human lane was exercised on the existing Change
> Review panel (real diff → Save selected → head advanced to `head_generation=1` with
> `calc.py`/`test_calc.py` in the snapshot).
>
> **Owner decision (2026-08-01): Phase TR P3 is accepted as COMPLETE**, including its 128 MiB
> workspace/change-set cap as the intentional trade-off for the 1 GiB worker budget. **B-8 itself
> is NOT closed by that acceptance** — P3 was always only the mechanical half.
>
> **Why this stays open:** the close criterion below has two halves and P3 delivered one. There
> is still **no Run control, no streaming log and no Stop** — `createSandboxRun` is *still* dead
> code and secondary problem 2 below is *still* true, because moving execution to the worker
> behind `202` + SSE is **P4** and the three-column UI is **P5**. Claiming B-8 closed now would
> be exactly the kind of overclaim the ADR-045 root-cause note was written about.

**Observed.** In a project-bound chat, "run the helloworld code" → the model calls `project_run({"command": "python main.py"})` and gets back
`Sandbox run sandbox_unavailable (exit -1, state persisted). No file changes were produced.`
It then fell back to *describing* what the code would print. The plain `run_code` tool works in the same stack (computed `1²+…+100²` = 338350, exit 0), so code execution as such is fine — only the **project** sandbox path fails. *(Since P0 the same failure reports `Sandbox run runtime_start_failed …` plus a named, redacted explanation and a worker log line — the run still fails, it is just no longer opaque.)*

**Root cause (reproduced from inside the worker).** The worker shares the host `docker.sock`, so the daemon resolves a sibling container's bind **source** on the *host*, where the worker-local scratch path does not exist:

```
APIError 400 ... invalid mount config for type "bind":
bind source path does not exist: /app/.sherpa/scratch/<run>
```

`app/sandbox/project_sandbox.py:252` therefore raises `DockerException` → `RunResult(error="sandbox_start_failed")`. Two follow-on problems make it opaque:
1. ~~`services/project_sandbox.py:141-144` collapses **every** error into `termination_reason="sandbox_unavailable"` — a start failure, a missing daemon and an unknown error are indistinguishable — and nothing is logged, so the worker log has no trace of it~~ **✅ fixed in Phase TR P0 (2026-07-30).** Each failure keeps its own contract name (`runtime_daemon_unreachable` / `runtime_image_missing` / `runtime_start_failed` / `runtime_transport_failed` / `sandbox_disabled` / `error:<class>`), `run_sandbox` emits exactly one structured worker log line carrying the bounded raw detail, and the model gets one redacted, reason-specific observation. The same pass also closed a **redaction leak on the neighbouring `run_code` path**, which returned `f"sandbox error: {result.error}"` — i.e. the raw docker exception string, host paths included — straight to the model. **The mount itself is still broken — this made the failure legible, not fixed.**
2. `docs/IMPLEMENTATION.md` (W3 exit note) describes this dev-stack limitation as "a `project_run` shell command sees an empty `/work`" — in reality it never starts. ✅ corrected 2026-07-30 in the design batch.

**Direction (~~undecided~~ ✅ decided 2026-07-30).** Options weighed:
- (a) Make the scratch path resolve identically on host and worker — bind a host directory at the *same absolute path* into the worker (or use a named volume shared with the sandbox container) so sibling mounts work.
- (b) Skip the bind entirely: move the materialized tree in and out of the container without any host path.
- (c) The ADR-039 production posture (gVisor/microVM runner), which removes the shared-socket assumption.
- (d) Regardless of the above: keep `sandbox_start_failed` distinct from `sandbox_unavailable`, attach a redacted detail to the tool observation, and log one worker line. **✅ shipped in Phase TR P0, 2026-07-30.**

**Resolved: (b) + (d) now, (c) stays roadmap.** [ADR-047](decisions.md#adr-047) replaces the bind mount with **tar ingress/egress** (`put_archive`/`get_archive` into an anonymous `/work` volume), so **no host path is ever passed to the Docker daemon** — the entire class of failure disappears rather than being configured around, and Windows/Linux/DinD/CI all behave identically. This is a **narrowing** of the ADR-025/ADR-039 mount wording, not a relaxation: every hardening control is retained, and the ADR-039 do-not-ship conditions for multi-user are unchanged. (a) was rejected because it needs a host absolute path in `.env` with a different correct value per topology — exactly the fragility that let this bug ship. (d) was Phase TR **P0** and **landed first, 2026-07-30**.

**Three secondary problems found during triage (not in the original report):**
1. **The human Run lane never existed.** `frontend/src/api.ts:1293` defines `createSandboxRun`, but it has **no call site anywhere in the frontend**. The capability matrix (`docs/11` §9) claimed UI ✅ — corrected.
2. **Even with the mount fixed, the REST lane would still fail, for a different reason.** `app/api/projects.py::create_sandbox_run` executes `sbx_svc.run_sandbox(...)` **synchronously inside the web process**, but `SANDBOX_KIND=docker` is set **only on the worker** (`infra/docker-compose.yml:163`) and web has no Docker socket — so it defaults to `disabled`. It also blocks the HTTP request for up to 120 s, while the contract describes it as `202`. *(P0 makes this legible — the route now reports `sandbox_disabled` rather than `sandbox_unavailable` — but does not fix it; P4/P5 do.)*
3. **The test suite is structurally blind to this.** `tests/test_project_sandbox.py` monkeypatches `_execute_in_scratch` and (until Phase TR P1 deleted it with `run_code`) `tests/test_sandbox.py` patched `_execute`; **no test in the repository ever starts a container**. That is why 297 green tests plus a two-lane Playwright pass did not catch it. P0 added a fake-docker-client lane that at least exercises the real classification branches of `_run_docker`; **✅ Phase TR P3 (2026-07-31) added the real-Docker lane** — `uv run pytest -m docker`, 17 tests, deselected by default and skipped with an actionable message when the daemon or image is missing. **It found a real bug on its very first run**: `TarTransport.build` emitted only the directories it was handed, so a file at `src/app.py` got an implicit parent created by docker as **root**, and the non-root runner could not write inside it. No fake-client test could ever have caught that — the fake has no filesystem and no uid.

**Close criterion (Phase TR P5).** A real command executes in a real container on the Windows dev stack and returns a real exit code and stdout ✅ **(P3, 2026-07-31)**; every failure injection maps to exactly one named `termination_reason` with a worker log line — ✅ for 13 of 16 rows, with `cancelled` (P4), `output_limit` + spill (P2.8) and `pids_limit` (no daemon signal; needs a P4 decision) recorded as open in [`IMPLEMENTATION.md` TR.11](IMPLEMENTATION.md) rather than assumed; the credential canary passes ✅ **(P3, verified in unit tests, end to end through the orchestration boundary with the secret in the base snapshot, and inside a real container)**; and the human lane can press **Run**, watch streaming output, press **Stop**, and review the resulting change set — ⬜ **not started (P4 + P5)**.

Also settled: `project_run` / `project_tree` / `project_read` / `run_code` are **deleted** (clean break, no shim) in favour of host-side `fs.*` plus an explicit `RuntimeSession` (`runtime_open` → `sh_exec` → `runtime_close`), so that **a sandbox outage costs the ability to run code, not the ability to edit it** — today it costs both. The `project_sandbox_runs` table is redesigned into `project_runtime_sessions` + `project_exec_runs`; `warm_until` is dropped because warm containers were never implemented in any code path. *(P3 note: the degradation guarantee is already half-true — with `SANDBOX_KIND=disabled` the in-memory copy, the host-side edits and the delta all still work and still persist. What is still missing is the `fs_*` tool surface that would let the model use it, which is P4.)*

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

---

## B-10 Tool-surface slimming: dead tools, prose diet, and *vertical* (workflow) consolidation

*Raised 2026-07-30 by the owner during a Phase TR P2 design review · kind: design · status: **open** — feeds [Phase TR](IMPLEMENTATION.md) **P2***

**Owner's challenge.** Reviewing the P2 plan, the owner objected that *`tools_search`/`tools_load` is the
classic answer to an oversized toolset, but we have not yet done the cheap compression that should come
first*, and separately that (a) the naming is inconsistent and (b) same-area simple tools might merge into
`domain(action, ...)`, borrowing from how local CLI agents avoid large tool definitions. Both challenges
held up; the investigation below changed the plan.

### Measured baseline (2026-07-30, after Phase TR P1)

```
build_default_registry().schemas("full") -> 47 tools
  17,432 B (compact separators)  /  18,303 B (default separators)
  descriptions 6,625 B (38%) | input_schemas 8,051 B (46%) | names+framing 2,756 B (16%)
```

> **Baseline-number correction.** ADR-046 and STATUS quote **19,848 B**; that is the **pre-P1, 52-tool**
> figure. Any "-70%" claim must be recomputed against 17,432 B (compact) or 18,303 B (default separators),
> and the two conventions must not be mixed. The measured general-chat core is only **3,627 B** today
> (10 tools), i.e. `TOOL_CATALOG_CORE_MAX_BYTES = 6144` is a **ratchet with 2,517 B of headroom**, not a
> stretch goal — the win comes from *not sending the other 37 tools*, not from squeezing the core.

### Finding 1 — deletion candidates that were never audited

| B | Tool | Verdict |
|---|---|---|
| 173 | `echo` | **delete** — dev leftover, zero product value, and it is **SAFE-tier** (visible even to untrusted-content sessions) |
| 239 | `drive_restore` | **delete — structurally uncallable (real bug)**, see below |
| 383 | `accept_candidate` | **merge into `edit_candidate`** — `edit` is documented as "edit fields *then accept*", so `accept` is `edit` with no fields: same effect class, same approval scope |
| 327 | `complete_todo` | **delete** — exactly `update_todo(status="completed")` (already in ADR-046 §8) |
| 283 | `memory_user_get` | **merge into `memory_recall`** (already in ADR-046 §8) |
| 255 | `drive_make_folder` | **owner decision** — `drive_write` documents "folders auto-created", leaving only "create an *empty* folder" |
| 176 | `list_notifications` | **owner decision** — its own description says "shown on the Today page", i.e. built for the human |
| 312 | `reindex_knowledge_source` | **owner decision** — maintenance action or agent capability? |
| 359 | `create_daily_digest` | **owner decision** — is it just `create_scheduled_task` with a fixed prompt? |
| 2,543 | `project_tree` · `project_read` · `project_run` | already scheduled for deletion in Phase TR **P4** |

Confirmed + pending + P4 = **47 -> 35 tools / -5,050 B**, before any prose work.

**`drive_restore` is dead surface (bug).** Its schema requires `node_id`, but **no tool ever emits a node
id**: `drive_list` prints `name/type/size/version` (`app/tools/drive_tools.py:105-110`) and `drive_search`
prints `path` only (`:129-133`). The agent can never obtain a legal argument, so the tool can only be
called with a hallucinated id — 239 B of pure liability plus a hallucination trap.

### Finding 2 — descriptions are 38% of the surface and unbudgeted

Worst offenders: `project_run` **641 chars**, `project_tree` 507, `search_knowledge` 398,
`create_scheduled_task` 276, `memory_note` 243. Trimming every description to <=80 chars alone is
17,432 -> 14,308 B (**-18%**) with no architectural change. `project_run`'s 641 chars are prose explaining
a broken abstraction, which is independent evidence for the P4 rewrite.

Also note `create_scheduled_task` encodes "**specify exactly one cadence**" in English prose — a `oneOf`
that is not, and cannot be, enforced (see Finding 4).

**Action:** enforce a per-tool description byte cap (proposal: <=160 B) at startup, next to the name regex,
so prose cannot silently refill — the same ratchet idea as `TOOL_CATALOG_CORE_MAX_BYTES`.

### Finding 3 — naming is inconsistent *within a single domain*

Measured over the 47: **28 `action_domain` · 15 `domain_action` · 4 neither**. The damaging part is not the
global split but the intra-domain mixing, which denies the model even local pattern-matching:

| Domain | Evidence |
|---|---|
| todo | `todo_write` vs `list_todos` / `update_todo` / `complete_todo` |
| project | `project_read`/`project_run`/`project_tree` vs `list_projects` / `create_project` |
| knowledge | `search_knowledge` vs `add_knowledge_source` / `list_knowledge_sources` |
| memory | `memory_user_set` vs `memory_note` (the `_user_` infix is itself inconsistent) |
| drive | all `domain_action` — the **only** internally consistent group |

ADR-046 §决策1 (`domain.verb`) already fixes this, and Anthropic's own examples use domain-prefixed names
(`asana_search`, `jira_search`). **But** the same source warns that prefix- vs suffix-based namespacing has
"non-trivial effects ... vary by LLM", so `domain.verb` vs `verb_domain` is an **empirical** question
(see B-11), not a taste one.

### Finding 4 — horizontal `domain(action, ...)` merging: rejected, but for corrected reasons

ADR-046 §决策5 rejected verb mega-tools on three grounds. Re-checked against the code:

| ADR ground | Re-check |
|---|---|
| ① weakens argument validation | **Holds — and is worse than stated** |
| ② breaks per-tool `effect_class` | **Contradicted by ADR-046 §决策6 itself** |
| ③ coarsens approval scope (`tool:drive`) | **Same contradiction** |

②/③ assume the policy engine cannot see arguments — but §决策6 of the *same ADR* upgrades it to args-aware
`evaluate(ctx, descriptor, args, scope)`. Once policy sees args, `drive(action="trash")` classifies exactly
as precisely as `drive_trash`. **The ADR rejects mega-tools partly on a ground its own next decision
removes**; the rejection must stand on ① plus model accuracy alone. (ADR-046 amended accordingly.)

① is real and understated. `app/tools/validate.py` is self-described as *"Not a full JSON-Schema engine ...
checks required keys are present and primitive types match"* — it honours neither `enum` nor `oneOf` nor
conditional `required`. So the schemas would not "collapse into a `oneOf`"; the validation would simply
**disappear**. Concretely, `update_todo` declares `"required": ["todo_id", "if_version"]`
(`app/tools/todo_tools.py:112`); merging create+update makes `if_version` un-requirable, **deleting the
optimistic-concurrency guard at the schema level**. Fixable by adopting a real validator, but that is a
dependency decision, and it does not address the second cost: models are empirically weak at discriminated
unions.

### Finding 5 — the merge axis was wrong (the actual correction)

An earlier draft of this review proposed merging the 9 near-empty `list_*` tools into one
`sherpa.list(kind, ...)`. **Withdrawn.** Anthropic's
[Writing effective tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents)
consolidates on a different axis entirely:

> - Instead of implementing a `list_users`, `list_events`, and `create_event` tools, consider implementing a
>   **`schedule_event`** tool which finds availability and schedules an event.
> - Instead of implementing a `read_logs` tool, consider implementing a **`search_logs`** tool ...
> - Instead of implementing `get_customer_by_id`, `list_transactions`, and `list_notes` tools, implement a
>   **`get_customer_context`** tool which compiles all of a customer's recent & relevant information at once.
>
> By selectively implementing tools whose names reflect **natural subdivisions of tasks**, you
> simultaneously reduce the number of tools **and offload agentic computation from the agent's context back
> into the tool calls themselves**.

| Axis | Example | Reduces |
|---|---|---|
| **Horizontal** (group by CRUD verb) — `sherpa.list(kind)`, `todo(action)` | withdrawn proposal; the Hermes-style pattern | tool count / bytes only; **offloads zero agentic work** |
| **Vertical** (group by workflow) — Anthropic's | `get_customer_context` | tool count **+ round trips + intermediate-output context** |

A `list(kind)` dispatcher is not a tool, it is a router: it saves bytes while adding a "which `kind`?"
decision. That is why it is rare in the wild. The same source goes further and questions whether `list_*`
tools should exist at all — *"you might choose to implement a `search_contacts` ... **instead of** a
`list_contacts` tool"*.

**Corroboration from shipped products.** GitHub MCP Server (`list_available_toolsets` / `get_toolset_tools`
/ `enable_toolset`) and GitHub Copilot CLI (31 deferred MCP tools behind a tool-search tool) both chose
**progressive disclosure while keeping tools separate** — Copilot CLI still exposes `browser_click`,
`browser_navigate`, `browser_type` etc. as 24 distinct tools rather than one `browser(action)`. No
first-party precedent was found for horizontal merging.

### Vertical consolidation candidates (the thing to record)

Not scheduled; each needs B-11 evidence before implementation.

| # | Candidate | Chain it replaces | Saves |
|---|---|---|---|
| V-1 | `todo_create(..., remind_at?)` | `todo_write` -> read back id -> `create_reminder(todo_id, ...)` | 1 round trip + `create_reminder`'s main use case. This *is* the `schedule_event` example |
| V-2 | `inbox_accept(candidate_id, if_version, patch?, remind_at?)` | `accept`/`edit` choice -> todo -> reminder | 2 tools + 1 round trip |
| V-3 | `today()` | `list_todos` + `list_candidates` + `list_notifications` + `list_activity` fired together | This *is* the `get_customer_context` example; the daily-brief workflow almost always wants all four |
| V-4 | `knowledge_add(query_or_path)` | `drive_search` -> read back path -> `add_knowledge_source(path)` | 1 round trip |

Also borrowed from CLI-agent practice: **`run_test` / `run_lint` (planned in Phase TR P4) are pure sugar
over `sh_exec("pytest")` / `sh_exec("ruff check")`** — Claude Code ships no `run_test` tool. Recommend
dropping both from P4 (-2 tools). Conversely, CLI agents deliberately keep `Read`/`Write`/`Edit`/`Glob`/
`Grep` **separate** from `Bash` (structured diffs, no shell-quoting hazards, permission tiering, enforceable
path bounds) — all four reasons apply to Sherpa's change-set projection, so **`fs.*` staying separate is
endorsed, not contradicted, by CLI practice**.

### Why "just use a CLI" does not transfer wholesale

| What makes CLI agents cheap | Transfers to Sherpa's own-data domains? |
|---|---|
| One schema, unbounded verbs (`Bash(cmd)`) | ✅ — this is the horizontal merge, available but costly (Finding 4) |
| **Pretrained priors** (the model already knows `ls`/`git`/`pytest`) | ❌ — **the single largest source of the saving, and it does not transfer.** Nobody on the internet has written `sherpa todo list`; it needs exactly as much teaching as `todo_list` |
| In-band discovery (`--help`, `man`) | ✅ — this *is* `tools_search`/`tools_load` |

Hard blocker on an in-sandbox `sherpa` CLI for own data: ADR-019/ADR-047 forbid credentials in the sandbox
and the sandbox is network-disabled, so it cannot reach the database. A host-side broker would just be a
tool call with extra steps.

### Decided / open

- **✅ Shipped 2026-07-31 (Phase TR P2.0a)** — the five confirmed deletions above are live:
  **47 → 42 tools, 17,432 → 16,153 B compact.** `edit_candidate` folded into `accept_candidate`
  (optional patch); `memory_user_list` folded into `memory_user_get` (optional `key`);
  `complete_todo` gone **with its dead service alias**; `echo` and `drive_restore` gone. SAFE tier is
  now `{get_time}` alone. Regression guard: `tests/test_tools.py::test_deleted_tools_are_gone`.
  Contract `api.md` §7.3 updated. Gate: 386 passed, ruff + format + mypy clean.
- **Decided:** P2.0 runs before P2.1 (slim first, index second); keep `domain.verb`;
  **do not** horizontally merge; amend ADR-046 §决策5 (done) and its baseline numbers (done).
- **Open (P2.0b, not started):** the prose diet. Descriptions are **still 39%** of the surface
  (6,336 B of 16,153) — the deletions barely moved that ratio because the offenders survive.
  Needs `TOOL_DESCRIPTION_MAX_BYTES` enforced at startup, or it refills.
- **Open (P2.0c, owner):** the 4 deletion decisions (`drive_make_folder`, `list_notifications`,
  `reindex_knowledge_source`, `create_daily_digest`); whether memory keeps **two systems** (KV
  `set/get/delete` + archival `note/search` = 5 tools, forcing a "is this a fact or a note?"
  judgement on every write); whether to schedule V-1..V-4.

---

## B-11 No tool-use evaluation harness (decisions are argued, not measured)

*Raised 2026-07-30 alongside B-10 · kind: gap · status: **open***

**Observed.** Every tool-surface decision in B-10 — which tools to delete, `domain.verb` vs `verb_domain`,
whether to consolidate, whether progressive disclosure helps or hurts — is **empirically decidable**, and we
decided all of them by argument. Anthropic's guidance is built around running an evaluation; we have none.
The owner scoped this as a **second, parallel line** to the Phase TR toolset work.

**Existing position is better than expected.** Phoenix is already in the stack
(`infra/docker-compose.yml`, `--profile observability`, healthy on `:6006`), `.env` has
`OTEL_ENABLED=true` + `OTEL_CAPTURE_MESSAGE_CONTENT=true`, and `app/observability/genai.py` already writes
OpenInference attributes on every provider call:

- `llm.tools.{i}.tool.json_schema` — **every tool schema offered, per call**
- `llm.output_messages.0.message.tool_calls.{j}.tool_call.function.name` / `.arguments` — what was chosen
- `gen_ai.tool.name`, `agent.tool.success` on `execute_tool` spans; token counts on `chat` spans

So the measurement substrate for a baseline **already exists in production traces** — no new
instrumentation is needed to start.

### Staged plan

**E0 — mine existing traces (near-zero cost; do this FIRST).** Query Phoenix spans and compute: tool-JSON
bytes per call; **per-tool call frequency, including the never-called set**; tool error rate and invalid-
argument rate; tool calls and round trips per turn. This alone converts several B-10 judgement calls into
measurements — if `list_notifications` / `reindex_knowledge_source` / `drive_make_folder` are never called
across all history, deleting them stops being an opinion; and `drive_restore` should show up as *called and
failed* with a hallucinated id, which would be direct proof of the B-10 bug.

> **⚠ Three corrections from actually running E0 on 2026-07-30.** The mechanism works; the assumptions did
> not survive contact.
>
> **(a) There is no history to mine — Phase TR P1 destroyed it.** Phoenix persists into the *same* Postgres
> (`phoenix` schema, `PHOENIX_SQL_DATABASE_URL` in `infra/docker-compose.yml:231`) on the *same* `pgdata`
> volume, so P1's `docker compose down -v` wiped the trace corpus along with the app data. Measured
> immediately after: **18 spans total (6 `execute_tool`, 8 `chat`), all dated 2026-07-30.** So E0 is not
> "mine the past", it is **"the queries are proven; now generate a corpus"** — either by deliberate usage
> before E1, or by folding E0's metrics into E1/E2 as reporting. **Operational note: any future destructive
> reset nukes the eval history too** — if trace history becomes an asset, it needs to stop sharing the
> volume's fate.
>
> The query shape is proven and worth keeping:
> ```sql
> SELECT attributes->'gen_ai'->'tool'->>'name' AS tool, count(*) AS calls,
>        count(*) FILTER (WHERE (attributes->'agent'->'tool'->>'success')='false') AS failures
> FROM phoenix.spans WHERE name='execute_tool' GROUP BY 1 ORDER BY 2 DESC;
> ```
>
> **(b) `agent.tool.success` does NOT capture semantic failure — do not use it as the error metric.**
> Verified on the corpus: `project_run`, the tool that **always fails** (B-8), records `status_code=UNSET`
> and is *not* counted as a failure. That is correct behaviour, not a bug: errors from tools are
> **observations** fed back to the model, never exceptions (AGENTS.md §4), so the tool "succeeded" at
> returning a failure observation. **Consequence for E2: evaluators must score terminal DB state and
> observation content, not the success flag.** A harness built on `agent.tool.success` would have scored
> B-8 as passing.
>
> **(c) Tool *result* content is not on the tool span.** `capture_llm_io` runs only at the provider-call
> boundary, so `execute_tool` spans carry no `output.value` (confirmed empty). Results are recoverable
> indirectly from the **next** `chat` span's `llm.input_messages.{i}.message.content` where the role is
> `tool`. Either accept that indirection in the E0/E2 queries or extend capture to the tool span — an
> explicit decision for the B-11 ADR.

**E1 — dataset.** Build a Phoenix Dataset of ~30-50 tasks, curated from real spans plus hand-written edge
cases, covering the v1 workflows (inbox triage, todo+reminder, knowledge retrieval, Drive, scheduling,
project coding). Follow the source guidance: realistic, **multi-step**, each paired with a verifiable
outcome; avoid trivial single-call prompts; record expected tool chains as metadata **without
over-specifying** (multiple valid paths exist).

**E2 — experiment + evaluators.** Task = drive one real Sherpa run per example. Evaluators should be
**mostly CODE, not LLM-judge** — Sherpa's own-data domain has verifiable terminal DB state (was the to-do
created? are the fields right? was the reminder linked?), which is a structural advantage over generic agent
evals. Reserve LLM-as-judge for answer quality/hallucination. Also score the cheap mechanical metrics: tool
calls, round trips, tool errors, tool-JSON bytes, tokens. **Per correction (b) above, "tool errors" must be
derived from observation content or terminal state, never from `agent.tool.success`.** The self-hosted
Phoenix already carries the full harness schema (`datasets`, `dataset_examples`, `dataset_splits`,
`experiments`, `experiment_runs`, `code_evaluators`, `llm_evaluators`, `builtin_evaluators` in the `phoenix`
schema), so no new datastore is needed.

**E3 — A/B the tool surface.** This is where the two lines meet: each toolset design becomes one experiment
over the same dataset — V0 today's flat 47 · V1 slimmed · V2 + `domain.verb` · V3 + catalog/progressive
disclosure · V4 + vertical consolidation · V-alt `verb_domain` (to settle the naming question Anthropic
says is LLM-dependent). Compare task success, tool calls, tokens, error rate.

### Prerequisites and hazards

- **Isolation.** Evals execute *real* runs and write real data. They must not touch the dev workspace —
  reuse the ADR-044 harness (`<app_db>_test`, Redis db 15, synthetic owner) rather than inventing a second
  isolation mechanism.
- **Real model required.** The mock provider cannot evaluate tool *choice*, so evals must run against the
  litellm proxy: budget for cost and non-determinism (pin temperature, sample repeatedly, report variance).
  This is the opposite of the `pytest` rule (deterministic, mock-only) and must not be conflated with it.
- **Sequencing.** The two lines are *not* fully parallel at the start: **E0's queries should be run before
  the B-10 deletion decisions** — but see correction (a): the corpus was destroyed by P1, so "run E0" now
  means *generate usage, then measure*, not *query history*. If building a corpus is too slow to gate B-10,
  fall back to shipping the five **confirmed** deletions (which need no evidence: `echo` is a dev leftover
  and `drive_restore` is provably uncallable) and hold the four **owner-decision** ones for E0/E1 data.
  E1–E3 then run in parallel with Phase TR P2/P3.
- Needs an ADR before implementation (new tooling: Phoenix datasets/experiments as a dev-time dependency),
  per AGENTS.md §1/§2.

---

## B-12 The Drive orphan GC deletes change-set diff spills

*Raised 2026-07-31 during Phase TR **P3** human-lane verification · kind: bug · status: **fixed** (retention question still open)*

**Observed.** With a real sandbox run finally working (P3), the Change Review panel rendered
`(could not load diff)` for a file whose diff had been generated correctly minutes earlier, and
`GET /projects/{id}/change-sets/{cs}/entries/{e}/diff` returned **500**:

```
minio.error.S3Error: code: NoSuchKey ... object_name:
  project-diff/48f13add-.../a807ec02-...
```

**Root cause — pre-existing, not caused by P3.** `build_change_set` spills each per-file unified
diff to the object store under `project-diff/{change_set_id}/{entry_id}`
(`app/services/project_changes.py:203-204`). Those objects deliberately have **no `storage_blobs`
row** — they are a projection, not user content. But `drive_svc.sweep_orphan_objects`
(`app/services/drive.py`) treats `storage_blobs` as the authority for *every* key in the bucket
and deletes anything without a row, exempting only `project-import/`. So the next
`cron:drive_maintenance` tick deletes every live change-set diff, while the
`project_change_set_entries.diff_object_key` column keeps pointing at the deleted object.

Timeline from the live stack, which is as direct as evidence gets:

```
13:40:00  cron:drive_maintenance ● 'gc=0 orphans=6'
13:40:26  Change Review opened → 500 NoSuchKey
```

**Why it was never seen before.** It needs a change set to survive a cron tick and *then* be
reviewed. W3's verification built and reviewed change sets inside the same few minutes, and the
maintenance cron happened not to land in between. P3 made a real `project_run` produce real diffs
and the stack was restarted mid-verification, which put a sweep exactly in the gap.

**Fix (shipped 2026-07-31).** `_GC_EXEMPT_PREFIXES = ("project-import/", "project-diff/")` — the
change set owns its spills' lifecycle, exactly as the import job owns its staging objects.
Regression test: `tests/test_project_changes.py::test_the_orphan_gc_does_not_delete_change_set_diff_spills`
(verified to fail with the prefix removed, not merely to pass with it present).

**Still open — retention.** Exempting the prefix stops the deletion but leaves the spills
unreclaimed: `build_change_set` supersedes the prior open change set on **every** boundary and
writes a fresh set of diff objects, so a long working session accumulates `project-diff/` objects
that nothing ever removes. They do not charge the user's quota (no blob row), which is precisely
why nothing notices. A real fix needs a decision:

- sweep diffs belonging to `superseded`/`applied`/`discarded` change sets (needs the GC to read
  change-set state rather than a blob table), **or**
- give the spills a TTL like `TOOL_OUTPUT_RETENTION_HOURS` (api §7.2 has the same open debt for
  tool-output spills — one janitor could serve both), **or**
- stop spilling and recompute diffs on demand from the two content hashes (both blobs are already
  content-addressed and deduped; this deletes a whole storage class and its GC problem, at the
  cost of CPU per review).

Recommend folding this into the same janitor as the api §7.2 spill retention (currently parked in
Phase TR **P2.8**) rather than inventing a second sweeper.

> **Status note (2026-07-31, P3 review).** This is **not fixed and not scheduled**. What shipped
> was only the *exemption* that stops live diffs being deleted; the unbounded growth is real,
> ongoing, and invisible to the user (the objects charge no quota, which is exactly why nothing
> surfaces it). Recording it plainly so the closed half of B-12 is not mistaken for the whole:
> **`project-diff/` currently grows without bound.**
