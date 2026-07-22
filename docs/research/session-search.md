# R-SESSION-SEARCH — Cloud session persistence, search, and resume

**Status:** research complete; recommendation awaiting owner decision. No implementation approved.

## Outputs

- [Research report and architecture recommendation](session-search-report.md)
- [Static Session Library prototype](../design-session-library/index.html)
- [Prototype notes](../design-session-library/README.md)

## Objective

Determine how Sherpa, as a durable cloud agent, should let users browse, search, inspect, and resume sessions across devices. The result must go beyond copying local-agent storage patterns while preserving their useful properties.

## Starting hypothesis to verify

Local coding agents such as GitHub Copilot CLI, Hermes, and comparable tools commonly combine append-oriented session artifacts (for example JSONL) with a local SQLite index for durable replay and fast search. The research must verify each product's actual design from primary sources rather than assume this pattern is universal.

Sherpa already has different foundations: Postgres sessions/messages, a durable event journal and outbox, run/effect recovery state, tenant keys, and Redis only as an accelerator. Session search must preserve those invariants.

## Research questions

1. How do Copilot CLI, Hermes, and at least two comparable local agents store transcripts, tool events, metadata, branches, compaction, and resumable state?
2. What is stored as an append log versus projected into SQLite or another index? How are indexes rebuilt?
3. What does "resume" mean in each product: reopen transcript, restore context, continue an interrupted run, branch from a turn, or all of these?
4. Which local assumptions fail for a cloud agent: one device, one filesystem, no tenancy, trusted local search, weak retention controls, or no cross-channel identity?
5. For Sherpa, which data remains canonical in Postgres, which projections/indexes are derived, and whether full-text search is sufficient before semantic search?
6. How should search results deep-link to an exact turn/tool/action while safely restoring pending approvals, run state, and `effect_unknown` outcomes?
7. What retention, encryption/redaction, authorization, pagination, backup, and index-rebuild guarantees are required?

## Target product effect

- A searchable Session Library available across devices and channels.
- Search over titles, user messages, assistant answers, tool/action receipts, channel, and time.
- Results open at the matching turn with surrounding context.
- An explicit **Resume** action continues the durable session without losing tool history.
- Interrupted sessions show truthful states such as running, waiting for approval, interrupted, or outcome unknown.
- Optional branch/fork semantics are clearly separated from resuming the original session.
- Every query and result remains tenant-scoped and respects redaction/retention rules.

## Deliverables

1. Evidence table with primary-source citations for the surveyed agents.
2. Comparison of at least three Sherpa architectures, including:
   - Postgres full-text projections only;
   - Postgres canonical data plus a dedicated search projection;
   - hybrid lexical + semantic retrieval.
3. Recommended source-of-truth, index schema, rebuild strategy, API surface, and migration path.
4. Session Library UX flow for desktop and mobile, preferably as static HTML before implementation.
5. Measurable acceptance criteria for result quality, resume fidelity, latency, isolation, and recovery.
6. ADR and frozen-contract changes if the recommendation changes canonical data or recovery semantics.

## Non-goals

- Do not implement search, add a search service, or migrate session data during the research task.
- Do not treat memory/RAG retrieval as a substitute for exact session search.
- Do not expose raw chain-of-thought or unredacted secret-bearing tool payloads.

## Exit criteria

Research is complete only when the owner can decide:

1. what users can search;
2. what "resume" guarantees;
3. which store is canonical;
4. which index is derived and rebuildable;
5. what ships in the first increment versus later semantic/branching capabilities.
