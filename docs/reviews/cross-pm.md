# Sherpa cross-review — Product Manager

**Review date:** 2026-07-19  
**Purpose:** resolve the product implications of the architecture and UI/UX reviews before implementation. This document builds on, rather than repeats, `pm-review.md`.

## Product verdict

Proceed, but change the rule for v1:

> Pay the security and durability cost for every capability v1 actually ships; remove risky capabilities rather than shipping weak versions of them.

This means accepting several foundational architecture costs now while cutting substantially more surface area. The first release should not be marketed as a hardened collaborative runtime. It should be a **self-hosted, single-owner Inbox-to-Action product** built on a tenant-safe schema: read a bounded Gmail scope, produce private candidate todos, let the user accept/edit/dismiss them, and follow accepted work with controlled Web/digest reminders.

The architecture review is right about irreversible data and effect contracts. It is too broad where it asks v1 to implement infrastructure for capabilities we should not ship yet. The UI review is right about trust, durable progress, and hiding raw reasoning. Its navigation and screen set describe the eventual platform, not the first product.

## 1. Response to the Architect

### Security now versus ship fast

The choice is not “harden everything” versus “move fast.” It is:

1. harden the data and effects in the v1 path;
2. do not expose features whose safe implementation would delay the wedge;
3. freeze irreversible contracts now, but do not build unused subsystems.

On a rough single-engineer planning basis, RLS/tenant constraints, the event journal/outbox, effect idempotency, delivery state, and baseline audit/limits add roughly **3–5 engineering weeks** to a disposable chat demo. I accept that cost for a user-facing v1. It is offset by removing the sandbox, team collaboration, multi-channel approval renderers, agentic email, QQ, multi-provider failover, general schedules, and full files/memory systems. Relative to the current P0–P6 plan, this should shorten—not lengthen—time to validated value.

### Architecture recommendations and product decisions

| Architect recommendation | Product decision | Scope/timeline consequence |
|---|---|---|
| RLS, tenant-qualified keys, and composite tenant FKs from the first multi-tenant migration (`architect-review.md` §2) | **Accept for the first PostgreSQL schema.** Use PostgreSQL rather than a divergent SQLite product schema. A personal account still owns sensitive Gmail data, and retrofitting isolation is a high-cost one-way door. | Adds migration/repository tests early (roughly 3–5 days), but no team UI or broad RBAC in v1. |
| Qualified identity/session tuple (`architect-review.md` §2, “Identity/session isolation”) | **Accept.** Include installation/account namespace and actor attribution before external IDs exist. | Small early cost; avoids destructive re-keying when QQ/team support arrives. |
| PostgreSQL event journal + transactional outbox, with Redis Streams only as acceleration (`architect-review.md` §4) | **Accept a minimum version.** Persist run/job state, correctness events, final output, and reconnect cursors. Token deltas may be batched or reconstructed from an authoritative snapshot. Redis pub/sub may only wake clients. | Roughly 1–2 weeks, shared with the Designer’s durable-progress requirement. Do not build a full event-sourcing platform or persist every token. |
| Durable tool invocation and effect idempotency (`architect-review.md` §5) | **Accept for every mutating v1 effect.** Gmail deliveries, connector items, candidates, todos, schedule firings, and notifications need stable external/idempotency keys and `pending/succeeded/failed/unknown` outcomes. Never blindly replay an unknown external effect. | Roughly 3–5 days if designed into the schema; much more if retrofitted. A generic marketplace-grade tool reconciliation framework is deferred. |
| Replace loss-prone at-most-once scheduling (`architect-review.md` §6, “Scheduler”) | **Accept.** Use transactional firing/outbox records and at-least-once workers with idempotent effects. Product policy still varies: digest favors no duplicates; important reminders favor eventual delivery and visible reconciliation. | Roughly 3–5 days. This is required because reminders are in the v1 promise. |
| gVisor/stronger isolation by multi-tenant code execution (`architect-review.md` §3) | **Accept the security conclusion; reject it as a v1 cost.** Remove `run_code`, arbitrary shell, and tenant code execution from v1. Ordinary Docker is local/trusted development only. gVisor becomes a release gate for the later code-execution milestone; Firecracker remains optional. | Saves several weeks and a major operational surface. No silent Docker fallback when hardened execution is later promised. |
| Durable, versioned cross-surface approval lifecycle (`architect-review.md` §1 ADR-008; §7) | **Freeze the semantic contract now; defer implementation.** v1 has no external representation, sandbox, QQ, or agentic email, so it has no runtime HITL approval flow. Ordinary connector grants and delete confirmations use normal product consent. | Avoids unused tables, resume logic, and renderers. Implement before the first approval-gated action ships. |
| SAFE/FULL is insufficient (`architect-review.md` §1 ADR-009; §11) | **Accept and narrow further.** The Gmail pipeline receives only the current connector item and may only emit a private candidate. It cannot read workspace/memory, fetch arbitrary URLs, send notifications, or perform external writes. | Small policy cost and a meaningful reduction in prompt-injection blast radius. This must amend ADR-009 before pipeline code. |
| Foundational audit, quotas, secrets, health, and failure reconciliation move earlier (`architect-review.md` §§10–12) | **Accept a v1 baseline, not the enterprise endpoint.** Audit auth, connector grants/use, candidate/todo mutations, notification delivery, and deletion. Enforce token, sync-batch, queue-age, and notification caps. Encrypt OAuth credentials and redact logs. | Roughly 1 week across the foundation. MFA, enterprise RBAC, HA, legal hold, billing ledger, KMS/HSM integration, and operator-grade replay UI are post-v1 unless managed hosting is chosen now. |

