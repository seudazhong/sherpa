# Session Search Research Report

**Date:** 2026-07-22  
**Task:** [R-SESSION-SEARCH](session-search.md)  
**Status:** research complete; awaiting owner decision before contracts or implementation change.

## 1. Executive conclusion

The user's starting observation is **partly correct but not universal**:

- **GitHub Copilot CLI** uses per-session `events.jsonl` as the complete local record and `session-store.db` (SQLite) as a rebuildable cross-session search/checkpoint index.
- **OpenAI Codex CLI** now follows a similar split: JSONL rollout files remain the replayable record, while `state_5.sqlite` stores indexed thread metadata and rollout paths.
- **Hermes Agent** moved in the opposite direction: it explicitly replaced per-session JSONL with a single SQLite/WAL store whose canonical `messages` table is indexed by FTS5.
- **Claude Code** and **Gemini CLI** use project-scoped JSONL transcripts without a separately documented full-text database.

The durable pattern is therefore not a particular file/database pair. It is:

> **Canonical, replayable session history + a derived, disposable search/browse projection.**

Sherpa should adopt that separation while using its existing cloud primitives:

- **Canonical:** Postgres sessions/messages/parts, event journal, runs, approvals, effect invocations, and audit receipts.
- **Derived:** a tenant-scoped `session_search_entries` Postgres projection.
- **Initial retrieval:** PostgreSQL full-text search plus `pg_trgm` for multilingual substring/fuzzy matching.
- **Later retrieval:** optional pgvector hybrid search only after it proves a measurable quality improvement.
- **No JSONL or SQLite in the cloud data plane.**
- **No OpenSearch service initially.**

The user-facing product should be a dedicated **Sessions** library rather than an ever-growing chat dropdown.

Static product design: [`../design-session-library/index.html`](../design-session-library/index.html).

## 2. Evidence from local coding agents

| Agent | Canonical session record | Browse/search projection | Resume and branching lessons |
|---|---|---|---|
| **GitHub Copilot CLI** | `~/.copilot/session-state/<id>/events.jsonl` plus workspace artifacts | `~/.copilot/session-store.db`; powers `/chronicle`, checkpoint indexing, and direct content search; rebuild with `/chronicle reindex` | Resume loads full conversation history. Compaction creates checkpoints. Local sessions can sync to the user's GitHub account for cross-device querying. |
| **Hermes Agent** | SQLite `~/.hermes/state.db`, WAL mode; canonical `sessions` and `messages` tables | FTS5 and FTS5-trigram tables maintained from canonical messages | Resume by ID/title; auto titles; session lineage through `parent_session_id`; compression creates a continuation session; cross-platform handoff retains the same logical conversation. |
| **OpenAI Codex CLI** | `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` | `state_5.sqlite` stores thread metadata and rollout paths; picker search is substring matching over preview/name/cwd/branch/ID, not full transcript FTS | `codex resume` replays a rollout. `codex fork` creates a separate branch. SQLite lookup falls back to filesystem search and performs read repair. |
| **Claude Code** | `~/.claude/projects/<project>/<session-id>.jsonl` | No separate FTS database documented; the picker filters loaded session metadata/content | Resume restores history, tool calls/results, model, agent, selected permission state, active goal, and unexpired schedules. `/branch` creates a new session ID; concurrent resume without branching can interleave messages. |
| **Gemini CLI** | Project-scoped JSONL containing messages and control records such as `$set.messages` and `$rewindTo` | No separate index documented; session browser reads project session files | Rewind is append-oriented: control records change the logical view without deleting earlier bytes. Conversation and code rewind are separate choices. |

### 2.1 GitHub Copilot CLI

Official documentation confirms:

- each session directory contains `events.jsonl` and workspace artifacts;
- `session-store.db` is a subset/index for cross-session search and checkpoints;
- `/chronicle search` directly searches all session content;
- `/chronicle reindex` reconstructs the SQLite store from session files after migration, corruption, deletion, or an interrupted process;
- local session data can be synchronized to the user's GitHub account and queried across Copilot surfaces.

This is the closest local analogue to Sherpa's required pattern: the searchable index is useful but never the only copy.

### 2.2 Hermes Agent

Hermes demonstrates a different valid design:

- SQLite WAL is both the durable message store and the transactional concurrency boundary.
- `messages_fts` and `messages_fts_trigram` are derived FTS5 projections.
- session titles are persisted and unique;
- search returns snippets plus neighboring-message context;
- `parent_session_id` models continuation/lineage after compression;
- platform/source is first-class metadata.

