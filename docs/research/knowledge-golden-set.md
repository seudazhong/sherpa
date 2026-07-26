# Knowledge retrieval golden set — spec (KB0, ADR-036)

Status: **spec only** (the actual fixtures + labeled queries are produced in **KB3**,
before hybrid retrieval is accepted). This defines the shape, taxonomy, and gates so
retrieval quality is measured, not asserted. Aligns with R-KNOWLEDGE-BASE §8 and the
single-owner eval posture (ADR-024): a small deterministic regression lane now, broad
evaluation deferred to roadmap #11.

## 1. Why a golden set

Hybrid retrieval (zhparser lexical + pgvector, RRF) is only trustworthy if we can show
it beats vector-only on exact terms and does not regress on paraphrase. The set is the
release gate for KB3 and the regression lane thereafter. It must be **deterministic**:
fixtures are checked-in files; embeddings run against the bundled local `ollama/bge-m3`
profile (or the mock embedding in CI), never a networked model in tests.

## 2. Corpus (fixtures)

- **20–30 representative files** under `backend/tests/fixtures/knowledge/`.
- Formats spanning the allowlist: PDF, Markdown, DOCX, TXT.
- **Language mix:** ~40% Chinese, ~40% English, ~20% mixed CN/EN in one doc.
- Include documents with: numbered sections/headings, tables, exact identifiers
  (codes, dates, amounts, product/person names), and near-duplicate passages across
  two files (to exercise per-source dedup/capping).
- Each file has a stable `source_id` label and, after chunking, deterministic chunk
  ordinals; the expected-answer labels below point at `{source_id, chunk_ordinal}` (not
  raw text offsets, which drift with the chunker).

## 3. Query set (≥30 labeled queries)

Stored as JSONL at `backend/tests/fixtures/knowledge/golden_queries.jsonl`. Each row:

```json
{
  "id": "q-cn-exact-01",
  "query": "季度预算的审批阈值是多少",
  "lang": "zh",
  "kind": "exact_term",
  "expected": [{"source_id": "budget_zh", "chunk_ordinal": 7}],
  "expected_matched_by": ["lexical"],
  "note": "审批/预算 must be caught by zhparser lexical, not vector-only"
}
```

Required coverage (min counts across the ≥30):

| kind | ≥ | Purpose |
|---|---|---|
| `exact_term` (name/code/date/amount) | 10 | The lexical/zhparser win; must not degrade to vector-only |
| `paraphrase` (semantic, no shared words) | 8 | The vector/bge-m3 win |
| `mixed_lang` (CN query over EN doc or vice versa) | 6 | Cross-lingual + hybrid; own regression lane |
| `multi_source` (answer spans ≥2 files) | 3 | Dedup + per-source cap + citation grouping |
| `no_answer` (not in the corpus) | 5 | Must return `sufficient=false`, never fabricate |

`no_answer` rows have `"expected": []` and assert the "insufficient evidence" path.

## 4. Metrics + gates (KB3 release gate)

Run over the full corpus with the active embedding profile; measure:

- **Recall@5 ≥ 0.85** on answerable queries (expected chunk in top-5 hits).
- **Citation resolvability = 100%**: every returned `citation_ref` resolves to the
  active source version + a real chunk/locator.
- **CJK lexical signal**: for `exact_term` zh queries, the expected hit's `matched_by`
  includes `lexical` (proves zhparser, not vector-only fallback).
- **No-answer precision**: 100% of `no_answer` queries return `sufficient=false`.
- **Isolation**: service- and raw-SQL-level tests return **zero** cross-user/tenant
  rows for any query.
- **Idempotency/lifecycle** (adjacent, not retrieval-quality): duplicate add/reindex is
  idempotent; a failed reindex leaves the previous `ready` version searchable; delete
  removes the source from retrieval immediately and eventually leaves no orphan
  chunks/vectors.

Record retrieval latency, candidate counts (lexical/vector/returned), source diversity,
active profile, and citation presence in the existing trace/audit surfaces.

## 5. zhparser / embedding spike (KB0, before KB1)

Timeboxed (~1 day), gates the CJK decision:

1. Can `zhparser` build/install on the `pgvector/pgvector:pg16` image (custom combined
   image or extension package), create a `sherpa_text` TS config, and index a sample?
2. On a ~10-doc Chinese slice, does zhparser lexical + vector (RRF) beat vector-only on
   `exact_term` zh queries by a clear margin?

**Pass** → ship `KNOWLEDGE_LEXICAL_BACKEND=zhparser`. **Fail/too costly** → fall back to
`app_jieba` (application-tokenized `lexical_text` + Postgres `simple`), same
`sherpa_text` logical name so the rest of the system is unchanged. Record the outcome
in the ADR-036 follow-up + STATUS.

## 6. Out of scope (now)

General RAGAS/LLM-graded answer quality, reranker A/B, large multilingual benchmark
suites — deferred to roadmap #11 (eval flywheel). This set is the minimum that makes
KB3's hybrid retrieval measurable and regression-safe.
