# Design Review — Consolidated Findings & Action List

> **Status:** Pre-P0 design review complete. This document consolidates a two-round review by three roles (PM, UI/UX Designer, Architect) plus UI mockups. It is the **decision-forcing summary**: read this first, then the individual reviews for depth.
>
> **Bottom line:** The design is sound, but the reviews converged strongly on **reframing v1**. Do **not** start P0 coding until the human confirms the decisions in §5 and the ADR amendments in §4 are applied.

## Review artifacts

| Round | Role | Document |
|---|---|---|
| 1 · independent | Product Manager | [`pm-review.md`](pm-review.md) (67 KB) |
| 1 · independent | UI/UX Designer | [`ui-design-review.md`](ui-design-review.md) (28 KB) + mockups |
| 1 · independent | Architect | [`architect-review.md`](architect-review.md) (47 KB) |
| 2 · cross | PM → UI+Arch | [`cross-pm.md`](cross-pm.md) |
| 2 · cross | UI → PM+Arch | [`cross-ui.md`](cross-ui.md) |
| 2 · cross | Arch → PM+UI | [`cross-arch.md`](cross-arch.md) |
| mockups | UI/UX Designer | [`../design/index.html`](../design/index.html) · dashboard · chat-session · todo-board · connectors |

---

## 1. The headline: a reframed v1 rule

All three roles independently arrived at the same reframing:

> **Pay the full security & durability cost for every capability v1 actually ships — and remove risky capabilities rather than shipping weak versions of them.**

The original roadmap (P0 minimal loop → sandbox → connectors → …) was **technically ordered**; the reviews re-cut it to be **value- and risk-ordered**. The result is a *narrower* product on a *production-shaped* foundation.

**Sherpa v1 = a self-hosted, single-owner "Gmail → Action" assistant.**
Connect one Gmail account read-only → durably sync & analyze → produce **private candidate todos** with source + uncertainty → user accepts/edits/dismisses → opt-in Web/digest reminders with visible delivery state → pause/disconnect/export/delete.

This is the flagship "analyze email → generate todo → notify" pipeline (docs 06) as the **whole** product, not one feature among twelve.

---

## 2. Where all three agree

### 2.1 Keep now — even though v1 is narrow (these are one-way doors)
The narrowing does **not** justify weakening the foundation. Retrofitting any of these after real user data exists is dangerous or impossible:

- **Tenant isolation from migration #1** — `tenant_id` on every table, composite FKs, Postgres **RLS** (`ENABLE/FORCE`, least-privileged app role, `SET LOCAL app.tenant_id`), tenant-filtered search. Ship *one personal workspace*, but store it multi-tenant-shaped.
- **Durable event journal as recovery source** — a PostgreSQL append-only event journal + **transactional outbox** is the source of truth for recovery/replay/streaming; Redis **Streams** *accelerate* delivery; Redis **pub/sub is never correctness-critical**. (This overturns the implicit "pub/sub" reading of ADR-005.)
- **Effect / idempotency semantics** — persist invocation identity *before* every side effect; classify retryability; idempotency keys; **stop for reconciliation on unknown outcomes** ("never blindly retry unknown effects").
- **Identity/UMO key** — internal UUID + unique `(tenant_id, channel, channel_installation_id, scope_type, external_scope_id)`; group actor identity separate from session identity; raw provider ID retained only for audit.
- **Candidate/source provenance** — stable connector item/revision → extraction version → generation → candidate → accepted-todo links, from the very first candidate.
- **Secret cryptography** — per-record AEAD, rotatable KEK, immediate encryption at the OAuth callback, connector-only decrypt authority, tested log redaction.
- **Reminder firing + delivery** — durable unique firings + outbox + at-least-once workers + idempotent delivery; **replaces global at-most-once** (see ADR-011 amendment). Never silently drop a firing.
- **Approval envelope (frozen, not built)** — versioned semantic payload bound to exact args/tenant/policy/expiry/nonce/authorized-decider; first valid response wins. Freeze the contract now; build no renderer until the first gated action ships.
- **Audit vs debug boundary** — stable *redacted semantic receipts* in an append-only audit model, distinct from raw debug/telemetry events (which may carry secrets).

### 2.2 Cut from v1 (defer with a tracking issue, not an undocumented assumption)
Sandbox / code execution · QQ & other IM · agentic (inbound) email · GitHub · teams & shared memory · files/MinIO · memory/RAG/pgvector · multi-provider failover · external write actions · general cron · cross-channel approval **renderers** · token-by-token streaming polish.

Each is hidden from the UI (or clearly "invitation-only"), keeps its interface/contract reserved, and re-enters only behind an explicit go/no-go gate.

