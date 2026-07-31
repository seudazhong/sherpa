# Config and Secrets Contract

**Status:** frozen for v1  
**Applies to:** self-hosted, single-instance, single-owner Sherpa  
**Normative words:** MUST, MUST NOT, SHOULD, and MAY are requirements in this contract.

This contract implements [ADR-019](../decisions.md) and the v1 profile in ADR-012/015/022. Table ownership and persistence belong to [data-model.md](data-model.md); HTTP exposure belongs to [api.md](api.md); durable jobs, notification delivery, and event payload rules belong to [events-and-effects.md](events-and-effects.md). This document does not redefine those contracts.

## 1. Configuration rules

1. Exact precedence is **process environment > `.env` > model defaults**. Application code MUST load `Settings()` without constructor overrides. Tests override process environment.
2. Environment variable names are the uppercase names below. Names and types are frozen; adding or removing a key requires a contract change.
3. Secret values have no usable default, use `SecretStr`, and MUST NOT select behavior. A non-secret selector such as `PROVIDER_KIND` selects behavior; its corresponding secret is then validated.
4. `.env` is for a private self-hosted deployment only. It MUST be mode `0600` where supported, MUST be excluded from source control and images, and MUST NOT be copied into the frontend.
5. Unknown `.env` keys fail startup. Missing or malformed role-required values fail startup before the service accepts work.
6. Fixed v1 Gmail scopes are `openid`, `email`, and `https://www.googleapis.com/auth/gmail.readonly`. Scopes are code policy, not configuration.
7. `APP_SECRET` signs sessions/CSRF and OAuth state only. It MUST NOT be reused as a KEK or provider/Gmail credential. Rotating it invalidates existing sessions.

### 1.1 Pydantic-settings model sketch

