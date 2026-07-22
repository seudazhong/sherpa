# R-WORKSPACE-PRODUCT — Research and design recommendation

**Status:** research complete. The Project-bound Chat and sandbox-lifecycle direction was owner-confirmed on 2026-07-22; no implementation is approved by this document.

**Date:** 2026-07-22

**Static prototype:** [`workspace-product-prototype/index.html`](workspace-product-prototype/index.html)

## Decision summary

| Question | Recommendation |
|---|---|
| User-facing taxonomy | Keep **Personal workspace** as the ownership container. Inside it, expose **Projects** and **Drive** as distinct sibling products. |
| Meaning of Project | A durable Sherpa-owned development state with a file tree, snapshots, activity, optional source binding, and sandbox actions. A Git repository is an optional source, not the Project identity or source of truth. |
| Meaning of Drive | General private documents, uploads, generated exports, and reusable assets. Drive is never mounted wholesale into a sandbox. |
| Execution term | Keep **Sandbox** for the ephemeral compute layer. Do not reuse the overloaded word "workspace" for a container or run. |
| Project Chat | A chat is either General or bound to exactly one Project. Selecting another Project starts a new chat; there is no global "development mode" that silently changes the current session. |
| Navigation | Future-state routes: `/work` (overview), `/work/projects`, and `/work/drive`. Keep API prefixes separate from SPA routes. |
| Canonical stores | Postgres owns metadata, ownership, quota, versions, snapshots, sync state, and change sets. MinIO owns immutable bytes. Redis remains an accelerator only. |
| Storage architecture | Extend the logical metadata tree with immutable, tenant-scoped blob records and project snapshots. Do not introduce a separate durable Git store. This is Option 1 with a deliberate path toward Option 3, not a full Merkle-tree platform on day one. |
| Quota | Deployment-configured **5 GiB per personal user** is a reasonable default, not a schema constant. Enforce a tenant limit and a deployment hard ceiling as well. |
| Accounting | Charge each owner once per distinct durable blob they reference. Multiple snapshots or versions pointing at unchanged bytes do not multiply usage. No deduplication credit crosses user or tenant boundaries. |
| Sandbox durability | Persist the **task working copy**, not the container. Project snapshot + durable working-copy overlay are authoritative; scratch volume and warm container are disposable caches. Only an explicit Save advances Project head. |
| Initial coding executor | Use Sherpa's built-in Project/file/sandbox tools only. Embedded coding agents or CLI adapters are explicitly deferred until the native Project Chat flow is proven. |
| Git synchronization | Fetch may update remote status but never overwrite local durable state. Apply remote changes explicitly; surface divergence/conflicts. Push, remote branch creation, and PR creation are external writes and require approval. No force push in the first implementation. |
| First implementation increment | Ship the Workspace shell plus a correct Drive foundation: quota/reservations, folders, versions, trash, search/sort, storage management, UI, and agent tools. Do not expose Projects navigation until the Project slice is real. |

## 1. Current Sherpa baseline

Milestone 2 intentionally shipped a thin storage seam:

- `files` stores one row per `(tenant_id, user_id, path)` with an opaque object key, size, content type, hash, and a version counter (`backend/app/models/files.py:19-35`, `backend/migrations/versions/0017_files.py:26-53`).
- `app.services.files` normalizes paths, rejects traversal, caps each file at 10 MiB, and implements put/read/list/delete (`backend/app/services/files.py:23-156`).
- REST and Tool adapters share that service (`backend/app/api/files.py`, `backend/app/tools/file_tools.py`), consistent with ADR-023.
- The UI exposes a single flat **Files** page at `/workspace`, with upload, download, and permanent delete (`frontend/src/views/FilesView.tsx`, `frontend/src/components/Sidebar.tsx:23-47`).
- MinIO is present in Compose with one persistent volume and shared service credentials (`infra/docker-compose.yml:35-47,59-128`).
- The Docker sandbox is ephemeral, network-disabled, non-root, read-only outside `/tmp`, and receives no user files (`backend/app/sandbox/runner.py:28-86`, ADR-025).

The seam is useful, but it is not yet a safe cloud-drive or project model:

1. **Flat namespace:** folders are path prefixes, not owned records; there is no move/rename transaction, tree pagination, folder trash, or subtree policy.
2. **No quota or reservations:** the 10 MiB per-file cap does not protect aggregate storage or concurrent writers.
3. **No retained history:** overwrite increments `version` but deletes the previous blob; the old bytes cannot be restored.
4. **Permanent delete only:** there is no trash, retention, restore, or purge workflow.
5. **Cross-store correctness gap:** `put_file` writes the new object before the database commit and deletes the old object before the adapter commits (`backend/app/services/files.py:54-84`, `backend/app/api/files.py:69-88`). `delete_file` deletes the object before the database commit (`backend/app/services/files.py:144-156`, `backend/app/api/files.py:109-119`). A crash or failed commit can therefore leave an orphan object or a committed row whose prior object was already removed.
6. **Whole-object buffering:** uploads and downloads are loaded fully into process memory.
7. **No reconciliation:** there is no orphan sweep, checksum audit, quota rebuild, or object-garbage-collection job.
8. **No Project entity:** there is no source binding, snapshot, branch state, change set, artifact, or development lifecycle.
9. **No durable sandbox boundary:** the sandbox cannot receive a selected Project and cannot emit a reviewable diff.
10. **Contract gap:** the frozen v1 contracts still reserve Files, GitHub, and sandbox surfaces rather than defining the post-v1 implementation. Further work must reconcile contracts before code.

## 2. Product and competitive evidence

All sources in this section are first-party documentation accessed 2026-07-22.