### What is truly pre-P0

Before durable runtime coding, freeze these contracts:

- tenant context, RLS ownership, and tenant-qualified foreign keys;
- canonical channel installation/identity/session keys;
- run, event sequence, outbox, and reconnect semantics;
- idempotency/effect-state rules for included writes and deliveries;
- connector-content capability boundaries;
- schedule firing, misfire, and delivery-state semantics;
- secret-bearing process boundaries and the supported deployment profile;
- one worker/queue model and its acknowledgement/cancellation behavior.

These are architecture decisions with expensive migration paths, matching `architect-review.md` §“What to change before P0 coding.”

### What is not a v1 prerequisite

The following are sound future requirements but gold-plating for the narrowed v1:

- gVisor/Firecracker, sandbox snapshots, image supply chain, and sandbox pools—because v1 executes no tenant code;
- team memory visibility/CAS, team roles, shared workspaces, and cross-tenant channel routing—because v1 is personal;
- full approval persistence and Web/QQ/email approval renderers—because v1 has no approval-gated effects;
- provider failover and cross-provider partial-stream reconciliation—ship one provider/BYOK first;
- pgvector, hybrid retrieval, generalized memory, and MinIO-backed personal file management—exclude attachments/files from the first loop unless user testing proves they are required;
- multi-node fair scheduling, HA, Firecracker, enterprise KMS/HSM, legal hold, and billing infrastructure—required only when the deployment/market promise changes;
- generalized plugin/MCP idempotency and a replay console—v1 needs effect contracts only for its closed set of operations.

This is where I disagree with the blanket minimum-schema list in `architect-review.md` §7 and §“What to change before P0 coding”: unused approval, sandbox, team-memory, and generalized quota infrastructure should be specified at the boundary, not necessarily implemented before a private personal release.

## 2. Response to the UI Designer

### MVP-critical UX

The following UI asks are product requirements, not polish:

- **Progressive onboarding:** deployment health, owner bootstrap, model-key validation, demo candidates, bounded Gmail OAuth, sync scope, resumable failures, and a first useful candidate. This is a reduced version of `ui-design-review.md` §2 “Login, signup, and onboarding.”
- **Durable progress:** immediate “saved/queued,” persisted job state, last event/time, reconnect by cursor, safe navigation away, completion inbox, and plain-language retry. Async-job-first is otherwise perceived as broken (`ui-design-review.md` §3 and §7 P0).
- **Candidate-first triage:** source excerpt, uncertainty, accept/edit/dismiss, dedupe, and source deep link. The Designer’s Todo “Inbox” direction is right (`ui-design-review.md` §2 “Todo board”), but candidates must remain distinct from committed todos.
- **Trust controls:** exact Gmail scopes, connected account, pause/disconnect/delete, private-by-default label, notification consent, quiet hours, cap, and action receipts (`ui-design-review.md` §5).
- **No raw reasoning:** never expose hidden chain-of-thought. Show curated status such as “Reviewing 24 recent messages,” with redacted diagnostic events restricted to operators (`ui-design-review.md` §2 “Chat session” and §7 P0).
- **Baseline accessibility:** keyboard operation, semantic status, focus preservation, non-color cues, reduced motion, and no token-by-token screen-reader announcements (`ui-design-review.md` §6).