```python
from __future__ import annotations

import base64
import json
from datetime import time
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AnyHttpUrl,
    Field,
    PostgresDsn,
    RedisDsn,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="forbid",
    )

    # App
    service_role: Literal["web", "worker", "migration"] = "web"
    app_env: Literal["development", "test", "production"] = "development"
    app_secret: SecretStr | None = None
    session_cookie_name: str = Field(default="sherpa_session", min_length=1)
    session_ttl_seconds: int = Field(default=604_800, ge=300, le=2_592_000)
    session_cookie_secure: bool = False
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    # DELETED (ADR-046, §1.4): workspace_root — the read/glob/grep tools it backed are removed;
    # project code is reached with fs.* and personal files with drive.*.
    tool_output_root: Path = Path(".sherpa/tool-output")
    tool_output_retention_hours: int = Field(default=24, ge=1, le=168)
    tool_output_max_bytes: int = Field(
        default=10_485_760, ge=65_536, le=104_857_600
    )
    tool_output_total_max_bytes: int = Field(
        default=1_073_741_824, ge=10_485_760
    )

    # Database and Redis. SecretStr prevents accidental repr/log disclosure.
    database_url: SecretStr | None = None
    redis_url: SecretStr | None = None

    # Credential encryption
    kek: SecretStr | None = None
    kek_id: str | None = Field(default=None, min_length=1)
    kek_key_version: int = Field(default=1, ge=1)
    kek_previous_keys: SecretStr = SecretStr("{}")

    # Model provider
    provider_kind: Literal["mock", "openai_compatible"] = "mock"
    provider_base_url: AnyHttpUrl | None = None
    provider_api_key: SecretStr | None = None
    provider_model: str = Field(default="mock-v1", min_length=1)
    provider_timeout_seconds: int = Field(default=60, ge=1, le=600)

    # Embeddings (ADR-032): a Sherpa-bundled local model by default, decoupled
    # from the chat provider. EMBEDDING_DIM MUST equal the memory_passages vector
    # column width; changing the model/dim is a full re-embed, not a toggle.
    embedding_kind: Literal["mock", "ollama", "openai_compatible"] = "mock"
    embedding_base_url: AnyHttpUrl | None = None       # e.g. http://ollama:11434
    embedding_model: str = Field(default="bge-m3", min_length=1)
    embedding_dim: int = Field(default=1024, ge=1, le=4096)
    embedding_api_key: SecretStr | None = None         # only for the openai_compatible override

    # Background memory formation (ADR-032): off by default (privacy/cost).
    memory_autoform_enabled: bool = False
    memory_autoform_every_turns: int = Field(default=0, ge=0)  # 0 = on run settle

    # Knowledge base (ADR-036): source-backed document KB. Embedding reuses the
    # EMBEDDING_* profile above (ollama/bge-m3, 1024-d). CJK lexical search resolves a
    # stable Postgres text-search config name at deploy time (zhparser, else an
    # app-tokenized fallback); the query- and index-side tokenizer versions must match.
    knowledge_text_search_config: str = Field(default="sherpa_text", min_length=1)
    knowledge_lexical_backend: Literal["zhparser", "app_jieba"] = "zhparser"
    knowledge_allowed_mime: list[str] = Field(default_factory=lambda: [
        "application/pdf", "text/markdown", "text/plain",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ])
    knowledge_max_file_bytes: int = Field(default=25 * 1024 * 1024, ge=1)  # per-source ingest cap
    knowledge_max_pages: int = Field(default=500, ge=1)
    knowledge_chunk_target_tokens: int = Field(default=450, ge=64, le=2048)
    knowledge_chunk_overlap_tokens: int = Field(default=64, ge=0, le=512)
    knowledge_retrieval_k: int = Field(default=6, ge=1, le=50)            # hits returned to the model
    knowledge_retrieval_min_score: float = Field(default=0.35, ge=0.0)   # vector cosine-similarity floor (0..1); below on all branches => "insufficient evidence"
    knowledge_evidence_retention_days: int = Field(default=30, ge=1)     # knowledge_retrieval_evidence TTL

    # Projects — Workspace W2a (ADR-037): blank/template/archive projects. GitHub import is
    # W2b; working-copy/sandbox is W3. These bound the archive-import + snapshot paths only.
    # (Design/contract-first — not yet wired; frozen here so the W2a impl reads them exactly.)
    project_max_archive_bytes: int = Field(default=200 * 1024 * 1024, ge=1)  # compressed archive upload cap
    project_max_expanded_bytes: int = Field(default=500 * 1024 * 1024, ge=1) # expanded tree cap (reserved before import)
    project_max_entries: int = Field(default=20000, ge=1)                    # file/dir count cap per snapshot
    project_max_expansion_ratio: int = Field(default=100, ge=1)             # zip-bomb guard: expanded/compressed
    project_max_path_depth: int = Field(default=40, ge=1)
    project_snapshot_retention_days: int = Field(default=30, ge=1)          # unpinned snapshot GC (pinned kept)

    # Projects — Workspace W2b (ADR-038): GitHub ONE-TIME import (select repo + ref -> bounded
    # archive fetch -> immutable initial snapshot -> record source repo/ref/OID). No sync/push/PR
    # (W4), no sandbox (W3). The archive-fetch path reuses the PROJECT_MAX_* bounds above.
    # (✅ W2b SHIPPED — migration 0029; wired by the import worker + connection service.)
    github_api_base: str = Field(default="https://api.github.com")          # override for GHE
    github_default_auth_kind: str = Field(default="pat")                    # pat | app_installation
    github_import_ref_types: list[str] = Field(default_factory=lambda: ["branch", "tag", "commit"])
    github_app_id: str | None = None                                        # GitHub App (app_installation)
    github_app_private_key: SecretStr | None = None                        # PEM; vault/secret, never logged
    github_archive_timeout_seconds: int = Field(default=120, ge=1)          # bounded archive fetch deadline

    # Projects — Workspace W3 (ADR-040 product/data + ADR-039 isolation): task working copy +
    # one-time scratch-copy coding runtime + change review. STATUS: the WORKING_COPY_* change-set
    # bounds are SHIPPED; the transport settings are TARGET (ADR-047 replaces the bind mount with tar
    # injection). The sandbox receives ONLY a tar-injected disposable copy of the working copy in an
    # anonymous /work volume, and mounts NO host path at all — never the Project snapshot/blob
    # store/credentials/another Project/Drive (see §1.7). Reuses the hardened offline
    # container from ADR-025 (network_disabled, cap_drop ALL, no-new-privileges, non-root, read-only
    # rootfs + tmpfs, mem/pids/cpu/wall caps).
    working_copy_idle_ttl_seconds: int = Field(default=86400, ge=60)        # durable working-copy idle expiry (24h)
    # DELETED (ADR-047): sandbox_warm_ttl_seconds — warm containers were never implemented; the
    # concept is now the RuntimeSession idle TTL below.
    # DELETED (ADR-047): sandbox_scratch_root — tar transport has no host scratch path at all.
    sandbox_runtime_idle_ttl_seconds: int = Field(default=600, ge=30)       # [target] RuntimeSession idle TTL (10m)
    sandbox_scratch_max_bytes: int = Field(default=2 * 1024 * 1024 * 1024, ge=1)  # per-session tar ingress cap (2 GiB)
    working_copy_max_changed_files: int = Field(default=5000, ge=1)         # change-set bound: changed-file count
    working_copy_max_changed_bytes: int = Field(default=500 * 1024 * 1024, ge=1)  # change-set bound: total changed bytes
    working_copy_max_artifact_bytes: int = Field(default=200 * 1024 * 1024, ge=1) # change-set bound: total artifact bytes
    working_copy_max_diff_bytes: int = Field(default=2 * 1024 * 1024, ge=1)  # per-file spilled unified-diff cap (2 MiB)
    sandbox_run_timeout_seconds: int = Field(default=120, ge=1)             # per-exec wall-clock deadline

    # Tool catalog budget (ADR-046) — [target]. Hard cap on the serialized JSON byte count of the
    # resolver's CORE tool set; startup fails above it. Baseline before ADR-046: 52 flat tools /
    # 19,848 bytes resent on every provider call.
    tool_catalog_core_max_bytes: int = Field(default=6144, ge=1024)

    # Agent observability (ADR-033): OpenTelemetry gen_ai spans, off by default.
    # A derived diagnostic layer over the ADR-016 journal — never a source of truth.
    otel_enabled: bool = False
    otel_exporter_otlp_endpoint: AnyHttpUrl | None = None    # e.g. http://phoenix:4317; unset = console/in-memory
    otel_capture_message_content: bool = False               # opt-in prompt/tool content into spans (PII; redacted)
    otel_traces_sampler: str = Field(default="always_on", min_length=1)  # 100% is fine at single-user scale

    # Gmail OAuth and retained Gmail data
    gmail_client_id: str | None = Field(default=None, min_length=1)
    gmail_client_secret: SecretStr | None = None
    gmail_redirect: AnyHttpUrl | None = None
    gmail_oauth_mode: Literal[
        "per_deployment", "project_managed", "both"
    ] = "per_deployment"
    gmail_data_mode: Literal["metadata_snippet", "full_body"] = "metadata_snippet"
    gmail_retention_days: int = Field(default=90, ge=1, le=3650)

    # Defaults used to seed the single owner's notification preferences
    notifications_enabled: bool = False
    notification_timezone: str = "UTC"
    notification_digest_time: time = time(8, 0)
    notification_quiet_start: time = time(22, 0)
    notification_quiet_end: time = time(8, 0)
    notification_daily_cap: int = Field(default=6, ge=0, le=100)
    notification_eventual_delivery_kinds: list[
        Literal["due_soon", "overdue"]
    ] = Field(default_factory=lambda: ["overdue"])

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Deliberately exclude init and file-secret sources: env > .env > defaults.
        return env_settings, dotenv_settings

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            raw = value.get_secret_value()
            if not raw.startswith("postgresql+asyncpg://"):
                raise ValueError("DATABASE_URL must use postgresql+asyncpg://")
            PostgresDsn(raw)
        return value

    @field_validator("redis_url")
    @classmethod
    def validate_redis_url(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            RedisDsn(value.get_secret_value())
        return value

    @field_validator("app_secret")
    @classmethod
    def validate_app_secret(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and len(value.get_secret_value().encode()) < 32:
            raise ValueError("APP_SECRET must contain at least 32 bytes")
        return value

    @field_validator("kek")
    @classmethod
    def validate_kek(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            try:
                raw = base64.b64decode(value.get_secret_value(), validate=True)
            except ValueError as exc:
                raise ValueError("KEK must be valid base64") from exc
            if len(raw) != 32:
                raise ValueError("KEK must decode to exactly 32 bytes")
        return value

    @field_validator("kek_previous_keys")
    @classmethod
    def validate_previous_keys(cls, value: SecretStr) -> SecretStr:
        try:
            keyring = json.loads(value.get_secret_value())
        except json.JSONDecodeError as exc:
            raise ValueError("KEK_PREVIOUS_KEYS must be a JSON object") from exc
        if not isinstance(keyring, dict):
            raise ValueError("KEK_PREVIOUS_KEYS must be a JSON object")
        for identity, encoded_key in keyring.items():
            if ":" not in identity or not isinstance(encoded_key, str):
                raise ValueError("previous key names must be '<kek_id>:<version>'")
            try:
                raw = base64.b64decode(encoded_key, validate=True)
            except ValueError as exc:
                raise ValueError(f"invalid previous KEK: {identity}") from exc
            if len(raw) != 32:
                raise ValueError(f"previous KEK must be 32 bytes: {identity}")
        return value

    @model_validator(mode="after")
    def validate_contract(self) -> Settings:
        required_by_role = {
            "web": (
                "app_secret",
                "database_url",
                "redis_url",
                "kek",
                "kek_id",
                "gmail_client_id",
                "gmail_client_secret",
                "gmail_redirect",
            ),
            "worker": (
                "database_url",
                "redis_url",
                "kek",
                "kek_id",
                "gmail_client_id",
                "gmail_client_secret",
                # "workspace_root" removed by ADR-046 (§1.4) together with the read/glob/grep tools.
            ),
            "migration": ("database_url",),
        }
        missing = [
            name for name in required_by_role[self.service_role]
            if getattr(self, name) is None
        ]
        if missing:
            raise ValueError(
                f"missing settings for {self.service_role}: {', '.join(missing)}"
            )

        if (
            self.app_env == "production"
            and self.service_role == "web"
            and not self.session_cookie_secure
        ):
            raise ValueError("SESSION_COOKIE_SECURE must be true in production")
        if (
            self.app_env == "production"
            and self.service_role == "web"
            and self.gmail_redirect is not None
            and self.gmail_redirect.scheme != "https"
        ):
            raise ValueError("GMAIL_REDIRECT must use HTTPS in production")

        if self.provider_kind == "openai_compatible" and self.service_role == "worker":
            if self.provider_base_url is None or self.provider_api_key is None:
                raise ValueError(
                    "real provider requires PROVIDER_BASE_URL and PROVIDER_API_KEY"
                )
            if self.provider_model == "mock-v1":
                raise ValueError("real provider requires an explicit PROVIDER_MODEL")

        if self.embedding_kind in ("ollama", "openai_compatible"):
            if self.embedding_base_url is None:
                raise ValueError("real embeddings require EMBEDDING_BASE_URL")
            if self.embedding_kind == "openai_compatible" and self.embedding_api_key is None:
                raise ValueError("openai_compatible embeddings require EMBEDDING_API_KEY")

        try:
            ZoneInfo(self.notification_timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("NOTIFICATION_TIMEZONE must be an IANA zone") from exc
        if self.notification_quiet_start == self.notification_quiet_end:
            raise ValueError("quiet-hours start and end must differ")
        if self.tool_output_total_max_bytes < self.tool_output_max_bytes:
            raise ValueError(
                "TOOL_OUTPUT_TOTAL_MAX_BYTES must be >= TOOL_OUTPUT_MAX_BYTES"
            )
        return self
```

The implementation MAY split this model into role-specific subclasses, but the environment names, types, validation, defaults, and precedence MUST remain equivalent.

### 1.2 Key inventory

“Required” means no valid default for the named service role. Secret values marked **yes** are never logged, serialized to events, returned by APIs, or exposed to the frontend.

