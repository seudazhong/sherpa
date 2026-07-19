# Sherpa UI/UX cross-review

**Role:** UI/UX Designer  
**Round:** Cross-review after PM, UI, and Architecture Phase-1 reviews  
**Position:** Accept the narrower personal Inbox-to-Action MVP, but do not confuse fewer capabilities with an incomplete experience.

## 1. Response to the PM

### I agree with the product cut

The PM is right that the first release needs one legible promise: **Gmail → candidate todo → user confirm/edit → reminder**, for self-hosting technical users. This gives the product a clearer reason to exist than a collection of chat, code, connector, and collaboration capabilities (PM review §0.1, §1.3–§1.5, and §3.2). It also fits the Phase-1 UX principle that Sherpa should be “a calm guide carrying work,” not a chat box surrounded by administration (UI review §Executive assessment).

Candidate-first is especially important. The current [`todo-board.html`](../design/todo-board.html) already distinguishes suggestions from accepted work, but the MVP should make that distinction structural rather than decorative. A candidate must remain a proposal until accepted, with source excerpt, date uncertainty, explanation, edit, dismiss, and duplicate handling (PM review §2.9 and §3.3). A percentage confidence score alone is not meaningful; use plain uncertainty such as **Date stated in email**, **Date inferred**, or **No date found**.

### Phase-1 screen priority

| Existing mockup | MVP disposition | UX decision |
|---|---|---|
| [`dashboard.html`](../design/dashboard.html) | **P0, substantially narrowed** | Keep orientation, Gmail sync/job health, candidates needing review, due reminders, recent outcomes, and durable notifications. Remove team, GitHub, QQ, agentic-email, file, and general-agent emphasis from the first-run experience. |
| [`todo-board.html`](../design/todo-board.html) | **P0, redesign around candidate triage** | Personal Candidate Inbox plus a simple Open/Done todo view is the flagship surface. Team assignees, GitHub cards, four-column team Kanban, and collaboration language are deferred. |
| [`connectors.html`](../design/connectors.html) | **P0 only for Gmail setup and health** | The current all-connectors catalog becomes a focused Gmail OAuth/setup flow and connection detail. GitHub is post-validation; QQ and agentic email are separate later bets. |
| [`chat-session.html`](../design/chat-session.html) | **Full screen deferred; run primitives are P0** | General chat, code tools, and external-email approval are not the MVP story. However, queued/running/retrying/failed states, activity receipts, and recovery affordances must be reused in sync and candidate-generation jobs. A minimal support conversation can remain secondary. |
| [`index.html`](../design/index.html) | **Design documentation, not a product milestone** | Revise the gallery later to label P0 versus future concepts and add the missing MVP flows. |

### Where I push back on “cut for v1”

The PM's feature cuts are sound, but the following cannot be cut without breaking the chosen promise:

1. **Do not cut onboarding because the audience is technical.** Self-host bootstrap, model-key validation, Gmail OAuth, account/scope selection, initial-sync progress, demo data, and recoverable errors are one activation journey (PM review §3.3A, §5.1, and §9.2). Technical users can understand OAuth; they should not have to diagnose a blank product after a callback, token refresh, or worker failure.
2. **Do not cut the reminder closure.** Web notification center, digest email, quiet hours, frequency controls, snooze, unsubscribe, and delivery-failure visibility are core usability, not “notification polish” (PM review §3.3D and §9.3). Without them the MVP stops at extraction, not follow-through.
3. **Do not cut run visibility with general chat.** Candidate generation and Gmail sync are still async jobs. Users need saved/queued acknowledgement, progress, backgrounding, failure impact, retry safety, and completion history even if the rich chat surface is deferred (PM review §5.7 and §6.3).
4. **Do not cut trust controls.** Exact Gmail scope, pause, disconnect, delete imported data, provenance, candidate correction, audit, and basic cost visibility belong on the critical path (PM review §2.6, §3.3E–F, and §11).
5. **Defer capabilities whole, not halfway.** QQ and agentic email may be absent from v1. If either is exposed, its binding/provisioning, identity confirmation, consent, health, failure, fallback, abuse controls, disconnect, and data lifecycle also ship. The same applies to OAuth: Gmail cannot be “included” while its real setup and recovery are cut (PM review §2.10–§2.12 and §7.3).

This preserves scope discipline without shipping dead-end cards, unexplained errors, or an MVP whose only usable path depends on operator intervention.

## 2. Response to the Architect

The architecture review correctly identifies several places where UX cannot paper over missing durability. The interface should mitigate uncertainty, but canonical event, invocation, approval, and schedule state must exist first.

### Redis pub/sub loss and SSE reconnect gaps