| Product | Documented model | Transferable lesson for Sherpa | Primary source |
|---|---|---|---|
| Google Drive | A personal file tree with folders, nested folders, clear names, and Starred as a cross-cutting view. Storage and trash are account-level concerns, not folder-level concepts. | Drive should remain a familiar tree with Recent/Starred/Trash views and one account-level meter. | [Organize files in Google Drive](https://support.google.com/drive/answer/2375091), [Drive storage quota fields](https://developers.google.com/resources/api-libraries/documentation/drive/v3/java/latest/com/google/api/services/drive/model/About.StorageQuota.html) |
| Dropbox | A file/folder root plus a separate **Deleted files** view with restore and restoration progress. Deleted items are retained for at least 30 days on the base plan. | Trash should be a recoverable lifecycle state with explicit progress, not a hidden folder or immediate object deletion. | [Recover deleted files or folders](https://help.dropbox.com/delete-restore/recover-deleted-files-folders) |
| OneDrive | Left navigation separates **My files**, **Recent**, **Shared**, and **Recycle bin**. Large uploads use a resumable upload session. | Cross-cutting views belong beside the tree; large upload is an explicit session, not one unbounded request. | [Change views on the OneDrive website](https://support.microsoft.com/en-us/office/change-views-on-the-onedrive-website-1c2a8fe8-45f3-472a-b6b3-ecb007b28573), [createUploadSession](https://learn.microsoft.com/en-us/graph/api/driveitem-createuploadsession?view=graph-rest-1.0) |
| Notion | Workspace is the outer member/billing container; Teamspaces are permission-scoped containers; pages live within them. | "Workspace" is credible as an ownership umbrella, but not as a synonym for every file tree, project, or execution environment. | [Intro to teamspaces](https://www.notion.com/help/intro-to-teamspaces), [What is a database?](https://www.notion.com/help/what-is-a-database) |
| GitHub Codespaces | A codespace is a containerized environment created for a repository branch/commit/template. A shallow clone is mounted under `/workspaces`; users may keep or discard the environment. | Durable source and ephemeral compute are separate concepts. Sherpa should make Project state durable and Sandbox state disposable. | [Deep dive into GitHub Codespaces](https://docs.github.com/en/codespaces/about-codespaces/deep-dive) |
| Replit | A **Project** is the top-level container for what the user builds. Its Git pane separately handles repository setup, review, staging, branches, pull, push, and conflict resolution. | Project is a richer product object than a folder. Git is an attached capability with explicit review and conflict states. | [What's a Project?](https://docs.replit.com/features/projects-and-artifacts/projects), [Using the Git pane](https://docs.replit.com/features/workspace-tools/git-interface) |
| Gitpod/Ona | A running workspace can be snapshotted into a point-in-time file state. The snapshot excludes environment variables, authentication, and Git credentials; processes are not preserved. | A checkpoint should capture durable file state without live processes or credentials. | [Collaboration and workspace snapshots](https://ona.com/docs/classic/user/configure/workspaces/collaboration) |
| Devin | "Workspace" is used for a repository subdirectory with its own blueprint in a monorepo. | The word is already overloaded even inside agent products; do not use it for Sherpa's sandbox or Project subtree. | [Workspaces and monorepos](https://docs.devin.ai/onboard-devin/environment/workspaces) |
| OpenAI Codex | A task can run in Local, Worktree, or Cloud mode. Worktree explicitly isolates changes from the current project directory. | Isolated change production is a first-class UX concept; users should know where changes are happening before they save them. | [Environment modes](https://learn.chatgpt.com/docs/environments/modes) |
| Firebase Studio | A workspace is a single-codebase cloud development environment; duplicating it creates an independent experiment. | A development environment is scoped to one codebase and can be copied for experimentation, but this should remain below Sherpa's durable Project layer. | [Get started with a workspace](https://firebase.google.com/docs/studio/get-started-workspace) |

### Product conclusions

1. **Workspace is an umbrella, not a leaf object.** It is too overloaded to mean a Project, folder, repository, and sandbox simultaneously.
2. **Drive and Projects are different nouns.** Drive optimizes for familiar file management; Projects add source, snapshots, activity, execution, and conflict semantics.
3. **Projects must not be rich folders.** A Project may contain a tree, but it also owns source state, checkpoints, change sets, and lifecycle. Those concepts do not belong on every Drive folder.
4. **The repository is a source binding.** Sherpa must preserve a durable Project even if the remote disappears, credentials expire, or the user unbinds it.
5. **Execution state is disposable.** The durable result is a reviewed snapshot/change set, not a long-lived container filesystem.
6. **Storage management is account-level.** Used, reserved, history, trash, and available bytes belong on Workspace home and Storage management, not inside one folder.

## 3. Product taxonomy and information architecture

### 3.1 Recommended vocabulary

| Term | Meaning | Do not use it for |
|---|---|---|
| Personal workspace | The user's private ownership and quota context. It contains Drive and Projects. | A sandbox container, Git checkout, or arbitrary folder |
| Drive | General-purpose private files, folders, uploads, exports, and reusable assets | Source-control status or a live development environment |
| Project | A named durable development state with a file tree, snapshots, optional source binding, activity, and sandbox actions | A remote repository alone |
| Source | Optional external origin/binding, initially GitHub repository + branch | Canonical Project state |
| Sandbox | A temporary isolated execution environment materialized from one Project snapshot | Durable storage |
| Project Chat | A session whose immutable context binding identifies one Project. `project_id = null` means General chat. | A mode that can silently switch the current conversation between projects |
| Task working copy | Durable pending Project state owned by one Project Chat, based on one saved snapshot and reusable across turns | The canonical Project head |
| Scratch volume | A node-local materialization/cache of a task working copy, mounted read-write into a sandbox | The only copy of pending work |
| Change set | The bounded, reviewable output of a sandbox run | An already-applied mutation |
| Checkpoint | A pinned Project snapshot that can be restored | A running container or Git credential bundle |
| Artifact | A retained run output. It belongs to the Project until explicitly copied or exported to Drive. | Every temporary build cache or log |

### 3.2 Future navigation

```text
Agent
  Chat
  Inbox
  Activity

Workspace
  Overview       /work
  Projects       /work/projects
  Drive          /work/drive

Organize
  Schedules
  Memory

Channels
  Messaging
  Connectors
```

The existing `/workspace` SPA route should redirect to `/work/drive` only when the production migration ships. API routes should remain transport nouns such as `/drive/*`, `/projects/*`, and `/storage/*`; using `/work/*` for the SPA avoids proxy-prefix collisions.

### 3.3 Boundary rules

- A Drive item can be copied into a Project, but the copy becomes Project-owned state with independent lifecycle.
- A Project artifact can be retained inside the Project or explicitly exported to Drive.
- Linking a Drive item to a Project does not make the whole Drive visible to the sandbox.
- A chat is created as General or Project-bound. After the first admitted message, its `project_id` is immutable; choosing another Project creates a new chat.
- Selecting a Project is the development context. Do not add a second global "normal/development" mode switch. A narrower `Plan only | Allow edits and tests` policy may control mutations without changing Project identity.
- A Project Chat creates its task working copy lazily on the first mutating action. The Project head remains unchanged until Save.
- Separate Project Chats use separate task working copies even when they target the same Project.
- The initial Project Chat executor is Sherpa itself through built-in tools. Embedded Copilot CLI, Claude Code, or other coding-agent processes are not part of the first increment.
- Search may be federated on Workspace home, but results retain their domain label and open in Drive or Project context.
- Trash is domain-aware: Drive items and deleted Projects can share a Storage management view while preserving different restore semantics.
- The static prototype shows the full future state. Production navigation must not expose Projects until the corresponding service, UI, tools, and lifecycle are implemented.

## 4. Scope recommendation

### Increment W1 — Workspace shell and Drive foundation

This is the recommended first implementation increment.

**Product**

- Workspace overview with storage meter, recent Drive items, and storage warnings.
- Drive with first-class folders, breadcrumbs, upload, download, search, sort, rename, move, bulk selection, versions, trash, restore, and permanent purge.
- Storage management with Active, History, Trash, Reserved, and Available categories.
- Responsive desktop and 390 px mobile UI.

**Architecture**

- Introduce storage accounts, quota reservations, immutable blob records, Drive nodes, and Drive versions.
- Migrate current `files` rows into the personal Drive root without exposing object keys.
- Replace permanent delete with trash; retain explicit purge.
- Stream/resume large uploads instead of reading the entire object into memory.
- Add orphan, checksum, quota, and GC reconciliation.

**Agent parity**

- The agent can list/search Drive, create folders, write/upload bounded content, rename/move, view versions, restore versions, trash, and restore through the same service layer.
- Permanent purge remains human-only or approval-gated.

**Deliberate exclusion**

- Do not add a Projects navigation placeholder in W1.
- Do not mount Drive into the sandbox.
- Do not implement GitHub sync or a new Git service.

### Increment W2 — Projects and one-time import

- Projects library and Project detail.
- Blank Project, template, uploaded archive, and one-time GitHub branch import.
- Project file tree, activity, storage usage, source metadata, and snapshots.
- **Open in Chat** creates a new Project-bound session. The project binding is visible in the chat header and cannot change in-place.
- The first W2 Project Chat may read/discuss the Project without creating a task working copy.
- GitHub import materializes the selected branch head and records the source commit. It does not retain or charge the full remote history by default.
- No background merge, push, force push, submodules, or live preview.

### Increment W3 — Project Chat working copy and sandbox change review

- On the first mutating turn, create a durable task working copy based on the current Project snapshot.
- Materialize that working copy into a scratch volume and run the current network-disabled hardened sandbox against it.
- Persist the working-copy overlay after each bounded tool batch, before waiting for user input, and before sandbox teardown.
- A container may remain warm for a short configurable idle TTL, but it is never authoritative and may be killed at any time.
- Persist a bounded change set and artifact manifest relative to the saved Project head.
- Dependency installation/resolution is not part of W3. Commands run only with runtimes/tools already present in the approved base image and dependencies already present in the Project snapshot; otherwise the run stops with an explicit `environment_missing_dependencies` result.
- Review added/modified/deleted files and artifacts.
- Save selected changes, optionally pin a checkpoint, or discard the change set.
- Reject save if the Project head moved since the run base unless the changes can be safely rebased and reviewed again.
- Use Sherpa's built-in file/edit/run/test tools only. Do not embed a specialist coding agent in W3.

### Increment W4 — GitHub synchronization and external writes

- Background fetch updates remote status only.
- Explicit apply/merge flow with ahead/behind/diverged/conflicted states.
- Approval-gated push, remote branch creation, and pull-request creation.
- Exact expected remote OID on push; stop and reconcile if the remote moved.
- No force push in the first Git write implementation.

### Later

- Fork automation, submodules, Git LFS, multiple remotes, full-history mirrors.
- Network-enabled development environments, dependency installation policy, prebuilds, live previews, and long-running services.
- Team/shared Drive, Project transfer, roles, pooled quota, and collaboration.
- Embedded coding-agent executors (for example CLI-based coding agents), multi-agent delegation, and provider-specific authentication/streaming adapters.
- Cross-tenant physical deduplication, storage tiering, and compliance retention.

## 5. Architecture comparison

| Option | Strengths | Weaknesses | Decision |
|---|---|---|---|
| 1. Logical tree over object storage | Closest to current code; clear ownership and folder semantics; easy REST/UI mapping | Needs explicit versions, quota, GC, and snapshots; naive overwrite/delete creates cross-store inconsistency | **Use as the base**, corrected with immutable blobs and durable lifecycle records |
| 2. Split Drive + durable Git stores | Native Git history, branches, merge, and worktrees | Adds a second canonical storage plane, separate backup/recovery, confusing accounting, and persistent filesystem requirements; Git LFS shows how split quota models surprise users ([Git LFS billing](https://docs.github.com/en/billing/concepts/product-billing/git-lfs)) | **Reject as canonical architecture** |
| 3. Universal content-addressed tree/snapshot store | Cheap snapshots, integrity, deduplication, immutable history | Highest implementation complexity; reachability GC, tree formats, and migration are premature for current scale | **Adopt selected semantics, not the full platform** |

### Selected architecture: logical namespaces over immutable blobs

The recommended model is Option 1 with these Option 3 properties:

- every committed blob is immutable and checksum-verified;
- Drive versions and Project snapshots reference blobs instead of overwriting them;
- duplicate content can share one physical blob within a tenant;
- Postgres remains the queryable source of truth for names, ownership, states, references, and quota;
- object-store keys are opaque and tenant-scoped; content hashes remain internal metadata;
- no durable `.git` working tree is canonical.

This gives Sherpa safe versions and snapshots without building a general Merkle-tree service before scale proves it necessary.

## 6. Recommended data model

Names are proposed, not frozen. Every table carries `tenant_id` and composite keys per ADR-015.

### 6.1 Ownership and quota

`storage_accounts`

- one personal account per user now;
- later accounts may represent a tenant/team-owned Drive;
- fields include `limit_bytes`, category counters, policy version, and status.

`storage_reservations`

- one row per upload, archive import, Git import, agent output, or sandbox save;
- states: `pending | committed | released | expired`;
- stores declared bytes, actual bytes, idempotency key, operation kind, and expiry.

`storage_blob_owners`

- per charging owner + blob reference count;
- the first reference charges the blob size once, additional references are free within that owner's history;
- no charging credit crosses owner boundaries.

### 6.2 Blob lifecycle

`storage_blobs`

- `id`, opaque `object_key`, `size_bytes`, `sha256`, media type, scan state, integrity state, and lifecycle state;
- active physical deduplication may be enforced within a tenant by a partial uniqueness rule over `(tenant_id, sha256, size_bytes)` that excludes retiring/deleted generations;
- states: `staged | active | quarantined | missing | delete_pending | deleting | deleted`.

`storage_object_jobs`

- durable outbox/reconciliation work for duplicate-stage cleanup, purge, checksum audit, and repair;
- alternatively reuse the existing generic outbox with typed storage payloads.

### 6.3 Drive

`drive_nodes`

- first-class `folder | file` records with `parent_id`, normalized name, status, trash metadata, and optimistic version;
- active sibling names are unique within one parent.

`drive_file_versions`

- immutable ordered versions pointing at `storage_blobs`;
- records cause, actor, source, creation time, and optional pinned state;
- rename/move changes node metadata but does not create a content version.

### 6.4 Projects

`projects`

- name, description, owner, status, current snapshot, default branch label, source status, storage rollup, and last activity.

`project_sources`

- optional provider binding: provider, stable repository ID, display name, installation/credential reference, selected branch, base/remote OIDs, fetch time, and sync state;
- credentials remain in the connector/vault boundary and never enter a Project tree or sandbox.

`project_snapshots`

- immutable parent-linked snapshots with reason (`import | save | checkpoint | sync`), manifest/tree reference, source OID, size rollup, and pinned state.

`project_snapshot_entries`

- normalized path, entry kind, blob reference, executable bit, and safe relative symlink target where allowed;
- a compact immutable manifest/tree representation may replace this projection later without changing Project semantics.

`project_change_sets`

- base snapshot, producing run, proposed file operations, artifacts, quota reservation, status, expiry, and applied snapshot;
- states: `staging | ready | applied | discarded | conflicted | expired`.

`project_artifacts`

- retained build outputs/logs/previews selected by the user; ephemeral run output is not durable quota until retained.

### 6.5 Project-bound Chat and working state

`sessions.project_id` (proposed contract extension)

- nullable: `null` means General chat;
- immutable after the first admitted user message;
- Project access is checked when creating the session and on every Project operation;
- changing Project creates a new session rather than mutating transcript/tool context in-place.

`project_working_copies`

- one durable pending state owned by a Project Chat and Project;
- fields include `project_id`, `session_id`, `base_snapshot_id`, overlay/manifest reference, quota reservation, optimistic version, single-writer fence token, last persisted event/turn, and timestamps;
- states: `open | ready_for_review | saved | discarded | conflicted | expired`;
- multiple chats may target one Project, but their working copies never share a writable scratch tree.

Sandbox executions reuse the existing durable `runs`/event journal and link to a working copy. A Docker container ID, local volume path, or warm-cache lease is operational metadata only and is never the recovery source of truth.

## 7. Quota and accounting semantics

### 7.1 Three ceilings

Every reservation must satisfy all applicable limits in one Postgres transaction:

1. **Personal user limit** — recommended default 5 GiB, configurable by deployment.
2. **Tenant limit** — aggregate ceiling; in today's single-user tenant it may equal the user limit.
3. **Deployment hard ceiling** — protects the self-hosted operator's actual disk and includes staging/reclaim-pending bytes.

Future team deployments may add optional per-user sublimits beneath a pooled tenant limit. A limit is configuration/policy, not hard-coded into schema or UI copy.

### 7.2 What counts

**Durable quota**

- current Drive file content;
- retained Drive versions;
- current Project snapshot content;
- retained Project checkpoints;
- retained Project artifacts;
- trash until purge;
- in-flight reservations shown separately.

**Not durable quota**

- object-store physical duplication hidden below the logical model;
- ephemeral sandbox rootfs, build cache, temporary logs, and previews before retention;
- bounded Git fetch/clone caches used by the sync worker;
- tool-output spill files, which retain their existing separate cap and retention contract.

### 7.3 Unique durable-byte rule

For one charging owner:

```text
used_bytes = sum(size(blob))
             for each distinct blob referenced by at least one retained object
```

- Ten unchanged checkpoints referencing the same bytes do not increase usage.
- Reverting to an existing version does not increase usage.
- A genuinely new blob increases usage once.
- The last retained reference to a blob releases its logical charge.
- Two users are each charged for their own reference even if the tenant stores one physical copy.
- Two tenants never receive cross-tenant deduplication credit or an existence signal.

The UI assigns each charged blob to one non-overlapping display category by strongest reachability:

1. Active, if referenced by a current Drive item or Project head;
2. History, if referenced only by a retained version/checkpoint;
3. Trash, if referenced only by trashed objects;
4. Reclaim pending, internal/operator-only after logical purge and before physical GC.

`available = limit - active - history - trash - reserved`.

### 7.4 Reservation flow

1. **Reserve:** atomically increment user, tenant, and deployment reserved counters if every ceiling has room. Store an idempotent reservation with TTL.
   - For an operation whose final size is not knowable up front (archive expansion, Git import, agent output, or sandbox change capture), reserve the configured per-operation maximum after any available preflight. Verify/commit releases the unused difference; if the maximum cannot be reserved, the operation does not start.
2. **Stage:** stream bytes to an opaque tenant-scoped object key. For multipart upload, the reservation owns the upload ID and parts.
3. **Verify:** confirm size and checksum before any user-visible metadata references the object. If a PUT outcome is unknown, reconcile with object HEAD/checksum before retrying.
4. **Commit:** in one Postgres transaction, insert or reuse the tenant blob row, create the Drive version/Project entry, update owner refcounts, convert reserved bytes to committed usage, and append required outbox cleanup work.
   - A new verified staged object may be adopted in place as the immutable canonical object; no cross-store rename is required.
   - If the content already exists within the tenant, metadata reuses the existing blob and an outbox job deletes the duplicate staged object.
   - A commit may reuse only an `active` blob row. A matching row in `delete_pending`, `deleting`, or `deleted` is treated as a miss and receives a new blob generation/object key; the active-dedup uniqueness rule must not force reuse of a retiring row.
5. **Abort/expire:** release reservation counters and enqueue staged-object cleanup.
6. **Purge:** remove logical references in Postgres first; only an idempotent outbox/GC worker deletes an unreferenced physical object after commit.
   - Immediately before physical deletion, GC must atomically re-check the exact blob generation is still `delete_pending` with zero retained references, then claim it for deletion. A queued job is never authority to delete by itself.

Expected failure states are asymmetric by design:

- object without committed metadata: benign orphan, reclaimed later;
- committed metadata pointing at unverified bytes: forbidden;
- committed metadata pointing at a verified object later found missing/corrupt: explicit `missing`/`corrupt` state, visible and repairable, never silently hidden.

### 7.5 Version and trash defaults

Recommended policy defaults, all deployment-configurable:

- trash retention: 30 days;
- unpinned version retention: 30 days with a count cap;
- pinned checkpoints retained until explicitly unpinned/deleted;
- open task working-copy idle retention: an initial 7-day hypothesis with warning before expiry;
- a working-copy reservation may not expire independently while the working copy remains `open`. Idle expiry atomically transitions the working copy to `expired`, releases reserved quota, and enqueues overlay cleanup;
- trash and history count toward quota;
- do not silently purge unexpired trash merely because a user is full. Block new durable writes, explain the categories, and offer explicit cleanup. Deployment emergency policy is separate and must be visible to the operator.

## 8. Object lifecycle, reconciliation, and backup

### Required jobs

1. **Expired reservation sweep:** release counters and abort multipart/staging uploads.
2. **Orphan object sweep:** delete old staged objects with no committed reservation/reference.
3. **Duplicate stage cleanup:** remove verified duplicate uploads after metadata reuses an existing blob.
4. **Reference GC:** delete blobs with zero retained references, using an idempotent delete job.
5. **Metadata-to-object audit:** detect missing objects and checksum/size mismatch; mark explicit integrity state.
6. **Quota reconciliation:** recompute unique owner blob usage and repair cached counters.
7. **Storage pressure monitor:** compare physical object/staging/reclaim-pending usage with the deployment hard ceiling and minimum free-space reserve.

MinIO bucket quota is only a secondary operator guard. MinIO documents that enforcement is best-effort and not real-time, so it cannot replace the Postgres reservation ledger ([MinIO bucket quotas](https://docs.min.io/aistor/administration/bucket-quotas/)).

### Backup and restore

- A supported backup is a coordinated Postgres backup plus MinIO object snapshot with a manifest/checkpoint time.
- Restore first loads metadata and objects, then runs integrity and quota reconciliation before write traffic resumes.
- Missing objects remain explicit; restore must not manufacture successful-looking file records.
- Tenant-scoped export should include metadata, versions/checkpoints selected by retention, checksums, and source bindings without plaintext credentials.

## 9. Project and Git semantics

### 9.1 Project state

A Project remains usable when:

- it was created blank and has no source;
- GitHub credentials expire;
- a repository is renamed, deleted, transferred, or access is revoked;
- the user disconnects the source.

Recommended source states:

```text
unbound
importing
clean
local_changes
remote_ahead
local_ahead
diverged
conflicted
auth_required
remote_unavailable
sync_error
```

These states describe the relation between `current_snapshot`, `source_base_oid`, and the latest fetched remote OID. They are not inferred from a long-lived working tree.

### 9.2 Creation paths

| Path | Semantics |
|---|---|
| Blank | Empty Project, unbound source |
| Template | Copy template content, sever source history, unbound until published |
| Archive | Safely extract into a new initial snapshot; no remote binding |
| GitHub import | Fetch selected branch head, materialize it as the initial snapshot, and record stable repository ID + branch + source OID |

GitHub import should be shallow by default. Remote history and clone caches are operational data, not automatically retained Project content.

### 9.3 Synchronization

- **Fetch** is read-only. It may run manually or periodically and only updates source metadata/status.
- If remote advanced and local state did not, the UI may offer **Apply update**, producing a new reviewed snapshot.
- If local and remote both changed, prepare a three-way merge in an isolated sync worker and surface the result as a change set.
- The sync worker transiently fetches enough remote history to resolve `source_base_oid`, the current remote OID, and their merge base. A shallow fetch may deepen as needed; if the base is no longer reachable (for example after a remote history rewrite), Sherpa stops with an explicit source-history state rather than guessing ancestry.
- Conflicts block apply/push until resolved.
- "Replace local with remote" is a distinct destructive action with explicit confirmation; it is never the default meaning of Sync.
- Source operations use stable repository IDs and expected OIDs, not display URLs alone.

### 9.4 External writes and credentials

- Push, remote branch creation, repository creation, fork creation, and pull-request creation are external writes and use ADR-020 approval envelopes.
- A push includes repository, branch, expected remote OID, commit/diff summary, and `force=false`.
- Project snapshots are not silently treated as Git commits. For push, the sync service re-fetches the expected remote OID, materializes the reviewed Project change set, creates a commit whose parent is that exact OID, and pushes it with lease/expected-ref semantics. If the parent cannot be reconstructed or the remote ref moved, the push does not start.
- If the remote moved or the push result is unknown, stop and reconcile the remote ref; never blind-retry.
- Use GitHub App installation tokens or another short-lived scoped credential.
- The sync worker may decrypt credentials; the sandbox, Project tree, prompt, logs, and tool results may not receive them.
- Branch protection and rulesets are surfaced as product guidance, not raw Git errors ([GitHub protected branches](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches)).

## 10. Project Chat and sandbox durability semantics

### 10.1 Authoritative state hierarchy

```text
Project head snapshot          durable, saved, user-visible
        |
        v
Task working copy              durable pending overlay, spans chat turns
        |
        v materialize / rebuild
Scratch volume                 node-local cache, replaceable
        |
        v mount
Sandbox container              ephemeral process boundary, optional warm TTL
```

The system maintains the task working copy, not a particular container. A running container improves latency; it is never required for correctness or recovery.

### 10.2 Project-bound Chat lifecycle

1. Create a General chat (`project_id = null`) or a Project Chat bound to one immutable `project_id`.
2. The Project Chat initially reads from the current Project head. The first mutating action atomically creates a task working copy with `base_snapshot_id = current_snapshot_id`.
3. Acquire the working copy's single-writer lease/fence token and materialize `base snapshot + persisted overlay` into a fresh scratch volume.
4. Launch a hardened sandbox with only that scratch volume mounted read-write. W3 intentionally supersedes ADR-025's present "no workspace mount" exclusion while preserving its network, identity, capability, rootfs, and resource limits.
5. After each bounded tool batch, before asking/waiting for the user, and before teardown, capture the scratch delta and persist it into the working copy. A run is not reported as successfully durable until this boundary completes.
6. A container may remain warm for a short configurable idle TTL (an initial 10–20 minute hypothesis) and reuse the scratch cache. Expiry, crash, worker restart, or host loss simply triggers rematerialization from the durable working copy.
7. Continue turns reuse the same working copy. Save/checkpoint/discard closes or advances it; switching Project always creates a new chat and working copy.

OpenHands explicitly warns that any read-write mount can be modified by the agent ([Docker sandbox](https://docs.openhands.dev/openhands/usage/sandboxes/docker)); therefore Sherpa mounts a disposable scratch copy, never its source of truth.

### 10.3 Persistence boundary

| Class | Examples | Rule |
|---|---|---|
| Durable authority | base snapshot, working-copy overlay/manifest, file operations, quota reservation, test/action receipts, run events | Persist in Postgres/MinIO/event journal; sufficient to rebuild |
| Rebuildable cache | materialized scratch tree, dependency/package cache, prepared image keyed by environment/lockfiles | Bounded, evictable, never the only copy |
| Never persisted as workspace state | PIDs, RAM, sockets, open shell sessions, temporary rootfs, model/Git/storage credentials | Lost on teardown; secrets never enter Project files/checkpoints |

Long-running dev servers, hosted previews, and process resurrection are later capabilities. The first increment persists file state and receipts only.

### 10.4 Concurrency and recovery

- One working copy has one active writer lease/fence token. A stale sandbox cannot publish a later overlay.
- Multiple Project Chats for the same Project receive isolated working copies.
- Save uses compare-and-set against `base_snapshot_id/current_snapshot_id`; a moved Project head produces a conflict/review flow.
- Container or node loss resumes from the last persisted working-copy boundary. Unpersisted scratch writes are not presented as completed work.
- Package caches and warm containers may improve recovery time but cannot change the resulting file tree or permission boundary.

### 10.5 Change set out

After each execution boundary:

- compare scratch state with the persisted working copy and saved base;
- reject path escape, unsafe symlinks, devices, sockets, and `.git` credential/config leakage;
- bound changed-file count, changed bytes, total artifact bytes, and diff/output size;
- reserve quota for proposed new durable bytes;
- persist the working-copy overlay and project change-set projection;
- stop or warm-idle the container according to policy.

### 10.6 User actions

| Action | Effect |
|---|---|
| Continue | Keep the durable working copy open for later chat turns; container reuse is optional |
| Save selected | Apply reviewed working-copy operations and advance Project head to a new snapshot |
| Save and checkpoint | Save, then pin the resulting snapshot with a name/note; the chat may start a fresh working copy from the new head |
| Keep artifact | Retain selected output under Project artifacts and charge durable quota |
| Export to Drive | Copy a retained artifact/file into Drive through the Drive service |
| Discard | Delete the working-copy overlay/staged bytes and release the reservation; Project head is unchanged |

If the Project head changed after the sandbox started, Save must fail with a conflict or produce a newly reviewed rebase/merge change set. It must never apply against the wrong base.

Gitpod/Ona snapshots demonstrate the correct credential boundary: file state is captured, while environment variables, authentication, Git credentials, and running processes are not ([workspace snapshots](https://ona.com/docs/classic/user/configure/workspaces/collaboration)).

### 10.7 Initial executor boundary

- Sherpa core remains the orchestrator and uses built-in Project read/write, sandbox command, and test tools.
- No Copilot CLI, Claude Code, or other specialist coding-agent process is embedded in the initial sandbox.
- The first sandbox remains network-disabled; it receives no model/provider credential.
- The initial environment is an approved predeclared runtime image. It does not fetch packages or resolve missing dependencies. A command requiring unavailable dependencies returns an explicit environment error; it never silently enables network access.
- Initial demonstrations and acceptance checks must be dependency-free/offline or operate only on dependencies already retained in the Project snapshot.
- A future `CodingExecutor` adapter may delegate a bounded task only after the native Project Chat workflow is validated. That adapter must still return structured events, diffs, test receipts, and artifacts into the same working-copy/save boundary; it cannot write Project head or approve Git actions directly.

## 11. Security and lifecycle requirements

### Paths and names

- Normalize separators and Unicode consistently.
- Reject absolute paths, `..`, NUL, device paths, and reserved names.
- Preserve display case but detect collisions when importing from case-insensitive sources.
- Enforce maximum depth, component length, total path length, and sibling count.
- Drive does not support symlinks.
- Project symlinks, if enabled later, must be relative and remain inside the Project root after resolution.

### Upload and archive safety

- Do not trust file extension or client `Content-Type`.
- Use server-generated object keys and sanitized download names.
- Bound declared size, streamed size, file count, expanded archive size, expansion ratio, nesting depth, CPU, memory, and wall time.
- Reject absolute/archive traversal paths, devices, FIFOs, hard links, and escaping symlinks.
- Extract in an isolated staging environment, never directly into canonical Drive/Project state.
- Provide a scanner interface and explicit `pending | clean | rejected | unavailable` states. A self-hosted profile may disable malware scanning only with an explicit operator policy; preview/extraction paths must still fail closed when their required parser/scanner is unavailable.

OWASP recommends generated filenames, size limits, storage outside the web root, authorization, type validation, and antivirus/sandbox scanning where available ([File Upload Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/File_Upload_Cheat_Sheet.html)).

### Download and preview

- Authorize every download against current tenant/user ownership.
- Prefer short-lived, single-object signed URLs or authenticated streaming; never expose a bucket or reusable object key.
- Sanitize `Content-Disposition`.
- Serve active content conservatively; previews run in an isolated origin or are downloaded rather than rendered inline.

### Encryption at rest

- The first increment does not add application-level per-blob AEAD.
- A supported deployment must configure storage encryption below Sherpa (for example an encrypted MinIO data volume or MinIO server-side encryption/KMS) and must report that posture truthfully in readiness/docs.
- If storage encryption is not configured, the product must not claim that Drive/Project bytes are encrypted at rest.
- Application-level per-blob encryption, tenant-specific data keys, and key rotation require a separate ADR because search, preview, deduplication, backup, and sandbox materialization all depend on the decrypt boundary.

### Isolation and secrets

- No client-visible response or timing behavior reveals whether another user/tenant already stores identical content.
- Physical deduplication is tenant-scoped at most.
- Project source credentials stay in the vault/connector boundary.
- Sandboxes receive no platform credentials.
- External Git writes retain invocation/idempotency records before execution and follow `effect_unknown` reconciliation.

## 12. Service, API, and Tool surface

ADR-023 remains binding: business rules live in services; REST and Tool adapters are thin.

### Services

```text
app/services/storage.py
app/services/drive.py
app/services/projects.py
app/services/project_sources.py
app/services/project_changes.py
```

### Candidate REST surface

```text
GET    /storage/usage
POST   /storage/uploads
PUT    /storage/uploads/{id}/parts/{part}
POST   /storage/uploads/{id}/complete
DELETE /storage/uploads/{id}

GET    /drive/nodes
POST   /drive/folders
PATCH  /drive/nodes/{id}
DELETE /drive/nodes/{id}              # trash
POST   /drive/nodes/{id}/restore
DELETE /drive/nodes/{id}/purge
GET    /drive/files/{id}/versions
POST   /drive/files/{id}/versions/{version_id}/restore

GET    /projects
POST   /projects
POST   /projects/imports
GET    /projects/{id}
GET    /projects/{id}/tree
POST   /projects/{id}/chats             # creates a new Project-bound session
GET    /sessions/{id}/project-context
POST   /projects/{id}/fetch
POST   /projects/{id}/apply-remote
POST   /projects/{id}/sandbox-runs
GET    /projects/{id}/working-copies/{working_copy_id}
GET    /projects/{id}/change-sets/{change_set_id}
POST   /projects/{id}/change-sets/{change_set_id}/apply
POST   /projects/{id}/change-sets/{change_set_id}/discard
POST   /projects/{id}/push             # approval-gated
```

Exact shapes belong in the frozen API contract before implementation.

### Candidate tools

- Drive: `drive_list`, `drive_search`, `drive_create_folder`, `drive_write`, `drive_move`, `drive_trash`, `drive_restore`, `drive_list_versions`, `drive_restore_version`.
- Projects: `project_list`, `project_create`, `project_import`, `project_tree`, `project_read`, `project_write`, `project_fetch`, `project_run`, `project_review_changes`, `project_save`, `project_checkpoint`, `project_discard`.
- `project_run` operates on the current Project Chat's working copy through Sherpa's built-in executor. There is no initial `delegate_coding_agent` tool.
- External Git: `project_push` and future PR/repository actions are `ask`.
- Permanent Drive purge remains human-only or `ask`.

Binary upload from a human may use upload sessions; agent-generated text/bounded artifacts call the same reservation and version services directly.

## 13. Static prototype

[`workspace-product-prototype/index.html`](workspace-product-prototype/index.html) covers:

1. Workspace overview and storage meter;
2. Projects library;
3. New/import Project flow;
4. Project detail with file tree and source status;
5. General Chat with no Project capabilities and an explicit transition to a new Project Chat;
6. Project-bound Chat with visible Project identity, durable task working copy, sandbox/cache state, built-in executor, and review handoff;
7. Drive browser with preview, versions, trash, and bulk actions;
8. quota warning/full and storage management;
9. Git import/source credential, divergence/conflict, and push-approval states;
10. sandbox change review with Continue/Save/checkpoint/discard;
11. desktop and mobile layouts.

The prototype is future-state research only. It does not alter production navigation or imply implementation approval.

## 14. Measurable acceptance criteria

### Product and UX

- In owner review, Drive, Project, Source, Sandbox, Change set, and Checkpoint each have one unambiguous meaning.
- The main human flows complete without a hidden technical ID: upload/restore a Drive file; create/import a Project; review/save/discard sandbox changes; understand a Git conflict; free storage.
- General Chat and Project Chat are visibly distinct. A Project Chat displays its bound Project, and selecting another Project starts a new chat rather than changing the current transcript in-place.
- Every shipped page works at 390 px with no horizontal scrolling and keyboard-visible focus.
- No navigation item is shipped before its underlying service, UI controls, tools, and truthful states exist.

### Quota and storage correctness

- Under concurrent reservations, user, tenant, and deployment counters never exceed their ceilings.
- Creating 100 unchanged checkpoints does not increase reported usage.
- Reverting to already-retained content does not increase usage.
- Trash and retained history remain visible in quota categories.
- Crash injection at every Stage/Verify/Commit/Cleanup boundary yields either a committed reference to verified bytes or a reclaimable orphan; never a success-shaped missing object.
- Quota reconciliation exactly rebuilds cached counters from retained owner/blob references.
- Object GC is idempotent and never deletes a blob with a live reference.
- A dedup commit racing a queued GC cannot reuse a retiring blob, and GC's final compare-and-set cannot delete a re-referenced/new-generation blob.

### Isolation and security

- Cross-tenant and cross-user access tests cover list, read, move, restore, version, Project tree, change set, and signed download.
- Duplicate-content uploads expose no cross-owner existence signal.
- Archive fixtures cover traversal, absolute paths, symlink escape, devices, excessive entries, nested archives, expansion ratio, and interrupted extraction.
- Unknown-size import/archive/output operations cannot start unless their configured maximum reservation fits; unused reservation is released at commit.
- A network-disabled initial sandbox reports missing dependencies truthfully and never attempts an undeclared package install.
- Sandboxes can access only the selected scratch Project tree and receive no Drive, other Project, credential, or spill path.
- Deployment readiness reports the configured storage-at-rest encryption posture and never overclaims it.

### Project and Git fidelity

- Project state survives source disconnect or remote deletion.
- Fetch never changes Project head.
- Clean fast-forward, local-only, remote-only, diverged, conflicted, auth-required, and remote-unavailable states are deterministic.
- A push is rejected if expected remote OID changed.
- `effect_unknown` push outcome stops and reconciles the remote ref instead of retrying blindly.
- No force push exists in the first implementation.

### Sandbox durability

- Discard leaves Project head byte-identical to the base snapshot.
- Save applies only reviewed operations and creates a new snapshot.
- A concurrent Project-head change blocks stale Save.
- Killing the warm container or losing the worker/node does not lose the last persisted working-copy boundary; the next run rematerializes an equivalent scratch tree.
- Two Project Chats targeting the same Project cannot observe or mutate each other's pending working copies.
- A stale sandbox fence token cannot publish an overlay.
- Working-copy idle expiry and reservation release are one atomic lifecycle transition; an open working copy cannot retain bytes after its reservation is independently swept.
- Credentials and running processes are absent from checkpoints.
- Artifact retention consumes quota only after explicit Keep/Export.
- The initial executor uses only Sherpa built-in tools; no embedded coding-agent process or model credential is present in the sandbox.

### Recovery

- A coordinated Postgres + MinIO backup restores a sample Workspace with Drive tree, versions, trash, Projects, snapshots, source metadata, and quota totals.
- Post-restore reconciliation detects intentionally removed/corrupted test objects and surfaces explicit integrity states.

## 15. Proposed ADR and frozen-contract changes

Do not implement until these are reviewed and frozen.

### Proposed ADRs

1. **ADR-029 — Workspace product model**
   - Personal workspace is the ownership umbrella.
   - Projects and Drive are distinct sibling products.
   - Project is durable Sherpa state; Source is optional; Sandbox is ephemeral.
   - Chat is General or immutably Project-bound; changing Project creates a new chat.

2. **ADR-030 — Durable workspace storage and quota**
   - Postgres canonical metadata + immutable tenant-scoped MinIO blobs.
   - unique durable-byte accounting per owner;
   - user, tenant, and deployment ceilings;
   - reserve/stage/verify/commit/outbox/GC lifecycle;
   - versions, trash, reconciliation, and backup guarantees.

3. **ADR-031 — Project source and sandbox durability**
   - remote Git is a binding, not source of truth;
   - fetch does not mutate Project head;
   - external writes are approval-gated and OID-checked;
   - immutable snapshot/COW scratch execution;
   - durable task working copy is authoritative while scratch volume/container are rebuildable caches;
   - initial execution uses Sherpa built-in tools; embedded coding agents are deferred;
   - explicit reviewed Save/checkpoint/discard.

### Amend existing ADRs

- **ADR-007 / ADR-025:** replace the old "persistent workspace volume" shorthand with immutable Project snapshot materialization and an explicit save boundary. Canonical storage is never mounted read-write.
- **ADR-012:** clarify that MinIO stores immutable workspace blobs while Postgres owns namespace, quota, references, and snapshots.
- **ADR-023:** add Drive and Project capability rows, including UI parity and external Git approval classifications.

### Frozen contracts

- `contracts/data-model.md`: add storage, Drive, Project, source, snapshot, working-copy, change-set, session Project binding, and quota tables/invariants.
- `contracts/events-and-effects.md`: add upload/import/save/purge/fetch/push events, idempotency, outbox, and `effect_unknown` reconciliation.
- `contracts/api.md`: add REST and Tool schemas, Project Chat creation/context, working-copy state, upload sessions, pagination, conflict payloads, and signed-download rules.
- `contracts/config-and-secrets.md`: add quota/retention/upload/import/archive/scanner/source/sandbox settings, working-copy idle retention, warm-container/scratch-cache bounds, and Git credential boundaries.
- `11-agent-tool-surface.md`: add UI + Tool parity matrix for Drive and Projects.

## 16. Owner decision gate

The research task is complete when the owner accepts or changes these eight decisions:

1. **Names/navigation:** Personal workspace umbrella; Projects + Drive siblings; Sandbox execution.
2. **Project Chat (owner-confirmed 2026-07-22):** General or bound to exactly one Project; switching Project starts a new chat.
3. **Boundary:** Projects are not Drive folders; remote Git is optional source.
4. **Quota:** 5 GiB configurable personal default plus tenant and deployment ceilings.
5. **Accounting:** per-owner unique durable blobs; history/trash count; ephemeral output does not count until retained.
6. **Durability (owner-confirmed 2026-07-22):** persist task working copy; scratch volume/container are rebuildable; explicit reviewed Save.
7. **Initial executor (owner-confirmed 2026-07-22):** Sherpa built-in tools only; embedded coding agents deferred.
8. **Implementation order:** W1 Workspace + Drive correctness before Projects, sandbox save, and Git sync.
