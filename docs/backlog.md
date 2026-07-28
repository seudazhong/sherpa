# BACKLOG — findings from manual testing

> Raw, unscheduled work items. Each entry is a **reported observation plus the evidence found while triaging it** — none of them are fixed, and none have been promoted into [`IMPLEMENTATION.md`](IMPLEMENTATION.md) yet. Promote an item (give it a task id + phase) before writing code; anything that changes behaviour covered by a frozen contract needs the contract/ADR update first (per [`../AGENTS.md`](../AGENTS.md) §1).
>
> Status values: `open` (not started) · `promoted` (now a task in `IMPLEMENTATION.md`) · `done` · `wontfix`.

## Index

| # | Kind | Item | Status |
| --- | --- | --- | --- |
| B-1 | bug | [Chat header shows a stale hard-coded model](#b-1-chat-header-shows-a-stale-hard-coded-model) | open |
| B-2 | design | [Built-in tool surface is too large (53 tools)](#b-2-built-in-tool-surface-is-too-large-53-tools) | open |
| B-3 | bug | [The model cannot see the chat's bound project](#b-3-the-model-cannot-see-the-chats-bound-project) | open |
| B-4 | bug/dx | [OTel tracing silently off after a stack restart](#b-4-otel-tracing-silently-off-after-a-stack-restart) | open |
| B-5 | gap | [Drive cannot upload a folder](#b-5-drive-cannot-upload-a-folder) | open |
| B-6 | feature | [Chat attachments: image upload/paste + attach from Drive](#b-6-chat-attachments-image-uploadpaste--attach-from-drive) | open |
| B-7 | ux | [`Inbox` nav label collides with the email inbox](#b-7-inbox-nav-label-collides-with-the-email-inbox) | open |

Suggested order: **B-4 → B-3** (restore observability first, then confirm what the model actually receives) → **B-1, B-7** (cheap, high perceived quality) → **B-5, B-6** (need a contract decision) → **B-2** (largest design question, own ADR).

---

## B-1 Chat header shows a stale hard-coded model

*Reported 2026-07-28 (manual test) · kind: bug · status: open*

**Observed.** The Chat page header always renders `claude-sonnet-4.6` next to the `Web chat` chip, while the session actually runs a different model (the session model switcher showed `Local litellm · gpt-5.5`).

**Evidence.** `frontend/src/views/ChatView.tsx:695-703` renders `meta.model` from `GET /meta`; `backend/app/main.py:89-98` returns `settings.provider_model`, i.e. the **process-level env default** (`PROVIDER_MODEL`, default `claude-sonnet-4.6`). It reflects neither the DB-configured model source (ADR-041) nor the per-session override (`sessions.model_provider_id` / `sessions.model`).

**Direction (undecided).** Either drop the static label and let the model switcher be the single source of truth, or make the label show the **session's effective** provider/model (resolved the same way the worker resolves it) and fall back to the env default only when unset. Also decide whether `/meta` should keep advertising the env model at all.

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

*Reported 2026-07-28 (manual test) · kind: bug · status: open · blocked-by: B-4 (to confirm the assembled prompt)*

**Observed.** In a project-bound chat (the UI chip showed `hello-world-py · project`), asking *"which project is this chat in?"* produced "I can't see an explicit binding" — the model only guessed from `list_projects`.

**Evidence.** The binding is real server-side: `sessions.project_id` (`backend/app/models/core.py:124`), set by `services/projects.py` `open_in_chat` and relied on by `services/project_workcopy.py:118-130`; the frontend renders it as a chip. But the assembled prompt never carries it — `backend/app/core/loop.py:474` builds `system_content = SYSTEM_PROMPT [+ core_memory]` and nothing else.

**Consequences.** The model cannot answer "where am I", and must be handed a `project_id` explicitly instead of defaulting to the bound project for `project_read` / `project_run`.

**Direction (undecided).** Add a small ambient **session context** slot (bound project id + name + head snapshot, channel/UMO label, current time + timezone) to the layered prefix, placed so the byte-stable cached prefix is not broken (docs/04 invariant ⑤: dynamic data on the tail); and/or default the project tools' `project_id` to the session binding when omitted. Update the prompt/contract docs with whatever is chosen.

---

## B-4 OTel tracing silently off after a stack restart

*Reported 2026-07-28 (manual test) · kind: bug/dx · status: open*

**Observed.** Phoenix (`localhost:6006`) shows the `default` project *last updated 2 days ago*, 12 traces — no new traces, so the assembled prompt for B-3 could not be inspected.

**Evidence (checked live).** `infra-phoenix-1` is up, but `infra-worker-1` / `infra-web-1` (rebuilt ~1 h earlier) run with `OTEL_ENABLED=false` and an empty `OTEL_EXPORTER_OTLP_ENDPOINT`. `infra/docker-compose.yml:99-101,146-149` default these to `false`/empty, so tracing depends on shell env at `up` time and is **silently lost on every rebuild/restart** that does not export them. Neither the app nor Phoenix indicates that collection is off.

**Direction (undecided).**
- Keep the settings across restarts — put `OTEL_ENABLED` / `OTEL_EXPORTER_OTLP_ENDPOINT` / `OTEL_CAPTURE_MESSAGE_CONTENT` in `.env(.example)`, or bind them to the `observability` compose profile.
- Log one startup line in web + worker stating tracing enabled/disabled and the endpoint, so the failure is never invisible.
- Optionally surface tracing status (and a Phoenix link) in Settings.
- Note in `AGENTS.md` / docs that the two verification lanes want tracing on.

---

## B-5 Drive cannot upload a folder

*Reported 2026-07-28 (manual test) · kind: gap · status: open*

**Observed.** Drive uploads exactly one file at a time; a folder cannot be uploaded.

**Evidence.** `frontend/src/views/WorkspaceView.tsx:248-257` renders a bare `<input type="file">` with neither `multiple` nor `webkitdirectory`, and the handler takes `files?.[0]` only. The contract offers just `POST /drive/files` (single multipart `path` + file) beside `POST /drive/folders` (`docs/contracts/api.md:1513-1523`). There is no directory drag-and-drop either.

**Direction (undecided, contract decision first).**
- (a) *Client-side expansion* — add `multiple` + `webkitdirectory` (plus a `DataTransferItem` directory walk for drag-drop), create the tree via `POST /drive/folders`, upload each file via `POST /drive/files`, with bounded concurrency, per-file progress/errors, and caps on file count + total bytes.
- (b) *Archive upload* — a new endpoint reusing the hardened bounded expander built for Projects (`backend/app/services/archive.py`, ADR-037) so a zip becomes a folder tree server-side.

Either way: respect quota (`507`) and per-file size (`413`) semantics, report partial failure honestly, and update `docs/contracts/api.md` + the §9 capability matrix.

---

## B-6 Chat attachments: image upload/paste + attach from Drive

*Requested 2026-07-28 · kind: feature · status: open*

**Ask.** In Chat, (a) upload or paste an image straight into the composer, and (b) attach an existing file from Drive.

**Current state.** The whole path is text-only: the composer has no attach/paste handling, admission stores a single text part, and `backend/app/core/history.py:57-74,202` collapses each user turn into a plain string `content` — nothing multimodal can reach the provider.

**Design work needed before code.**
1. **Message model** — typed parts (`text` / `image` / `file_ref`); decide what is persisted versus referenced (a Drive node id + version, not a byte copy), and how redaction/bounding applies.
2. **Provider layer** — OpenAI-shape content arrays and Anthropic image blocks, plus a per-source vision capability flag (ADR-041) so a non-vision model degrades honestly instead of erroring.
3. **Storage** — pasted images should land in Drive (quota `507` / size `413` semantics) so they are versioned and re-referenceable rather than orphaned blobs.
4. **Non-image files** — extract-text versus attach-as-is; reuse the bounded reader.
5. **UI** — composer attach button, clipboard paste, a Drive picker, removable attachment chips, and attachment rendering in the transcript.
6. **Prompt/cache + replay** — impact on the layered prefix (docs/04 invariant ⑤) and how attachments replay across turns.

Order: ADR → `docs/contracts/api.md` + data-model contract → backend → UI → §9 capability matrix.

---

## B-7 `Inbox` nav label collides with the email inbox

*Reported 2026-07-28 (manual test) · kind: ux/naming · status: open*

**Observed.** The sidebar item **Inbox** reads as "my email inbox", but the page is the agent triage surface — its own subtitle says *"Review suggestions, approvals, and follow-through in one place"* (candidates extracted from Gmail per ADR-009/ADR-010, not raw mail).

**Why it is worse here.** Gmail is *the* v1 connector (ADR-022), and other views already show an **Inbox** chip meaning the mail folder (`frontend/src/views/ConnectorsView.tsx:332`, `views/MessagingView.tsx:185`) — one word, two meanings. Secondary IA problem: the subtitle claims approvals live here while **Approvals** is a separate nav item.

**Direction (undecided).** Pick a name that says *"things Sherpa surfaced for you to decide"* — candidates: Triage / For you / Suggestions / Today / Needs you — and fix the subtitle so it stops claiming approvals. Touch points: `frontend/src/components/Sidebar.tsx:34` (label), `frontend/src/App.tsx:42` (`/inbox` route), `frontend/src/views/InboxView.tsx:117,127` (heading + aria), plus the design-bright mockups, the §9 capability matrix, and contracts if the route changes. Per `AGENTS.md`, an SPA route name must not collide with an API proxy prefix.
