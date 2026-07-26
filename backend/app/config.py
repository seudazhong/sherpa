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

    # Code execution sandbox (ADR-007/025). "disabled" (default) keeps dev/tests
    # offline; "docker" runs each snippet in a hardened ephemeral container
    # (no network, dropped caps, non-root, read-only rootfs, mem/pids/time caps).
    sandbox_kind: str = "disabled"
    sandbox_image: str = "python:3.11-slim"
    sandbox_timeout_seconds: int = 10
    sandbox_mem_mb: int = 256
    sandbox_pids_limit: int = 128

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
