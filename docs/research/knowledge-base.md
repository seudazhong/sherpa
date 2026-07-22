# R-KNOWLEDGE-BASE — Source-backed personal knowledge and cited retrieval

**Status:** research and design complete; implementation is not yet approved.

**Recommendation:** **GO for a narrow, file-backed Personal Knowledge vertical
slice**, but do not extend the current `memory_passages` table into a document
store and do not add a dedicated vector database. Before code, approve the scope,
author an ADR plus post-v1 contracts, and choose the embedding/privacy profile.

## 1. Executive answer

Sherpa is not missing RAG entirely. It already ships:

- bounded user facts in `user_memory`;
- manually authored semantic notes in `memory_passages`;
- OpenAI-compatible embeddings;
- pgvector cosine search plus PostgreSQL FTS, fused with reciprocal-rank fusion;
- `memory_note` / `memory_search` tools;
- REST and the Memory UI.

That is a useful **archival-memory notebook**, not a document knowledge-base
product. A real knowledge base still needs source and revision ownership, file
parsing, chunking, asynchronous indexing states, reindex/delete semantics,
citations, multilingual lexical retrieval, embedding-model migration, evaluation,
and explicit defenses for untrusted documents.

The product boundary should remain clear:

| Capability | Purpose | Canonical unit |
|---|---|---|
| Core memory | Stable facts/preferences about the user, injected into context | key/value fact |
| Archival memory | User/agent-authored durable notes recalled semantically | note/passage |
| Knowledge | Externally authored documents searched with source citations | source → version → chunk |
| Session search | Find and resume exact historical conversations/actions | session/turn/event |

`memory_passages` should remain archival memory. The knowledge subsystem should
use separate `knowledge_*` tables even though both reuse Postgres, pgvector, the
embedding provider, and hybrid retrieval helpers.

## 2. Current-state audit

### What already works

- `memory_passages` stores one text blob, a 1536-dimensional vector, embedding
  model tag, content hash, source string, and owner keys.
- Identical notes are deduplicated per user by SHA-256.
- Search performs a tenant/user-filtered vector branch and a PostgreSQL FTS
  branch, then RRF-fuses the two rankings.
- Users can add/list/delete notes in `/memory`; the agent can add and search them.
- Files already provide a separate MinIO-backed source primitive with content
  hashes and versions.

### Why this is not yet a knowledge base

- A passage is both the stored source and retrieval unit; there is no parent
  document or multi-chunk document.
- Files and passages are disconnected: upload/overwrite/delete never triggers an
  index lifecycle.
- Embedding is synchronous inside the REST/tool transaction, with no durable
  queued/processing/failed state.
- There is no parser, normalized document representation, structural chunking,
  page/heading locator, source revision, atomic active-version switch, or reindex.
- Search returns note text only, with no structural citation contract.
- Changing the embedding model/dimension can mix incompatible vectors or require a
  flag-day rebuild; the SQL column is fixed at 1536 dimensions.
- The lexical branch is hard-coded to PostgreSQL's `english` configuration.
  Chinese passages therefore rely mostly on the vector branch and lose the exact
  term/name/code benefit of hybrid retrieval.
- There is no retrieval golden set, citation check, prompt-injection treatment, or
  document-level deletion/export contract.
- The frozen contracts still accurately describe **v1**, where RAG was deferred;
  the post-v1 memory/RAG implementation has not yet been reconciled into a new
  ADR and frozen post-v1 contract.

## 3. Research findings

Stable patterns across OpenAI file search, Anthropic Contextual Retrieval,
Open WebUI, Dify, pgvector/PostgreSQL, and current RAG practice:

1. A useful knowledge product exposes a source lifecycle, not only a vector
   endpoint: add → queued → parsing → chunking → embedding → ready/failed →
   update/reindex/delete.
2. Hybrid lexical + vector retrieval is the safe default. Lexical search catches
   exact terms, identifiers, names, and dates that embeddings can miss.
3. Citations must be structured pointers to a source and location, not prose such
   as "according to your files."
4. Source content and search indexes have different durability roles. Raw source
   and version metadata are canonical; chunks, FTS, and embeddings are derived and
   rebuildable.
5. Embedding model identity and dimension are schema concerns. A provider/model
   change requires parallel re-embedding and a controlled cutover.
6. Reranking and model-generated contextual chunk summaries can improve quality,
   but they add latency/cost and should follow measured retrieval failures rather
   than ship by default.
7. Retrieved documents are untrusted data. They must not become instructions or
   bypass Sherpa's tool policy and approval gates.