### 2.3 UX posture (all three)
- **Durable progress states over real-time theater** — queued/running/needs-attention/completed/failed + reconnect/catch-up + settled ≠ turn-end. A spinner must never imply liveness the backend can't guarantee.
- **Candidate-first autonomy** — connector content proposes *private* candidates only; formal todo needs accept/edit; notifications opt-in with quiet hours + caps; **no** memory/workspace read or external action driven by email content.
- **Hide raw chain-of-thought**; show a curated rationale (sources, rule/trigger, inferred fields + uncertainty, model/version, cost).
- **Scope always visible; every autonomous action inspectable, provenanced, and undoable where real.**
- **A shipped capability includes its whole safety lifecycle** — onboarding, consent, health, degraded state, retry/fallback, pause/revoke, audit, export/delete, accessible empty/error states.

---

## 3. Unresolved tensions (resolved by the reviews, pending human sign-off)

The full tension tables are in each cross-review (`cross-pm.md` §3, `cross-ui.md` §3, `cross-arch.md` §3). Headlines:

| Tension | Resolution the reviews converged on |
|---|---|
| Ship-fast MVP ↔ multi-tenant/RLS hardening | **Personal product, multi-tenant-shaped storage.** RLS + keys now; team behavior later. |
| Real-time UX ↔ durable event-log cost | Persist semantic transitions + canonical snapshots (Postgres outbox + Redis Streams); **defer token polish**. Pay the ~7–12 day foundation now. |
| Broad autonomy ↔ blast radius | Pipeline-specific **no-tool** structured extraction; candidate-first; opt-in notifications. Add authority only after measured trust. |
| Agentic email / QQ value ↔ operational fragility | Keep interfaces + approval schema; **cut from v1 & nav**; each returns behind an independent gate; never the sole approval/critical-reminder route. |
| Reminder urgency ↔ ADR-011 "never duplicate" | Per-job policy: digest prefers no-duplicate; critical reminder prefers eventual delivery. Both via durable firing + idempotent delivery; surface missed/unknown. |
| Cross-channel identical approval ↔ MVP scope | **Freeze the versioned contract now; build no renderer until an `ask` action enters scope.** |
| One provider speed ↔ portability | One provider behind a canonical adapter; persist model/provider/prompt versions + canonical IDs; defer failover. |

---

## 4. Action list — changes to make **before** P0 coding

### 4.1 ADR amendments (edit `docs/decisions.md`)

| ADR | Change |
|---|---|
| **ADR-003** (identity/UMO key) | Expand canonical key to include environment/tenant, channel installation, scope type, external scope; separate group-actor identity from session identity. |
| **ADR-005** (async runtime) | Make explicit: **PostgreSQL canonical run state + sequenced event journal + transactional outbox** is the recovery source; Redis Streams accelerate; pub/sub is never correctness-critical. |
| **ADR-006** (core loop, turn-granular) | Add: side effects need idempotency keys + effect classification; on `effect_unknown`, stop for reconciliation (turn-granular recovery can re-execute a tool). |
| **ADR-007** (sandbox) | Downgrade to **deferred**; gate `run_code` behind backend-neutral execution contract + gVisor/Firecracker (or dedicated nodes) for unrelated tenants + egress policy + aggregate quotas + threat review. |
| **ADR-009** (SAFE/FULL toolsets) | Replace origin-only SAFE with a pipeline-specific **`CONNECTOR_ANALYSIS` no-tool structured-extraction** capability for connector content. |
| **ADR-010** (autonomy boundary) | Candidate-first: connector content auto-creates **candidates only**; formal todos require confirmation; notifications opt-in + policy-gated. |
| **ADR-011** (at-most-once scheduling) | Replace with **durable unique firings + outbox + at-least-once + idempotent/reconciled delivery**, per-job missed/duplicate policy, explicit unknown state. |
| **ADR-012** (storage) | Note: MinIO/pgvector deferred out of v1; keep Postgres + Redis (+ web/worker) as the supported profile. |

### 4.2 New ADRs to author (design decisions the reviews surfaced)
- **ADR-015** — Tenant isolation model (RLS + composite keys + transaction-local tenant context).
- **ADR-016** — Event journal + outbox as recovery source of truth; projection/replay + SSE cursor/reset semantics.
- **ADR-017** — Candidate/source provenance chain (connector item/revision → extraction version → generation → candidate → accepted todo).
- **ADR-018** — Secret cryptography (per-record AEAD, KEK rotation, connector-only decrypt).
- **ADR-019** — Semantic approval envelope (frozen contract; renderers deferred).
- **ADR-020** — Audit-receipt vs debug-event boundary (redacted append-only audit projection).