The important lesson is not "use SQLite"; it is that titles, source, lineage, snippets, and index rebuild behavior are explicit product/data concepts.

### 2.3 Codex CLI

Current Codex source shows an active migration toward the same logical split as Copilot CLI:

- rollout JSONL remains inspectable and replayable;
- SQLite `threads` rows store `rollout_path`, title, preview, cwd, Git metadata, archive state, provider, sandbox policy, approval mode, and token use;
- ID lookup prefers SQLite, falls back to file traversal, then repairs the database mapping;
- the picker performs in-memory substring search over preview, name, cwd, branch, and ID.

Codex is evidence that a metadata database dramatically improves browsing even before full transcript search exists.

### 2.4 Claude Code and Gemini CLI

Both prove that JSONL is sufficient for local durability and replay, but their file-centric assumptions do not transfer to Sherpa:

- one filesystem and OS user;
- project path as the security boundary;
- no tenant authorization in every query;
- limited cross-device consistency;
- runtime recovery is distinct from transcript replay.

Claude Code also makes a product distinction Sherpa should preserve: **resume** continues the original session; **branch** creates a new session ID and leaves the original unchanged.

## 3. Current Sherpa baseline

Sherpa already has stronger cloud durability than the surveyed local tools:

### Canonical state

- `sessions`, `runs`, `messages`, and `parts`;
- sequenced `event_journal` plus transactional outbox;
- `effect_invocations` with `effect_unknown` and reconciliation states;
- `approval_envelopes`;
- audit receipts;
- tenant-scoped composite keys.

### Existing browse/resume behavior

- `GET /sessions` lists sessions using cursor pagination.
- Chat automatically opens the most recent session.
- The header dropdown switches sessions.
- `GET /sessions/{id}/messages` restores redacted user/assistant transcript text.
- Provider history reconstruction correctly restores historical tool calls/results from the journal for the next model run.

### Gaps

1. Session title is not persisted in the `sessions` table.
2. The frontend does not page session or message history.
3. There is no transcript/content search index.
4. Session switching clears activity, approvals, and running state instead of reconstructing them.
5. Pending approvals are discovered from a live `permission.asked` SSE event, so an old pending approval is not restored reliably.
6. Search cannot deep-link to an exact matching message/tool action.
7. A stale running run, interrupted run, and `effect_unknown` outcome do not have distinct library actions.
8. Branch/fork lineage is not modeled.
9. `sessions.last_activity_at` exists and is indexed, but current write paths do not maintain it.
10. `messages.seq` and `event_journal.session_seq` are independent counters and cannot be treated as one merged timeline position.

## 4. Product definition: Session Library

### 4.1 Primary job

Let a user answer:

1. **Where did I work on this?**
2. **What happened in that session?**
3. **Can I safely continue it?**

### 4.2 Search scope

The first increment should search:

- persisted title;
- user messages;
- assistant answers;
- safe summaries of tool calls/results;
- audit/action receipts;
- channel, date, project/workspace, and status metadata.

It must not index:

- raw chain-of-thought;
- unredacted tool arguments or secrets;
- full oversized tool output;
- deleted/expired content outside retention policy.

### 4.3 Result behavior

Search results are grouped by session and include:

- title;
- last activity;
- origin channel;
- current recovery state;
- best matching snippet;
- match kind (`message`, `tool`, `action`, `title`);
- exact deep-link anchor;
- number of additional matches.

Opening a result is always read-only. The action button changes by state.

## 5. Resume semantics

The product must stop using one generic "Resume" action for every condition.

| Durable state | User-facing state | Primary action |
|---|---|---|
| No active run; latest run settled | Ready to continue | **Resume session** |
| Worker/run has a fresh durable lease | Running | **Reconnect** |
| Run says running but its lease is stale | Interrupted / stale | **Recover run** |
| Pending approval | Waiting for you | **Review approval** |
| Interrupted before an external effect | Interrupted safely | **Continue from checkpoint** |
| `effect_unknown` / needs reconciliation | Outcome unknown | **Resolve outcome** |
| Failed run | Failed | **Review failure**, then continue |
| Archived | Archived | **Open** or **Restore** |
| Deleted/retention-expired | Unavailable | No resume |

### 5.1 Open versus resume