8. For Sherpa's expected single-user scale, Postgres + pgvector + PostgreSQL
   lexical search is sufficient and operationally preferable to a second vector
   service.

## 4. Product design

### 4.1 Information architecture

Keep **Memory** and **Knowledge** separate:

- **Memory**: "what Sherpa remembers about me" — facts and semantic notes.
- **Knowledge**: "documents Sherpa may consult" — explicit sources, status,
  search, and citations.

Do not expose an empty Knowledge navigation item before the vertical slice ships.
The first increment has one implicit private library per user; multiple named
libraries, sharing, and ACL management are deferred until demonstrated necessary.

### 4.2 First useful user flow

1. Open **Knowledge**.
2. Choose an existing file from Files, or upload a supported file that is first
   persisted through the Files capability.
3. See honest indexing phases and any bounded failure reason.
4. Search the library and inspect matching excerpts with source/page/heading.
5. Ask in chat; Sherpa calls `search_knowledge` and answers with clickable
   citations.
6. Overwrite a source file; the source becomes `stale` and can reindex to a new
   version without exposing a half-built index.
7. Remove a source; it disappears from retrieval immediately and derived rows are
   purged durably.

### 4.3 UI surfaces

**Knowledge home**

- source name/type;
- `queued | parsing | chunking | embedding | ready | stale | failed | deleting`;
- file version, indexed time, chunk count, and bounded error;
- actions: add, retry/reindex, open source, remove.

**Source detail**

- source metadata and current active revision;
- parsing/indexing timeline;
- preview with page/heading anchors;
- reindex and remove controls;
- embedding/profile disclosure.

**Search test**

- query input;
- ranked excerpts grouped by source;
- retrieval mode badges (lexical/vector/both);
- stable citation links.

**Chat**

- citation chips that open the exact source locator;
- an explicit "insufficient evidence in Knowledge" result rather than an
  uncited answer presented as grounded.

## 5. Recommended architecture

### 5.1 Source of truth

```text
Files / explicit future connector source
        │
        ▼
knowledge_sources ──► knowledge_source_versions  (canonical metadata)
                              │
                    immutable source snapshot
                              │
                       durable ingestion job
                              │
                              ▼
                    knowledge_chunks             (derived text + locators)
                       │               │
                       ▼               ▼
                 lexical index    chunk embeddings (derived/rebuildable)
                       └──── hybrid retrieval ────┘
                                  │
                                  ▼
                         cited context for core
```

The `files` row is the user-visible origin, but it is not revision storage: today's
Files overwrite replaces and deletes the old blob. Each knowledge source version
therefore owns an **immutable object-store snapshot** of the exact file
version/hash it indexed. That snapshot is canonical for parsing, preview, and
citations until the knowledge version is purged. The active pointer references one
fully indexed source revision; search never reads partially built revisions.

### 5.2 Proposed tables

Names are design proposals, not frozen contracts.

| Table | Important fields and invariants |
|---|---|
| `knowledge_sources` | `tenant_id`, `id`, `user_id`, `source_kind`, `file_id`, display name, `visibility`, `trust_level`, lifecycle status, `active_version_id`, monotonic `desired_generation`, optional tombstone; first release permits private file-backed sources only |
| `knowledge_source_versions` | source generation/revision, expected file version/hash, immutable `snapshot_object_key`, parser + pipeline version, embedding-profile ID, language, status, counts, bounded failure code, timestamps; unique idempotency key per source content/pipeline |
| `knowledge_chunks` | source-version FK, stable ordinal, original text, token count, heading path, page/offset locator, content hash, versioned lexical text/`tsvector`, vector under the first release's pinned embedding profile |
| `embedding_profiles` | provider kind, model, dimension, normalization, privacy mode; the first release has one reviewed active profile |
| `knowledge_ingestion_jobs` | source/version/generation binding, stage, lease/claim owner, attempt count, named termination reason, timestamps; supports bounded retry, recovery, and fencing |
| `knowledge_retrieval_evidence` | retrieval invocation ID, run/tool-call binding, globally unique citation reference, source/version/chunk IDs, bounded excerpt, retention/tombstone state; replayable but removable without putting document text in the append-only journal |

Every table carries `tenant_id` and composite tenant-scoped keys. Team sharing and
RLS activation follow ADR-015's general team/hosted gate; the first release remains
private to one owner.

### 5.3 Ingestion pipeline

The API/service transaction:

1. validates that the caller owns the file;
2. creates or updates `knowledge_sources`;
3. advances `desired_generation` and creates a queued source version/job with the
   expected file version/hash and a deterministic idempotency key;
