# Sherpa — Quiet Work UI

`Quiet Work` is the production UI direction introduced after the 2026-07-22 Playwright review. It takes cues from Notion's content-first restraint without copying its layout or brand.

## Design goals

1. **Quiet chrome** — warm neutral surfaces, thin borders, minimal shadow, one restrained green accent.
2. **Content first** — page titles orient quickly; cards exist only when they group a real task or state.
3. **Progressive disclosure** — technical IDs, webhook details, and test consoles stay behind disclosures.
4. **Honest agent state** — live status, approvals, tool receipts, schedules, and data controls remain explicit.
5. **Responsive by construction** — desktop sidebar becomes an accessible mobile drawer; every form and list reflows without horizontal scrolling.

## Core system

| Layer | Direction |
|---|---|
| Canvas | Warm off-white `#F7F7F5` |
| Sidebar | Near-white `#FBFBFA`, 232 px desktop |
| Text | Graphite `#242523`; secondary `#72716D` |
| Accent | Sherpa green `#47705B`, used sparingly |
| Shape | 8–12 px radii; pills only for status |
| Depth | Thin borders first; shadows reserved for login, composer, and overlays |
| Type | Native system stack for fast, familiar rendering |

## Product patterns

| Surface | Pattern |
|---|---|
| Chat | Rendered Markdown, plain agent prose, quiet user bubbles, collapsible run activity, anchored composer |
| Inbox | Four at-a-glance metrics followed by review, approval, todo, and notification sections |
| Activity | Timeline-like receipts plus a separate, less prominent data-control card |
| Schedules | Two task-focused creation cards and an honest upcoming-delivery list |
| Settings | Delivery and timing groups with consistent switches and labeled controls |
| Memory / Files | Creation first, then compact durable-item lists |
| Messaging / Connectors | Friendly status summaries first; technical details and simulations disclosed on demand |
| Mobile | 56 px top bar, modal navigation drawer, single-column forms, wrapping list actions |

## UX guardrails

- Do not surface implementation identifiers unless they help diagnose or complete a task.
- Do not make destructive actions visually equal to everyday actions.
- Empty states must explain the next useful step, not only report zero items.
- Agent output must be rendered as readable Markdown; raw formatting tokens are not product UI.
- New pages must work at 390 px without horizontal scrolling before they are considered complete.