### Useful later, but not MVP-critical

- rich turn/tool cards, Answer/Activity/Debug density, steering, stop-after-step, and token streaming;
- full “Now → Conversations → Work → Connections → Administration” hierarchy;
- global search/command palette, cross-channel continuation, identity and fallback management;
- visual-system completeness, dark mode perfection, elaborate motion, and all component variants;
- board/list toggle, saved filters, dependencies, assignees, watchers, and team activity;
- generalized schedule builder/history; v1 only needs reminder and digest preferences plus visible delivery history.

The minimum progress object should be a **user task**—Gmail sync, analysis batch, or digest delivery—not a generic internal `run`. The run ID remains available for support. The Designer’s full run model is appropriate for later conversational and tool-heavy work, but making users understand turns, retries, and compaction would expose architecture rather than clarify value.

### Screens to cut or reshape for v1

The mockups are explicitly prototypes, not behavior specs (`ui-design-review.md` §“Mockups”). They are still broader than the MVP:

| Proposed surface | v1 decision |
|---|---|
| “Now” dashboard | **Cut as a separate destination.** Land directly on Candidate Inbox with sync/reminder status. |
| Rich Chat session | **Keep only a basic optional conversation/support surface, or cut if schedule pressure requires.** It is not the activation path. |
| Todo board | **Reshape into two simple views:** Candidate Inbox and accepted Todos. No Kanban columns, saved filters, team fields, or dependencies. |
| Files | **Cut.** No general upload/sync promise and no sandbox artifacts in v1. |
| Connectors | **Keep Gmail only.** Show account, scope, sync status/history, pause, disconnect, and delete. |
| Scheduled tasks | **Cut the general editor.** Keep due reminders, daily digest time, quiet hours, and delivery history. |
| Settings | **Keep a narrow set:** model/BYOK status, Gmail/data controls, notifications, cost/budget, account/security. |
| Team management/workspace switching | **Cut.** Preserve tenant context internally; add persistent workspace chrome when a second workspace can actually exist. |
| QQ identities and agentic email onboarding | **Cut.** They should not appear in first-run setup or navigation. |

The Designer’s six-step onboarding does **not** match the MVP cut. QQ linking, agentic mailbox provisioning, workspace choice, and multiple OAuth sign-in options create failure points before value. The v1 path should be: owner bootstrap → demo result → model validation → Gmail read grant/scope → first sync → candidate triage → optional reminder consent.

### Cross-channel approvals

`ui-design-review.md` §2 “Approval card” and §7 correctly require one versioned semantic schema, expiring one-use decisions, and first-valid-response-wins. I accept that as the design contract. I do **not** accept Web, QQ, and email approval implementation in v1 because no v1 action should need runtime approval. Build it immediately before external representation or code execution, not as speculative UI.

## 3. Cross-role tensions and resolutions

