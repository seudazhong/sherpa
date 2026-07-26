# postgres-zhparser — Sherpa Postgres image (pgvector + zhparser)

Combined image for the ADR-036 Knowledge hybrid retrieval: `pgvector/pgvector:pg16`
plus **SCWS** + the **zhparser** text-search parser, so a `sherpa_text` config can
segment Chinese for the lexical branch (the vector branch keeps using `bge-m3` via the
`ollama` service).

Build:

```bash
docker build -t sherpa-pg-zhparser:pg16 infra/postgres-zhparser
```

One-time DB setup (KB1 will apply this via an Alembic migration):

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS zhparser;
CREATE TEXT SEARCH CONFIGURATION sherpa_text (PARSER = zhparser);
ALTER TEXT SEARCH CONFIGURATION sherpa_text
  ADD MAPPING FOR a,b,c,d,e,f,g,h,i,j,k,l,m,n,o,p,q,r,s,t,u,v,w,x,y,z WITH simple;
```

**GUC gotcha (KB1):** `zhparser.multi_short = on` improves recall (emits both the
compound word and its components). A per-session `SET` only affects that session;
**generated `tsvector` columns compute with server defaults**, so set it at the server
level (`ALTER SYSTEM SET zhparser.multi_short = on;` / `postgresql.conf`) — or populate
the lexical `tsvector` via an explicit trigger/update on a connection that has `SET` it.

## KB0 spike outcome (2026-07-26) — PASS

De-risked the ADR-036 CJK decision (see `docs/research/knowledge-golden-set.md §5`):

1. **Builds on `pgvector/pgvector:pg16`** — SCWS 1.2.3 + zhparser compile and install
   cleanly (extension + `dict.utf8.xdb` into `tsearch_data`).
2. **Segments Chinese** — `to_tsvector('sherpa_text','本季度预算的审批阈值是多少')` →
   `季度 / 预算 / 审批 / 阈值 / …` (words, not one blob or single chars).
3. **Hybrid works + each branch contributes** (mini corpus, real `bge-m3` embeddings via
   the `ollama` service, RRF fusion):
   - exact-code `6602-05 对应什么费用` → lexical ranks the right chunk above the
     near-identical `6602-03` chunk (the exact `05` token disambiguates); vector agreed;
     RRF top = correct.
   - paraphrase `花一大笔钱需要谁点头` → lexical NO MATCH (zero word overlap), vector
     rescues it; RRF top = correct.

**Decision:** keep `KNOWLEDGE_LEXICAL_BACKEND=zhparser`; the `app_jieba` fallback stays
documented but unused. **KB1** wires this image into `infra/docker-compose.yml` and adds
the `sherpa_text` config migration.
