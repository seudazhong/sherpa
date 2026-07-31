"""Layered application settings.

Full config + secret contract: docs/contracts/config-and-secrets.md (ADR-019).
Secrets are read from the environment and MUST NOT be logged.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    app_name: str = "sherpa"
    environment: str = "dev"
    log_level: str = "INFO"

    # Infra (sane localhost defaults for `uv run`; compose injects real values)
    database_url: str = "postgresql+asyncpg://sherpa:sherpa@localhost:5432/sherpa"
    redis_url: str = "redis://localhost:6379/0"

    # Model provider (contract: config-and-secrets.md §Provider). "mock" keeps
    # dev/tests offline; "openai_compatible" targets an OpenAI-style Chat
    # Completions endpoint (e.g. the local litellm proxy forwarding Copilot).
    provider_kind: str = "mock"
    provider_base_url: str = "http://localhost:4000"
    provider_api_key: str = ""
    provider_model: str = "claude-sonnet-4.6"
    provider_timeout_seconds: int = 60

    # Embeddings (ADR-032): a Sherpa-bundled local model by default, decoupled
    # from the chat provider. embedding_dim MUST equal the memory_passages vector
    # column width; changing the model/dim is a full re-embed, not a toggle.
    embedding_kind: str = "mock"  # mock | ollama | openai_compatible
    embedding_base_url: str | None = None  # e.g. http://ollama:11434
    embedding_model: str = "bge-m3"
    embedding_dim: int = 1024
    embedding_api_key: str = ""  # only for the openai_compatible external override
    # Throughput: texts are embedded in batches of `embedding_batch_size`, with
    # `embedding_concurrency` batches in flight at once and a bounded retry per batch.
    # The timeout is per batch and deliberately decoupled from the chat provider's
    # (a whole document used to ride on one request under provider_timeout_seconds).
    # Batch size trades granularity for request efficiency: parallelism comes from
    # concurrent *requests*, not from batch size, so on the default local CPU ollama a
    # smaller batch costs almost nothing (HTTP overhead is microseconds against seconds
    # of inference) and buys far more frequent progress plus cheaper retries. Raise it
    # for a GPU or a cloud endpoint, which do benefit from bigger batches.
    embedding_batch_size: int = 16
    embedding_concurrency: int = 3
    embedding_max_retries: int = 3
    embedding_timeout_seconds: int = 300

    # Knowledge base (ADR-036): source-backed document KB (reuses the EMBEDDING_*
    # profile above). CJK lexical search resolves a stable Postgres text-search config
    # at deploy time (zhparser, else an app-tokenized fallback).
    knowledge_text_search_config: str = "sherpa_text"
    knowledge_lexical_backend: str = "zhparser"  # zhparser | app_jieba
    knowledge_max_file_bytes: int = 25 * 1024 * 1024
    knowledge_max_pages: int = 500
    knowledge_chunk_target_tokens: int = 450
    knowledge_chunk_overlap_tokens: int = 64
    knowledge_retrieval_k: int = 6
    knowledge_retrieval_min_score: float = 0.35  # vector cosine-similarity floor (0..1)
    knowledge_evidence_retention_days: int = 30
    # A book-length source is legitimate but long-running. The arq job timeout must
    # bound it explicitly (arq's 300s default silently killed mid-embed), the lease
    # must outlive that timeout so a killed job is not instantly re-dispatched, and
    # attempts must be bounded so a permanently-too-slow source fails by name instead
    # of looping forever.
    knowledge_ingest_job_timeout_seconds: int = 3600
    knowledge_ingest_lease_margin_seconds: int = 300
    knowledge_ingest_max_attempts: int = 3
    knowledge_max_chunks: int = 8000

    # Object storage for personal files (ADR-012). "memory" keeps dev/tests
    # offline; "minio" targets an S3-compatible MinIO service.
    storage_kind: str = "memory"
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = ""
    minio_secret_key: str = ""
    minio_bucket: str = "sherpa-files"
    minio_secure: bool = False

    # Personal Drive (ADR-030, W1). Per-user quota + per-file upload cap are
    # deployment-configurable (NOT schema constants). Defaults: 5 GiB / 100 MiB.
    drive_quota_bytes: int = 5 * 1024 * 1024 * 1024
    drive_max_file_bytes: int = 100 * 1024 * 1024
    drive_trash_retention_days: int = 30
    drive_blob_gc_retention_hours: int = 24

    # Chat attachments (ADR-043). Attachments are references to Drive nodes; these
    # bound how much of them is replayed to the model, NOT what may be stored
    # (storage is bounded by DRIVE_* above).
    chat_max_attachments: int = 8
    chat_attachment_max_image_bytes: int = 5 * 1024 * 1024
    chat_attachment_assembly_max_bytes: int = 15 * 1024 * 1024
    chat_attachment_text_extract_bytes: int = 32 * 1024

    # Projects — Workspace W2a (ADR-037): blank/template/archive projects. GitHub
    # import is W2b; working-copy/sandbox is W3. These bound the archive-import +
    # snapshot paths only. Project snapshots reuse the ADR-030 immutable, deduped,
    # ref-counted storage_blobs + the shared per-user storage account/quota.
    project_max_archive_bytes: int = 200 * 1024 * 1024  # compressed archive upload cap
    project_max_expanded_bytes: int = 500 * 1024 * 1024  # expanded tree cap
    project_max_entries: int = 20000  # file/dir count cap per snapshot
    project_max_expansion_ratio: int = 100  # zip-bomb guard: expanded/compressed
    project_max_path_depth: int = 40
    project_snapshot_retention_days: int = 30  # unpinned snapshot GC window (pinned kept)

    # Projects — Workspace W2b (ADR-038): GitHub ONE-TIME import (select repo + ref ->
    # bounded archive fetch -> immutable initial snapshot -> record source repo/ref/OID).
    # No sync/push/PR (W4), no sandbox (W3). The archive-fetch path reuses the
    # PROJECT_MAX_* bounds above. GitHub credentials live only in the AEAD vault
    # (github_connections) and never enter a project tree/snapshot/prompt/log/event.
    github_api_base: str = "https://api.github.com"  # override for GitHub Enterprise
    github_default_auth_kind: str = "pat"  # pat | app_installation (forward path)
    github_import_ref_types: list[str] = ["branch", "tag", "commit"]
    github_app_id: str | None = None  # GitHub App (app_installation only)
    github_app_private_key: str | None = None  # PEM; vault/secret, never logged
    github_archive_timeout_seconds: int = 120  # bounded archive fetch deadline

    # Recurring scheduled agent tasks (ADR-031). Guardrails on autonomous runs.
    scheduled_task_max_concurrency: int = 3
    scheduled_task_min_interval_seconds: int = 300

    # Agent observability (ADR-033): OpenTelemetry `gen_ai` spans over the bounded
    # loop — a derived, ephemeral diagnostic layer over the ADR-016 journal, never a
    # source of truth. Off by default (zero overhead). When otel_enabled and an OTLP
    # endpoint is set, spans export there (Phase B); otherwise a console/in-memory
    # exporter is used. Content capture is opt-in (PII) and redacted. 100% sampling
    # is fine at single-user scale.
    otel_enabled: bool = False
    otel_exporter_otlp_endpoint: str | None = None
    otel_capture_message_content: bool = False
    otel_traces_sampler: str = "always_on"

    # Container sandbox (ADR-025/039/047). "disabled" (default) keeps dev/tests offline;
    # "docker" runs each project command in a hardened ephemeral container (no network,
    # dropped caps, non-root, read-only rootfs, mem/pids/time caps). The general-purpose
    # `run_code` snippet runner is deleted (ADR-048 O-12), so SANDBOX_TIMEOUT_SECONDS —
    # which only ever bounded that snippet — goes with it; the project wall clock is
    # SANDBOX_RUN_TIMEOUT_SECONDS below.
    #
    # SANDBOX_IMAGE MUST be the repository's own sandbox-runner image, pinned by digest
    # (config §1.7), and this is enforced fail-closed at run time by
    # app/sandbox/runtime.verify_runner_image — an unpinned tag or a foreign image is
    # refused with `runtime_image_untrusted` rather than silently executed. It is built
    # locally and never pushed, so it has no registry RepoDigest; pin it by IMAGE ID digest:
    #   docker build -t sherpa-sandbox-runner:dev sandbox-runner
    #   docker image inspect sherpa-sandbox-runner:dev --format '{{.Id}}'
    # The default is EMPTY on purpose: a fresh checkout must not appear to work by running
    # whatever a mutable tag happens to point at today.
    sandbox_kind: str = "disabled"
    sandbox_image: str = ""
    sandbox_mem_mb: int = 1024
    sandbox_pids_limit: int = 128
    # Which deployment owns a sandbox container, and therefore which containers the orphan
    # sweeper may remove. Empty (the default) derives a stable id from the data-plane
    # identity, so the ADR-044 test harness — which already has its own database — is
    # automatically distinct from the dev worker sharing the same Docker daemon. Set this
    # explicitly only when two deployments share one database. Sweeping without this scope
    # was a confirmed bug: the dev worker deleted a live test container mid-run.
    sandbox_owner_id: str = ""

    # Projects — Workspace W3 (ADR-040 + ADR-039) + the ADR-047 tar transport: task working
    # copy + one-time disposable copy + change review (config §1.7). The sandbox reuses the
    # ADR-025 hardened container and receives the disposable copy as a TAR into an anonymous
    # /work volume — no bind mount and no host path, which is why SANDBOX_SCRATCH_ROOT is
    # gone (it was the direct cause of backlog B-8). SANDBOX_WARM_TTL_SECONDS is gone too:
    # warm containers were never implemented anywhere, and the idle bound the RuntimeSession
    # actually needs is SANDBOX_RUNTIME_IDLE_TTL_SECONDS (wired by P4).
    working_copy_idle_ttl_seconds: int = 86400  # durable working-copy idle expiry
    sandbox_runtime_idle_ttl_seconds: int = 600  # RuntimeSession idle TTL (P4 lifecycle)
    # PEAK-MEMORY MODEL (config §1.7), because these two numbers are a memory budget, not
    # just a product limit. Computing a delta inherently requires BOTH trees in memory: the
    # materialized base (old) and what came back from the container (new). Measured end to
    # end, worker peak is therefore:
    #
    #     peak ~= 2 x (workspace bytes) + C,  C ~= 40 MiB (interpreter + imports)
    #
    # Everything beyond that 2x has been removed: the tar is streamed rather than buffered,
    # blob upload takes a buffer instead of a `bytes()` copy, hashing is chunked, staged
    # buffers are released per file, and the change-set projection decides diffability from
    # recorded sizes so it never reads an over-cap object at all.
    #
    # 128 MiB therefore budgets ~296 MiB of worker RSS for the sandbox path. The previous
    # 512 MiB implied ~1 GiB, which was not a claim this worker could honour — the compose
    # worker now declares a 1 GiB limit, so raising this cap REQUIRES raising that limit by
    # twice the delta. `WORKING_COPY_MAX_CHANGED_BYTES` must stay <= the transfer cap.
    #
    # OWNER-APPROVED 2026-08-01: 128 MiB is the accepted product trade-off for the current
    # 1 GiB worker budget, not a placeholder. A boundary whose changed bytes exceed it is
    # refused with `changeset_bounds` and nothing is persisted. Do not raise it without
    # raising the worker's mem_limit with it (config §1.7).
    sandbox_scratch_max_bytes: int = 128 * 1024 * 1024  # per-session tar cap (128 MiB)
    working_copy_max_changed_files: int = 5000  # change-set bound: changed-file count
    working_copy_max_changed_bytes: int = 128 * 1024 * 1024  # change-set bound: changed bytes
    working_copy_max_artifact_bytes: int = 200 * 1024 * 1024  # change-set bound: artifact bytes
    working_copy_max_diff_bytes: int = 2 * 1024 * 1024  # per-file spilled unified-diff cap (2 MiB)
    sandbox_run_timeout_seconds: int = 120  # per-exec wall-clock deadline

    # QQ official bot (ADR-028) is configured at runtime in the DB (channel_configs,
    # AppID/AppSecret via the sealed vault), NOT via env — no qq_* settings here. The
    # botpy WebSocket client runs in the worker; see app/channels/qq_official.py.

    # Agentic email (roadmap milestone 5, ADR-013/027). The single outbound email
    # seam (`send_email` tool + notification digests both go through
    # build_email_sender()). "recording" (default) records without sending (offline
    # /tests); "agentmail" sends via the AgentMail API (agent-owned mailbox). Inbound
    # agentic email arrives at POST /channels/email/webhook, Svix-signature verified
    # against agentmail_webhook_secret (whsec_...). Secrets are env-only, never logged.
    email_kind: str = "recording"
    agentmail_api_base: str = "https://api.agentmail.to"
    agentmail_api_key: str = ""
    agentmail_inbox_id: str = ""
    agentmail_webhook_secret: str = ""
    # Trusted-sender allowlist for inbound agentic email (the human owner's address).
    # When set, only this sender drives the agent (FULL tier, like the QQ owner
    # allowlist). Empty = accept any sender; ADR-013 then requires dropping to the
    # SAFE tool tier for untrusted content — a documented post-v1 follow-up.
    agentmail_owner_email: str = ""

    # Transcript compaction (docs/04 core-loop). When the assembled provider
    # message window exceeds the char budget, keep the head + the most recent
    # turns and summarize the middle; a compaction that does not shrink is
    # rejected. Tune down to exercise in tests.
    compaction_char_budget: int = 24_000
    compaction_keep_head: int = 2
    compaction_keep_recent: int = 6

    # Tool output spill (api.md §7.2). Oversized tool output (>2000 lines / 50 KB)
    # is written here as {invocation_id}.txt and replaced with a head/tail summary.
    tool_output_root: str = ".sherpa/tool-output"

    # App/session auth (contract: docs/contracts/config-and-secrets.md §2).
    # Dev defaults keep `uv run`/tests working; production MUST override via env
    # and set session_cookie_secure=true. These values are never logged.
    app_secret: str = "dev-insecure-app-secret-change-me-32bytes+"
    session_cookie_name: str = "sherpa_session"
    session_ttl_seconds: int = 604_800
    session_cookie_secure: bool = False

    # Single owner credential (v1 is single-user). Overridden by env in prod.
    owner_email: str = "owner@localhost"
    owner_password: str = "sherpa-dev-password"

    # Credential encryption (contract: config-and-secrets.md §3, ADR-019).
    # KEK is base64 of exactly 32 bytes (AES-256). The dev default keeps tests
    # runnable; production MUST override KEK/KEK_ID via env and never reuse this.
    kek: str = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="
    kek_id: str = "env-dev"
    kek_key_version: int = 1
    kek_previous_keys: str = "{}"

    # Gmail connector OAuth (contract: config-and-secrets.md §Gmail). Dev
    # placeholders keep the flow wireable + testable; real client id/secret and
    # redirect come from env per deployment.
    gmail_client_id: str = "dev-gmail-client-id"
    gmail_client_secret: str = "dev-gmail-client-secret"
    gmail_redirect: str = "http://localhost:8000/connectors/gmail/oauth/callback"
    gmail_scope: str = "https://www.googleapis.com/auth/gmail.readonly"
    oauth_state_ttl_seconds: int = 600


settings = Settings()