| Group | Environment key | Python type | Default | Required | Secret? | Contract |
|---|---|---|---|---|---|---|
| App | `SERVICE_ROLE` | `web \| worker \| migration` | `web` | No; Compose overrides per service | No | Selects startup validation only. |
| App | `APP_ENV` | `development \| test \| production` | `development` | No | No | Production enables stricter validation. |
| App/session | `APP_SECRET` | `SecretStr`, at least 32 bytes | None | `web` | **Yes** | Session, CSRF, and OAuth-state signing only. |
| App/session | `SESSION_COOKIE_NAME` | `str` | `sherpa_session` | No | No | Host-only cookie name. |
| App/session | `SESSION_TTL_SECONDS` | `int`, 300–2,592,000 | `604800` | No | No | Seven-day session default. |
| App/session | `SESSION_COOKIE_SECURE` | `bool` | `false` | Must be `true` in production | No | Cookie is also `HttpOnly` and `SameSite=Lax` in code. |
| App | `LOG_LEVEL` | level enum | `INFO` | No | No | `DEBUG` never disables redaction. |
| App/runtime | ~~`WORKSPACE_ROOT`~~ | — | — | — | — | **DELETED (ADR-046, §1.4)** — the `read`/`glob`/`grep` tools it backed are gone; project code uses `fs.*`, personal files use `drive.*`. |
| App/runtime | `TOOL_OUTPUT_ROOT` | `pathlib.Path` | `.sherpa/tool-output` | No | No | Runtime-owned spill root shared by web and worker; never a user workspace. |
| App/runtime | `TOOL_OUTPUT_RETENTION_HOURS` | `int`, 1–168 | `24` | No | No | Maximum lifetime from spill creation. |
| App/runtime | `TOOL_OUTPUT_MAX_BYTES` | `int`, 65,536–104,857,600 | `10485760` | No | No | Maximum bytes persisted for one invocation (10 MiB). |
| App/runtime | `TOOL_OUTPUT_TOTAL_MAX_BYTES` | `int >= 10,485,760` | `1073741824` | No | No | Maximum aggregate spill storage (1 GiB); must be at least the per-invocation cap. |
| DB | `DATABASE_URL` | `SecretStr`, validated `PostgresDsn` | None | `web`, `worker`, `migration` | **Yes** | Must use `postgresql+asyncpg://`; it may contain a password. |
| Redis | `REDIS_URL` | `SecretStr`, validated `RedisDsn` | None | `web`, `worker` | **Yes** | Queue/Streams/locks; it may contain a password. |
| Security/KEK | `KEK` | base64 `SecretStr`, exactly 32 decoded bytes | None | `web`, `worker` | **Yes** | Active v1 environment-backed AES-256 KEK. |
| Security/KEK | `KEK_ID` | non-empty `str` | None | `web`, `worker` | No | Stable key identifier stored with credentials; never use a key value or hash. |
| Security/KEK | `KEK_KEY_VERSION` | `int >= 1` | `1` | No | No | Version within `KEK_ID`; increment when key material changes. |
| Security/KEK | `KEK_PREVIOUS_KEYS` | secret JSON object | `{}` | During rotation only | **Yes** | Map of `"<kek_id>:<version>"` to base64 32-byte old KEKs. |
| Provider | `PROVIDER_KIND` | `mock \| openai_compatible` | `mock` | No | No | **Fallback only (ADR-041):** the env single-provider used when no `model_providers` row is configured, plus the test/mock provider. Runtime multi-source config lives in `model_providers` (DB + AEAD key). |
| Provider | `PROVIDER_BASE_URL` | `AnyHttpUrl` | None | `worker` when real | No | OpenAI-compatible API root (env fallback). |
| Provider | `PROVIDER_API_KEY` | `SecretStr` | None | `worker` when real | **Yes** | Env-fallback key, sent only to the selected provider origin. **User-configured provider keys are NOT env** — they are AEAD-sealed in `model_providers` (ADR-041/019), decrypted only at the `Provider.stream()` boundary. |
| Provider | `PROVIDER_MODEL` | `str` | `mock-v1` | Explicit when real | No | Persisted with generation telemetry. |
| Provider | `PROVIDER_TIMEOUT_SECONDS` | `int`, 1–600 | `60` | No | No | Whole outbound provider request timeout (applies to all provider kinds). |
| Embeddings | `EMBEDDING_KIND` | `mock \| ollama \| openai_compatible` | `mock` | `worker` when real | No | Embedding backend; **decoupled** from `PROVIDER_KIND` (ADR-032). |
| Embeddings | `EMBEDDING_BASE_URL` | `AnyHttpUrl` | None | `worker` when ollama/openai | No | `http://ollama:11434` (bundled CPU container) · `http://host.docker.internal:11434` (host-installed ollama, uses the host GPU; the host daemon must bind `0.0.0.0`) · or an external `/v1` root. |
| Embeddings | `EMBEDDING_MODEL` | `str` | `bge-m3` | No | No | Persisted per passage; a change requires re-embedding all passages. |
| Embeddings | `EMBEDDING_DIM` | `int`, 1–4096 | `1024` | No | No | MUST equal the `memory_passages.embedding` column width. |
| Embeddings | `EMBEDDING_API_KEY` | `SecretStr` | None | when `openai_compatible` | **Yes** | Only for the external-provider embedding override. |
| Embeddings | `EMBEDDING_BATCH_SIZE` | `int` ≥ 1 | `16` | No | No | Texts per outbound embedding request. Parallelism comes from concurrent *requests*, not batch size, so on the default local CPU ollama a smaller batch costs ~nothing and buys much finer progress + cheaper retries. Raise it for a GPU/cloud endpoint. |
| Embeddings | `EMBEDDING_CONCURRENCY` | `int` ≥ 1 | `3` | No | No | Batches in flight at once (shared connection pool). Raise only if the backend can take it. |
| Embeddings | `EMBEDDING_MAX_RETRIES` | `int` ≥ 1 | `3` | No | No | Bounded exponential-backoff attempts **per batch**; exhaustion is a named ingest exit (`embedding_failed`). |
| Embeddings | `EMBEDDING_TIMEOUT_SECONDS` | `int`, 1–600 | `300` | No | No | Per-**batch** timeout, deliberately decoupled from `PROVIDER_TIMEOUT_SECONDS` (a slow CPU embedder is not a slow chat model). |
| Knowledge | `KNOWLEDGE_INGEST_JOB_TIMEOUT_SECONDS` | `int` ≥ 60 | `3600` | No | No | arq job timeout for one ingest. **Must be set explicitly** — arq's 300 s default silently killed book-length sources mid-embed. |
| Knowledge | `KNOWLEDGE_INGEST_LEASE_MARGIN_SECONDS` | `int` ≥ 0 | `300` | No | No | Lease = job timeout + this. The lease MUST outlive the timeout so a killed job is not instantly re-dispatched on top of itself. |
| Knowledge | `KNOWLEDGE_INGEST_MAX_ATTEMPTS` | `int` ≥ 1 | `3` | No | No | Durable attempt bound; exhaustion is the named exit `too_many_attempts`. |
| Knowledge | `KNOWLEDGE_MAX_CHUNKS` | `int` ≥ 1 | `8000` | No | No | Per-version chunk cap; over it the source fails as `document_too_large` instead of re-burning the job timeout. |
| Memory | `MEMORY_AUTOFORM_ENABLED` | `bool` | `false` | No | No | Background memory-formation kill-switch (ADR-032). |
| Memory | `MEMORY_AUTOFORM_EVERY_TURNS` | `int` ≥ 0 | `0` | No | No | `0` = form on run settle; `N` = every N user turns. |
| Knowledge | `KNOWLEDGE_TEXT_SEARCH_CONFIG` | `str` | `sherpa_text` | No | No | Postgres TS config name for CJK lexical (ADR-036); query- and index-side tokenizer versions must match. |
| Knowledge | `KNOWLEDGE_LEXICAL_BACKEND` | `zhparser \| app_jieba` | `zhparser` | No | No | Index-time tokenizer; `app_jieba` fallback stores tokenized `lexical_text`. |
| Knowledge | `KNOWLEDGE_ALLOWED_MIME` | `list[str]` | PDF/MD/TXT/DOCX | No | No | Ingest allowlist; archives/OCR/executable formats deferred. |
| Knowledge | `KNOWLEDGE_MAX_FILE_BYTES` | `int` ≥ 1 | `26214400` | No | No | Per-source ingest cap (25 MiB). |
| Knowledge | `KNOWLEDGE_MAX_PAGES` | `int` ≥ 1 | `500` | No | No | Per-source page cap. |
| Knowledge | `KNOWLEDGE_CHUNK_TARGET_TOKENS` | `int`, 64–2048 | `450` | No | No | Structural chunk target; tune against the golden set. |
| Knowledge | `KNOWLEDGE_CHUNK_OVERLAP_TOKENS` | `int`, 0–512 | `64` | No | No | Chunk overlap. |
| Knowledge | `KNOWLEDGE_RETRIEVAL_K` | `int`, 1–50 | `6` | No | No | Hits returned to the model per `search_knowledge`. |
| Knowledge | `KNOWLEDGE_RETRIEVAL_MIN_SCORE` | `float` ≥ 0 | `0.35` | No | No | Vector cosine-similarity floor (0..1); a query clearing no branch ⇒ `sufficient=false` ("insufficient evidence"). |
| Knowledge | `KNOWLEDGE_EVIDENCE_RETENTION_DAYS` | `int` ≥ 1 | `30` | No | No | `knowledge_retrieval_evidence` TTL (GC sweep). |
| Projects (W2a) | `PROJECT_MAX_ARCHIVE_BYTES` | `int` ≥ 1 | `209715200` | No | No | Compressed archive upload cap for archive-import (ADR-037; 200 MiB). |
| Projects (W2a) | `PROJECT_MAX_EXPANDED_BYTES` | `int` ≥ 1 | `524288000` | No | No | Expanded-tree cap, reserved before import (500 MiB). |
| Projects (W2a) | `PROJECT_MAX_ENTRIES` | `int` ≥ 1 | `20000` | No | No | File/dir count cap per snapshot. |
| Projects (W2a) | `PROJECT_MAX_EXPANSION_RATIO` | `int` ≥ 1 | `100` | No | No | Zip-bomb guard: expanded/compressed ratio. |
| Projects (W2a) | `PROJECT_MAX_PATH_DEPTH` | `int` ≥ 1 | `40` | No | No | Max path component depth in a snapshot entry. |
| Projects (W2a) | `PROJECT_SNAPSHOT_RETENTION_DAYS` | `int` ≥ 1 | `30` | No | No | Unpinned snapshot GC window; pinned checkpoints kept. |
| Projects (W2b) | `GITHUB_API_BASE` | `str` | `https://api.github.com` | `web`, `worker` | No | GitHub REST base (override for GitHub Enterprise); ref-resolve + archive fetch (ADR-038). |
| Projects (W2b) | `GITHUB_DEFAULT_AUTH_KIND` | `pat \| app_installation` | `pat` | No | No | First-version credential kind; `app_installation` (GitHub App) is the forward path. |
| Projects (W2b) | `GITHUB_IMPORT_REF_TYPES` | JSON list of `branch \| tag \| commit` | `["branch","tag","commit"]` | No | No | Accepted one-time-import ref kinds (all three first-version, ADR-038). |
| Projects (W2b) | `GITHUB_APP_ID` | `str` | None | `web`, `worker` | No | GitHub App id (only when `app_installation`). |
| Projects (W2b) | `GITHUB_APP_PRIVATE_KEY` | `SecretStr` | None | `worker` | **Yes** | GitHub App PEM for minting installation tokens; vault/secret, never logged or in project content. |
| Projects (W2b) | `GITHUB_ARCHIVE_TIMEOUT_SECONDS` | `int` ≥ 1 | `120` | No | No | Bounded deadline for the archive (tarball) fetch. |
| Projects (runtime) | `WORKING_COPY_IDLE_TTL_SECONDS` | `int` ≥ 60 | `86400` | No | No | Durable task working-copy idle expiry (ADR-040; expiry + reservation release are one atomic transition). |
| Projects (runtime) | ~~`SANDBOX_WARM_TTL_SECONDS`~~ | — | — | — | — | **DELETED (ADR-047)** — warm containers were never implemented in code; superseded by `SANDBOX_RUNTIME_IDLE_TTL_SECONDS`. |
| Projects (runtime) | ~~`SANDBOX_SCRATCH_ROOT`~~ | — | — | — | — | **DELETED (ADR-047)** — tar transport passes no host path to the daemon at all; this setting was the direct cause of backlog B-8. |
| Projects (runtime) | `SANDBOX_RUNTIME_IDLE_TTL_SECONDS` | `int` ≥ 30 | `600` | No | No | **`[target]`** RuntimeSession idle TTL; the container is closed after it, and the working copy survives. |
| Projects (runtime) | `SANDBOX_SCRATCH_MAX_BYTES` | `int` ≥ 1 | `2147483648` | No | No | Per-session tar ingress cap (2 GiB). |
| Projects (runtime) | `WORKING_COPY_MAX_CHANGED_FILES` | `int` ≥ 1 | `5000` | No | No | Change-set bound: changed-file count; overflow ⇒ explicit truncated review. |
| Projects (runtime) | `WORKING_COPY_MAX_CHANGED_BYTES` | `int` ≥ 1 | `524288000` | No | No | Change-set bound: total changed bytes (500 MiB). |
| Projects (runtime) | `WORKING_COPY_MAX_ARTIFACT_BYTES` | `int` ≥ 1 | `209715200` | No | No | Change-set bound: total artifact bytes (200 MiB); artifacts charge quota only when kept. |
| Projects (runtime) | `WORKING_COPY_MAX_DIFF_BYTES` | `int` ≥ 1 | `2097152` | No | No | Per-file spilled unified-diff cap (2 MiB); over ⇒ `diff_truncated`. |
| Projects (runtime) | `SANDBOX_RUN_TIMEOUT_SECONDS` | `int` ≥ 1 | `120` | No | No | Per-exec wall-clock deadline; over ⇒ `wall_timeout`. |
| Projects (runtime) | `SANDBOX_IMAGE` | `str` | pinned `sherpa-sandbox-runner` digest | `worker` | No | **`[target]`** MUST be the repository's own runner image by digest (python + pytest + ruff + `capabilities.json`, no git, no network tooling) — never a stock upstream tag. |
| Tools | `TOOL_CATALOG_CORE_MAX_BYTES` | `int` ≥ 1024 | `6144` | No | No | **`[target]`** Hard cap on the serialized core tool-set bytes (ADR-046); startup fails above it. Pre-ADR-046 baseline was 19,848 bytes across 52 flat tools. |
| Observability | `OTEL_ENABLED` | `bool` | `false` | No | No | Emit OpenTelemetry `gen_ai` spans (ADR-033); a derived diagnostic layer over the journal, never a source of truth. |
| Observability | `OTEL_EXPORTER_OTLP_ENDPOINT` | `AnyHttpUrl` | None | No | No | OTLP endpoint (e.g. self-hosted Phoenix `http://phoenix:4317`); unset = console/in-memory exporter only. |
| Observability | `OTEL_CAPTURE_MESSAGE_CONTENT` | `bool` | `false` | No | No | Opt-in capture of prompt/completion/tool content into spans (PII); redacted when on. Also set the upstream `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` if using OTel auto-instrumentation. |
| Observability | `OTEL_TRACES_SAMPLER` | `str` | `always_on` | No | No | 100% sampling is fine at single-user scale. |
| Gmail OAuth | `GMAIL_CLIENT_ID` | `str` | None | `web`, `worker` | No | OAuth client identifier. |
| Gmail OAuth | `GMAIL_CLIENT_SECRET` | `SecretStr` | None | `web`, `worker` | **Yes** | OAuth code exchange and refresh only. |
| Gmail OAuth | `GMAIL_REDIRECT` | `AnyHttpUrl` | None | `web` | No | Exact registered URL: configurable origin plus fixed `/connectors/gmail/oauth/callback`; HTTPS in production. |
| Gmail OAuth | `GMAIL_OAUTH_MODE` | `per_deployment \| project_managed \| both` | `per_deployment` | No | No | Operating model remains open in review §5. |
| Gmail data | `GMAIL_DATA_MODE` | `metadata_snippet \| full_body` | `metadata_snippet` | No | No | v1 safe default stores no full body or attachments. |
| Gmail data | `GMAIL_RETENTION_DAYS` | `int`, 1–3650 | `90` | No | No | Rolling connector-content retention window. |
| Notifications | `NOTIFICATIONS_ENABLED` | `bool` | `false` | No | No | Opt-in by default; config seeds owner preferences. |
| Notifications | `NOTIFICATION_TIMEZONE` | IANA zone `str` | `UTC` | No | No | Interprets digest and quiet-hour times. |
| Notifications | `NOTIFICATION_DIGEST_TIME` | `datetime.time` | `08:00` | No | No | One daily digest when enabled. |
| Notifications | `NOTIFICATION_QUIET_START` | `datetime.time` | `22:00` | No | No | Quiet interval may cross midnight. |
| Notifications | `NOTIFICATION_QUIET_END` | `datetime.time` | `08:00` | No | No | Queued notifications resume after this time. |
| Notifications | `NOTIFICATION_DAILY_CAP` | `int`, 0–100 | `6` | No | No | Maximum non-digest notifications per local day. |
| Notifications | `NOTIFICATION_EVENTUAL_DELIVERY_KINDS` | JSON list of `due_soon \| overdue` | `["overdue"]` | No | No | These reminder kinds use durable eventual delivery; digest prefers no duplicate. |

