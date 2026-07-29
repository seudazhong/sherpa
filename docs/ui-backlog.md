# UI / UX Backlog

> Source: per-phase **acceptance + UX review** (Playwright human-lane pass, 2026-07-21) after the UI-completion phase (session mgmt + Schedules + Settings). Process: [`../AGENTS.md §2`](../AGENTS.md).
>
> **How to use:** pick the lowest-priority-number open item; fix it end-to-end (incl. the human-lane UI check + a row update here). This is the living board that catches "backend done, frontend forgotten" and UX rough edges. Each row: gap → suggested fix → refs.
>
> **Status (2026-07-22): UX-1…UX-16 ✅ cleared** and browser-verified. Rows kept as history; append new findings below.

## P1 — fix soon (small, high-value)

| # | Item | Gap (real-user finding) | Suggested fix | Refs |
|---|---|---|---|---|
| UX-1 | **Todo controls in Inbox** | Todos are **read-only** in the Inbox — you can see open/completed but there's no Complete/Edit/Delete button. Backend `PATCH /todos` + `update_todo`/`complete_todo` exist; UI never wired them. Users can only complete a todo via chat. | Add a "Complete" (and ideally "Edit" drawer) control to each todo row in `InboxView`, calling `api.patchTodo(csrf, id, {if_version, status:"completed"})`. | api §4.3; `services/todos.py`; matrix `docs/11 §9` |
| UX-2 | **Session dropdown labels are raw markdown** | The chat session switcher shows the last message text as the title (e.g. `✅ 每日日报已设置！\| 设置 \| 值 \|…`) — long, markdown-y, unreadable. | Label from the **first user message**, strip markdown, clean-truncate; or show a timestamp. Consider a proper session title on create. | `ChatView.tsx` `sessionLabel()` |
| UX-3 | **"Mock model" mislabel** | Chat subtitle is hardcoded "Mock model" but the stack runs the real model (litellm). Misleading. | Reflect the actual provider (expose provider kind via a small API/health field, or drop the label). | `ChatView.tsx` header |

## P2 — next batch

| # | Item | Gap | Suggested fix | Refs |
|---|---|---|---|---|
| UX-4 | **Create reminder from the UI** | Schedules page can only create a **digest**; reminders must be made in chat, yet you *can* cancel them in the UI → inconsistent entry points. | Add a "new reminder" form (pick a todo + time) on `SchedulesView`, calling `POST /schedules` (kind=todo_reminder). | `SchedulesView.tsx`; `services/schedules.py` |
| UX-5 | **Settings page looks raw** | Plain stacked checkboxes + inline inputs; inconsistent with the card/pill design used elsewhere. | Card-ify: styled toggles + labeled rows matching Inbox/Activity. | `SettingsView.tsx` |
| UX-6 | **Connectors nav is a dead placeholder** | Greyed "⌁ Connectors" with no explanation of why it's disabled. | Either build the connect page (needs real Google OAuth creds) or show an explicit "deferred — needs OAuth" affordance. Don't leave a silent placeholder (AGENTS.md §2). | `Sidebar.tsx`; `api/connectors.py` |
| UX-10 | **Approval outcome not confirmed inline** | After Approve/Reject in chat, the card just vanishes and the result only appears in "Run activity" (`Tool result · send_email: email sent…`); there's no inline "✓ Approved — sent" and the assistant doesn't narrate completion (continuation turn deferred). Found in the approval-closure human-lane pass (2026-07-21). | Show a resolution state on the card (or drive card removal + a ✓ from the catalog's `permission.resolved` event, which the resume path can emit). | `ChatView.tsx` `resolveApproval`; `core/resume.py` |

## P3 — polish

| # | Item | Gap | Suggested fix | Refs |
|---|---|---|---|---|
| UX-7 | **Quiet-hours times not editable** | Settings toggles quiet hours on/off, but 22:00–08:00 is fixed display; backend `SettingsPatch` doesn't expose start/end either. | Add start/end to `SettingsPatch` + service + a UI time picker (if desired). | `api/schemas.py SettingsPatch`; `services/insights.py` |
| UX-8 | **Redundant schedule name** | Rows show "Daily digest · Daily digest" (kind + name both the same). | De-dupe display, or let the user name a schedule. | `SchedulesView.tsx` |
| UX-9 | **Missing favicon + free-text timezone** | `favicon.ico` 404 (console error); timezone is a free-text input with no validation feedback. | Add a favicon; validate/normalize timezone (dropdown or on-blur check with a clear error). | `frontend/index.html`; `SchedulesView`/`SettingsView` |
| UX-11 | **Model points to an "external approval interface"** | With web inline approval, the assistant still tells the user to "approve via your connected platform or approval interface (reference ID …)" — confusing when the **Approve** button is right there in chat. Found in the approval-closure human-lane pass. | Tune the loop `SYSTEM_PROMPT` so the model references the inline approval control for web sessions instead of an external interface. | `core/loop.py` `SYSTEM_PROMPT` |

---

**Note:** UX-1 is itself a fresh instance of "backend implemented, UI control missing" — caught by the human-lane review, which is why that pass is now part of the Definition of Done.

## Full-product Playwright review — 2026-07-22

| # | Finding | Resolution | Status |
|---|---|---|---|
| UX-12 | **Mobile layout was structurally broken**: the fixed 248 px sidebar left only ~127 px for every page. | Rebuilt the shell with a 232 px desktop sidebar and an accessible mobile top bar + modal drawer; verified at 390×844 with no horizontal overflow. | ✅ |
| UX-13 | **Chat exposed raw Markdown** (`###`, list markers, tables), making real model replies look unfinished. | Added safe React Markdown + GFM rendering and message typography. | ✅ |
| UX-14 | **Every page had the same flat title + oversized empty-card rhythm**, so hierarchy and next actions were weak. | Introduced page eyebrows, compact section cards, metrics, task-specific creation cards, and actionable empty states. | ✅ |
| UX-15 | **Messaging and Connectors led with AppIDs, webhook paths, environment variables, and simulation controls.** | Moved technical detail and development-only test consoles behind progressive disclosures; connection health now leads. | ✅ |
| UX-16 | **Destructive data deletion was promoted beside routine export in the page header.** | Moved both into a dedicated trust/data card and gave deletion a distinct danger treatment. | ✅ |

## Owner-reported findings

| # | Finding | Resolution | Status |
|---|---|---|---|
| UX-17 | **Checkboxes were stretched to full row width by a global input rule**, so their label was squeezed to 0 px: Knowledge → *Add from Drive* showed only a tick box and a type badge with **no file name** (owner screenshot, 2026-07-29). The same latent bug affected Change Review's file rows and the new Settings → Models vision toggle. | Root cause was `styles.css` `input, select, textarea { width: 100%; padding: 9px 10px; border… }` also matching `input[type=checkbox]`. Scoped that rule to text-like controls (`input:not([type=checkbox]):not([type=radio])`) and gave checkboxes/radios their native box (`width: auto; flex: none; accent-color: var(--brand)`). Verified live: picker rows now render `DIR/TXT` badge + name (checkbox 13 px, name 434 px), Settings + Models toggles unaffected. | ✅ |
