# design-workspace — Projects (Workspace W2a) static UI

Static, self-contained mockup for the **W2a Projects** slice, landing the ADR-037
Workspace product model on the production **Quiet Work** design system
([`../design-refined/README.md`](../design-refined/README.md)). Open `index.html`
directly in a browser (no build). Radio-driven tabs switch the four surfaces; pure
HTML/CSS, no JS logic (a couple of inline `label for=` links jump between tabs).

> **Design/contract-first only (ADR-037).** This is a static design draft — the
> capability is **not implemented** and production Projects navigation is **not
> exposed**. Do not read the mock as shipped behaviour. Implementation starts only
> after owner review of ADR-037 + the frozen contract deltas.

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

**Notes:** SPA route is `/work/projects` (avoids the REST `/projects` proxy prefix).
Responsive: desktop sidebar + a single-column layout under 900 px; verified at 390 px
with no horizontal scroll. Storage reuses ADR-030's immutable, content-addressed,
ref-counted blobs + quota ledger. Non-goals (GitHub / sandbox / Git writes) each land in
their own later ADR. See ADR-037 and the contract deltas (`docs/contracts/`).
