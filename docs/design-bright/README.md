# Sherpa — Daybreak (bright) UI mockups

Offline, self-contained HTML prototypes sharing one `base.css`. Open [`index.html`](index.html) to browse. This is the **recommended default light theme** (the v1 "Alpine warmth" set in [`../design/`](../design/index.html) becomes the dark theme — see [`../reviews/ui-comparison.md`](../reviews/ui-comparison.md)).

> ⚠️ **These screens depict the long-term product vision.** The confirmed **v1** ([ADR-022](../decisions.md)) is a narrower slice: **self-hosted, single-instance, single-user, Gmail → Action** (+ web chat as a secondary surface). This file maps what is **v1** vs **later**, so the mockups don't imply v1 ships everything shown.
>
> 📝 **Naming drift (2026-07-28, backlog B-7):** the shipped app renamed the triage surface **Inbox → Today** (`/today`), because "Inbox" collided with the Gmail inbox that v1 connects to. Where these mockups say "Inbox" they mean today's **Today** page; the mail-source chips still mean a real mail inbox.

## Screen → scope map

| Screen | In v1? | Notes |
|---|---|---|
| [`chat-session.html`](chat-session.html) | ✅ v1 (secondary) | Web chat is kept as a secondary surface. Approval card / reasoning-hidden / run log align with ADR-020/021/016. |
| [`connectors.html`](connectors.html) | 🟡 v1 **Gmail-only** | Gmail (read-only) is v1. **GitHub, agentic email, QQ are deferred** (ADR-012/013/022) — shown here as vision. |
| [`todo-board.html`](todo-board.html) | 🟡 reshape for v1 | v1 = **personal Candidate Inbox + simple todo list**, not a team Kanban. Remove team/assignees/GitHub source for v1 (candidate-first pattern stays — ADR-010/018). |
| [`settings.html`](settings.html) | ✅ v1 | Notifications + autonomy (candidate-first) + data controls are v1. Some sub-nav targets (Connections, Model) are thin until those features land. |
| [`schedules.html`](schedules.html) | 🟡 v1 **reminders only** | Reminder scheduling with honest missed/failed states is v1 (ADR-017). **General cron + GitHub scans are deferred.** |
| [`dashboard.html`](dashboard.html) | 🟡 v1 (personal parts) | Personal home is v1. **Team workspace switcher, Files, GitHub, QQ, agentic-email route, "isolated sandbox" are deferred.** |
| [`files.html`](files.html) | ❌ later | Files / personal storage is **deferred out of v1** (ADR-012/022). |

## Deferred features shown in the mockups (per [reviews §2.2](../reviews/README.md))
Team workspace & collaboration · Files/storage · GitHub · QQ/IM · agentic email · code sandbox · general cron · multi-provider · external write actions.

Each keeps its interface reserved and re-enters behind an explicit go/no-go gate — they are **not** v1.

## v1 screens still to add (from [`../reviews/cross-ui.md`](../reviews/cross-ui.md) §4)
Not yet mocked, but required for v1:
1. **Personal Candidate Inbox** (reshape of `todo-board`).
2. **Run / activity receipt** — "what Sherpa did on my behalf" (redacted audit view, ADR-021).
3. **Data & connection controls** — export / disconnect / delete imported data.
4. **Setup / first-value wizard** — deploy health → provider key → Gmail OAuth → first candidate → reminder opt-in.
5. **Reconnect / "catching up"** states — SSE cursor catch-up (ADR-016).
6. **Scheduled-run / reminder failure detail** — missed / failed / unknown with safe rerun (ADR-017).

## Before implementation
Apply the [`cross-ui.md`](../reviews/cross-ui.md) §4 revisions so the *first-build* screens are both **bright** and **v1-scoped**, while preserving the (well-aligned) run/approval/candidate/trust patterns verbatim.