- **Open:** load transcript, search anchors, activity receipts, and durable state.
- **Resume:** submit a new prompt to the same durable session after state preflight.
- **Reconnect:** attach to the active SSE stream without creating a new run.
- **Recover:** reconcile an interrupted/stale run before accepting new work.
- **Branch:** create a new session from a chosen turn; later phase.

### 5.2 Branching

Branching must create:

- a new session ID;
- `parent_session_id`;
- `branched_from_message_id` or `branched_from_message_seq`;
- a copied/assembled context boundary;
- no inherited session-only approvals.

The original session and event journal remain unchanged.

## 6. Recommended architecture

### 6.1 Source of truth

Do not introduce JSONL or SQLite into the Sherpa server.

| Data | Canonical source |
|---|---|
| Human/model transcript | `messages` + `parts` |
| Tool and run timeline | `event_journal` |
| External-effect truth | `effect_invocations` |
| Human approvals | `approval_envelopes` |
| User-facing action history | `audit_receipts` |
| Session metadata/state | `sessions` + latest `runs` |

### 6.2 Derived projection

Add a rebuildable table similar to:

```text
session_search_entries
  tenant_id
  user_id
  id
  session_id
  source_kind          # title, user_message, assistant_message, tool, action
  source_id
  anchor_kind          # message, event, audit, session
  anchor_id
  message_seq          # nullable
  event_session_seq    # nullable; independent from message_seq
  run_id               # nullable; maps tools/actions back to a turn
  content_text         # bounded, redacted
  normalized_text
  cjk_terms            # application-generated character bigrams
  channel
  occurred_at
  projection_version
  redacted_at
  fts                  # generated tsvector
  embedding            # later, nullable
```

Recommended constraints/indexes:

- unique `(tenant_id, source_kind, source_id, projection_version)`;
- B-tree `(tenant_id, user_id, occurred_at DESC, id)`;
- B-tree `(tenant_id, session_id, message_seq)`;
- B-tree `(tenant_id, session_id, event_session_seq)`;
- GIN on generated `fts`;
- GIN on generated `cjk_fts`;
- GIN `gin_trgm_ops` on `normalized_text`;
- partial indexes excluding redacted rows;
- optional HNSW only after semantic search is enabled.

### 6.3 Why a separate projection table

It:

- searches messages, tools, and actions through one shape;
- avoids expensive session-message-part-event joins;
- keeps raw/canonical records separate from indexable redacted text;
- supports deterministic rebuilds;
- allows tokenizer/ranking changes without rewriting canonical data;
- provides exact typed deep-link anchors without conflating message and event counters;
- can later add embeddings without changing search API semantics.

### 6.4 Projection updates

Do **not** rely on `event_journal` alone: current prompt admission does not emit a
user-message event, and title/status/deletion updates are not journal events.

Use one transactional projection-change feed:

1. append or update canonical state first;
2. write an internal projection job/outbox row in the same transaction for message, title, archive, delete, and redaction changes;
3. continue consuming `event_journal` for tool/run events and audit receipts;
4. projector upserts or tombstones the corresponding search entry;
5. persist a projection checkpoint;
6. retry idempotently after failure.

The live projector and full rebuild must read the same canonical set:
`sessions`, `messages`/`parts`, selected `event_journal` events, and
`audit_receipts`. Deletion and redaction must produce explicit tombstones rather
than waiting for a future append event.

The projection may lag by a few seconds. Search is not correctness-critical.

For self-hosted deployments, a full rebuild can truncate a projection version and replay canonical Postgres data. For hosted deployments, build a new projection version in parallel and switch after validation.

### 6.5 Lexical and multilingual retrieval

Initial retrieval should combine:

- `websearch_to_tsquery` over a `simple`-style generated `tsvector` for whitespace-delimited exact tokens/phrases;
- application-generated Unicode character bigrams stored in `cjk_terms` and indexed as a second `simple` tsvector for Chinese/CJK queries;
- `pg_trgm` for typo tolerance and substring matching when the normalized query is at least three code points;
- weighted fields: title > user message > assistant response > tool/action summary;
- a small recency boost;
- grouping by session with the best matching entry.

One-character CJK queries should require an additional filter or return a bounded
recent scan; trigram search alone is not selective enough.

`ts_headline` output must never be rendered as trusted HTML. Return escaped text plus match ranges, or sanitize before response.

### 6.6 Semantic search

Do not make embeddings a prerequisite for the first release.