### 1.3 Tool-output spill storage

Large tool output is an ephemeral runtime artifact, not a database record, user workspace, connector credential, or sandbox file. [api.md](api.md) owns the response schema and spill reference; this contract owns its storage settings.

- The only valid path is `TOOL_OUTPUT_ROOT/{invocation_id}.txt`. The invocation ID comes from [events-and-effects.md](events-and-effects.md), is validated as a path-safe canonical ID, and MUST NOT contain client-provided path segments or filenames.
- The runtime creates the root with owner-only access (`0700` where supported), creates files atomically with owner-only access (`0600`), refuses symlinks, and never serves the root as a static directory.
- `TOOL_OUTPUT_MAX_BYTES` is a hard per-invocation persisted-byte limit. Any truncation marker or typed resource-limit response is defined by [api.md](api.md); the runtime never silently writes beyond the cap.
- A janitor deletes each spill no later than `TOOL_OUTPUT_RETENTION_HOURS` after creation. When aggregate usage reaches `TOOL_OUTPUT_TOTAL_MAX_BYTES`, it removes expired files first and otherwise rejects a new spill with the API's typed resource-limit error; it does not evict an unexpired file silently.
- Web reads and deletes authorized spill references; worker writes them. Compose MUST mount the same private named volume at the configured root in both services.
- Spill files are excluded from database/object-store backups, events, logs, prompts, frontend assets, and sandbox mounts. They may contain sensitive user content and receive the same access-control and deletion treatment as the originating tool result.

