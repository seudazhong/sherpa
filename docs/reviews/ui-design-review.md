# Sherpa UI/UX design review

**Review status:** pre-implementation design review  
**Product stance:** Sherpa should feel like a calm guide carrying work in the background—not a chat box with administrative pages attached.

## Executive assessment

The architecture is a strong fit for a cloud agent, but it creates three UX obligations:

1. **Make scope unmistakable.** A user can belong to personal and team tenants, arrive through several identities, and act on private or shared data. Workspace and audience must remain visible at every consequential action ([02 §四个概念](../02-identity-session-memory.md#四个概念别混为一谈)).
2. **Make asynchronous work legible.** Sending a message admits a durable job; it does not synchronously obtain an answer ([03 §一条消息的完整生命周期](../03-runtime-async-jobs.md#一条消息的完整生命周期)). The UI needs a first-class run model, not a typing indicator.
3. **Make agency inspectable and reversible.** Sherpa may read, create todos, and notify automatically, while external representation requires approval ([06 §自治边界](../06-connectors-autonomy.md#自治边界已锁定autonomy-ladder)). Every automated action needs source, actor, time, outcome, and an undo or recovery path where technically possible.

The recommended product hierarchy is **Now → Conversations → Work → Connections → Administration**. “Now” is the orienting dashboard; Chat is where users steer; Todos and Files are durable outputs; Connectors and Schedules govern automation; Settings and Team govern scope and policy.

## 1. Information architecture and navigation

### App shell

Use a persistent left rail on desktop and a bottom bar plus “More” sheet on mobile:

| Group | Destination | Why it exists |
|---|---|---|
| **Now** | Home | Runs needing attention, proactive notifications, next scheduled work, recent outcomes |
| **Conversations** | Chat | Session list and event-stream conversation view |
| **Work** | Todos, Files | Durable artifacts independent of any one session |
| **Automate** | Schedules, Connectors | Triggers, external data, sync health, and agent-owned email |
| **Workspace** | Team | Members, roles, shared tasks, shared memory, audit activity; hidden in personal space |
| **Account** | Notifications, Settings | Channel priority, persona, model, identity links, security |

Global search/command palette should search conversations, todos, and files but clearly group results by workspace. A notification inbox in the app header is the canonical record even when delivery occurs over QQ or email.

### Personal/team workspace switching

Place the workspace switcher at the top of the navigation, always visible. Each option shows:

- avatar, workspace name, and explicit **Personal** or **Team** label;
- the user’s role for teams;
- unread/attention count;
- an optional default-workspace marker.

Switching should preserve the current destination where it exists (for example, Personal Todos → Atlas Team Todos) and otherwise land on Home. Never silently merge results across tenants. The page title, composer, breadcrumbs, file picker, approval card, and destructive dialogs must repeat the active workspace. Team content receives a “Shared with Atlas Team” label; personal memory receives “Only you.”

This reflects the single-schema tenant model ([00 §目标用户](../00-overview.md#目标用户)) while guarding against cross-tenant mistakes. For inbound DMs, show and allow changing the default workspace defined by the identity model; group sessions show a locked team destination ([02 §这个模型暴露隐含的-3-个选择](../02-identity-session-memory.md#这个模型暴露隐含的-3-个选择)).

### One identity, many channels

Do not expose UMO keys as primary UI. Translate them into human labels: **Web chat**, **QQ direct message**, **QQ group · Design Crew**, **Email thread**, **GitHub · sherpa/runtime**. Show channel chips on session rows and event provenance, with a “Continue here” affordance when a conversation began elsewhere.

Create **Settings → Identities & delivery** as the user-facing hub for:

- linked web/OAuth, QQ, verified email, and agentic email identities;
- verification and last-used status;
- default workspace for direct messages;
- outbound channel priority and fallback;
- “Test notification” and per-channel consent.

This makes the architecture’s `identity → user` linkage and mirrored outbound channel selection understandable without leaking implementation language ([02 §心脏 resolve_inbound](../02-identity-session-memory.md#心脏resolve_inbound), [06 §主动推送](../06-connectors-autonomy.md#主动推送出站入站的镜像--幂等)).

## 2. Key screens

### Login, signup, and onboarding

**Purpose:** establish a verified person, personal workspace, delivery identity, and safe first automation.

**Recommended flow:**

1. **Sign in:** Continue with Google/GitHub OAuth, or verified email. Explain that OAuth sign-in is separate from granting Gmail/GitHub connector access.
2. **Name your Sherpa:** display name, timezone, locale, personal workspace name.
3. **Choose channels:** optional QQ binding uses a short-lived verification code entered in the bot conversation; display expiry, retry, and the QQ account being linked before confirmation.
4. **Provision agentic email:** suggest an address, explain that it belongs to the agent—not the user’s Gmail—and require confirmation before activating inbound mail.
5. **Connect data (optional):** Gmail readonly and GitHub scopes are presented separately, with “Skip for now.”
6. **First win:** choose a starter automation such as “Create todos from important email”; preview exactly what Sherpa will read, create, and notify.

**States:** skeleton while session is restored; OAuth redirect progress; code sent/countdown; expired or incorrect code; account already linked to another user; scope denied; partial onboarding saved; agentic address conflict; completed state with test notification. Never strand the user after an OAuth popup closes—return to the same step with a diagnostic and retry.

### Chat session

**Purpose:** let a person converse, understand an autonomous run, inspect actions, and steer or stop it.

**Core layout:**

- session list with channel origin, workspace, running/waiting indicators;
- header with session name, workspace/audience, source channel, model, and run status;
- transcript grouped by **run**, then **turn**, rather than a flat token stream;
- composer with workspace label, attachment scope, send/steer mode, and stop control;
- right-side activity drawer on wide screens; inline activity on mobile.

**Event rendering:**

| Event | Presentation |
|---|---|
| `run.started` | Persistent run banner: Queued/Starting, elapsed time, background-safe message |
| `text-delta` | One growing assistant message; batch visual updates to avoid flicker; preserve selection and scroll position |
| `reasoning-delta` | Collapsed “Reasoning activity” summary by default; do not expose raw hidden chain-of-thought. Offer safe status summaries such as “Comparing three sources.” |
| `tool-call` | Tool card with friendly verb, target, arguments summary, running spinner, start time, cancelability |
| `tool-result` | Same card transitions to success/error; concise display output, duration, and expandable details |
| `tool-error` | Redundant icon + label, plain-language effect, retry/recover action; errors remain observations rather than terminating the transcript |
| `turn.end` | Subtle divider: “Turn complete · continuing”; never show a success checkmark for the whole run |
| `run.settled` | Final status, named reason (`completed`, `stopped:budget`, `failed`, `interrupted`), elapsed time, action summary, notification eligibility |

Tool output respects the 2,000-line/50KB limit. The card shows head/tail, “Output shortened,” original size, and a durable **Open spilled output file** link ([05 §工具接口](../05-tools-permissions-sandbox.md#工具接口内置mcp子-agent-长一样)). Expanded output is monospace, searchable, copyable, and never auto-expands while streaming.

**States:** empty state with suggested first tasks; durable “Message saved · queued” acknowledgement; reconnecting with last event time; resumed stream; waiting for approval; waiting for another run in the same session; backgrounded; interrupted; budget stop with partial result; provider retry/failover; fatal error with run ID and retry. On SSE reconnect, reconcile by event ID and do not duplicate tool cards.

### Approval card

**Purpose:** obtain informed, correlated consent without making the user reconstruct context.

The semantic card must be identical across web, QQ, and email, even where visual layout must adapt:

1. **Action:** “Send email to maya@northstar.example”
2. **Requester/context:** Sherpa, session, workspace, and originating trigger
3. **Why:** one-sentence rationale
4. **Scope/impact:** recipients, files, network/domain, or command; destructive/external badge
5. **Preview:** exact outgoing content or redacted command arguments
6. **Expiry and correlation:** “Expires in 9 min” plus short request ID
7. **Choices:** **Allow once**, **Allow for this session**, **Always allow…**, **Reject**

“Allow once” is primary; Reject is always visible; “Always” opens a policy confirmation naming workspace, tool, target pattern, and revocation path. Email and QQ use signed, expiring actions or a short reply code—never ambiguous free text. After action, all surfaces replace or append a receipt (“Allowed once by Dana via QQ · 10:42”) and disable stale controls. Rejection must explain that all pending approvals in that session are cancelled, matching the permission protocol ([05 §四道闸 + 权限引擎](../05-tools-permissions-sandbox.md#四道闸--权限引擎)).

**States:** pending, expiring, approved elsewhere, rejected, expired, superseded, unavailable/offline, policy conflict, and action failed after approval. The card always names the active tenant to prevent cross-workspace grants.

### Todo board

**Purpose:** turn connector-derived work into an editable, accountable plan.

Use columns **Inbox, Next, In progress, Waiting, Done**. Cards show title, assignee, due date, priority, dependency/blocker, workspace visibility, and source badge (**Gmail**, **GitHub**, **Manual**, **Sherpa**). Auto-generated cards start in Inbox with “Suggested by Sherpa,” a confidence/source excerpt, **Accept**, **Edit**, and **Dismiss**. Opening a source uses a privacy-safe preview and deep link when available.

Board/list toggle, saved filters, bulk triage, and “Show completed” keep the board usable. Team cards expose assignee and activity; personal cards do not imply sharing.

**States:** no connectors/no todos; sync in progress; optimistic drag with rollback; stale conflict; permission error; source removed; duplicate merged; offline/retrying.

### Files / personal storage

**Purpose:** manage durable workspace inputs and artifacts while distinguishing them from ephemeral compute.

Components: upload drop zone, folder/list views, storage meter, sync status, origin/owner, modified time, preview, version/activity, download/share/delete, and “Used by runs” references. Uploads show queued/scanning/syncing/ready/failed. Team files require explicit shared visibility; default uploads in personal space are private.

Copy should state: **Files persist; each code execution starts in a fresh isolated container.** For generated artifacts, show the run and tool that created them. “Spilled output” files have a recognizable type and retention policy.

**States:** first upload; drag-active; progress; checksum/duplicate; unsupported preview; quota exceeded; sync conflict; connector unavailable; deleted/recoverable; access denied.

### Connectors management

**Purpose:** connect accounts safely, reveal health, and control what automation may do.

Separate **User accounts** (Gmail, GitHub), **Sherpa channels** (agentic email, QQ), and future integrations. Each card shows connected identity, scopes, read/write mode, last/next sync, items imported, health, owning user/workspace, and Disconnect. Health states are Connected, Syncing, Needs attention, Paused, and Error.

Connection detail includes scope explanation, cursor/history, sync now, polling frequency, data deletion, token refresh errors, and activity log. Gmail defaults to readonly; GitHub permissions are repository-selective. Agentic email gets address, copy/test controls, allowed senders, and trust warning. QQ onboarding uses the verification flow and default workspace.

**States:** not connected; OAuth opening; callback; partial scope; initial sync; rate limited; token expired; account mismatch; webhook/polling delay; disconnect confirmation and post-disconnect data choice. Reinforce that authenticated account access does not make message content trustworthy ([06 §信任分级](../06-connectors-autonomy.md#信任分级两种邮箱是两回事)).

### Scheduled tasks

**Purpose:** create, understand, and recover recurring autonomous work.

The list shows task, owner/workspace, human-readable schedule with raw `cron`/`every`/ISO detail, timezone, next run, last outcome, enabled state, notification target, and history. The editor previews the next five occurrences and warns about DST, missed-run semantics, hard timeout, and actions that will request approval.

History distinguishes **claimed**, **running**, **settled**, **failed**, and **claimed with no result**. Because scheduling is at-most-once, manual rerun must create a new explicitly labeled run rather than imply automatic recovery ([06 §调度器](../06-connectors-autonomy.md#调度器at-most-once-是可靠性命门)).

**States:** no schedules; validation error; timezone ambiguity; paused; due/queued; running; failed; skipped/claimed with no result; scheduler unavailable.

### Settings

**Purpose:** control assistant behavior and delivery without exposing infrastructure jargon.

- **Notifications:** event categories, quiet hours/timezone, channel priority with fallback, per-workspace overrides, digest vs immediate, test notification, consent log.
- **Persona:** name, tone, response length, working preferences, preview, and reset. Separate personal preferences from team shared instructions.
- **Model:** default/automatic routing first; advanced users can choose provider/model, cost/quality preference, budget, and failover. Explain that an active run keeps its initial tool set and may use failover.
- **Identities & security:** linked channels, sessions, approvals/policies, data export/delete.

**States:** saved/unsaved; validation; policy controlled by team; model unavailable/fallback active; channel unverified; notification test success/failure.

### Team management

**Purpose:** manage membership and shared context while preserving private boundaries.

Members view shows invitation status, role, last active, and channel-neutral identity—not private linked addresses. Recommended roles: Owner, Admin, Member, Viewer, with a capability matrix for connectors, schedules, approvals, shared files, and memory.

Shared memory is a small, reviewable set of team facts/instructions with author, version, affected runs, and restore. Shared tasks have assignees and watchers. Team activity records membership, policy, connector, schedule, shared-memory, and external-action changes. Personal memory must never appear in team search or admin exports ([02 §两层记忆](../02-identity-session-memory.md#两层记忆个人助理--团队协作都要)).

**States:** empty/new team; invited/pending/expired; role change; last-owner protection; member removal impact; shared-memory rebuild in progress; access denied.

## 3. Real-time and streaming UX

### A run is the primary progress object

Use a compact persistent run banner with:

- state: **Queued → Thinking → Using tools → Waiting for you → Finalizing → Settled**;
- current step and friendly tool target;
- elapsed time, last event time, and bounded budget (“Step 3 of up to 50”);
- “You can leave; Sherpa will notify you”;
- **Steer**, **Stop after current step**, and, only where safe, **Stop now**.

Steering entered during a tool call is visibly queued: “Guidance saved; Sherpa will read it after this tool.” This aligns with the separate steer and interrupt queues and safe drain boundary ([04 §护栏](../04-core-loop.md#护栏都要且都有界)). Do not fake smooth percent completion for nondeterministic work; use completed steps and named phases.

### Orientation rules

- Auto-scroll only if the user is already near the bottom. Otherwise show “3 new events.”
- Announce meaningful state transitions through an `aria-live="polite"` region, not every token.
- Collapse repetitive tool activity into a step group while preserving individual audit events.
- Let users switch between **Answer**, **Activity**, and **Debug** density; Answer is default.
- Show reconnect heartbeat and “Live/Delayed/Reconnecting” status. Persisted events refill gaps after reconnect.
- Allow safe navigation away. Running sessions retain a nav indicator and completion enters the in-app inbox.

### `turn.end` versus `run.settled`

This architectural distinction is critical: a turn can end before retries, compaction, or queued continuation finish ([04 §流式事件词汇](../04-core-loop.md#流式事件词汇emit--redis--sse一套通吃-ui--可观测)). Therefore:

- `turn.end` creates only a quiet divider: **Turn complete · Sherpa is continuing**.
- Input can become **Steer this run**, not start a concurrent session run.
- success sounds, final push notifications, summary/checkmark, and composer reset happen only on `run.settled`.
- a settled card always shows the named exit reason and partial-output status.

## 4. Design system

### Visual direction

Use “alpine warmth”: warm snow backgrounds, deep forest text, evergreen action color, and restrained sunrise orange. Avoid icy enterprise blue as the dominant color and avoid anthropomorphic mountain clichés. The mark can abstract a path between two peaks.

### Color tokens

| Token | Light | Dark | Use |
|---|---:|---:|---|
| Background | `#F6F4EE` | `#111714` | App canvas |
| Surface | `#FFFFFF` | `#18211D` | Cards, nav |
| Muted surface | `#EEEAE0` | `#202C27` | Tool output, secondary areas |
| Text | `#1F2A26` | `#EDF3EF` | Primary copy |
| Muted text | `#66736D` | `#9EADA5` | Metadata |
| Border | `#DDD9CE` | `#33423A` | Dividers |
| Primary / evergreen | `#2F6F62` | `#70BFA9` | Primary actions, live state |
| Primary hover | `#255B51` | `#8DCEBB` | Hover |
| Accent / sunrise | `#D9824B` | `#F0A66B` | Highlights, source emphasis |
| Success | `#2D7D5B` | `#66C495` | Completed |
| Warning | `#B66A24` | `#E8AD62` | Waiting/attention |
| Danger | `#B84A45` | `#F0837D` | Errors/reject/stop |
| Info | `#356FA1` | `#72AEE6` | Informational/source |
| Focus | `#6E56CF` | `#B6A3FF` | Keyboard focus ring |

Color is never the sole status signal. Verify all text/background pairings for WCAG AA; use tinted colors only behind dark text in light mode and light text in dark mode.

### Typography, spacing, and shape

- Font stack: `Inter, ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif`; mono: `"SFMono-Regular", Consolas, monospace`. Ship a local font only if licensing and performance justify it.
- Type scale: 12 px caption, 14 px secondary/body-small, 16 px body, 18 px body-large, 24 px section title, 32 px page title, 40 px display. Body line-height 1.5–1.65.
- Spacing scale: 4, 8, 12, 16, 24, 32, 48 px. Use an 8 px baseline with 4 px for tight internal gaps.
- Radius: 8 px controls, 12 px cards, 16 px feature panels; pills use 999 px.
- Shadows: subtle (`0 8px 24px rgba(31,42,38,.08)`); borders carry most hierarchy.
- Motion: 120–180 ms for controls, 220 ms for panels; no pulsing token animation; respect reduced motion.

### Component inventory

- buttons: primary, secondary, quiet, danger; icon-only requires tooltip and accessible name;
- inputs, textarea/composer, select, combobox, segmented control, date/time and schedule builder;
- cards: standard, run, tool call, approval, connector, todo, notification;
- status badge, source badge, workspace chip, channel chip, autonomy chip;
- tabs, breadcrumbs, sidebar items, command palette;
- progress stepper, skeleton, spinner, reconnect banner, empty state;
- toast for transient acknowledgement; inbox/activity log for durable events;
- modal for focused confirmation only; drawer for inspect/edit; never put a multi-step OAuth flow in a fragile modal;
- table/list/board, file upload/progress, code/output viewer;
- inline error, error boundary, undo bar.

Icons use a consistent rounded 2 px outline set, 16/20/24 px grid, with filled variants reserved for active state. Product/source logos may retain brand color; action meaning never relies on emoji.

## 5. Trust and safety UX

### Communicating autonomy

Every automation carries an autonomy badge:

- **Observe** — read/search only;
- **Organize** — may create todos and internal artifacts;
- **Notify** — may send informational notifications;
- **Act with approval** — external representation or risky tools pause;
- **Always allowed by policy** — names the policy and offers Review/Revoke.

Before enabling a connector automation, preview “Sherpa will / won’t.” For example: “Will read new Gmail messages, create private Inbox suggestions, and notify you. Won’t reply, send mail, or run code.” This operationalizes the SAFE/FULL trust split ([decisions ADR-009](../decisions.md#adr-009--信任分级工具集safe-vs-full)).

### Evidence and audit

Each autonomous outcome has an action receipt: initiator/trigger, workspace, data sources, tools, files changed, external recipients, approval actor/channel, timestamp, run ID, and result. Prefer plain language with expandable raw event details. Redact secrets by default.

Offer Undo where the underlying operation is reversible: restore dismissed todo, restore deleted file, revert team-memory version, pause schedule, revoke persistent permission. For irreversible external actions, say **Cannot be unsent** before approval and provide remediation, not a dishonest Undo.

Notification setup requires explicit per-channel consent, preview, test, quiet hours, rate summary, and one-click mute. Delivery priority and fallback must be visible because `pick_channel()` otherwise feels arbitrary.

## 6. Accessibility and responsive behavior

- Target WCAG 2.2 AA; keyboard access, visible `#6E56CF` focus ring, semantic landmarks/headings, and skip link.
- Maintain 44×44 px touch targets. On mobile approval actions stack; Allow once and Reject remain visible without horizontal scrolling.
- Do not stream every delta to assistive technology. Update visual text continuously but announce complete sentences or run-state changes politely.
- Tool and run statuses pair icon, label, and text. Errors explain impact and recovery.
- Preserve focus when cards update in place; approval resolved on another channel announces the change without stealing focus.
- Support 200% zoom, 320 px width, reflow without two-dimensional scrolling except intentionally scrollable boards/code.
- Respect `prefers-reduced-motion`, increased contrast, text spacing, and OS color scheme; never use shimmer as the only loading cue.
- Captions/labels are localized and allow 30–50% expansion. Dates always include timezone where scheduling or expiry matters.
- Mobile nav prioritizes Home, Chat, Todos, and Inbox. Files, Connectors, Schedules, Team, and Settings live in More. The running/approval tray stays reachable above the bottom nav.
- Email approval markup must remain usable with images and CSS disabled; QQ cards need concise numbered actions and clear expiry.

## 7. UX risks and recommendations

| Priority | Risk | Recommendation |
|---|---|---|
| **P0** | Workspace confusion could expose private/team content or approve the wrong tenant. | Persistent workspace chrome; scope repeated at composer, file actions, approvals, and external actions; never cross-tenant global results by default. |
| **P0** | Raw `reasoning-delta` can disclose sensitive prompts, confuse users, and create false confidence. | Treat it as an internal event; render curated activity summaries by default. Gate raw diagnostic events to an admin/debug view with redaction. |
| **P0** | “Identical” approvals across web/QQ/email are visually impossible and stale cards can race. | Define one versioned semantic approval schema and state machine; channel renderers preserve field order, action labels, expiry, and correlation ID. First valid response wins; all views receive the receipt. |
| **P0** | Async-job-first (ADR-005) can feel slow or broken after Send. | Immediate durable “Saved and queued” receipt, queue/start states, elapsed/heartbeat, safe backgrounding, completion inbox, and event-ID resume. Never fake a synchronous typing state. |
| **P1** | Event vocabulary can overwhelm ordinary users. | Progressive disclosure: Answer by default, Activity for tools, Debug for raw normalized events. Group events into runs/turns. |
| **P1** | `turn.end` may be mistaken for completion. | Reserve success styling and notifications for `run.settled`; label intermediate boundary “continuing.” |
| **P1** | Ephemeral sandbox (ADR-007) cannot offer a persistent dev-server URL. | Set expectation before run; support static artifact preview, screenshots, downloadable builds, and persisted files now. Later add explicit TTL-based persistent preview sessions/tunnels with network and cost warnings. |
| **P1** | At-most-once schedules can lose an occurrence after claim/crash, surprising users. | Show “Claimed; no result recorded,” explain it was not auto-repeated to prevent duplicate side effects, and offer an explicit idempotent-aware manual rerun. |
| **P1** | “Always allow” can silently widen long-lived authority. | Put behind a second policy sheet with target scope, workspace, expiry option, and immediate revoke; send a durable security receipt. |
| **P1** | Automatic todos and proactive notifications can become noisy. | Suggestions land in Inbox, source/confidence shown, dedupe controls, daily digest default, per-source mute, and feedback that tunes extraction. |
| **P1** | OAuth sign-in and OAuth connector grant are easily conflated. | Separate language and steps: “Sign in to Sherpa” versus “Allow Sherpa to read Gmail”; show exact scopes and connected account. |
| **P2** | Team shared memory rebuild is invisible and edits can affect everyone. | Versioned review screen, impact summary, activity log, rebuild status, rollback, and strict separation from personal memory. |
| **P2** | Next/Vite remains undecided, risking premature component choices. | Lock tokens, semantic event/approval schemas, routing model, and accessibility contracts first; keep mockups framework-neutral. |

### Recommended validation before implementation

Prototype and test five tasks with personal and team users: switch workspace and send a message; resume a QQ-origin session on web; approve an external email from mobile; understand a five-minute tool run; triage an auto-generated Gmail todo. Measure scope comprehension, approval comprehension, time-to-detect current run state, and notification trust—not just completion time.

## Mockups

These are static, self-contained, offline-viewable HTML/CSS prototypes:

- [`design/index.html`](../design/index.html) — gallery, design tokens, type scale, and core components.
- [`design/dashboard.html`](../design/dashboard.html) — “Now” overview with workspace switcher, runs, schedule, and proactive feed.
- [`design/chat-session.html`](../design/chat-session.html) — streaming run, assistant answer, tool states, spilled output, and inline approval.
- [`design/todo-board.html`](../design/todo-board.html) — multi-column todo triage with Gmail/GitHub/Sherpa source provenance.
- [`design/connectors.html`](../design/connectors.html) — connector health plus Gmail, GitHub, agentic-email, and QQ onboarding.

The prototypes demonstrate visual direction and interaction hierarchy; they are not production behavior specifications.
