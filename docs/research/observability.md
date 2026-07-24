# R-OBSERVABILITY — Agent observability for Sherpa, and whether to adopt OpenTelemetry

**Status:** research and design complete. Answers four questions the owner asked:
*what is agent observability, how do you do it, what is OpenTelemetry, should Sherpa
use it (and how)*. **ADR-033 + config/events contract diffs are now drafted in the
same batch; implementation order is TBD (no business code yet).**

**Recommendation:** **GO** — but as a **thin, additive diagnostic layer, not a new
platform**. (1) Adopt **OpenTelemetry GenAI semantic conventions** (`gen_ai.*`) as
the *wire format* and instrument the bounded loop's LLM calls + tool executions as
spans. (2) Keep the **ADR-016 Postgres event journal as the source of truth** —
OTel spans are ephemeral/derived, correlated by `run_id`, never a substitute. (3)
For a UI backend, prefer **Arize Phoenix** (single container, reuses Sherpa's
existing Postgres, OTLP-native) over the docs/07-earmarked **Langfuse** (now a
6-service stack incl. ClickHouse) — OTLP as the wire format keeps the backend
swappable. (4) This also closes **STATUS item 0**'s deferred gap (no per-LLM-call
record — the exact blind spot hit while debugging the memory bug). Content capture
stays **off by default** (privacy). Gate evals on evidence, like session-search.

---

## 1. What is agent observability? (Q1)

