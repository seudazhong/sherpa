# design-workspace — Projects (Workspace W2a + W2b + W3) static UI

Static, self-contained mockups for the **Projects** slice, landing the ADR-037 /
ADR-038 / ADR-040 Workspace product model on the production **Quiet Work** design system
([`../design-refined/README.md`](../design-refined/README.md)). Open the `.html`
files directly in a browser (no build).

- [`index.html`](index.html) — **W2a** (blank / template / archive import; ADR-037).
- [`github-import.html`](github-import.html) — **W2b** (GitHub one-time import; ADR-038).
- [`w3-change-review.html`](w3-change-review.html) — **W3** (task working copy + one-time
  scratch-copy sandbox + change review; ADR-040 product/data + ADR-039 isolation).

> **Design/contract-first only (ADR-037 / ADR-038 / ADR-040 / ADR-039).** These are static design
> drafts — the capability is **not implemented** and production Projects navigation is **not
> exposed** (W2a/W2b are shipped; **W3 is design/contract-first only**). Do not read the mocks as
> shipped behaviour. W3 implementation starts only after owner review of ADR-039 (isolation) +
> ADR-040 (product/data/tool/lifecycle) + the ADR-025 revision + the frozen contract deltas.

**Owner-approved decisions (2026-07-27):**

- **Workspace is the umbrella entry**; **Projects and Drive are siblings** inside it.
- Implementation order is **W2a → W2b → W3 → W4**.
- **W2a = blank / template / archive import (no GitHub).** GitHub one-time import is
  **W2b**.
- **W3** lets the sandbox mount **only a one-time scratch copy**, never the project
  source of truth; the formal **ADR-025 revision** happens **before W3 starts**, after
  an isolated security review.
- Before W3, the **`docker.sock` / multi-user isolation** boundary must be reviewed and
  hardened.

**Surfaces (W2a):**

1. **Projects 列表** — project library with storage metrics, per-project type tag
   (blank/template/archive), lifecycle pill (ready / importing), all `unbound` (no
   source), Open action.
2. **新建项目** — three creation paths (archive upload selected, blank, template);
   **GitHub shown disabled → W2b**; archive-upload form with reservation + isolated
   bounded-extraction safety notes.
3. **项目详情** — read-only file tree, snapshots (import/checkpoint), activity, storage
   facts, source = unbound; explicit note that edit/test = W3.
4. **Open in Chat** — a Project-bound chat (`sessions.project_id`, immutable after the
   first message) that **reads/discusses only** — no working copy, no sandbox, no change
   set (those are W3). Switching Project starts a new chat.

**Surfaces (W2b — `github-import.html`, ADR-038):** GitHub **one-time** import (read-only).

1. **① 连接 GitHub** — connection status (`GET/POST/DELETE /connections/github`): auth
   kind (fine-grained PAT `contents:read`, extensible to a GitHub App installation
   token), account login, scopes, revoke; the token is AEAD-sealed in the vault and
   **never** returned or shown — connect-new form for the unconnected state.
2. **② 选择 repo / ref** — repo picker (stable numeric repo id) + ref selector across
   **branch / tag / commit** (all three first-version); the ref resolves to a concrete
   commit **OID** and is pinned; server-side proxy so the token never reaches the client.
3. **③ 导入进度** — the durable `project_import_jobs (create_kind=github)` stages
   (resolve ref→OID · bounded tarball fetch · W2a safe-expand · immutable snapshot ·
   atomic activate) **and** a failed/retry state (`termination_reason`, no snapshot on
   failure, idempotent retry; read-only fetch ⇒ no `effect_unknown`).
4. **④ 成功 · 来源元数据** — imported project detail: provenance (provider / repo id /
   ref / **source OID** / imported-at / connection) with an explicit **"remote is not the
   source of truth"** note, read-only tree, and the initial `import` snapshot. No credential
   ever appears in project content.

**Notes:** SPA route is `/work/projects` (avoids the REST `/projects` proxy prefix). The
W2b flow reuses that route; **no W2b production navigation is exposed** and the capability
matrix UI cells stay ⬜ until implementation lands. GitHub import is **human-only** (not an
agent tool — it crosses the credential boundary and pulls untrusted external content).

**Surfaces (W3 — `w3-change-review.html`, ADR-040 + ADR-039):** Project-bound Chat working
copy + one-time scratch-copy sandbox + change review. **Design/contract-first only — not
implemented, no real sandbox mount, no W3 navigation exposed.**

1. **Project Chat（执行态）** — a Project-bound chat whose first mutating action opens a
   **durable task working copy**; the sandbox-run status card shows the lifecycle (acquire
   lease + fence → materialize a **one-time scratch copy** → hardened offline execution →
   persist overlay + change-set boundary), `project_run` built-in file/edit/run/test tool
   calls, and an explicit **`environment_missing_dependencies`** (offline; no package install).
2. **变更评审（Change Review）** — added/modified/deleted counts + artifacts + an explicit
   **truncated/partial** note when bounds are hit; a file list with per-file **selection
   checkboxes**, a unified diff, a binary-file note, and the artifacts section (ephemeral →
   **Keep** / **Export → Drive**, charges quota only when kept). Action bar: **Save selected /
   Save + checkpoint / Discard** (Save is a **human review** action — not an agent tool).
3. **Stale / 冲突** — a moved Project head → **`409 head_moved`** (`head_generation` CAS); the
   working copy is preserved, and the user reviews a **rebased** change set. Explains the
   **single-writer lease + fence** (a stale sandbox can never publish an overlay) and isolated
   per-chat working copies.
4. **隔离与真相源** — the authoritative-state hierarchy (snapshot head → working-copy overlay →
   scratch → container), the **sandbox mount boundary** (only the disposable scratch; **never**
   the snapshot / blob store / credentials / other Project / Drive / `docker.sock`), the ADR-025
   hardening retained, idempotency/recovery facts, and the **ADR-039 do-not-ship conditions**
   for multi-user (gVisor / microVM + no shared socket + per-tenant scratch/egress/quota).

**Notes:** SPA route is `/work/projects` (avoids the REST `/projects` proxy prefix).
Responsive: desktop sidebar + a single-column layout under 900 px; verified at 390 px
with no horizontal scroll. Storage reuses ADR-030's immutable, content-addressed,
ref-counted blobs + quota ledger. Non-goals (GitHub / sandbox / Git writes) each land in
their own later ADR. See ADR-037 and the contract deltas (`docs/contracts/`).