### 4.3 "Freeze the contracts" work (pre-P0, from `cross-arch.md` §4)
Lock these schemas/contracts before writing code (each is a one-way door): first-release profile · identity/session keys · run/event contracts (states, sequences, IDs, versioned envelopes, bounded/redacted payloads, cursor/reset) · ingress + candidate contracts (item/revision + extraction-version uniqueness, candidate state machine, provenance, thread reconciliation, deletion) · effect contracts (invocation ID, idempotency key, effect class, succeeded/failed/effect_unknown) · worker model (recommend **arq** for P0) · Gmail credential/data boundaries (scopes, callback, AEAD, KEK, refresh serialization, retention/export/delete) · minimum audit/telemetry spine · supported deployment profile (health/readiness, migration ownership, backup/restore, RPO/RTO, graceful drain).

### 4.4 Roadmap change (edit `docs/09-roadmap.md`)
Replace P0–P6 component order with the reviews' **value/risk milestones**:
1. **Contract & value gate** — freeze the contracts above; 50–100-message redacted extraction benchmark; clickable Candidate Inbox prototype. Exit when extraction precision justifies real Gmail access.
2. **Personal Inbox-to-Action alpha** — Postgres+RLS, durable jobs/events/outbox, one provider, owner bootstrap, demo mode, Gmail read-only OAuth, scoped sync, candidate triage + provenance + dedupe, baseline cost/feedback, pause/disconnect/delete. Exit when real users reach a useful candidate and cross-tenant/effect-replay tests pass.
3. **Trustworthy follow-through (private beta)** — accepted todos, due/snooze, Web inbox, daily digest, quiet hours/caps, durable schedule firings, delivery reconciliation, connector health, export, backup/restore, a11y baseline. Exit on quality gates (candidate precision target, zero cross-tenant actions, no silent job failures, controlled notification complaints, weekly action value).

### 4.5 Mockup revisions (from `cross-ui.md` §4)
Existing mockups need rework for the narrowed v1: **dashboard** (lead with Gmail candidates + reminders; add sync freshness, catching-up, missed/failed reminder detail), **todo-board** (team Kanban → personal **Candidate Inbox** + todo list; source deep-link, rationale, edit-before-accept, dedupe merge), **connectors** (Gmail-only setup journey; remove active QQ/agentic-email onboarding), **chat-session** (keep as future concept; add durable run states; keep reasoning hidden), **index** (relabel P0 / later-exploration).
**New P0 screens revealed:** setup/first-value wizard · candidate detail/edit drawer · notification center & prefs · run/activity receipt ("what Sherpa did on my behalf") · scheduled-run/reminder failure detail · reconnect/catching-up states · data & connection controls (export/retention/delete) · (deferred) sandbox result view.

---

## 5. Decisions needed from the human (blocking P0)

From `cross-pm.md` §5 — only the project owner can decide these:

1. **Approve/reject the v1 promise:** self-hosted technical individual, Gmail→Action, single owner.
2. **Single-installation/single-owner, or must v1 support unrelated hosted tenants?** (The latter raises the KMS/RBAC/incident-response bar materially.)
3. **Gmail OAuth operating model:** project-managed verified app / per-deployment app / both.
4. **Gmail data retained:** metadata+snippets vs full body; history window; labels; attachments; deletion period.
5. **Confirm self-hosted BYOK** and pick the single initial model/provider.
6. **Keep a basic Web chat surface in v1, or cut entirely?**
7. **Approve candidate-first defaults** and the threshold/process for any later auto-promotion to formal todos.
8. **Notification defaults:** opt-in timing, digest time, quiet hours, cap, which reminders warrant eventual-delivery.
9. **Confirm explicit v1 exclusions:** sandbox, files, GitHub, QQ, agentic email, teams, external writes, general schedules, multi-provider failover.
10. **Set release quality thresholds** and recruit first ~10 users; decide who owns deployment/OAuth support.
11. **Is managed hosting a near-term commitment?** If yes, fund stronger KMS/HA/abuse/compliance work before launch.

---

## 6. Verdict

**Proceed to lock decisions, not to code yet.** The architecture's core instincts (bounded loop, stop-reason gate, layered-cache context, narrow-waist, durable-before-call, two-tier memory) held up well under review. The changes are about **scope and foundation contracts**, not a redesign: ship less, but build the tenant/event/effect/secret/provenance contracts as production code from day one. Once §5 is answered and §4.1–4.3 are applied, P0 (milestone 1: contract & value gate) can begin.