The Phase-1 design called for **Live / Delayed / Reconnecting** plus event-ID resume (UI review §3, “Orientation rules”). The Architect's §4 “Durable jobs, event bus, and streaming” makes the necessary correction: PostgreSQL state and a sequenced event journal must be authoritative; Redis may accelerate delivery but cannot define truth.

The user-facing state model should be:

- **Live** — events are current; show the last event time.
- **Delayed** — heartbeat is late, but the run remains active; avoid declaring failure.
- **Reconnecting** — preserve transcript, focus, and scroll; do not duplicate cards or re-enable stale approvals.
- **Catching up from saved activity** — request events after the last cursor, reconcile by stable run/event/invocation ID, then announce the number of recovered updates.
- **Up to date** — quiet confirmation; no success treatment unless the run settled.
- **Some live detail expired** — restore the authoritative assistant snapshot and final/current run state, explicitly saying that transient streaming detail is unavailable.

Sending work must immediately produce a durable **Saved and queued** receipt before streaming starts. If the canonical state cannot be fetched, say **Status temporarily unavailable; your request is saved**, not “Still thinking.” Approval controls remain disabled until their current persisted state is known. A reconnect banner and catch-up state belong in [`chat-session.html`](../design/chat-session.html) and any dashboard run card.

### Turn recovery and possible duplicate side effects

Architect §5 “Turn-granular crash gaps” is directly user-visible. A recovered run must not rewrite history to look seamless. Each tool/action card needs a durable invocation identity and one of these outcomes:

- **Completed earlier — result reused**;
- **Interrupted before action — safe retry available**;
- **Retrying safely with the same request ID**;
- **Checking whether the action happened**;
- **Outcome unknown — not automatically repeated**.

For an unknown external or destructive effect, Sherpa pauses and shows target, intended action, last known time, evidence, and choices appropriate to the tool: **Check again**, **I verified it**, or **Run a new action** with a duplicate-risk warning. There must be no generic “Retry all.” If a duplicate is later detected, keep both receipts and offer remediation rather than hiding one. This is the UI consequence of the Architect's requirement to reuse succeeded results, retry only idempotent tools, and stop on `effect_unknown`.

Provider failover also must not splice partial prose into a deceptively continuous answer. Per Architect §8 “Partial streams and identity reconciliation,” mark the first attempt **Interrupted before completion**, replace or visually de-canonicalize its partial draft, and begin the recovered answer from the last durable state.

### Ephemeral sandbox and no persistent dev-server URL

Ephemeral compute is compatible with a useful code experience, but not with an implied live workspace. Before execution, copy should state: **This run uses a fresh isolated environment. Files you save persist; processes and local URLs stop when the run ends.** This extends UI review §2 “Files / personal storage” and §7’s sandbox recommendation.

After execution, show:

- command and sanitized environment summary;
- start/end time, duration, exit code, and timeout/resource-limit reason;
- bounded stdout/stderr with a durable spilled-output link;
- files created/changed as a reviewable diff;
- downloadable artifacts;
- static HTML preview, screenshot, test report, or packaged build when applicable;
- an explicit **Process ended** state for any attempted server.

Never render `localhost:3000` as an openable URL after the container is gone. A persistent preview should be a later, explicitly named TTL session with expiry, cost, network, and sharing controls—not a silent exception to the sandbox model (Architect §3 “Workspace and prewarming”).

### Scheduling and honest missed-run states

The Architect recommends durable firing records and at-least-once processing rather than global at-most-once (§6 “Scheduler”). That is the right architectural resolution to the PM's concern that a critical reminder should not silently disappear (PM review §2.8 and §6.7).

The UI must still distinguish:

**Scheduled → Queued → Running → Succeeded / Failed / Skipped / Outcome unknown**, with `scheduled_for`, timezone, actual start, attempts, and delivery status. “Outcome unknown” is not “failed”: it means Sherpa cannot prove whether an effect occurred. For a missed occurrence, show:

> Scheduled for 9:00 AM; not started because the service was unavailable. No reminder was sent.

Then apply the schedule's visible policy: **catch up now**, **skip and continue**, or **ask me**. A manual rerun creates a newly labeled run and warns when duplication is possible. Critical missed reminders create a durable in-app alert; they must never vanish from the upcoming list. This replaces the “claimed; no result” workaround in [`dashboard.html`](../design/dashboard.html) with clearer firing and delivery semantics.

### Async-job-first latency

Async is the right system model, but “instant acknowledgement” must not be mistaken for “instant completion” (Architect §4 and PM review §6.3). Every async surface needs:

