# R-WORKSPACE-PRODUCT — Personal drive and project workspaces

**Status:** research/design complete; awaiting owner decision. No implementation approved.

**Outcome:** [`workspace-product-report.md`](workspace-product-report.md) recommends Personal workspace as the umbrella, Projects + Drive as distinct sibling products, Postgres metadata + immutable tenant-scoped MinIO blobs, configurable 5 GiB personal quota with tenant/deployment ceilings, and snapshot-in/change-set-out sandbox durability. Static flows: [`workspace-product-prototype/index.html`](workspace-product-prototype/index.html).

## Product thesis

`Files` should remain a backend capability rather than the final user-facing product.

The product surface should likely be a top-level **Workspace** containing two distinct concepts:

1. **Drive / Personal storage** — general documents, uploads, generated artifacts, exports, and reusable assets.
2. **Projects** — structured development workspaces created in Sherpa or imported/synchronized from GitHub and similar sources.

The research must validate the naming and information architecture. "Workspace", "Projects", and "Drive" are hypotheses, not frozen labels.

## Current baseline

Milestone 2 implemented a deliberately thin primitive:

- per-user logical paths in Postgres;
- blobs in MinIO/S3-compatible storage under server-generated object keys;
- upload, list, read/download, overwrite, and delete;
- a 10 MiB per-file limit;
- no account quota, folders as first-class records, trash, sharing, project model, repository sync, storage ledger, or durable sandbox mount.

This is a useful storage seam, but not yet a complete personal cloud workspace.

## Research questions

### Product and competitive research

1. How do Google Drive, Dropbox, OneDrive, Notion, GitHub Codespaces, Replit, Gitpod, and comparable agent/development products separate files, projects, repositories, artifacts, and environments?
2. Which mental model is clearest for Sherpa: one Workspace with Projects + Drive, separate top-level products, or Projects as folders with richer metadata?
3. Which capabilities belong in the first useful increment versus later collaboration and hosted-development stages?

### Storage and quota

4. Should the default allowance be 5 GB per user, per tenant, or configurable by deployment? What counts toward it?
5. How are uploads, repository working trees, generated artifacts, snapshots, file versions, trash, and retained build outputs accounted for?
6. Do content-addressed storage, deduplication, compression, or lifecycle policies materially reduce cost without making accounting confusing?
7. How are quota reservations made safely before large uploads, imports, archive extraction, or agent-generated output?

### Projects and source synchronization

8. What is a Project entity: metadata around a directory tree, a Git repository, a workspace snapshot, or a composition of these?
9. Which creation paths ship first: blank project, template, uploaded archive, GitHub import, or connector sync?
10. For Git-backed projects, what are the clone/fetch/pull/push, branch, conflict, credentials, and approval semantics?
11. How are remote repository state and Sherpa's durable workspace reconciled without silently overwriting user changes?

### Development and agent execution

12. How does a project mount into a sandbox while preserving the ADR-025 isolation boundary?
13. Which data is ephemeral during execution and which changes become durable only after an explicit save/checkpoint?
14. How are diffs, generated files, build artifacts, logs, and previews reviewed before consuming durable storage?
15. How do UI and agent tools expose the same project/drive capabilities through the ADR-023 service + dual-adapter pattern?

### Security and lifecycle

16. How are paths, object keys, signed downloads, archive extraction, malware/content scanning, encryption, retention, trash, and permanent deletion handled?
17. What tenant/user ownership model works now while preserving future team projects, shared drives, roles, and transfer of ownership?
18. What backup/restore and object/metadata reconciliation guarantees are required?

## Target product effect

- Every user has a clearly metered private storage allowance; 5 GB is the initial hypothesis.
- The UI explains used, reserved, trashed, and available capacity.
- Users can manage ordinary files through a familiar drive-like browser.
- Users can create a project, import one from GitHub, or synchronize an existing repository.
- Projects have a file tree, source status, recent activity, storage usage, and development entry point.
- Sandboxes can work on a project without gaining access to unrelated user data.
- Changes and artifacts can be reviewed, saved, versioned, or discarded explicitly.
- The agent can perform every safe UI capability through the same service layer, with external Git writes approval-gated.

## Architecture options to compare

1. **Logical tree over object storage** — extend the current file table with folders, quota ledger, versions, and project metadata.
2. **Split Drive + Git project stores** — object storage for drive files; Git-native storage/working trees for projects; unified quota/accounting above both.
3. **Content-addressed workspace store** — immutable blobs/trees/snapshots with derived working views, Git integration, and deduplication.

The recommendation must identify the canonical metadata store, blob layout, transactional boundaries, quota reservation model, and reconciliation jobs.

## Static product designs to produce

1. Workspace home with storage meter and recent projects/files.
2. Projects library and "new/import project" flow.
3. Project detail with file tree, Git/source state, activity, and development actions.
4. Personal Drive with folders, upload, search, sort, preview, versions, trash, and bulk actions.
5. Quota warning/full states and storage-management flow.
6. GitHub import/sync conflict and credential/approval states.
7. Sandbox change review: save, checkpoint, discard, and artifact retention.
8. Desktop and mobile variants.

## Deliverables

1. Competitor evidence table with primary-source citations.
2. Product taxonomy and naming recommendation.
3. First-increment scope and later capability map.
4. Architecture comparison and recommended data/storage model.
5. Quota/accounting, sync, versioning, trash, and sandbox durability semantics.
6. Static HTML prototypes before changes to the production UI.
7. Measurable acceptance criteria and proposed ADR/frozen-contract changes.

## Non-goals

- Do not redesign the production Files page or change navigation during research.
- Do not implement GitHub synchronization, collaborative sharing, hosted previews, or new storage infrastructure yet.
- Do not mount the entire personal drive into code-execution sandboxes.
- Do not hard-code 5 GB until deployment policy and accounting semantics are reviewed.

## Exit criteria

Research is complete only when the owner can decide:

1. the user-facing names and navigation;
2. the boundary between Drive and Projects;
3. the quota owner, default, and accounting rules;
4. the durable/ephemeral boundary for development;
5. Git import and synchronization semantics;
6. the architecture and first implementation increment.
