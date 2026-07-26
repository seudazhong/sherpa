# design-knowledge — Knowledge (source-backed KB) static UI

Static, self-contained mockup for the **Knowledge** vertical slice (KB0), landing
the R-KNOWLEDGE-BASE design ([`../research/knowledge-base.md`](../research/knowledge-base.md)).
Open `index.html` directly in a browser (no build). Tabs switch the four surfaces;
pure HTML/CSS, no JS.

**Owner-locked decisions (2026-07-26):**

- First release = **private, file-backed** Knowledge only (no crawler/connector
  sync, multiple libraries, team sharing, OCR, or dedicated vector DB).
- Embedding = **local `ollama` / `bge-m3`, 1024-d** (document text never leaves the
  box); reuses the just-shipped embedding seam (ADR-032).
- CJK lexical retrieval = **`zhparser`** behind a stable `sherpa_text` text-search
  config (app-layer jieba fallback documented); hybrid with pgvector via RRF.
- **Static UI + retrieval golden set before backend** (research gate).

**Surfaces (research §4.3):**

1. **Knowledge 主页** — source list with lifecycle pills
   (`queued|parsing|chunking|embedding|ready|stale|failed`), add-from-Drive,
   reindex/retry/remove, embedding disclosure banner.
2. **来源详情** — indexing timeline (claim→snapshot→parse→chunk→embed→activate),
   metadata, immutable-snapshot/active-version, embedding-profile disclosure,
   preview with page/heading anchors.
3. **检索测试** — hybrid results grouped by source, `both|lexical|vector` badges,
   zhparser CJK exact-term hits, structured `K:<tool_call_id>:N` citation refs, RRF
   scores.
4. **Chat 引用** — inline citation chips → source locator; explicit
   **insufficient-evidence** state instead of an uncited answer.

**Notes:** SPA route is `/library` (avoids the REST `/knowledge/*` proxy prefix).
Direction sketch only — not final visuals/copy. Next KB0 pieces: ADR + post-v1
contract deltas + capability-matrix rows + the retrieval golden set.
