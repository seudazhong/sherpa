# R-MEMORY — Agent memory: survey and a redesign for Sherpa

**Status:** research and design complete; implementation **not** approved. Written
after a live failure was observed (see §1). This document supersedes nothing yet;
approving it means authoring **ADR-031** (extends/supersedes ADR-004, revisits the
ADR-012 deferral) plus post-v1 contract edits before any code.

**Recommendation:** **GO** for a **tiered memory redesign** on the existing
Postgres + pgvector + Redis stack — no new datastore. Replace the free-form
exact-match key/value core with a small set of **named, bounded, always-in-context
memory blocks**; keep `memory_passages` as the **on-demand semantic tier** and add
**asynchronous background formation** with a **deterministic merge** (add / update /
invalidate) so facts converge instead of duplicating. Move memory **out of the
cached system-prompt prefix** (it currently violates docs/04 铁律 ⑤). Phase the
work; gate the heavy semantic-ranking pieces on a golden-set, exactly like
session-search.

---

## 1. Why this document exists (the observed failure)

A user told Sherpa two emails and a name across turns. Sherpa saved three facts
(UI confirms: `name`, `personal.email`, `work.email`). In a **later** chat, asked
to "email my todos to my personal address," the agent called
`memory_user_get('personal_email')` and got `no memory stored for 'personal_email'`,
then told the user it had no personal email — **even though `personal.email` was
already injected into that turn's system prompt.**

Two root causes, both structural — not a one-off model mistake:

1. **Exact-match, free-form keys.** `user_memory` is a `(tenant,user,memory_key)`
   primary-key lookup (`backend/app/services/memory.py:38-40`). Keys are invented
   by the model per call; nothing is canonical. The writer chose `personal.email`;
   a later reader, in a fresh session with no memory of that string, guessed
   `personal_email`. `.` ≠ `_` ⇒ different PK ⇒ miss. No normalization, aliasing,
   fuzzy, or semantic fallback exists.
2. **A redundant lookup overrode already-injected context.** `_load_core_memory`
   (`backend/app/core/loop.py:274-291`) renders the whole table into the system
   prompt every run, so the fact was *right there*. The model still issued a keyed
   `memory_user_get`, and trusted the failed tool result over its own context.

The fix is not "add key normalization" (that is a band-aid, tracked as Phase 0
below). The convergent industry answer is: **the always-needed facts should live
in one small block that is always in context, so there is no key to guess and no
lookup to miss.** That reframes the whole subsystem — hence this redesign.

---

## 2. Current state (what Sherpa ships today)

| Tier | Store | Read path | Write path | Retrieval |
|---|---|---|---|---|
| Core (facts) | `user_memory` KV, PK `(tenant,user,key)`, key `^[a-z][a-z0-9_.-]{0,63}$`, value ≤16 KiB, `version` CAS | **Whole table** injected into the **system prompt** each run (`_load_core_memory`) | `memory_user_set/delete` tools + REST + Memory UI | **Exact key** `db.get(...)` |
| Archival (notes) | `memory_passages` + pgvector(1536) + PG FTS, fused RRF; embeddings via litellm, deterministic fakes in tests | Not pre-injected; `memory_search` tool | `memory_note` tool + REST (manual only) | Semantic + lexical, RRF |
| Episodic | `sessions/runs/messages/parts` + `session_search_entries` (ADR-029) | `session_search` tool / Session Library UI | automatic (journal projection) | FTS + trigram + bigram |

Gaps vs. the field: **no auto-extraction/consolidation, no conflict resolution or
dedup on write, no importance/recency ranking, no forgetting/history, no
cache-stable injection.** Two contract facts to respect: `user_memory` is declared
"the only v1 memory store … no embeddings, similarity search"
(`data-model.md:1808`); and docs/02 originally specified memory injected as a
`<memory-context>` block **on the user message, never the system prompt** (docs/02
line 45) — the implementation deviated and now breaks the cached prefix.

---

## 3. Taxonomy (the axes every system varies on)