### 1.4 Read-only workspace root — **REMOVED (2026-07-30, ADR-046)**

> **DELETED.** `WORKSPACE_ROOT` and its `read`/`glob`/`grep` tools are removed by the
> ADR-045 clean break. They were the filesystem authority for an api.md §7.3 starter
> registry that no longer exists, and they are strictly superseded:
> - **Project code** is reached with `fs_list`/`fs_read`/`fs_grep` (api §7.6), which read
>   the Project working copy's **effective tree** and enforce project-relative paths.
> - **Personal files** are reached with `drive.*` over the ADR-030 Drive.
>
> Removing it also removes a standing hazard: `WORKSPACE_ROOT` pointed at a **host
> directory bind-mounted into the worker**, with a documented rule that the deployment must
> not select a directory containing `.env`, KEKs or the Docker socket. The clean break
> deletes the setting, the mount and the rule together rather than keeping a warning about
> a path nobody uses. The path-safety requirements themselves are not lost — they are
> restated for project paths in §1.7 and api §7.6.

### 1.5 Projects security boundary — Workspace W2a (ADR-037)

Design/contract-first (ADR-037); the settings above are frozen but **not yet wired**. When the W2a implementation lands it MUST honor this boundary:

- **Archive imports are untrusted input.** Expand in an isolated staging area — never directly into a canonical snapshot. Enforce `PROJECT_MAX_ARCHIVE_BYTES`, `PROJECT_MAX_EXPANDED_BYTES`, `PROJECT_MAX_ENTRIES`, `PROJECT_MAX_EXPANSION_RATIO`, and `PROJECT_MAX_PATH_DEPTH` before materializing. Reject absolute/traversal (`..`) paths, NUL, device/FIFO nodes, hard links, and symlinks that escape the project root. Do not trust the client `Content-Type` or file extension; generate server-side object keys.
- **Project bytes are content-addressed and reference-counted** via the ADR-030 `storage_blobs`/quota ledger (reserve before write, `507` over quota; distinct blobs charged once). Snapshot entries are immutable; snapshot bytes are never in the append-only journal (events §2.9).
- **No credentials in project state.** Project file trees, snapshots, prompts, logs, and tool results MUST NOT contain provider/model/storage/source credentials. GitHub source credentials arrive only in **W2b** and stay in the vault/connector boundary (ADR-019); they never enter a snapshot or (W3) a sandbox.
- **W2a has no sandbox.** Open in Chat is read/discuss only — no working copy, no container, no mount. The scratch-copy sandbox and the `docker.sock`/multi-user isolation hardening are **W3 preconditions** governed by a later ADR-025 revision (ADR-037 §决策3/4).

### 1.6 GitHub source boundary — Workspace W2b (ADR-038)

**✅ W2b SHIPPED (migration `0029`); the `GITHUB_*` settings are wired.** W2b is a **one-time GitHub import** (select repo + ref → bounded archive fetch → immutable initial snapshot → record source repo/ref/OID); the remote is **not** authoritative after import. The W2b implementation honors this boundary:

- **Credentials live only in the vault/connector boundary (ADR-019).** The GitHub token (a fine-grained PAT with `contents:read`, or a GitHub App installation token) is AEAD-sealed in `github_connections` and decrypted **only** by the import worker at the connector boundary. It MUST NOT appear in a project file tree, snapshot, snapshot entry, prompt, log, tool result, the event journal, an export, or (W3) a sandbox. `project_sources.connection_id` is a reference, never the token. `GITHUB_APP_PRIVATE_KEY` is a secret handled like other AEAD/KEK material (never logged).
- **The fetched archive is untrusted input.** The worker resolves the ref → a concrete commit OID, fetches the **tarball** of that OID (contents only, no git history — no `git clone`, no `.git`, no working copy), and expands it through the **same W2a isolated, bounded, in-memory safe expander**: enforce `PROJECT_MAX_ARCHIVE_BYTES`/`PROJECT_MAX_EXPANDED_BYTES`/`PROJECT_MAX_ENTRIES`/`PROJECT_MAX_EXPANSION_RATIO`/`PROJECT_MAX_PATH_DEPTH` and reject absolute/traversal (`..`)/NUL paths, device/FIFO nodes, hard links, and escaping symlinks before materializing. Do not trust upstream file names as safe.
- **Bytes are content-addressed + reference-counted** via the ADR-030 `storage_blobs`/quota ledger (reserve before write, `507` over quota; distinct blobs charged once) — the same as Drive/archive imports. Snapshot bytes are never in the append-only journal (events §2.10).
- **Read-only fetch ⇒ idempotent; no external write.** The archive fetch does not mutate the remote, so there is no `effect_unknown` remote reconciliation in W2b (that is W4 push). A failed/partial fetch is retryable by resolved OID. `source_oid` is recorded as provenance; W2b never re-fetches or tracks the remote.
- **No sandbox / no external write in W2b.** Working copy + scratch-copy sandbox is W3; GitHub sync/push/PR (ADR-020 approval, expected remote OID, no first-version force push) is W4. Each is a later ADR.

### 1.7 Sandbox transport / lifecycle / resource / network / credential boundary (ADR-039 isolation + ADR-047 transport + ADR-048 runtime)

**STATUS (2026-07-30).** The `WORKING_COPY_*` change-set bounds and the hardened container
are **`[shipped]`**. The **transport** is **`[target]`**: ADR-047 replaces the bind mount
with tar injection, which deletes `SANDBOX_SCRATCH_ROOT` and adds
`SANDBOX_RUNTIME_IDLE_TTL_SECONDS`; `SANDBOX_WARM_TTL_SECONDS` is deleted because warm
containers were never implemented anywhere in the code. This section governs the one change
the coding runtime makes to the ADR-025 sandbox: **it injects a disposable copy of the
working copy and nothing else.**

- **Transport boundary (the core rule) `[target]`.** A runtime session materializes
  `base snapshot + persisted overlay` into an **in-memory tar** and `put_archive`s it into
  the container's **anonymous** `/work` volume (`nosuid,nodev`); the reverse boundary is
  `get_archive`. **There is no bind mount and no host path in the container-create call at
  all.** The sandbox therefore has no path to, and cannot be pointed at, the Project
  `project_snapshots`/`project_snapshot_entries`, the MinIO/`storage_blobs` object store,
  another Project or working copy, Drive, `TOOL_OUTPUT_ROOT`, the `.env`/KEK/Docker socket,
  or any credential file. Canonical source-of-truth storage is **never** mounted read-write
  (ADR-025 revision). *Because there is no `src=` parameter, the ADR-039 §决策1③ requirement
  to validate a constructed scratch source path as untrusted input no longer applies — the
  attack surface is structurally removed rather than guarded.* The socket-holding
  orchestrator remains the trust boundary and must never be influenced by agent/project
  content.