4. writes a transactional outbox/recovery record in the same commit. Queue
   delivery is at-least-once; a recovery sweep re-enqueues committed work that was
   not delivered.

The worker performs bounded, resumable stages:

1. **Claim and snapshot** — lease the bound generation, verify the file still has
   the expected version/hash, and copy it to the source version's immutable object
   key. A changed or missing file terminates with a named reason instead of
   indexing the wrong bytes.
2. **Read and validate** — allowlisted MIME/type, size/page/time limits; no archive
   extraction or remote fetching in the first release.
3. **Parse and normalize** — no-tool processing; remove active HTML/script
   behavior; retain page/heading/offset provenance.
4. **Chunk** — structural sections first, then bounded child chunks. Start near
   300–600 tokens with modest overlap, but tune against the golden set rather than
   freezing folklore defaults.
5. **Embed in batches** — record profile/model/dimension and generation cost;
   retry only missing deterministic batches with bounded attempts.
6. **Index and activate** — write all chunks/embeddings for the new version, then
   atomically switch `active_version_id` only when the source is not tombstoned and
   its `desired_generation` still equals the job generation. An obsolete job can
   never reactivate an old version.

Failure leaves the previous ready version active. Every exit has a named reason
and visible state. Claims expire and can be recovered without allowing two
workers to activate different generations.

File lifecycle is explicit:

- overwrite marks linked knowledge sources `stale`, advances their desired
  generation, and may enqueue reindex; the previous active snapshot stays
  searchable until a replacement activates;
- deleting a file tombstones linked knowledge sources and removes them from the
  searchable active set in the same database transaction, then durably purges
  snapshots/chunks/vectors;
- removing a knowledge source does not delete the underlying user file.

### 5.4 Retrieval

1. Normalize the query and create its embedding under the active profile.
2. Filter by tenant, user, visibility, ready source, and active source version
   **before ranking**.
3. Run separate lexical and vector candidate queries.
4. Fuse ranks with RRF.
5. Deduplicate overlapping chunks and cap results per source.
6. Assemble a bounded context with structured citation metadata.
7. Return no-evidence explicitly when the threshold is not met.

The first cut should use exact tenant-filtered vector search at small scale.
Enable or rely on HNSW only after recall/latency measurements; approximate indexes
can under-return with selective tenant/visibility filters.

Reranking and query rewriting remain pluggable later stages. They should ship only
when the golden set demonstrates a material gain.

### 5.5 Chinese and mixed-language lexical search

The current `english` FTS parser is not suitable for Chinese word boundaries, and
switching only to PostgreSQL's `simple` dictionary does not solve the parser-level
segmentation problem.

Design the contract around a stable logical configuration name such as
`sherpa_text`, not a hard-coded language:

1. Prefer a maintained PostgreSQL CJK parser such as `zhparser` if its packaging,
   Postgres-version support, and operational requirements pass a focused spike.
2. Keep a documented fallback using application-versioned CJK tokenization stored
   as lexical text and indexed with the `simple` configuration.
3. Add `pg_trgm` only as a language-agnostic fuzzy/substring signal, not as the
   sole relevance engine.
4. Continue vector retrieval for cross-lingual semantics.

The query-side configuration/tokenizer version must match the indexed version.
Mixed Chinese/English retrieval gets its own regression queries; it must not
silently degrade to vector-only.

The already-shipped `memory_passages` path has the same bug and should receive a
small, separate contract/ADR-backed correction rather than waiting for the full
knowledge feature.

### 5.6 Embedding provider and migration

Add an `EmbeddingProvider` seam separate from the chat provider. The first release
pins one reviewed embedding profile and records its identity on every indexed
version. It must support:

- the current OpenAI-compatible hosted embedding path;
- an explicit self-hosted multilingual option, with BGE-M3 as a candidate to
  benchmark rather than an unreviewed mandatory dependency;
- visible disclosure when document text leaves the deployment;
- profile records containing model, dimension, normalization, and version.

The first release handles a model change as an explicit full reindex under a newly
approved schema/profile; it does not implement concurrent multi-profile serving.
Parallel re-embedding and atomic profile cutover are deferred until a real
provider/model migration requires them. Never mix vectors from different profiles
inside one search index.

### 5.7 Context and citation contract

`search_knowledge` returns bounded structured hits:

```json
{
  "query": "…",
  "retrieval_invocation_id": "…",
  "hits": [
    {
      "citation_ref": "K:<tool_call_id>:1",
      "source_id": "…",
      "source_version_id": "…",
      "chunk_id": "…",
      "title": "…",
      "locator": {"page": 3, "heading": "Deployment"},
      "excerpt": "…",
      "score": 0.0,
      "matched_by": ["lexical", "vector"]
    }
  ]
}
```

The provider still receives search output as a normal `role=tool` result, matching
the current core protocol. A stable cached instruction says that retrieval tools
return untrusted evidence, never executable instructions; the dynamic tool result
contains the labeled excerpts and citation references.

Structured citations cannot live only in `llm_content`: the milestone must extend
the persisted tool-result/event shape to carry a retrieval invocation ID plus
citation references (or finally wire the existing `ToolResult.return_display`
through the core). A reference is unique within the run by including the tool-call
namespace, for example `K:<tool_call_id>:1`; a bare `K1` is invalid because one run
may search more than once.

The append-only event stores references and bounded metadata, not document
excerpts. The retention-scoped `knowledge_retrieval_evidence` rows hold the
provider-visible evidence used for crash/history replay. Core execution must
therefore decouple the full provider `llm_content` from the redaction-safe event
payload instead of persisting the same raw string for both.

History replay resolves each reference through the evidence store and current
source tombstone state. The final answer may cite only references from that run's
persisted map; unknown references render as plain text, not links. Chat resolves a
valid reference to source/version/locator.

The model may use evidence to propose an action, but every tool still passes the
normal policy and approval gates. A document cannot grant permission, change tool
scope, or instruct Sherpa to reveal secrets.

## 6. Service, API, and agent-tool surface

Follow ADR-023: one capability layer, thin REST and Tool adapters.

### Services

- `list_knowledge_sources`
- `add_file_source`
- `get_knowledge_source`
- `reindex_knowledge_source`
- `remove_knowledge_source`
- `search_knowledge`

### REST

- `GET /knowledge/sources`
- `POST /knowledge/sources` with an owned `file_id`
- `GET /knowledge/sources/{id}`
- `POST /knowledge/sources/{id}/reindex`
- `DELETE /knowledge/sources/{id}`
- `POST /knowledge/search`

### Tools

- `list_knowledge_sources` — read-only;
- `add_knowledge_source` — own-data idempotent write, explicit file ID;
- `search_knowledge` — read-only, returns citations;
- `reindex_knowledge_source` — own-data idempotent write;
- `remove_knowledge_source` — destructive and approval-gated.

No agent tool may silently index Gmail, arbitrary URLs, or every file. Adding a
source requires an explicit authenticated-user instruction.

## 7. Security and lifecycle

- Direct user-selected files are still untrusted document content; parsing and
  chunking are no-tool, bounded operations.
- Future connector/web sources require a dedicated source adapter and the same
  isolation posture as `CONNECTOR_ANALYSIS`; they do not enter a tool-bearing
  model call during ingestion.
- HTML is sanitized and never fetches remote subresources. OCR, archives, web
  crawling, and executable document formats are deferred.
- Source credentials, when future connectors arrive, reuse the AEAD credential
  vault; no knowledge-specific secret path.
- Every query is tenant/user/visibility scoped in SQL before ranking.
- Export/delete includes source metadata, snapshots, versions, chunks, embeddings,
  and retention-scoped retrieval evidence. Source deletion revokes citation links
  and causes history reconstruction to substitute `[knowledge source deleted]`
  rather than replaying old excerpts.
- Prior assistant prose in an already persisted conversation remains governed by
  session retention/deletion, not source deletion; the UI must state this
  distinction rather than imply that deleting a source rewrites every past answer.
  Backups' deletion limits must be documented separately.
- Audit receipts record add/reindex/remove and which cited sources supported a
  grounded answer, without storing chain-of-thought.

## 8. Evaluation and release gates

Do not build a general RAGAS platform now. Use ADR-024's existing single-owner
posture: a small deterministic regression lane now, broader evaluation in roadmap
milestone #11.

Minimum golden set before release:

- 20–30 representative files;
- at least 30 Chinese, English, and mixed-language queries;
- exact-name/code/date questions and semantic paraphrases;
- no-answer questions;
- expected source/chunk labels.

Initial gates:

- retrieval Recall@5 target at least 0.85 on the agreed golden set;
- every rendered citation resolves to the active source version;
- no cross-user/tenant result in service and raw-query isolation tests;
- duplicate add/reindex is idempotent;
- failed reindex leaves the previous version searchable;
- delete removes the source from retrieval immediately and eventually leaves no
  orphaned chunks/embeddings;