- **Memory types:** *working/core* (always in context) · *semantic* (facts about
  the world/user) · *episodic* (past events/conversations) · *procedural*
  (behavior rules / the agent's own prompt).
- **Read path:** *always-injected* (cheap, bounded) vs *retrieved on demand* (tool
  call / top-k pre-fetch) vs *paged* (OS-style syscall).
- **Write path:** *hot path* (explicit tool calls mid-turn) vs *background
  formation* (async extraction after the turn).
- **Conflict/update semantics:** *overwrite* vs *merge (add/update/delete)* vs
  *ADD-only + temporal metadata* vs *bi-temporal soft-invalidation*.
- **Retrieval ranking:** exact key · semantic only · hybrid (semantic + BM25 +
  graph, RRF) · scored (recency + importance + relevance).
- **Consolidation / forgetting:** char caps · summarization/eviction · reflection ·
  invalidation.
- **Cross-cutting for Sherpa:** cache-stable prefix · deterministic tests (no real
  model calls) · single-user but tenant-forward · self-hosted (no new infra).

---

## 4. Survey (what the leaders actually do)

**Letta / MemGPT** (`C:\src\letta`; arXiv:2310.08560). "LLM as an OS": *core
memory* = named blocks (`human`, `persona`) always rendered into the system prompt
at a `{CORE_MEMORY}` placeholder, hard **char** cap (100 k default) enforced at
write time with `chars_current/chars_limit` shown to the model; *recall* = full
message history, paged via `conversation_search`; *archival* = pgvector passages,
paged via `archival_memory_search`. Self-edits: `core_memory_append/replace`,
`memory_insert` (line-precise), `rethink_memory` (block rewrite). **Sleeptime
agents** run fire-and-forget after a turn and rewrite shared blocks; a summarizer
evicts at 90 % context. Blocks use optimistic `version` locking; embeddings are
Redis-cached by `{model}:{endpoint}:{text}`.

**Hermes** (`C:\src\hermes-agent`). Two bounded markdown files — `MEMORY.md`
(**2200** chars) and `USER.md` (**1375** chars), entries split by `§`, injected into
a **volatile** system-prompt tier that is **snapshotted at session start and never
mutated mid-session** (preserves the KV prefix cache; ~26 % cost win noted). Writes
go to disk immediately but are only picked up next session. Overflow returns a
structured "consolidate first" error (max 3/turn). A **background review fork**
fires **every 10 user turns**: a forked agent sharing the parent's history + cached
prompt, tool-scoped to `memory`+`skill`, decides surgical `add/replace/remove`.
Entries are threat-scanned at snapshot time (`[BLOCKED: …]`).

**Sydney** (`C:\src\SydneyDocV2`). **Four** stores + a user profile: *saved memory*
(Annotation Store, LLM-extracted, user-editable, injected as a prompt section);
*long-term inferred* (offline-processor summaries by `knowledge`/`behavior`/
`conversation`, versioned); *short-term* (~50 latest utterances, Entity Serve);
*relevant conversation history* (Substrate Search V3, **on-demand** via a
`personal_context` tool — the only tier not pre-injected). Reads are cache-first,
distilled into synthetic internal-summary messages, then rendered into named prompt
sections with per-section token budgets. Writes go through `record_memory` →
**incremental-summary LLM** over *old memory + new info* that returns
**add/edit/delete** ops ("I moved teams" edits, doesn't append).

**Mem0** (mem0.ai; arXiv:2504.19413). **Async, post-response** pipeline. Paper v2:
*extract* candidate facts (latest messages + rolling summary) → *update* step where
a second LLM compares each candidate to top-k similar memories and picks exactly one
of **ADD / UPDATE / DELETE / NOOP** (contradiction ⇒ DELETE+ADD; duplicate ⇒ NOOP).
Production v3: **ADD-only** + hash-dedup + entity linking + a temporal pass tagging
`event_at`, `is_ongoing`, `memory_type`. Retrieval fuses semantic + BM25 + entity +
rule-based temporal, scoped by `user_id/agent_id/run_id`; ~**7 k** tokens/query vs
25 k+ full-context; LoCoMo 92.5.

**Zep / Graphiti** (getzep.com; arXiv:2501.13956). **Bi-temporal knowledge graph.**
Episodic nodes (raw), entity nodes (evolving summaries + embedding), entity edges =
facts with four timestamps: `created_at`/`expired_at` (system time) and
`valid_at`/`invalid_at` (real-world time). **Contradiction never deletes**: it sets
the old edge's `invalid_at ← new fact's valid_at` and `expired_at ← now()`, then
writes the new edge — full history + point-in-time queries. Three-tier entity dedup
(exact → MinHash/LSH → LLM). Hybrid retrieval (BM25 + vector + BFS) fused with RRF,
optional cross-encoder rerank.

**Generative Agents** (Park 2023; arXiv:2304.03442). *Memory stream* of NL
observations, each with timestamp, last-access, embedding, and an LLM **importance
(poignancy 1–10)** score set at write time. Retrieval score =
`w_recency·0.995^Δt + w_importance·importance + w_relevance·cosine`, each normalized
to [0,1] (reference impl weights 0.5 / 2.0 / 3.0). **Reflection** fires when summed
importance crosses a threshold: generate questions → retrieve → synthesize ~5
higher-level insights → store back as new memories.

**LangMem / LangGraph** (LangChain). `BaseStore` = namespaced KV + optional vector
search. Three categories: **semantic** (profile doc *or* consolidating collection),
**episodic** (successful `(observation,thought,action,result)` few-shots),
**procedural** (rules / prompt, evolved by a prompt-optimizer). **Hot-path** tools
(`manage_memory`/`search_memory`) vs **background** ("subconscious") formation after
the turn.

**Anthropic / OpenAI.** Anthropic: a **memory tool** (model issues
`view/create/str_replace/…` against a `/memories` dir the app backs with any store;
system prompt nudges "view memory first") + **context editing/compaction**. OpenAI:
**saved memories** (explicit, user-managed) + **reference chat history**
("dreaming," async background extraction) + Responses-API server-side conversation
state.

### 4.1 Comparison matrix

| System | Core (always-in-ctx) | Semantic recall | Write path | Conflict/update | Ranking | Store |
|---|---|---|---|---|---|---|
| **Letta** | named blocks, char-cap | pgvector passages (paged) | hot tools + sleeptime | append/replace, block rewrite | vector NN | Postgres+pgvector, Redis |
| **Hermes** | 2 md files, snapshot | — | background fork /10 turns | surgical add/replace/remove | — | files + locks |
| **Sydney** | saved-memory + profile section | Substrate Search V3 (on-demand) | `record_memory` | **incremental-summary add/edit/delete** | budgeted sections | Annotation/Entity/3S |
| **Mem0** | "User Memories" block | vector top-k | **async pipeline** | ADD/UPDATE/DELETE/NOOP → ADD-only+temporal | semantic+BM25+entity+temporal RRF | vector+graph+SQL |
| **Zep** | — | graph facts | async KG build | **bi-temporal invalidation** | BM25+vector+BFS RRF | temporal KG |
| **GenAgents** | — | memory stream | write per observation | append | **recency+importance+relevance** | stream + embeddings |
| **LangMem** | profile doc | collection search | hot **or** background | manager delete/consolidate | vector | BaseStore (Postgres) |
| **Sherpa (today)** | **whole KV in system prompt** | passages (manual only) | hot tools only | **overwrite by exact key** | **exact key** | Postgres+pgvector+Redis |

**Convergence:** everyone runs (a) a **small always-in-context tier** the model
never has to look up, (b) an **on-demand semantic tier** for depth, (c) a
**background formation** loop, and (d) an explicit **conflict-resolution** rule.
Sherpa has none of (c)/(d), and its (a) is a lookup-by-guessed-key — exactly the
failure in §1.

---

## 5. Sherpa constraints the design must honor

- **Cache-stable prefix** (docs/04 铁律 ⑤): static instructions byte-stable; dynamic
  data on the tail. Today memory is glued into the system prompt and changes the
  prefix on every write — **must fix**.
- **Deterministic tests** (mock provider, no real model calls): every LLM step
  (extraction, merge, importance) needs a `mock` branch like `embeddings._fake_embedding`.
- **Single-user, tenant-forward** (ADR-015): keep `tenant_id` + composite keys.
- **Self-hosted, no new infra** (ADR-012 as revised): Postgres + pgvector + FTS +
  Redis only. No Neo4j, no external vector DB.
- **Capability ⇒ UI** (AGENTS.md DoD): the Memory page must expose every tier.
- **Untrusted content** (ADR-009/019): never let email-sourced text write memory
  without the no-tool analysis path; threat-scan entries before injecting.

---

## 6. Proposed design — Sherpa Memory v2

Three tiers on the existing stack, one deterministic write-merge, cache-stable
injection, async formation.

### 6.1 Tier 1 — Core memory: named, bounded, always-in-context blocks

Replace "whole free-form KV in the system prompt" with a **small fixed set of
named blocks** (Letta/Hermes/Sydney convergence):

- `profile` — who the user is (name, emails, timezone, role, key relationships).
- `preferences` — how the agent should behave (tone, defaults, do/don't).
- `agent_notes` — durable things the agent learned (optional).

Each block is a short, **char-bounded** (e.g. 2 000 / 1 500 / 1 500) free-text
region the agent edits with **surgical ops** (`append` / `replace(old→new)` /
`remove`), cap enforced **at write time** with a "consolidate first" error (Hermes).
Because the block is **always rendered in full**, the §1 email lives in `profile`
and the model reads it directly — **no key to guess, no lookup to miss.**
`memory_user_get` becomes rarely-needed and, when used, gets key-canonicalization +
a list/fuzzy fallback so a wrong guess still resolves (Phase 0).

Storage: introduce `memory_blocks(tenant_id, user_id, label, value, char_limit,
version, updated_at)` with optimistic `version` (Letta). Migrate existing
`user_memory` rows into the seeded `profile` block; keep `user_memory` only if a
structured index proves needed (open question §8).

### 6.2 Tier 2 — Archival/semantic memory: on-demand, now auto-formed

Keep `memory_passages` + pgvector + FTS + RRF. Add:

- **Auto-formation** (new): passages are written not only by the manual `memory_note`
  but by background formation (§6.4).
- **Dedup on write**: normalized-text **hash** skip (Mem0/Graphiti tier-1) +
  semantic near-duplicate check ⇒ NOOP/UPDATE instead of a 6th copy.
- **Scored retrieval** (phase-gated): extend pure relevance with **recency +
  importance** (Generative Agents): `score = wr·0.995^Δt_hours + wi·importance +
  wv·cosine`, fused with the existing lexical RRF. `importance` is set at write time
  (heuristic in tests, cheap LLM in prod).

### 6.3 Tier 3 — Episodic recall: reuse session-search

The "relevant past conversation" tier (Sydney `personal_context` / Letta
`conversation_search`) is **already built** as session-search (ADR-029). Expose it
as the recall path; do not build a parallel store.

### 6.4 Write paths

- **Hot path (in-turn):** the `memory_*` tools stay, but core-memory becomes
  block-edit ops with dedup, and archival gets dedup. This is the low-latency,
  user-visible "remember this" path.
- **Background formation (new, async post-run):** a worker job (Sherpa already runs
  arq + the event journal) reads a finished run's transcript, **extracts** candidate
  facts, and runs the **deterministic merge** (§6.5) against core + archival. Fired
  by config cadence (end-of-run or every N turns, Hermes/Letta). Zero added
  inference latency (Mem0/Sydney/LangMem/ChatGPT-"dreaming"). **Must** have a mock
  deterministic branch.

### 6.5 Conflict resolution — the crux (kills the §1 bug at the root)

On every write (hot or background), decide one of **ADD / UPDATE / INVALIDATE /
NOOP** against existing memory (Mem0 v2 + Sydney incremental-summary):

- **Core memory:** a single always-loaded block per topic ⇒ no key collisions; the
  merge decides *edit-in-place vs append* so "new email"/"moved teams" **updates**,
  not duplicates.
- **Changing facts:** **bi-temporal soft-invalidation** (Zep) — `valid_at` /
  `invalid_at` columns; a superseding fact sets the old row's `invalid_at` rather
  than deleting it. Preserves history, enables point-in-time answers, and is
  **deterministic** (no hidden deletes) — ideal for tests.
- **Determinism:** in `mock` mode the merge is a rule (exact/normalized match ⇒
  UPDATE; hash dup ⇒ NOOP; else ADD), so tests never call a model.

### 6.6 Cache-stable injection (fix the 铁律 ⑤ violation)

Stop concatenating memory into the static system prompt. Instead:

- Keep `SYSTEM_PROMPT` **byte-stable** (cached prefix).
- Render core-memory blocks as a **separate layer** with its own cache breakpoint,
  **snapshotted at run start** and not mutated mid-run (Hermes). Between runs it can
  change without disturbing the static prefix; within a run the whole window is
  stable. (Alternative, per docs/02's original intent: a `<memory-context>` block on
  the latest user turn — also acceptable; §8 open question.)
- Archival/episodic never pre-injected — retrieved on demand or top-k pre-fetched
  onto the **tail** (Mem0), never the prefix.
- Threat-scan block content at snapshot time (Hermes `[BLOCKED: …]`) since some
  facts originate from untrusted email.

### 6.7 UI (DoD)

Extend the existing Memory page: edit the **core blocks** (with live char count),
list **archival notes** tagged by **origin** (you vs auto-formed) and importance,
and show **superseded/invalidated** facts as history. No nav placeholder for a
backend that ships.

---

## 7. Phasing (small, reviewable, effort-proportional)

- **Phase 0 — bugfix band-aid (tiny, no ADR; currently paused by user).** Key
  canonicalization + `get` list/fuzzy fallback + write-merge dedup + reliable
  always-injection + a system-prompt line "core memory is already in context; do not
  re-look-up known facts." Would alone have prevented §1.
- **Phase A — core-memory redesign (ADR-031 + contracts).** `memory_blocks`,
  run-start snapshot, cache-stable layer, surgical edit tools + dedup, migration from
  `user_memory`, Memory-page blocks. No new infra.
- **Phase B — background formation.** Async post-run extraction + deterministic merge
  into core + archival; importance/recency scoring; opt-in cadence + kill-switch.
- **Phase C — evidence-gated.** Bi-temporal fact history surfaced in UI; full hybrid
  scored archival retrieval; reflection/insights. **Gate on a golden set** (retrieval
  MRR / answer-accuracy lift ≥ 10 %), mirroring the session-search bar.

---

## 8. Contract / ADR work before code

- **ADR-031** — "Tiered agent memory (blocks + async formation + deterministic
  merge)"; extends/supersedes **ADR-004**, revisits the **ADR-012** pgvector
  deferral (already partly landed for `memory_passages`).
- **data-model.md** — add `memory_blocks`; add `origin`, `importance`, `valid_at`,
  `invalid_at` to `memory_passages`; decide `user_memory`'s fate; update the
  "only v1 memory store" note (`:1808`). All tables keep `tenant_id` + composite keys.
- **events-and-effects.md** — `memory.formed` / `memory.updated` / `memory.invalidated`
  journal events; formation is a run side-effect (idempotency key per candidate).
- **api.md** — block CRUD + history endpoints (REST↔tool parity, ADR-023).
- **config-and-secrets.md** — formation cadence, char caps, importance-model id,
  auto-formation default + kill-switch.

## 9. Open questions (resolve before Phase A)

1. **Core store:** new `memory_blocks` vs evolve `user_memory` into a namespaced
   block? (Recommend blocks — cleanest map to UI + cache-stable rendering.)
2. **Merge strategy:** online merge-LLM (Sydney) vs ADD-only + temporal (Mem0 v3)?
   (Recommend merge-LLM: single-user, small store ⇒ coherence beats scale.)
3. **Injection point:** dedicated cached layer after the prompt vs `<memory-context>`
   on the user turn (docs/02 original)?
4. **Auto-formation default:** on or opt-in? (Privacy/cost; recommend opt-in + visible
   receipts, ChatGPT-style.)
5. **Importance:** LLM poignancy vs deterministic heuristic for v1? (Heuristic first;
   LLM behind the mock seam.)
6. **Reflection:** defer to Phase C? (Recommend yes.)

---

## 10. Primary sources

- Letta/MemGPT — `C:\src\letta`; arXiv:2310.08560.
- Hermes — `C:\src\hermes-agent` (`tools/memory_tool.py`, `agent/background_review.py`,
  `agent/system_prompt.py`).
- Sydney — `C:\src\SydneyDocV2\en\agent-memory.html`.
- Mem0 — https://arxiv.org/abs/2504.19413 · https://docs.mem0.ai/core-concepts/memory-evaluation · https://github.com/mem0ai/mem0
- Zep/Graphiti — https://arxiv.org/abs/2501.13956 · https://github.com/getzep/graphiti
- Generative Agents — https://arxiv.org/abs/2304.03442
- LangMem — https://langchain-ai.github.io/langmem/concepts/conceptual_guide/
- Anthropic memory tool + context editing — https://docs.anthropic.com/en/docs/build-with-claude/memory
- OpenAI/ChatGPT memory — https://help.openai.com/en/articles/8590148-memory-faq · https://openai.com/index/chatgpt-memory-dreaming/