- **Materialize from durable state, never from a live mount.** Ingress is bounded by
  `SANDBOX_SCRATCH_MAX_BYTES`. The container and the prepared image are **rebuildable
  caches**, never a recovery source of truth (events §2.11). **No credential is ever written
  into the tar** — the materializer strips and then asserts the absence of `.env*`, `*.pem`,
  `*.key`, `id_*` and `.git/config` before the archive is built.
- **Untrusted archive on both directions.** The egress tar is untrusted input: expansion
  rejects absolute paths, `..` traversal, NUL, device/FIFO nodes, hard links, and symlinks
  resolving outside the project root, reusing the bounded expander already used for project
  imports. A violation ends the exec with `path_escape`.
- **Lifecycle + orphan sweep.** The orchestrator persists the overlay/change-set boundary
  **before** teardown, then removes the container in a `finally` (`--rm`). A worker-startup
  sweep purges containers labelled with this deployment's runtime label from crashed runs. A
  runtime session idle for `SANDBOX_RUNTIME_IDLE_TTL_SECONDS` is closed; a durable working
  copy idle for `WORKING_COPY_IDLE_TTL_SECONDS` expires, and idle-expiry release plus
  quota-reservation release are **one atomic transition**.
- **Resource bounds (reuse ADR-025 + change-set caps).** Keep the ADR-025 hardened
  container: `network_disabled`, `cap_drop=ALL`, `no-new-privileges`, non-root (`nobody`),
  read-only rootfs + `tmpfs /tmp`, mem/pids/cpu limits, and a wall-clock kill
  (`SANDBOX_RUN_TIMEOUT_SECONDS`). Keep `SANDBOX_SCRATCH_MAX_BYTES` and the
  `WORKING_COPY_MAX_*` change-set bounds (changed-file count, changed bytes, artifact bytes,
  per-file diff bytes) — overflow ⇒ a named termination reason + an **explicit truncated**
  change set, never a silent full-looking diff.
- **Image boundary `[target]`.** `SANDBOX_IMAGE` MUST be a **pinned digest of the
  repository's own `sandbox-runner` image**, not a stock upstream tag. The v1 image carries
  Python + `pytest` + `ruff` and a `capabilities.json` manifest that the orchestrator probes
  at `runtime_open`; it deliberately contains **no `git` and no network tooling**. Node is a
  later optional profile. Probed capabilities let a missing dependency return
  `environment_missing_dependencies` **with the list of what is available**, instead of an
  unexplained exit 127.
- **Network + dependency policy.** The sandbox stays **network-disabled**; there is **no
  egress and no package installation**. A command needing an unavailable runtime/dependency
  ends with `environment_missing_dependencies` (events §2.11) — the sandbox **never**
  silently enables network to fetch packages.
- **Credential boundary (ADR-019/039).** No model/provider/storage/GitHub/KEK credential is
  ever passed into the sandbox environment, command line, tar, overlay, change set,
  artifact, snapshot, prompt, log, or tool result — reaffirming §1.5/§1.6 and the ADR-025
  rule "无任何密钥注入". A **canary test** asserts this: a synthetic KEK-shaped secret placed
  in a project tree must not appear in the tar, overlay, change set, artifact, log, prompt
  or tool result.
- **`docker.sock` / multi-user gate (ADR-039 do-not-ship conditions) — UNCHANGED.** The
  transport change makes the single-user dev posture *correct* rather than broken; it does
  **not** move the multi-user gate. **Do NOT ship multi-user or genuinely-untrusted-
  third-party code** on the shared-`docker.sock`/shared-kernel runc baseline: per ADR-039
  that still requires a gVisor (`runsc`) or microVM (Kata/Firecracker) runtime for untrusted
  containers, per-tenant isolation, a tenant-aware egress policy, and aggregate per-tenant
  quotas — with a threat review — first. This boundary must be reported truthfully in
  readiness/docs and never overclaimed.

### 1.10 Tool catalog budget (ADR-046) **`[target]`**

- `TOOL_CATALOG_CORE_MAX_BYTES` (initial **6144**) is a hard cap on the serialized JSON byte
  count of the resolver's **core** tool set. Startup fails if the core set exceeds it, and a
  regression test asserts it — this is the guard that stops the catalog from silently
  refilling to today's 19,848 bytes.
- The measured baseline for the pre-ADR-046 flat registry is **52 tools / 19,848 bytes /
  ≈4,962 tokens** sent on **every** provider call. Telemetry (`toolset.resolved`,
  events §2.2) records `tools_offered`, `core_bytes` and `total_bytes` per turn so the
  budget is observable in production, not only in tests.

### 1.8 Chat attachment boundary (ADR-043)

**✅ SHIPPED (migration `0032`).** Chat attachments are **references to Drive nodes**, never a second byte store. The implementation honors this boundary:

- **Drive is the only byte store.** Pasted/uploaded images are written to Drive (`Chat uploads/`) before admission, so the ADR-030 quota (`507`), per-file cap `DRIVE_MAX_FILE_BYTES` (`413`), versioning, trash, and blob GC apply unchanged. `parts` rows of kind `image`/`file_ref` carry only `{drive_node_id, version, name, content_type, size_bytes}`; bytes never enter `parts`, the append-only journal, an event payload, or an SSE frame.
- **Bounded assembly.** `CHAT_MAX_ATTACHMENTS` (8) bounds a prompt; `CHAT_ATTACHMENT_MAX_IMAGE_BYTES` (5 MiB) bounds one replayed image; `CHAT_ATTACHMENT_ASSEMBLY_MAX_BYTES` (15 MiB) bounds one provider-history assembly; `CHAT_ATTACHMENT_TEXT_EXTRACT_BYTES` (32 KiB) bounds an inlined text extract. Overflow degrades to an explicit placeholder — never a silent truncation and never an unbounded prompt (docs/04 invariant ⑥).
- **Ownership is structural.** Attachment resolution reuses the Drive service's tenant + user scoping, so referencing another owner's node is impossible; unknown/trashed/not-owned nodes return `404` (never `403`, api §2.1).
- **Attachments are a human act.** Only the composer (or a Drive pick) creates one; untrusted connector content (email, ADR-009) never becomes an attachment, so the no-tool `CONNECTOR_ANALYSIS` boundary is unchanged. There is no agent tool for attaching — the agent reads the same bytes through `drive_read`.
- **Capability, not optimism.** A source with `supports_vision = false` never receives image content; the assembler substitutes an honest text placeholder instead of provoking a provider error.

### 1.9 Test-harness environment — NOT application configuration (ADR-044)

**✅ SHIPPED (backlog B-9; no migration, no `Settings` change).** The pytest suite runs against a **dedicated data plane**, and the variables that steer it are deliberately **not** part of `Settings`, the key inventory (§1.2), or the frozen `.env.example` (§2). They are read directly from the environment by `backend/tests/db_guard.py`, so the production configuration surface stays exactly as frozen above.

| Variable | Default | Meaning |
|---|---|---|
| `TEST_DATABASE_URL` | derived: `DATABASE_URL` with the database name suffixed `_test` | The throwaway database the suite provisions and may delete rows in. |
| `TEST_REDIS_URL` | derived: `REDIS_URL` with logical db **15** | Queue/Streams/leader-lock isolation, so a running dev worker never consumes a job the suite enqueued. |
| `SHERPA_TEST_DB_ADOPT` | unset | One-time opt-in to stamp the marker on a pre-existing database. |
| `SHERPA_TEST_DB_RESET` | unset | Drop and recreate the test database before the run. |

Rules (all fail-closed — the suite aborts rather than degrading to the application database):

- **The owner is synthetic.** The harness forces `OWNER_EMAIL=test-owner@sherpa.test`. Because `owner_ids()` derives the tenant/user uuid5 from that address, the tenant the suite deletes is provably not the one the running stack authenticates as.
- **A marker table is the only evidence.** `_sherpa_test_marker` is written **only** by the harness into a database it created or that was explicitly adopted. Destructive fixtures refuse to run without it. It is intentionally absent from `Base.metadata`, so **never run `alembic revision --autogenerate` against the test database**.
- **Same-database is fatal.** If the resolved test database equals the application database — including via an explicit `TEST_DATABASE_URL` — the run aborts at import, before any connection is opened.
- **No secrets move.** The test database holds no real credentials; `KEK`/`KEK_ID` remain env-only (§3) and the harness neither reads nor relocates them.

## 2. Frozen `.env.example`
The repository-level `.env.example` MUST contain the following template. Secret placeholders intentionally fail secure validation or authentication until replaced.