Later:

1. embed bounded search entries or coherent turn chunks;
2. run lexical and vector retrieval independently;
3. combine with Reciprocal Rank Fusion;
4. keep semantic search only if it improves golden-query MRR by at least 10%;
5. partition or otherwise isolate HNSW retrieval by tenant before hosted multi-user use.

### 6.7 Durable run liveness

`runs.status='running'` is not proof that a worker is alive. Add a Postgres-backed
lease or heartbeat (`heartbeat_at`, `lease_expires_at`, and worker identity,
either on `runs` or a dedicated lease table).

Recommended initial behavior:

- worker refreshes the lease every 15 seconds;
- lease expires after 45 seconds;
- **Reconnect** is available only while the lease is fresh;
- an expired lease becomes **Interrupted / Recover run**;
- Redis heartbeat may accelerate display but must not be the correctness source.

### 6.8 Why not OpenSearch initially

OpenSearch adds:

- another service and operational footprint;
- asynchronous dual-write consistency;
- separate authorization and deletion enforcement;
- more difficult self-hosting.

Postgres FTS + trigram + pgvector is sufficient until measured scale or ranking needs prove otherwise.

## 7. Proposed API surface

### Session browse/search

```http
GET /sessions
  ?query=
  &status=
  &channel=
  &updated_before=
  &cursor=
  &limit=
```

Response additions:

```json
{
  "items": [
    {
      "id": "session-id",
      "title": "Fix approval resume",
      "updated_at": "2026-07-22T10:00:00Z",
      "channel": "web",
      "resume_state": "ready",
      "match": {
        "kind": "tool",
        "snippet": "…approved send_email and resumed…",
        "anchor_kind": "event",
        "anchor_id": "event-id",
        "additional_matches": 3
      }
    }
  ],
  "next_cursor": "opaque"
}
```

An empty `query` returns recent sessions. A non-empty query returns session-grouped matches.

Browse and ranked search use different opaque cursors:

- browse cursor: snapshot time + `(last_activity_at, session_id)`;
- search cursor: query hash + `(score, last_activity_at, session_id)`.

Phase A must begin maintaining `last_activity_at` on every admitted message and
durable run/activity update. The snapshot time prevents a newly active session
from causing skips or duplicates while paging.

### Session detail around an anchor

```http
GET /sessions/{id}/timeline
  ?anchor_kind=event
  &anchor_id=event-id
  &before_turns=10
  &after_turns=10
```

Returns transcript messages plus safe tool/action timeline entries around the
exact typed source. The backend maps an event/tool match to its `run_id` and
surrounding message turn. It must not compare `messages.seq` with
`event_journal.session_seq`.

### Resume preflight

```http
GET /sessions/{id}/resume-state
```

Returns:

- latest run state;
- durable run lease/heartbeat freshness;
- pending approval;
- unresolved effect;
- active stream/reconnect information;
- allowed next action.

### Recovery

```http
POST /sessions/{id}/recover
```

This is not a generic retry. It accepts only a state-specific reconciliation decision and reuses invocation idempotency.

Approval state is computed as:

- waiting: `status='pending' AND now() < expires_at`;
- expired: `status='pending' AND now() >= expires_at` or persisted `expired`.

The API must not advertise an approval action that will fail immediately.

Every browse, search, timeline, resume-state, recovery, and branch query must
authorize both `tenant_id` and `user_id`. Tenant-only checks are insufficient for
the future multi-user tenant model.

### Branching, later

```http
POST /sessions/{id}/branches
{
  "at_message_id": "message-id",
  "title": "Alternative approach"
}
```

## 8. Delivery phases

### Phase A — trustworthy browsing and continuation

- persist session title;
- dedicated Sessions page;
- pagination and filters;
- restore transcript, run state, approvals, and activity;
- maintain `last_activity_at`;
- add a Postgres-backed run lease/heartbeat so live and stale runs are distinguishable;
- state-specific actions;
- no content search yet.

### Phase B — lexical session search

- `session_search_entries`;
- Postgres FTS + trigram;
- grouped results and snippets;
- exact timeline anchors;
- projection health/rebuild command;
- retention/redaction propagation.

### Phase C — semantic recall and branching

- hybrid pgvector retrieval after quality evidence;
- natural-language history questions;
- branch from a turn;
- lineage UI;
- hosted multi-tenant partitioning/RLS.

## 9. Acceptance criteria

### Correctness and isolation