Traditional APM (logs, RED metrics, HTTP traces) answers "is the service up and
fast." An agent is a **non-deterministic, multi-step, tool-using loop**, so you
also need to answer "*what did the model decide, which tools did it call with what
arguments, why did it stop, what did each step cost, and was the output good.*"
The field (2024–2026) converges on **six pillars** ([OTel AI-agent blog 2025](https://opentelemetry.io/blog/2025/ai-agent-observability/);
[Braintrust 2026](https://www.braintrust.dev/articles/agent-observability-complete-guide-2026)):

1. **Structured tracing** — a hierarchical **span tree**, not flat logs.
2. **Token / cost / latency attribution** — per span, run, session, user.
3. **Payload capture** — the *exact assembled prompt*, the completion, tool I/O.
4. **Evaluation layer** — automated (LLM-as-judge / code) + human scores on traces.
5. **Session analytics** — aggregation across runs in a conversation/thread.
6. **Feedback / dataset flywheel** — traces → curated dataset → CI regression.

**Vocabulary.** *Span* = one atomic op (an LLM call, a tool call) with
start/end, input/output, status, attributes; parent→child spans form a tree.
*Trace* = the whole tree for one agent turn/thread (the execution DAG). *Observation*
= Langfuse's word for a node (generation / span / event). *Run* = the top-level
trace (one agent invocation). A multi-step loop looks like:

```
invoke_agent (root)                 tokens/cost/latency roll up here
├─ chat  #1     finish_reason=tool_calls
├─ execute_tool get_todos   success=false  (error fed back to model)
└─ chat  #2     finish_reason=stop
```

Agent-specific concerns beyond APM: **errors-as-observations** (a tool error is
fed back to the model as an observation, *not* thrown — Sherpa's docs/04 铁律 says
exactly this: "errors from tools are observations, not exceptions"), **loop /
runaway detection** (bounded turns + a named stop reason + cost ceiling),
**session/thread views**, **replay/time-travel** debugging, and **online vs
offline eval** (score live traffic vs a curated dataset; LLM-as-judge; human
feedback). This is *the* thing I lacked when proving the memory bug — I had to
reconstruct "what was in the prompt" from timestamps because no per-call record
existed.

## 2. Current state in Sherpa (audit)

Sherpa already has strong **domain** observability and a deliberate design note.

| Layer | What exists | Gap |
|---|---|---|
| **Event journal** (ADR-016) | Ordered, append-only Postgres `event_journal` — normalized domain events (`run.started`, `tool-call`, `tool-result`, `permission.asked`, …); the design calls events "observability primitives" and is replayable | Domain-level, not LLM-call-level; no assembled prompt, no per-call tokens/latency |
| **`traces`** (`observability/projection.py`) | One row **per run**, projected on settle; `tags` carry provider/model/tokens/cost; + session token/cost rollups | Run-granularity only; **v1 estimates tokens from char count** (`~4 chars/token`), cost = 0; no per-turn/per-tool spans |
| **`generations`** | Per-LLM-call telemetry table (provider/model/prompt_version/tokens/cost/latency) | **Only written by the extraction/`CONNECTOR_ANALYSIS` path — the chat loop writes none** (STATUS item 0, deferred) |
| **Structured logging** (`observability/logging.py`) | JSON logs, correlation-id contextvars (tenant/run/session/request) + secret redaction | Correlation ids are *domain* ids, not W3C `trace_id`/`span_id`; no span tree |
| **Planned backend** (docs/07) | "先用自己的 events 表；专业化再接 **Langfuse**（同栈）" — already models it as TRACE→OBSERVATION(generation/tool/retriever)→SCORE | Never built; **no OpenTelemetry anywhere** in code |

**STATUS item 0 (deferred observability)** already names the precise gap: *"persist
each LLM call's exact assembled input as a redacted `model.request` journal event
and/or a `generations` row, and emit chat-loop generation records (model /
prompt-version / tokens / stop_reason)."* This research says: do that using the
OTel `gen_ai` vocabulary so it is standard and backend-portable.

## 3. What is OpenTelemetry? (Q3)

**OpenTelemetry (OTel)** is the CNCF vendor-neutral standard for telemetry — three
signals (**traces, metrics, logs**), a common data model, and a wire protocol
(**OTLP**, over gRPC or HTTP). You instrument once against the OTel **API/SDK** and
**export** to any backend (Jaeger, Grafana Tempo, Prometheus, Phoenix, Langfuse…),
optionally via an **OpenTelemetry Collector** (receive → process/redact → export).
Core concepts: **spans** (with `trace_id`/`span_id`, kind, attributes, events,
status), **context propagation** (W3C `traceparent`; in Python it rides
`contextvars`, which asyncio copies to child tasks automatically), **resource
attributes** (service identity), and **sampling** (head vs tail).

The relevant part is the **GenAI semantic conventions**
([semantic-conventions-genai](https://github.com/open-telemetry/semantic-conventions-genai)) —
a standard attribute vocabulary for LLM/agent spans:

- **Operation kinds** (`gen_ai.operation.name`): `chat`, `text_completion`,
  `embeddings`, `execute_tool`, `invoke_agent`, `create_agent`. Span name =
  `"{operation} {model}"` (low cardinality), e.g. `chat gpt-4o`.
- **Attributes** (no-PII, captured by default): `gen_ai.provider.name`,
  `gen_ai.request.model`, `gen_ai.request.temperature/max_tokens`,
  `gen_ai.response.model`, `gen_ai.response.finish_reasons` (`["tool_calls"]`),
  `gen_ai.usage.input_tokens`/`output_tokens` (+ cache read/creation),
  `gen_ai.conversation.id`, `gen_ai.tool.name`, `gen_ai.tool.call.id`.
- **Content is opt-in / off by default** — `gen_ai.input.messages`,
  `gen_ai.output.messages`, `gen_ai.system_instructions`, `gen_ai.tool.call.arguments/result`
  are captured only when `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=true`.
  **Safe by default** for a no-secrets runtime.
- **Metrics**: `gen_ai.client.token.usage`, `gen_ai.client.operation.duration`.
- **Status**: the GenAI semconv is **Development** — it *will* rename things
  (`gen_ai.system` → `gen_ai.provider.name` already happened). Wrap all
  `set_attribute` calls behind one helper to isolate churn.
- **Python**: `opentelemetry-sdk` + `BatchSpanProcessor(OTLPSpanExporter(...))`;
  `InMemorySpanExporter` for deterministic tests; auto-instrumentation
  (`opentelemetry-instrumentation-openai`, OpenLLMetry) covers the LLM HTTP call
  but **not** bespoke `execute_tool` code — those spans are manual.

## 4. How do you do it? The landscape (Q2)

You either self-instrument with the OTel SDK, or drop in an OTel-native SDK
(OpenLLMetry/Traceloop) that auto-instruments providers, then export OTLP to a
backend. The backend gives the trace-tree UI, session views, evals, and the dataset
flywheel. Landscape (self-host + OTel lens):

| Tool | Self-host | License | OTel | Self-host footprint |
|---|---|---|---|---|
| **Arize Phoenix** | ✅ | Elastic 2.0 | **OTLP-native** (gRPC+HTTP); ingests OpenInference **and** `gen_ai.*` (auto-converts) | **1 container**; SQLite **or reuse existing Postgres** |
| **Langfuse** | ✅ | MIT core | OTLP/HTTP ingest + own SDK | **6 services** (web, worker, Postgres, **ClickHouse ≥4 GB**, Redis, MinIO); no lighter mode |
| **OpenLLMetry / Traceloop** | ✅ (SDK only) | Apache 2.0 | **Native OTLP** | none — points at any OTLP backend |
| **Raw OTel + Jaeger/Tempo** | ✅ | Apache 2.0 | the standard | Jaeger all-in-one (1 container) |
| LangSmith | ⚠️ enterprise-gated | proprietary | SDK, not OTel-native | n/a for self-host |
| Braintrust | ❌ | proprietary SaaS | accepts OTLP | n/a |
| Helicone | ✅ | Apache 2.0 | proxy + OTel export | Docker |
| W&B Weave | ✅ (W&B Server) | proprietary | accepts OTLP | enterprise |

Key facts for a single user: **Phoenix** runs as one distroless container and can
point `PHOENIX_SQL_DATABASE_URL` at Sherpa's **existing Postgres** (separate db/schema)
— no new datastore; it converts `gen_ai.*` → its OpenInference model at ingest, so
instrumenting to OTel semconv "just works." **Langfuse** is richer (prompt mgmt,
polished evals) but now **mandates ClickHouse** and a 6-service stack (≥8 GB RAM
practical) — heavy for one user. Because everything speaks **OTLP**, the backend is
a swappable detail: instrument once, change one exporter URL later.

## 5. Should Sherpa use OpenTelemetry? (Q4) — Yes, as a thin layer

**For:**
- **Standard, portable vocabulary.** `gen_ai.*` gives stable names for tokens,
  latency, finish reasons, tool calls — query them in Phoenix/Jaeger/Grafana or
  even in Postgres, and swap backends without re-instrumenting.
- **Deterministic tests.** `InMemorySpanExporter` + the mock provider lets tests
  assert the exact span tree (correct tool called, `finish_reason` recorded, cost
  attributed, error span on tool failure) — snapshot fixtures catch accidental data
  loss. Fits 铁律 "no real model calls in tests."
- **Privacy-safe defaults.** Content attributes are opt-in; token/cost/latency are
  not — cost visibility for free with no prompt leakage. Aligns with ADR-019.
- **Dual-sourcing, not duplication.** Spans give *timing/structure*; the journal
  gives *business/audit* truth. They share `run_id`/`session_id`.
- **asyncio-native** context propagation (contextvars) — Sherpa's async loop needs
  no plumbing.

**Against / caveats to manage:**
- **Semconv is Development-status** and breaking → centralize attribute writes in
  one wrapper module.
- **Not a substitute for the journal (ADR-016/021).** Spans are ephemeral, sampled,
  retention-bounded; the journal stays the replay/audit source of truth. OTel is the
  *diagnostic* surface only. Redacted, bounded semantic receipts stay in
  Postgres; raw prompt text lives only in a short-retention span.
- **Manual tool spans.** Auto-instrumentation covers the model HTTP call, not
  Sherpa's `execute_tool`; budget a thin `start_as_current_span("execute_tool …")`
  wrapper in the loop.
- **Operational overhead** must stay proportional: at one user, **sample 100%**, no
  Collector required at first (SDK → local backend), a few hundred traces/day.

## 6. Proposed Sherpa design

A minimal OTel layer wrapping the existing loop, exporting `gen_ai` spans, correlated
to the journal.

- **Instrument the bounded loop** (`core/loop.py`): a root `invoke_agent` span per
  run; a `chat` span per model call (attrs: provider/model, temperature/max_tokens,
  `response.finish_reasons` = the loop's `stop_reason`, `usage.input/output_tokens`,
  latency); a child `execute_tool` span per tool (attrs: `gen_ai.tool.name`,
  `gen_ai.tool.call.id`, `status=ERROR` + `success=false` when the tool returns an
  error observation). Root span carries `agent.loop_count`, `agent.total_cost_usd`,
  `agent.stop_reason` (Sherpa already bounds turns + names every exit — 铁律 ①).
- **Correlate, don't conflate.** Put `run_id`/`session_id` on every span; optionally
  stamp the event journal row's `trace_id`. The journal remains canonical; if the
  trace backend is down, runs are unaffected and traces can be re-projected from the
  journal later.
- **Closes STATUS item 0.** The `chat` span *is* the "generation record" (model,
  prompt-version, tokens, `stop_reason`); optionally also persist a **bounded,
  redacted `model.request` journal event** as the durable record (ADR-021: bounded +
  desensitized), while the full assembled prompt lives only in the span under a
  `CAPTURE_PROMPTS`/`OTEL_..._CAPTURE_MESSAGE_CONTENT` flag (default **off**, short
  retention). This is the fix for the exact blind spot from the memory debug.
- **Redaction.** Reuse `security/redaction.py`; content off by default; if content
  capture is on, mask via a span processor / Collector `attributes` processor
  stripping `*secret*`/`*token*`/`*api_key*`; secret-scan any committed span
  fixtures. Never secrets (ADR-019).
- **Determinism.** `InMemorySpanExporter` + mock provider in tests; assert span-tree
  snapshots.
- **Backend.** Ship **Phoenix** as an *optional* container in
  `infra/docker-compose.yml` pointed at Sherpa's existing Postgres (separate db) —
  lightest; **revises docs/07's Langfuse default** on footprint grounds. Keep the
  exporter OTLP so Langfuse remains a drop-in alternative if its prompt-mgmt/eval DX
  is wanted later. Backend stays **off by default**; the SDK can export to a console/
  in-memory exporter with no backend running.

## 7. Phasing

- **Phase A — instrument (no backend).** OTel SDK + a `gen_ai` attribute wrapper;
  `invoke_agent`/`chat`/`execute_tool` spans in the loop; real per-call tokens
  (from the provider) replacing the char-estimate; `InMemorySpanExporter` tests +
  snapshots. Closes STATUS item 0. No new infra.
- **Phase B — a UI backend.** Optional Phoenix container (reuse Postgres) + OTLP
  exporter behind a config flag; loop/cost ceilings surfaced; retention policy.
- **Phase C — evals / flywheel (evidence-gated).** Score traces (LLM-as-judge +
  human feedback), export failing runs (`stop_reason=error`, `loop_count>N`) into a
  `datasets/regression.jsonl`, run in CI against the mock provider. Gate deeper
  eval-platform adoption on it proving value, like session-search's bar.

## 8. Contract / ADR work before code

- **ADR-033** — "Agent observability = OpenTelemetry `gen_ai` spans as a derived
  diagnostic layer over the ADR-016 journal; optional self-hosted Phoenix backend."
  Revises the docs/07 "接 Langfuse" note (Phoenix on footprint), and supersedes the
  deferred half of STATUS item 0.
- **config-and-secrets.md** — add `OTEL_ENABLED` (default false), `OTEL_EXPORTER_OTLP_ENDPOINT`,
  `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` (default false),
  `OTEL_TRACES_SAMPLER` (default `always_on` at single-user scale), retention.
- **events-and-effects.md** — optional bounded, redacted `model.request` /
  `model.response` journal events (durability `debug`), if we want a durable per-call
  record beyond the ephemeral span. All rows keep `tenant_id` (ADR-015).
- **data-model.md** — optionally extend `generations` to be written by the chat loop
  (it already exists); no new table strictly required — the span backend holds the
  tree.

## 9. Open questions — ✅ RESOLVED (owner-locked 2026-07-24, per the recommendations; see ADR-033)

1. **Backend:** → **Phoenix** (self-hosted, reuse Postgres, optional/off by default). Phase B.
2. **Durable per-call record:** → **both** — OTel span (depth) + a bounded, redacted `model.request`/`model.response` journal event (durable, ADR-021).
3. **Content capture default:** → **off by default**; on behind a dev flag with masking.
4. **Instrumentation:** → **hand-rolled** OTel spans for the loop/tools/chat span (Sherpa uses raw-httpx streaming + a bespoke loop/tools, so provider auto-instrumentation like OpenLLMetry does not apply; keep it as a future option if Sherpa adopts mainstream SDKs/frameworks).
5. **Sequencing vs ADR-032:** → **do it independently first** (synergistic but non-blocking; it helps debug everything, memory included).

## 10. Primary sources

- OTel GenAI semconv — https://github.com/open-telemetry/semantic-conventions-genai
  (spans / agent-spans / events / metrics / attribute registry); OTel concepts —
  https://opentelemetry.io/docs/concepts/signals/ ; OTel AI-agent blog (2025) —
  https://opentelemetry.io/blog/2025/ai-agent-observability/
- opentelemetry-python — https://opentelemetry.io/docs/languages/python/ ;
  `opentelemetry-instrumentation-openai` — https://pypi.org/project/opentelemetry-instrumentation-openai/
- Arize Phoenix — https://arize.com/docs/phoenix/self-hosting/architecture ;
  local clone `C:\src\phoenix` (`server/grpc_server.py`, `trace/otel.py`,
  `trace/gen_ai/conversion.py`, `db/engines.py`, `Dockerfile`); OpenInference —
  https://github.com/Arize-ai/openinference
- Langfuse — https://langfuse.com/docs (OTel ingest, self-hosting, masking); local
  clone `C:\src\langfuse` (`docker-compose.yml` = web/worker/Postgres/ClickHouse/Redis/MinIO)
- OpenLLMetry / Traceloop — https://github.com/traceloop/openllmetry
- Agent-observability guides — https://www.braintrust.dev/articles/agent-observability-complete-guide-2026 ;
  https://langfuse.com/blog/2024-07-ai-agent-observability-with-langfuse