1. durable acceptance within the interaction response;
2. named phases: **Queued, Starting, Processing, Waiting for you, Retrying, Finalizing, Settled**;
3. elapsed time, last update, and queue reason; show an ETA only when evidence supports it;
4. safe navigation away and a canonical notification center;
5. cancel/stop semantics that explain what already completed;
6. plain-language failure impact and safe next action.

For long Gmail imports, show scope and counts such as **126 of 430 messages scanned; 4 candidates found**, not a fake percentage for model reasoning. Under backpressure, say **Queued behind other work** and preserve the user's place. The run banner in [`chat-session.html`](../design/chat-session.html) and hero in [`dashboard.html`](../design/dashboard.html) are the starting components, but they need queued, delayed, reconnecting, catch-up, and retry states.

## 3. Cross-role tensions and recommended resolutions

| Conflict | PM position | Architect position | UX stake | Recommended resolution |
|---|---|---|---|---|
| **Real-time polish ↔ async complexity** | Async needs queue, progress, reconnect, cancel, and completion UX (PM §6.3). | Pub/sub is lossy; durable journal, cursor replay, and reconciliation are required (Architect §4). | A polished spinner can falsely imply liveness or completeness. | Build polish only on canonical run state: immediate durable receipt, explicit connectivity state, cursor catch-up, snapshot recovery, and success only on settled state. Degrade from live detail to truthful state, never to invented continuity. |
| **Broad autonomy ↔ consent and trust** | Candidate-first trust ladder; notifications opt-in; external action approved (PM §5.2 and §6.6). | Origin-only SAFE/FULL is too broad; policy must consider untrusted data and requested effect (Architect §1 ADR-009/010 and §11). | Noise, prompt injection, leaked content, and authority creep all look like “the agent acted behind my back.” | Use `CONNECTOR_ANALYSIS` for current Gmail item → private candidate only. Promote autonomy explicitly: observe, suggest, limited auto-file, remind. External representation always previews target/content and asks. Every level has scope, history, and one-click downgrade. |
| **MVP scope cut ↔ onboarding completeness** | Defer QQ, agentic email, GitHub, sandbox, and team; focus Web + Gmail (PM §3.4). | Each shipped identity/connector needs authorization, lifecycle, idempotency, and audit contracts (Architect §1, §11). | A visible but half-configured feature adds friction and creates unsafe identity ambiguity. | Hide deferred capabilities entirely or label invitation-only. For anything shipped, onboarding, consent, health, recovery, fallback, disconnect, and deletion are acceptance criteria. Gmail OAuth is part of the MVP product, not deployment documentation. |
| **Hiding reasoning ↔ transparency/debuggability** | Users need short “why” explanations, not internal traces (PM §5.12). | Operators need durable events, generation attempts, tool state, and audit evidence (Architect §4, §5, §8, §11). | Raw chain-of-thought can leak sensitive data and create false confidence; hiding all evidence destroys trust. | Keep raw model reasoning hidden. Default UI shows a curated rationale: sources used, rule/trigger, inferred fields and uncertainty, tools/actions, model/version, and cost. Activity exposes structured receipts; permissioned Debug exposes redacted normalized events and attempt IDs—not hidden chain-of-thought. |
| **Uniform at-most-once ↔ reminder reliability** | Delivery policy should vary by job; critical reminders favor eventual delivery (PM §2.8 and §6.7). | Durable firings, outbox, idempotent at-least-once work, and explicit unknown delivery are required (Architect §6). | Silent loss breaks the flagship promise; blind retry can duplicate external actions. | Store every firing. Apply per-effect delivery policy and provider idempotency. Surface missed, skipped, failed, and unknown separately; offer policy-aware catch-up and manual rerun. |
| **Secure ephemeral code ↔ expected live preview** | Sandbox is outside the flagship MVP and should be deferred (PM §3.4 and §6.4). | Ordinary Docker is not a hostile multi-tenant boundary; one-use hardened execution is required (Architect §3). | A “Run server” interaction that returns a dead URL feels broken; weakening isolation is unacceptable. | Defer code from MVP. When introduced, design artifact-first output—logs, diffs, downloads, static previews—and clearly end processes. Add persistent previews later as isolated, expiring, metered sessions. |
| **Personal v1 ↔ multi-tenant/team foundation** | Team collaboration is a later, separate product stage (PM §1.1 and §4.2 R6). | Tenant isolation and identity correctness must exist before multi-tenant data is stored (Architect §1–§2). | Exposing team chrome early confuses the promise; postponing isolation risks irreversible privacy failures. | Keep tenant-safe contracts in the foundation, but present a personal-only P0 IA. Do not show fake team affordances. Add team navigation only with explicit sharing, roles, activity, and private/team boundaries. |

## 4. Mockup impact

### Revisions to existing mockups