- zero cross-tenant/user results;
- search index is never the only copy of session data;
- deleting/expiring a session removes it from results within one minute;
- rebuild produces the same searchable entries from canonical data;
- no raw reasoning or unredacted secret enters the projection.

### Search

- P95 browse < 150 ms for 10,000 sessions;
- P95 lexical search < 300 ms for 100,000 indexed entries;
- projection lag < 5 seconds;
- exact match opens the correct session and turn;
- at least 80% precision@5 on separate English and Chinese golden sets;
- CJK bigram retrieval handles two-character queries;
- trigram fallback handles three-plus-character substrings and common typos.

### Resume fidelity

Deterministic tests must cover:

1. idle session;
2. live running session;
3. stale running session;
4. pending approval;
5. expired approval;
6. interrupted safe tool;
7. `effect_unknown`;
8. failed run;
9. archived session;
10. concurrent open from two devices.

No state may silently become "ready" when human reconciliation is required.

### UX

- desktop and 390 px mobile Session Library paths;
- keyboard-accessible search and filters;
- result preview before destructive/recovery actions;
- explicit Open / Resume / Reconnect / Review / Resolve labels;
- no raw UUID required for ordinary use.

## 10. Decision requested

Recommended decision:

1. approve **Session Library** as a dedicated product surface;
2. approve Postgres `session_search_entries` as a derived projection;
3. approve lexical + trigram search first;
4. defer semantic search and branching until Phase C;
5. require state-specific recovery rather than a generic Resume button.

Contracts and migrations should not change until this recommendation is approved.

## 11. Primary sources

### GitHub Copilot CLI

- [Configuration directory: `session-state/`, `events.jsonl`, `session-store.db`](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-config-dir-reference)
- [About session data, syncing, deletion, and reindexing](https://docs.github.com/en/copilot/concepts/agents/copilot-cli/chronicle)
- [Resume and `/chronicle search`](https://docs.github.com/en/copilot/how-tos/copilot-cli/use-copilot-cli/chronicle)
- [Context compaction and checkpoints](https://docs.github.com/en/copilot/concepts/agents/copilot-cli/context-management)

### Hermes Agent

- [SQLite session storage, FTS5, WAL, migrations, and lineage](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/developer-guide/session-storage.md)
- [Resume, handoff, naming, export, and management UX](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/sessions.md)

### OpenAI Codex CLI

- [JSONL rollout recorder](https://github.com/openai/codex/blob/main/codex-rs/rollout/src/recorder.rs)
- [Rollout listing, SQLite lookup fallback, and read repair](https://github.com/openai/codex/blob/main/codex-rs/rollout/src/list.rs)
- [SQLite thread schema](https://github.com/openai/codex/blob/main/codex-rs/state/migrations/0001_threads.sql)
- [Resume picker filtering](https://github.com/openai/codex/blob/main/codex-rs/tui/src/resume_picker.rs)
- [TypeScript SDK resume contract](https://github.com/openai/codex/blob/main/sdk/typescript/src/codex.ts)

### Claude Code and Gemini CLI

- [Claude Code session management and JSONL storage](https://code.claude.com/docs/en/sessions)
- [Gemini CLI session management](https://geminicli.com/docs/cli/tutorials/session-management/)
- [Gemini JSONL recorder implementation](https://github.com/google-gemini/gemini-cli/blob/main/packages/core/src/services/chatRecordingService.ts)

### Search infrastructure

- [PostgreSQL full-text search controls](https://www.postgresql.org/docs/current/textsearch-controls.html)
- [PostgreSQL GIN indexes](https://www.postgresql.org/docs/current/gin.html)
- [`pg_trgm`](https://www.postgresql.org/docs/current/pgtrgm.html)
- [pgvector hybrid search guidance](https://github.com/pgvector/pgvector)

### Sherpa

- `backend/app/api/sessions.py`
- `backend/app/core/history.py`
- `backend/app/core/resume.py`
- `backend/app/events/`
- `backend/migrations/versions/0001_initial_core.py`
- `backend/migrations/versions/0002_events_outbox.py`
- `backend/migrations/versions/0003_effect_invocations.py`
- `backend/migrations/versions/0012_approval_envelopes.py`
- `backend/migrations/versions/0016_memory_passages.py`
- `frontend/src/views/ChatView.tsx`
- ADR-015, ADR-016, ADR-017, ADR-020, ADR-021, ADR-023
