# Playwright UI Review — rendered mockups

> **Method:** Served `docs/design/*.html` over a local HTTP server and opened each in Chromium via Playwright MCP. Reviewed at desktop (1440×900) and mobile (390×844). **Console:** clean except one harmless `favicon.ico` 404 on every page — no JS errors, no external requests (confirms self-contained/offline). Reviewer: UI review pass, 2026-07-19.

## Verdict

The mockups are **visually polished and production-credible**, and they render the review's hardest UX ideas correctly (durable async runs, hidden reasoning, structured cross-channel approvals, candidate-first todos, trust-tiered connectors, output spill-to-file). They are **Phase-1 artifacts** that depict the *broad* pre-narrowing vision, so their main gap is **v1 scope** — they still surface deferred capabilities (team, GitHub, QQ, agentic email, sandbox, Files). This exactly matches the revision list already agreed in [`cross-ui.md`](cross-ui.md) §4; this pass confirms it with rendered evidence.

## Per-screen findings

### `index.html` — design-system gallery ✅
- Cohesive **"Alpine warmth"** palette (evergreen `#2F6F62` + sunrise `#D9824B`), warm/trustworthy tone fitting "Sherpa". **Light + dark theme tokens**, type scale (40→12 px), and a component inventory (buttons, chips, status badges, cards) are all defined. Strong foundation.
- v1 gap: frames screens as "Core product views"; should relabel **P0 / later-exploration** (cross-ui §4).

### `dashboard.html` — home/overview ✅ (strong)
- **Workspace context always visible** (top-left `My workspace · Personal · Only you`) → UX non-negotiable #1. ✓
- **Honest async run banner**: "In progress · Step 3 of up to 50 · *You can leave this page safely* · Will notify via QQ" → UX non-negotiable #4 (async is honest). ✓
- "Needs your attention" inbox, recent-activity receipts ("No external actions taken"), upcoming schedules, notification route (QQ → Agentic email) with quiet hours.
- The **"One run has no result … claimed but did not settle"** card is the exact at-most-once problem the review flagged; per ADR-017 reword to explicit **missed/failed/unknown** with a policy-aware rerun.
- v1 gaps (deferred): team workspace switcher, Files nav, GitHub cards, QQ, agentic email, sandbox ("Run tests in the isolated sandbox").

### `chat-session.html` — streaming run ✅ (standout)
Best screen; renders the review's core loop UX faithfully:
- Live run banner with step count + "you can leave; Sherpa will notify".
- **"Reasoning activity … Raw internal reasoning is hidden."** → ADR-021 / UX "hide chain-of-thought, show curated rationale". ✓
- **Tool-call card** with status (`Success · 1.8s`) + monospace output preview.
- **Output bounding**: "Output shortened · 2,486 lines · head and tail shown · Open spilled output file →" → Ch5 spill-to-file. ✓
- **Safe-boundary steering**: "Steer this run… Sherpa will read your guidance after the current tool finishes · guidance will be queued safely." ✓
- "Turn 1 complete · Sherpa is continuing" → turn.end ≠ settled. ✓
- **Approval card** = textbook [ADR-020](decisions.md) envelope: structured fields (Workspace/Recipient/Requested-by/Why + subject preview), **Expires in 9 min**, `Request PERM-7K2Q · You can also approve from QQ · First valid response wins`, buttons Allow once / this session / always / Reject. ✓
- **Mobile (390px):** sidebar collapses to hamburger; the approval buttons **stack full-width (thumb-friendly)** — important because people approve from phones. ✓

### `todo-board.html` — todos ✅ (pattern) / ⚠️ (v1 scope)
- **Candidate-first** rendered perfectly: source badges (Gmail/GitHub/Sherpa/Manual), suggestions with **Accept / Edit / Dismiss**, confidence % + provenance ("Detected in a requested-changes review · 94%"), banner "*Nothing is assigned until your team accepts it*". ✓ ([ADR-010](decisions.md)/[018](decisions.md))
- **Biggest v1 revision**: it's a **team Kanban** (SHARED, assignees, avatars, "Members & memory") + GitHub sources. v1 = **personal Candidate Inbox + simple todo list** (cross-ui §4). The candidate pattern itself is reusable as-is.

### `connectors.html` — connectors & channels ✅ (strong on trust)
- **"Account access is not content trust — email/webhook content always gets a read-only restricted tool set, even when your account is verified."** → [ADR-009](decisions.md)/[013](decisions.md). ✓
- **"Tokens stay encrypted and never enter the code sandbox."** → [ADR-019](decisions.md) secret isolation. ✓
- Read-only scope chips (Read metadata / Read messages / **No send access**), sync health, per-connection Manage/Pause.
- **QQ binding by one-time code** ("Send to @SherpaBot in a DM; never post in a group; expires 08:42") → [ADR-003](decisions.md) verified identity linking. ✓
- v1 revision: lead **Gmail-only**; defer/hide GitHub, agentic email, QQ onboarding (cross-ui §4).

## Cross-cutting

**Strengths:** consistent design system across all 5 files (shared CSS variables); warm, calm, inspectable tone; every autonomous action has provenance + receipts; async is honest; approvals are structured & cross-channel; responsive down to mobile; fully offline/self-contained.

**Minor issues to watch:**
- Some muted captions on light backgrounds and on the dark hero look **low-contrast**; verify **WCAG AA (4.5:1)** for body/secondary text before build.
- Confirm visible **focus states** and keyboard order for approval buttons and cards (a11y baseline is an M3 exit criterion).
- Reword "claimed but did not settle" (dashboard) to explicit missed/failed/unknown per ADR-017.

**No code defects** found (no console errors beyond favicon; no broken layout at either width).

## Bottom line
Keep these as the visual north star. Before implementation, apply the [`cross-ui.md`](cross-ui.md) §4 revisions to fit **v1 = single-owner Gmail→Action (+ secondary web chat)**: personal Candidate Inbox instead of a team board, Gmail-only connectors, and P0/later relabeling — while preserving the (excellent) run/approval/candidate/trust patterns verbatim.