```dotenv
# Sherpa v1 — copy to .env, keep private, never commit.
# Compose overrides SERVICE_ROLE to "worker" and "migration" for those services.
SERVICE_ROLE=web
APP_ENV=development
LOG_LEVEL=INFO

# SECRET — generate at least 32 random bytes, e.g.:
# python -c "import secrets; print(secrets.token_urlsafe(48))"
APP_SECRET=REPLACE_WITH_AT_LEAST_32_RANDOM_BYTES
SESSION_COOKIE_NAME=sherpa_session
SESSION_TTL_SECONDS=604800
# false is for local HTTP only; production startup requires true.
SESSION_COOKIE_SECURE=false

# DELETED by ADR-046 (§1.4): WORKSPACE_ROOT. The read/glob/grep tools it backed are gone;
# project code is reached with fs.*, personal files with drive.*. Remove it from .env and
# remove its read-only worker mount from compose during Phase TR.

# Runtime-owned tool-output spill storage (not a user workspace).
TOOL_OUTPUT_ROOT=.sherpa/tool-output
TOOL_OUTPUT_RETENTION_HOURS=24
TOOL_OUTPUT_MAX_BYTES=10485760
TOOL_OUTPUT_TOTAL_MAX_BYTES=1073741824

# SECRET — URLs can contain passwords and must never be logged.
DATABASE_URL=postgresql+asyncpg://sherpa:REPLACE_DB_PASSWORD@postgres:5432/sherpa
REDIS_URL=redis://redis:6379/0

# SECRET — active master key source is env in v1; KMS is a later key-provider.
# Generate KEK with:
# python -c "import base64,secrets; print(base64.b64encode(secrets.token_bytes(32)).decode())"
KEK=REPLACE_WITH_BASE64_OF_32_RANDOM_BYTES
KEK_ID=env-main
KEK_KEY_VERSION=1
# SECRET — temporary old-key ring used only during rotation.
KEK_PREVIOUS_KEYS={}

# Safe implementation default. The initial real provider/model remains open in review §5.
PROVIDER_KIND=mock
PROVIDER_MODEL=mock-v1
PROVIDER_TIMEOUT_SECONDS=60
# For PROVIDER_KIND=openai_compatible, uncomment and replace:
# PROVIDER_BASE_URL=https://api.example.com/v1
# PROVIDER_API_KEY=REPLACE_WITH_PROVIDER_API_KEY  # SECRET

# Embeddings (ADR-032). Default = bundled local ollama; decoupled from the chat provider.
EMBEDDING_KIND=mock
EMBEDDING_MODEL=bge-m3
EMBEDDING_DIM=1024
# Throughput (ADR-032): batches of N, C in flight, bounded retry, per-batch timeout.
EMBEDDING_BATCH_SIZE=32
EMBEDDING_CONCURRENCY=3
EMBEDDING_MAX_RETRIES=3
EMBEDDING_TIMEOUT_SECONDS=120
# For EMBEDDING_KIND=ollama (bundled) set the service URL:
# EMBEDDING_BASE_URL=http://ollama:11434
# For EMBEDDING_KIND=openai_compatible (external override) also set:
# EMBEDDING_API_KEY=REPLACE_WITH_EMBEDDING_API_KEY  # SECRET
MEMORY_AUTOFORM_ENABLED=false
MEMORY_AUTOFORM_EVERY_TURNS=0

# Agent observability (ADR-033). OpenTelemetry gen_ai spans; off by default.
OTEL_ENABLED=false
OTEL_CAPTURE_MESSAGE_CONTENT=false
OTEL_TRACES_SAMPLER=always_on
# For OTEL_ENABLED=true with a backend (e.g. self-hosted Phoenix), set:
# OTEL_EXPORTER_OTLP_ENDPOINT=http://phoenix:4317

# Gmail OAuth. The operating mode remains open in review §5.
GMAIL_OAUTH_MODE=per_deployment
GMAIL_CLIENT_ID=REPLACE_WITH_GMAIL_CLIENT_ID
GMAIL_CLIENT_SECRET=REPLACE_WITH_GMAIL_CLIENT_SECRET  # SECRET
GMAIL_REDIRECT=http://localhost:8000/connectors/gmail/oauth/callback

# Safe v1 retention: metadata + snippet only, no full body/attachments, rolling 90 days.
GMAIL_DATA_MODE=metadata_snippet
GMAIL_RETENTION_DAYS=90

# Defaults seed the single owner's preferences; notifications remain opt-in.
NOTIFICATIONS_ENABLED=false
NOTIFICATION_TIMEZONE=UTC
NOTIFICATION_DIGEST_TIME=08:00
NOTIFICATION_QUIET_START=22:00
NOTIFICATION_QUIET_END=08:00
NOTIFICATION_DAILY_CAP=6
NOTIFICATION_EVENTUAL_DELIVERY_KINDS=["overdue"]
```

## 3. Secret handling and credential envelope

### 3.1 Boundary and timing

- The OAuth callback validates state/PKCE, exchanges the code, serializes the token response in memory, and **encrypts it before any database write, event, outbox message, job argument, trace, or log**.
- The callback transaction stores only the encrypted credential envelope and non-secret connector state. Plaintext buffers are released immediately after sealing.
- Refresh tokens, access tokens, authorization codes, cookies, `APP_SECRET`, KEKs, provider keys, and Gmail client secrets MUST never enter Redis.
- Only the connector credential-vault module exposes decrypt. No generic API route, agent tool, debug endpoint, or shared utility may expose plaintext. The web process may call the vault's seal operation for the callback; connector execution in the worker may call unseal.
- v1 enforces this as a package/capability boundary and with tests. A future split service/KMS deployment MUST additionally enforce it with service identity/IAM.
- Credentials MUST **never be placed in a sandbox environment, command, workspace, prompt, model request, or tool result**. Sandbox is outside v1, but this remains binding if sandbox support returns. Connectors return only normalized, bounded data.

### 3.2 AES-256-GCM scheme

Each credential record gets an independent random 256-bit data-encryption key (DEK). The credential JSON is encrypted with AES-256-GCM using that DEK, a fresh 96-bit nonce, and canonical AAD. The DEK is wrapped by the active environment-backed KEK; this lets rotation rewrap DEKs without exposing OAuth plaintext.

The logical envelope stored through the connector credential fields owned by [data-model.md](data-model.md) MUST contain at least **`kek_id`, `key_version`, `nonce`, `ciphertext`, and `aad`**, plus `algorithm`, `aad_version`, and `encrypted_dek`. This is a storage mapping, not a new DDL definition:

| Envelope value | Required representation |
|---|---|
| `algorithm` | Constant `AES-256-GCM`. |
| `kek_id`, `key_version` | Identify the KEK needed to unwrap the DEK. |
| `nonce` | Fresh 12-byte payload nonce; never reused with the same DEK. |
| `ciphertext` | AES-GCM payload ciphertext with the 16-byte authentication tag appended. |
| `aad` | Stored canonical UTF-8 bytes, base64-encoded if the envelope is JSON. |
| `aad_version` | `1` for the canonical structure below. |
| `encrypted_dek` | Opaque versioned blob containing a fresh wrap nonce plus the KEK-wrapped DEK and tag. |

AAD v1 is canonical JSON (`sort_keys=True`, compact separators) containing only immutable values:

```json
{
  "aad_version": 1,
  "credential_id": "<uuid>",
  "connector_id": "<uuid>",
  "credential_kind": "gmail_oauth",
  "tenant_id": "<uuid>"
}
```

The decrypt path MUST recompute this AAD from the row identity and compare it with the stored AAD before decryption. Mutable values such as connector status, cursor, scopes, or token expiry MUST NOT be in AAD.

### 3.3 Helper pseudo-code