| Conflict | PM position | UI Designer position | Architect position | Trade-off | Recommended resolution |
|---|---|---|---|---|---|
| Fast MVP vs hardened multi-tenancy | Validate one personal workflow quickly; team is later (`pm-review.md` §§3–4). | Persistent workspace/audience context is P0 (`ui-design-review.md` §§1, 7). | RLS, composite keys, qualified identity, and isolation tests are mandatory (`architect-review.md` §2). | Retrofitting isolation is dangerous, but full team product work delays value. | **Build tenant-safe storage now; ship one personal workspace.** RLS and keys are release gates; workspace switcher, roles, and collaboration are not. Do not market v1 as unrelated-tenant SaaS. |
| Real-time streaming polish vs durable async work | Users need confidence, not a streaming spectacle. | First-class run banners, resumable streams, tool cards, steering, and settled states (`ui-design-review.md` §3). | Canonical journal/outbox and cursor replay; token events may be batched (`architect-review.md` §4). | Rich streaming adds protocol/UI complexity and can still be unreliable. | **Persist task-level milestones and final snapshots; stream only as enhancement.** Ship queued/running/needs-attention/completed/failed, reconnect, and background completion. Defer tool-by-tool theater. |
| Broad autonomy vs consent and blast radius | Long-term autonomy matters, but candidate-first and opt-in notifications establish trust (`pm-review.md` §§5.2, 6.6). | Autonomy badges, receipts, undo, consent, and scoped approvals (`ui-design-review.md` §5). | Origin-only SAFE/FULL is unsafe; notifications and todos can leak or poison state (`architect-review.md` §1 ADR-009/010). | More autonomy creates delight only after accuracy and boundaries are trusted. | **Default to Analyze → private Candidate.** Formal todo requires accept/edit; notifications require opt-in and deterministic policy. No memory/workspace read or external action from email content. Allow trust-level upgrades only after measured use. |
| Agentic email autonomy vs trust/deliverability/security | It is a later product bet, not part of Gmail activation (`pm-review.md` §§2.10, 6.9). | Designs provisioning, allowed senders, health, and cross-channel approval (`ui-design-review.md` §§2, 5). | Requires authenticity, idempotency, quotas, abuse controls, and secret separation (`architect-review.md` §§10–11). | A compelling agent identity creates a second mailbox product and operational liability. | **Cut agentic email from v1 and navigation.** Use ordinary digest email. Revisit only with a provider, sender/auth model, reputation owner, abuse budget, and evidence of incremental user value. |
| Code execution value vs sandbox risk | Sandbox is not needed for Inbox-to-Action (`pm-review.md` §§3.4, 4.1 P2). | Designs files, tool output, approvals, artifacts, and persistent expectations (`ui-design-review.md` §§2, 7). | Docker is not hostile-tenant isolation; gVisor and separate sandbox nodes are required (`architect-review.md` §3). | Doing it safely is expensive; doing it cheaply undermines the product’s security claim. | **No code execution in v1.** Permit a local technical spike only. Later milestone requires gVisor-equivalent isolation, backend-neutral execution contract, diff/revert, quotas, and approval UX. |
| At-most-once simplicity vs reliable reminders | Delivery semantics should depend on consequence (`pm-review.md` §§2.8, 6.7). | Wants visible “claimed with no result” and manual rerun (`ui-design-review.md` §§2, 7). | Current scheduler loses firings; use firing/outbox and at-least-once effects (`architect-review.md` §6). | Duplicates annoy; silent missed commitments destroy trust. | **Adopt durable firing records.** Digest: dedupe/no duplicate priority. Important reminder: eventual-delivery priority with idempotent delivery and visible unresolved state. Never silently drop either. |
| Cross-channel approvals vs MVP scope | No v1 external action, so no approval flow. | Semantic parity across Web/QQ/email is P0 for trust (`ui-design-review.md` §2 “Approval card”). | Approval must be durable, scoped, expiring, one-use, and resumable (`architect-review.md` §1 ADR-008). | Building all renderers now is waste; delaying the contract risks incompatible channels later. | **Freeze the versioned semantic schema now; build no renderer until the first gated action.** When added, Web ships first and must satisfy the durable contract before QQ/email. |
| General agent UI vs focused product wedge | Candidate Inbox is the home and Chat is secondary (`pm-review.md` §§1.5, 3.2). | Proposes a platform-wide IA and “Now” dashboard (`ui-design-review.md` §1). | Runtime abstractions support many future surfaces. | Future-proof IA can make v1 feel empty and increase implementation surface. | **Use four v1 destinations at most:** Inbox, Todos, Gmail, Settings. Add Home/Chat/Files/Automate/Team only when each has a validated job. |
| Team-ready vision vs private-data safety | Personal v1; team needs a separate collaboration proof (`pm-review.md` §§2.13, 4.2 R6). | Designs workspace switching, roles, shared memory, and activity (`ui-design-review.md` §§1–2). | Team memory visibility, CAS, audit, and role policy are unresolved (`architect-review.md` §§1, 6, 11). | Schema reuse does not create collaboration, and accidental sharing is catastrophic. | **Keep personal and team in the long-term schema, but ship no team behavior.** Team beta begins only with explicit share/assign/comment/activity and proven private-memory separation. |
| Observability/security foundation vs feature speed | Basic quality, cost, deletion, and reliability metrics must precede beta (`pm-review.md` §§3.3, 4.1 P6). | Users need action receipts and understandable recovery (`ui-design-review.md` §§2, 5). | Audit journal, usage limits, health, reconciliation, and secrets are foundational (`architect-review.md` §§10–12). | A full platform is expensive; no evidence makes model quality and failures unmanageable. | **Ship a narrow evidence spine:** run/job history, prompt/model/cost, candidate feedback, connector/delivery health, security actions, and deletion status. Defer enterprise SIEM, billing, and generalized replay tooling. |
| One provider vs resilience | One provider is enough to test product quality (`pm-review.md` §§3.4, 4.1 P0). | Model/failover settings are part of the future Settings design (`ui-design-review.md` §2 “Settings”). | Failover needs capability/policy checks and partial-stream reconciliation (`architect-review.md` §8). | A second provider doubles test and failure semantics before the core value is known. | **Keep a canonical provider interface; implement one provider/BYOK.** Show outage clearly. Add failover only after provider incidents materially block retention. |
| Raw reasoning vs explainability | Users need “why this candidate,” not model internals (`pm-review.md` §5.12). | Hide raw reasoning and show curated activity (`ui-design-review.md` §§2, 7). | Events/logs may carry secrets and require redaction (`architect-review.md` §§3, 10). | Raw traces aid debugging but leak data and falsely imply faithful reasoning. | **Never expose chain-of-thought.** Store only necessary redacted operational events; generate concise evidence from source, rule, and result. Operator diagnostics require access control and retention limits. |