- **[`dashboard.html`](../design/dashboard.html):** Make Gmail candidate review and upcoming reminders primary. Remove launch-readiness/GitHub/QQ/agentic-email examples from P0. Add Gmail sync scope and freshness, queued/delayed/catching-up states, failed or missed reminder detail, digest status, and a canonical notification-center entry. The current “claimed but did not settle” message should become a precise firing/delivery state with policy-aware actions.
- **[`todo-board.html`](../design/todo-board.html):** Change from a shared-team Kanban to a personal Candidate Inbox plus simple todo list for P0. Add source deep link, extraction rationale, date/assignee uncertainty, edit-before-accept, bulk triage, duplicate merge, dismiss feedback, snooze, completion, reminder status, and source-deletion behavior. Defer team labels, avatars, GitHub, assignment, and collaboration.
- **[`connectors.html`](../design/connectors.html):** Split the current catalog from the P0 setup journey. Lead with Gmail only: sign-in-versus-connector explanation, exact scopes, selected label/time window, attachments/retention, connected account, OAuth callback progress, initial scan progress, token-expired recovery, pause, reconnect, disconnect, and delete-imported-data. Remove or clearly defer the currently active agentic-email and QQ onboarding.
- **[`chat-session.html`](../design/chat-session.html):** Retain as a future general-agent concept, but revise the run system with Saved/Queued, Delayed, Reconnecting, Catching up, recovered attempt, effect-unknown, and settled states. Add Answer/Activity/Debug density, action receipts, safe retry choices, and artifact-first sandbox output. Keep raw reasoning hidden. The approval card remains the semantic reference for later external actions, not a reason to ship outbound email in v1.
- **[`index.html`](../design/index.html):** Relabel concepts by **P0 / later exploration**, replace the broad “core product views” framing, and eventually add thumbnails for the missing activation, candidate-detail, notification, and failure-recovery work.

### Missing screens and states revealed by the reviews

1. **Setup and first-value wizard (new P0 screen):** deployment health, admin bootstrap, provider-key test, demo mode, Gmail OAuth/scope, initial scan, first candidate, then reminder opt-in.
2. **Candidate detail/edit drawer (new P0 surface):** source excerpt/deep link, inferred fields, uncertainty, why it was suggested, accept/edit/dismiss, duplicate and privacy handling.
3. **Notification center and preferences (new P0 screen):** durable outcomes, reminder delivery, digest, quiet hours, cap, snooze/mute, failure, unsubscribe, and “why received.”
4. **Run/activity receipt view (new P0 screen):** what Sherpa read, inferred, created, retried, or failed to do; IDs and redacted technical detail under progressive disclosure. This is the user-facing “what the agent did on my behalf” record.
5. **Scheduled-run/reminder failure detail (new P0 screen or drawer):** intended time/timezone, firing, attempts, delivery status, missed/unknown explanation, catch-up policy, and safe manual rerun.
6. **Reconnect/catching-up and retained-history-reset states (new cross-screen states):** needed in dashboard jobs, Gmail import, candidate generation, chat, and approvals—not only as a transient toast.
7. **Data and connection controls (new P0 settings section):** export, retention, pause/disconnect, delete imported content and derived data, with completion status.
8. **Sandbox result view (deferred new screen):** logs, exit reason, changed files, diff, static preview, screenshot/report, artifact download, and explicit process termination.

## 5. UX non-negotiables

Regardless of scope cuts:

1. **Scope is unmistakable:** active workspace, connected account, source, audience, recipient, and automation level appear at every consequential action.
2. **Autonomy is inspectable and correctable:** every generated or autonomous outcome has provenance, a plain-language rationale, status, receipt, and Undo where technically real; irreversible actions say so before approval.
3. **Approvals are semantically identical across channels:** same action, target, impact, expiry, request ID, choices, and final receipt; stale controls fail closed and resolution on one channel updates all others.
4. **Async work is honest:** durable acknowledgement, named phase, last update, reconnect/catch-up, safe backgrounding, and explicit settled/failure/unknown state. No spinner substitutes for missing truth.
5. **No scheduled work fails silently:** every occurrence has history; missed, skipped, failed, and unknown are distinct, with visible delivery policy and recovery.
6. **Candidate-first is the default:** connector content can propose private work but cannot silently promote it, read unrelated workspace data, write memory, or notify externally.
7. **A shipped capability includes its whole safety lifecycle:** onboarding, consent, health, degraded state, retry/fallback, pause/revoke, audit, export/delete, and accessible empty/error states are not optional polish.

These guarantees preserve the trust model from the Phase-1 UI review while accepting the PM's narrower value proposition and the Architect's stronger durability and safety contracts.