```python
def seal_oauth_credential(token_json, identity, active_kek):
    aad = canonical_json_bytes({
        "aad_version": 1,
        "credential_id": identity.credential_id,
        "connector_id": identity.connector_id,
        "credential_kind": "gmail_oauth",
        "tenant_id": identity.tenant_id,
    })

    dek = random_bytes(32)
    nonce = random_bytes(12)
    ciphertext = AESGCM(dek).encrypt(nonce, canonical_json_bytes(token_json), aad)

    wrap_aad = canonical_json_bytes({
        "purpose": "sherpa-dek-wrap-v1",
        "credential_id": identity.credential_id,
        "kek_id": active_kek.id,
        "key_version": active_kek.version,
    })
    wrap_nonce = random_bytes(12)
    wrapped = AESGCM(active_kek.bytes).encrypt(wrap_nonce, dek, wrap_aad)

    return CredentialEnvelope(
        algorithm="AES-256-GCM",
        aad_version=1,
        aad=aad,
        nonce=nonce,
        ciphertext=ciphertext,
        encrypted_dek=encode_v1(wrap_nonce, wrapped),
        kek_id=active_kek.id,
        key_version=active_kek.version,
    )


def open_oauth_credential(envelope, identity, connector_capability, keyring):
    require_connector_capability(connector_capability)
    expected_aad = credential_aad_v1(identity)
    constant_time_require_equal(envelope.aad, expected_aad)

    kek = keyring.require(envelope.kek_id, envelope.key_version)
    wrap_nonce, wrapped = decode_v1(envelope.encrypted_dek)
    wrap_aad = dek_wrap_aad(identity.credential_id, envelope.kek_id,
                            envelope.key_version)
    dek = AESGCM(kek).decrypt(wrap_nonce, wrapped, wrap_aad)
    plaintext = AESGCM(dek).decrypt(
        envelope.nonce, envelope.ciphertext, envelope.aad
    )
    return parse_and_validate_oauth_json(plaintext)
```

AES-GCM authentication failure is a terminal credential-integrity error: mark the connector unhealthy, emit only a redacted semantic failure, and require operator reconciliation. Never return partial plaintext and never retry with a different identity/AAD.

### 3.4 KEK rotation

1. Generate a new random 32-byte KEK; assign a new `KEK_ID` or increment `KEK_KEY_VERSION`. Never replace bytes under the same `(id, version)`.
2. Deploy the new key as active `KEK`; temporarily place every still-referenced old key in `KEK_PREVIOUS_KEYS`. New credentials immediately use the new key.
3. A connector-vault rotation job locks credentials in bounded batches, unwraps each DEK with its old KEK, wraps the same DEK with the new KEK, and updates only the wrapped-DEK/key metadata and rotation timestamp. It MUST NOT decrypt OAuth payload plaintext.
4. Verify every live credential references the active key and run connector decrypt canaries. Rotation progress logs contain IDs/counts only.
5. Keep old KEKs until all live rows are migrated **and every backup that can contain an old wrapped DEK has expired or passed a restore-and-rewrap drill**.
6. Remove old keys from runtime configuration. Key deletion is a separately approved operator action.

The v1 key provider reads environment-backed KEKs. A later KMS provider replaces raw key loading, not the envelope fields or rotation semantics.

### 3.5 Redaction and canary test

Structured logging MUST redact values whose field/header names match, case-insensitively: `authorization`, `cookie`, `set-cookie`, `token`, `secret`, `password`, `credential`, `kek`, `api_key`, `ciphertext`, and `encrypted_dek`. OAuth/provider HTTP request and response bodies are never logged. Exception formatting must use secret-safe messages rather than interpolating requests or settings.

CI MUST include a canary-secret regression test:

1. Use a unique value such as `SHERPA_CANARY_SECRET_<uuid>` as a fake refresh token.
2. Exercise callback sealing, a refresh success and failure, job retry, connector/API errors, and event/outbox/audit emission.
3. Capture logs and persisted diagnostic payloads.
4. Assert that the raw canary and its URL-encoded and base64 forms do not occur; scan database text/JSON columns and Redis payloads for plaintext.
5. Assert ciphertext decrypts only through the connector-vault capability and that API serialization contains connector status only.

Any match fails CI. Redaction applies at every log level, including `DEBUG`.

## 4. Open review §5 parameters and unblocking defaults

This freeze locks the parameter names and safe fallbacks; it does not pretend the owner has made the remaining product/operations choices.

| Still-open decision | Awaiting keys | Safe v1 default until decided |
|---|---|---|
| Initial real model/provider and BYOK choice | `PROVIDER_KIND`, `PROVIDER_BASE_URL`, `PROVIDER_API_KEY`, `PROVIDER_MODEL` | `mock` / `mock-v1`; deterministic, no external model call. The first real adapter is OpenAI-compatible, with no failover. |
| Gmail OAuth operating model | `GMAIL_OAUTH_MODE`, client ID/secret, redirect | `per_deployment`; each self-hosted installation supplies its own OAuth client. |
| Gmail data retained and retention window | `GMAIL_DATA_MODE`, `GMAIL_RETENTION_DAYS` | `metadata_snippet`, rolling 90 days, no full body and no attachments. After expiry, retain only minimum tombstone/provenance identifiers required by [data-model.md](data-model.md). |
| Notification defaults and eventual-delivery class | all `NOTIFICATION_*` keys | Opt-in off; digest `08:00`; quiet `22:00–08:00`; cap `6`; timezone `UTC`; only `overdue` reminders prefer eventual delivery. Digest prefers no duplicate. |

Explicit environment values may replace these defaults after the owner decides. Notification preference changes made through the API are owner data, not environment mutation; API shape belongs to [api.md](api.md), and firing/delivery semantics belong to [events-and-effects.md](events-and-effects.md).

Retention age is measured from Gmail `internalDate` in UTC, falling back to first fetch time when absent. A daily retention job removes stored snippet/body content past the cutoff; it keeps only the minimum non-content identifiers, deletion marker, and provenance required by [data-model.md](data-model.md). v1 never fetches attachment bytes. Quiet-hour and daily-cap deferrals remain durable: an `overdue` reminder waits for the next permitted window rather than being silently dropped.

## 5. Docker Compose and operations

Compose MUST map explicit keys per service; it MUST NOT pass the complete `.env` wholesale to every container.

| Service | Receives |
|---|---|
| `web` | `SERVICE_ROLE=web`; app/session/log and tool-output keys; `DATABASE_URL`; `REDIS_URL`; active `KEK`/ID/version for callback sealing; Gmail OAuth keys including redirect/mode; Gmail retention and notification defaults. It receives no previous KEKs and no `PROVIDER_API_KEY`. |
| `worker` | `SERVICE_ROLE=worker`; `APP_ENV`, `LOG_LEVEL`, and tool-output keys; `DATABASE_URL`; `REDIS_URL`; active/previous KEK keys; Gmail client ID/secret and data policy; all provider and embedding keys; memory-formation settings; observability/OTel settings; notification defaults; the `SANDBOX_*`/`WORKING_COPY_*` runtime keys and the Docker socket for sandbox orchestration. It does **not** receive `APP_SECRET`. |
| one-shot `migration` | `SERVICE_ROLE=migration`; `APP_ENV`, `LOG_LEVEL`, and `DATABASE_URL` only. |
| `frontend` | No key from this contract and no backend secret. Public API origin, if needed at build time, is a separate public frontend setting. |
| `postgres` | No Sherpa application secret and no KEK. Image bootstrap credentials come from deployment-managed Docker secrets and must match `DATABASE_URL`; they are not application `Settings`. |
| `redis` | No `APP_SECRET`, KEK, provider key, or Gmail secret. Redis authentication is deployment-managed and represented to clients only through secret `REDIS_URL`. |
| backup job | Database backup credential only. It does not need KEK access because credential rows are already ciphertext. |

MinIO and pgvector are not v1 services. Redis is not a backup/recovery source of truth.

The `web` and `worker` services mount one private tool-output named volume at `TOOL_OUTPUT_ROOT`. The former read-only `WORKSPACE_ROOT` worker mount is **removed** (ADR-046, §1.4). No sandbox scratch mount exists at all: under ADR-047 the working copy is tar-injected into the container's anonymous `/work` volume, so **no host path is ever passed to the Docker daemon** — this is what makes the transport behave identically on a Windows Docker-Desktop host, a Linux host, in DinD, and in CI. Only `worker` holds the Docker socket, and only `worker` executes sandbox work.

### 5.1 Migration ownership

- Exactly one deployment step owns migrations: a one-shot container running `uv run alembic upgrade head`.
- Web and worker MUST NOT auto-migrate at startup. They start only after the migration step succeeds.
- The migration runner takes a PostgreSQL advisory lock so two deploys cannot migrate concurrently.
- Take a database backup before destructive schema changes or credential-envelope/key migrations.

### 5.2 Backups

- Back up PostgreSQL on a documented schedule and perform restore drills. Redis may be rebuilt from PostgreSQL/outbox state.
- Store backups encrypted at rest and separately from KEKs. Never bundle `.env`, Docker secrets, or historical KEKs with a database dump.
- A restored database may reference an older `(kek_id, key_version)`; therefore historical KEKs remain available to the controlled restore/rotation procedure until the corresponding backup expires.
- Backup and restore logs obey the same secret redaction rules. Restores must verify connector credential authentication before declaring success.