## 4. Revised MVP recommendation

### Single shippable v1

**Sherpa v1 is a self-hosted, single-owner Gmail-to-Action assistant.**

Its complete promise is:

1. connect one Gmail account read-only with a bounded scope;
2. durably sync and analyze messages using a connector-item-only capability;
3. show private, deduplicated candidates with source and uncertainty;
4. accept/edit/dismiss into a basic todo list;
5. send opt-in Web/digest reminders with quiet hours and visible delivery state;
6. pause, disconnect, export, and delete the relevant data;
7. show job status, failures, model usage/cost, and an audit receipt.

Not in v1: arbitrary code execution, files, team workspaces, shared memory, QQ, agentic email, GitHub, external write actions, general cron, multi-provider failover, or rich agent/tool streaming.

### Ordered next three milestones

1. **Contract and value gate**
   - Freeze tenant/event/effect/schedule/secret contracts and the self-hosted deployment profile.
   - Build a 50–100-message redacted benchmark and a clickable Candidate Inbox prototype.
   - Exit when target users understand the permission/value exchange and extraction precision justifies real Gmail access.

2. **Personal Inbox-to-Action alpha**
   - PostgreSQL with RLS, durable jobs/events/outbox, one provider, owner bootstrap, demo mode, Gmail read-only OAuth, scoped sync, candidate triage, source traceability, dedupe, baseline cost/feedback, pause/disconnect/delete.
   - Exit when real users independently reach a useful candidate, reconnect/retry works, all accepted items trace to source, and cross-tenant/effect-replay tests pass.

3. **Trustworthy follow-through v1/private beta**
   - Accepted todos, due/snooze, Web inbox, daily digest, quiet hours/caps, durable schedule firings, delivery reconciliation, connector health, export, backup/restore guidance, and accessibility baseline.
   - Exit on the existing PM quality gates (`pm-review.md` §§3.5 and 8): target candidate precision, zero unauthorized/cross-tenant actions, no silent job failures, controlled notification complaints, and evidence of weekly action value.

After those milestones, the next bet should be selected by observed demand—GitHub source, code execution, or team sharing—not by the old component order.

## 5. Decisions needed from the human

- Approve or reject the v1 customer and promise: self-hosted technical individual, Gmail-to-Action, single owner.
- Decide whether v1 is explicitly single-installation/single-owner or must support unrelated hosted tenants; the latter triggers a materially higher operations, KMS, RBAC, and incident-response bar.
- Choose the Gmail OAuth operating model: project-managed verified app, per-deployment app, or both.
- Choose what Gmail data is retained: metadata/snippets versus full body, history window, labels, attachments, and deletion period.
- Confirm self-hosted BYOK and select the single initial model/provider.
- Decide whether basic Web chat remains a secondary v1 surface or is cut entirely.
- Approve candidate-first defaults and the threshold/process for any later automatic promotion to formal todos.
- Set notification defaults: opt-in timing, daily digest time, quiet hours, cap, and which reminders warrant eventual-delivery behavior.
- Confirm the explicit v1 exclusions: sandbox, files, GitHub, QQ, agentic email, teams, external writes, general schedules, and multi-provider failover.
- Set release quality thresholds and recruit the first 10 target users, including who owns support for deployment and OAuth failures.
- Decide whether managed hosting is an active near-term commitment; if yes, fund the stronger KMS, HA, abuse, compliance, and operational work before launch.
