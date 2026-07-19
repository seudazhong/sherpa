# UI Direction Comparison — v1 "Alpine warmth" vs v2 "Daybreak" (bright)

> Two complete, offline mockup sets of the same 5 screens (dashboard, chat, todo-board, connectors, gallery) with **identical content** — so they differ only in visual direction. Rendered and reviewed via Playwright at desktop width.
>
> - **v1 — Alpine warmth:** [`../design/index.html`](../design/index.html) · deep evergreen, **dark sidebar + dark hero**, warm-snow content.
> - **v2 — Daybreak (bright):** [`../design-bright/index.html`](../design-bright/index.html) · **light sidebar + pastel gradient hero**, bright teal + sunny amber, sunlit-white content. Shares one `base.css`.

## TL;DR

Both are polished and production-credible; content and IA are identical. The difference is **mood and weight**:

- **v1** reads **premium, focused, calm-serious** — the dark chrome frames content and gives "gravitas for carrying complex work."
- **v2** reads **approachable, optimistic, airy, modern-SaaS** — lighter cognitive weight, friendlier for a personal assistant.

**Recommendation:** For Sherpa's *personal-assistant* positioning, **v2 (Daybreak) is the better default base** — it feels calmer and more inviting for daily, at-a-glance use — provided two guardrails are met (contrast AA + restraint on simultaneous pastel tints, see §5). Keep **v1's dark chrome as the built-in dark theme.** They're not either/or: the shared token system makes shipping both themes cheap.

## 1. Side-by-side at a glance

| Dimension | v1 · Alpine warmth | v2 · Daybreak (bright) |
|---|---|---|
| Sidebar | **Dark evergreen** (`#17332B`) | **White / light** with teal active tint |
| Hero / run banner | **Dark green** block, light text | **Pastel gradient** (mint→sky→peach), ink text |
| Primary | Deep evergreen `#2F6F62` | **Bright teal `#10B3A5`** |
| Accent | Sunrise `#D9824B` | **Sunny amber `#FF9F45`** + sky `#38BDF8` |
| Content bg | Warm snow `#F6F4EE` | **Sunlit white `#F2FAFB`** |
| Overall luminosity | Medium-low (grounded) | **High (airy)** |
| Emotional tone | Premium · focused · serious | Optimistic · friendly · calm |
| Contrast | Strong (dark chrome vs light content) | Softer — needs AA verification on muted text |
| Depth | Warmer shadows, solid blocks | Softer shadows, more whitespace |

## 2. The decisive difference: chrome

The single biggest driver is the **sidebar + hero**:

- **v1's dark sidebar** anchors the eye, separates navigation from content, and lends a confident, "pro tool" feel. Downsides: heavier, more serious, can feel enterprise.
- **v2's light sidebar + pastel hero** dissolves the chrome into the content for an open, effortless feel that suits a *companion* that lives with you all day. Downsides: less separation between nav and content; pastel hero has lower text-contrast headroom.

## 3. Screen-by-screen notes (content is identical; only styling differs)

| Screen | v2 bright observations |
|---|---|
| **Dashboard** | Pastel run banner + white cards feel light and scannable; the "you can leave, I'll notify" banner reads friendlier. Slightly less visual hierarchy than v1's dark hero, which commanded attention more. |
| **Chat** | Excellent — pastel user bubble + white agent bubble; the amber **approval card** pops nicely against the light page (arguably *clearer* than v1). The tool **code block stays dark** for readability — good contrast anchor in a bright UI. |
| **Todo board** | Light columns on a bright page feel airy; source badges (Gmail/GitHub/Sherpa/Manual) stay legible. v1's grey columns gave marginally stronger column separation. |
| **Connectors** | Trust banner + brand-colored connector icons read well on white; the amber one-time-code chip stands out. Very close call with v1. |
| **Gallery** | Bright pastel hero + Daybreak swatches communicate the direction immediately. |

## 4. Pros & cons

**v2 Daybreak — pros:** inviting/optimistic; airy and modern; less fatiguing for glanceable daily use; the amber approval card is more prominent; shared `base.css` = trivially themeable. **Cons:** pastel + white risks lower contrast; many soft tints can compete; can read "lighter-weight/less premium"; light nav = weaker chrome separation.

**v1 Alpine warmth — pros:** premium, focused, strong figure/ground; dark chrome frames content and reduces UI noise; distinctive vs generic bright SaaS. **Cons:** heavier/serious; dark hero dominates; can feel enterprise for a personal companion.

## 5. Guardrails if we pick v2 (must-fix before build)

1. **WCAG AA (4.5:1)** for body/secondary text — bright pastels lose contrast fast; darken `--muted`/`--faint` and verify captions on the pastel hero.
2. **Tint discipline** — cap how many soft tints appear together (e.g., don't stack pastel banner + pastel cards + pastel pills in one viewport); let white do the work, reserve tints for meaning.
3. **Keep dark anchors** — the code block and (optionally) the run banner's dark variant give the bright UI needed contrast; keep at least one dark anchor per dense screen.
4. **Visible focus states** + keyboard order (a11y baseline) — especially approval buttons.

## 6. Recommendation & next step

- **Adopt v2 "Daybreak" as the default light theme** for the personal-assistant product; **retain v1's dark palette as the dark theme** (the token system already supports both).
- Apply the §5 guardrails, then fold the winning direction into the **v1-scope revisions** already agreed in [`cross-ui.md`](cross-ui.md) §4 (personal Candidate Inbox, Gmail-only connectors) so the *first-build* mockups are both bright **and** v1-scoped.

**Decision for the owner:** confirm the default theme — **v2 bright (recommended)**, v1 deep, or ship **both as light/dark**.