- Chinese exact-term cases exercise a lexical signal rather than vector-only
  fallback;
- bounded retrieval/context output and explicit insufficient-evidence behavior.

Record retrieval latency, candidate counts, source diversity, active embedding
profile, and citation presence in existing trace/audit surfaces.

## 9. Options considered

| Option | Decision | Reason |
|---|---|---|
| Reuse/expand `memory_passages` for documents | Reject | Notes and source-backed documents have incompatible ownership, revision, chunk, citation, and deletion semantics |
| Separate Knowledge subsystem on Files + Postgres FTS + pgvector | **Recommend** | Reuses existing seams, preserves ACID/tenant scoping, and creates a complete user-visible flow |
| Dedicated vector database now | Reject | Adds deployment, backup, consistency, and tenancy work without current scale evidence |
| Auto-index Gmail/connectors | Reject for first release | Violates explicit-source and untrusted-content boundaries; needs its own adapter and product consent |
| Reranker/contextual LLM chunking by default | Defer | Useful only after measured misses; adds ingest/query cost and complexity |

## 10. Go / no-go decision

**GO — but only for the narrow vertical slice above.**

Reasons:

1. It turns two already-shipped primitives (Files and pgvector memory) into a
   visible core-agent capability: "ask my documents and show where the answer came
   from."
2. It can remain inside the existing Postgres/Redis/MinIO/web/worker stack.
3. It aligns with the product priority of user-visible UI and the core agent path,
   unlike adding a broad infrastructure platform.
4. The missing work is well-bounded when file-backed sources, one private library,
   and citations are the only first-release promise.

Implementation remains **not approved** until the owner confirms these gates:

1. first release = private file-backed Knowledge only;
2. no crawler, connector sync, multiple libraries, team sharing, OCR, or dedicated
   vector service;
3. embedding privacy choice is explicit;
4. proposed ADR and contract changes are reviewed;
5. a static desktop/mobile flow and the small retrieval golden set exist before
   backend work.

If approved, this is a strong user-value candidate relative to broad
cron/provider/plugin infrastructure, but the owner still decides whether and where
it enters the roadmap. Deliver it as small vertical tasks:

1. **KB0** — ADR/contracts, capability matrix, static UI flow, embedding/CJK spike.
2. **KB1** — source/version/job schema and file-backed lifecycle.
3. **KB2** — bounded parsers, structural chunking, embedding profiles, async index.
4. **KB3** — hybrid retrieval, citations, Chinese/English golden tests.
5. **KB4** — services + REST + tools + permission policy.
6. **KB5** — Knowledge UI, chat citations, agent and human Playwright lanes.

## 11. Proposed ADR/contract changes before implementation

Do not edit frozen contracts until the owner accepts the go decision. The accepted
change should:

- add an ADR separating archival memory from source-backed Knowledge;
- select Postgres FTS + pgvector and define the scale trigger for reconsidering a
  dedicated store;
- define canonical source/version data versus rebuildable chunks/embeddings;
- freeze immutable source snapshots, ingestion status, outbox/recovery, job
  fencing, active-version switching, file-change/delete behavior, and embedding
  profile/reindex semantics;
- freeze search/citation event schemas, history replay, and untrusted-evidence
  handling;
- extend data-model, API, events/effects, and config/secrets contracts;
- add Knowledge rows to the UI/agent capability matrix.

## 12. Primary references

- OpenAI file search:
  <https://developers.openai.com/api/docs/guides/tools-file-search>
- Anthropic Contextual Retrieval:
  <https://www.anthropic.com/engineering/contextual-retrieval>
- pgvector:
  <https://github.com/pgvector/pgvector>
- PostgreSQL full-text search:
  <https://www.postgresql.org/docs/current/textsearch-intro.html>
- PostgreSQL text-search parsers:
  <https://www.postgresql.org/docs/current/textsearch-parsers.html>
- PostgreSQL `pg_trgm`:
  <https://www.postgresql.org/docs/current/pgtrgm.html>
- `zhparser`:
  <https://github.com/amutu/zhparser>
- Open WebUI Knowledge:
  <https://docs.openwebui.com/features/workspace/knowledge/>
- Microsoft indirect prompt-injection defenses:
  <https://learn.microsoft.com/en-us/security/zero-trust/sfi/defend-indirect-prompt-injection>
- PostgreSQL + pgvector hybrid search and RRF:
  <https://jkatz05.com/post/postgres/hybrid-search-postgres-pgvector/>
- BGE-M3 model card:
  <https://huggingface.co/BAAI/bge-m3>
