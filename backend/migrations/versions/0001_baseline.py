"""baseline: the whole post-ADR-046/047/048 schema in one revision

Revision ID: 0001
Revises:
Create Date: 2026-07-30

**Baseline squash (ADR-045, approved by the owner 2026-07-30).** Revisions
``0001_initial_core`` … ``0032_chat_attachments`` are deleted and replaced by this single
revision. This is not a data migration — it is its opposite: the 32-revision history existed
only to carry an old schema forward, and a clean break has nothing to carry. All existing
Sherpa data was disposable test data; every environment rebuilds with
``docker compose ... down -v`` then ``up --build`` (data-model.md §Alembic migration plan).

Differences from what the deleted 32 revisions produced — these three, and nothing else:
  * ``files`` is **gone** (the legacy personal-file stack; ADR-046 §7, Phase TR P1.1).
  * ``project_sandbox_runs`` is **gone**, replaced by ``project_runtime_sessions``
    (session level) + ``project_exec_runs`` (per command). ``scratch_ref`` is meaningless
    under ADR-047 tar transport, ``container_ref`` belongs to the session, and ``warm_until``
    was never implemented in any code path (ADR-047 §7 / ADR-048 §6).
  * the new tables carry the contract's CHECKs and the ``uq_prs_live`` partial unique index
    (at most one live runtime session per working copy — the single-writer lease mirror).

Everything else is byte-for-byte the schema the 32 revisions produced, taken from the running
dev database at ``0032`` and re-emitted here, so an empty database reaches the identical state
in one step. Deliberately NOT added: the immutability triggers and PL/pgSQL function sketched
in data-model.md §Alembic item 1 — they were never implemented, and a baseline squash is not
the place to introduce new enforcement (that needs its own ADR + tests).

The zhparser/``sherpa_text`` text-search configuration keeps revision 0027's graceful DO
block so ``alembic upgrade head`` still succeeds on a Postgres image without zhparser.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- extensions -------------------------------------------------------------
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")
    # Best-effort CJK: zhparser + the stable `sherpa_text` config. Degrade gracefully
    # where zhparser is unavailable so `alembic upgrade head` stays portable.
    op.execute(
        """
        DO $$
        BEGIN
            CREATE EXTENSION IF NOT EXISTS zhparser;
            IF NOT EXISTS (SELECT 1 FROM pg_ts_config WHERE cfgname = 'sherpa_text') THEN
                CREATE TEXT SEARCH CONFIGURATION sherpa_text (PARSER = zhparser);
                ALTER TEXT SEARCH CONFIGURATION sherpa_text
                    ADD MAPPING FOR a,b,c,d,e,f,g,h,i,j,k,l,m,n,o,p,q,r,s,t,u,v,w,x,y,z
                    WITH simple;
            END IF;
        EXCEPTION WHEN OTHERS THEN
            RAISE NOTICE 'zhparser/sherpa_text unavailable (%); knowledge lexical branch dormant until the combined image is used', SQLERRM;
        END $$;
        """
    )

    # --- tables -----------------------------------------------------------------
    op.execute(r"""
        CREATE TABLE approval_envelopes (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            envelope_version smallint NOT NULL,
            correlation_id uuid NOT NULL,
            run_id uuid NOT NULL,
            session_id uuid NOT NULL,
            invocation_id uuid NOT NULL,
            tool_name text NOT NULL,
            permission_scope text NOT NULL,
            effect_class text NOT NULL,
            args_hash bytea NOT NULL,
            policy_version text NOT NULL,
            expires_at timestamp with time zone NOT NULL,
            nonce_hash bytea NOT NULL,
            preview_redacted jsonb NOT NULL,
            authorized_decider_user_id uuid NOT NULL,
            status text DEFAULT 'pending'::text NOT NULL,
            decision text,
            decided_by_user_id uuid,
            decided_via_channel text,
            decided_at timestamp with time zone,
            version integer DEFAULT 1 NOT NULL,
            requested_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT ck_approval_envelopes_action CHECK ((((char_length(tool_name) >= 1) AND (char_length(tool_name) <= 200)) AND ((char_length(permission_scope) >= 1) AND (char_length(permission_scope) <= 512)) AND (effect_class = ANY (ARRAY['read_only'::text, 'idempotent_write'::text, 'reconcilable_write'::text, 'non_idempotent_write'::text])))),
            CONSTRAINT ck_approval_envelopes_decision CHECK (((decision IS NULL) OR (decision = ANY (ARRAY['allow_once'::text, 'allow_session'::text, 'always'::text, 'reject'::text])))),
            CONSTRAINT ck_approval_envelopes_expiry CHECK ((expires_at > requested_at)),
            CONSTRAINT ck_approval_envelopes_hashes CHECK (((octet_length(args_hash) = 32) AND (octet_length(nonce_hash) = 32))),
            CONSTRAINT ck_approval_envelopes_preview CHECK ((octet_length((preview_redacted)::text) <= 32768)),
            CONSTRAINT ck_approval_envelopes_state CHECK ((((status = 'pending'::text) AND (decision IS NULL) AND (decided_by_user_id IS NULL) AND (decided_via_channel IS NULL) AND (decided_at IS NULL)) OR ((status = 'decided'::text) AND (decision IS NOT NULL) AND (decided_by_user_id IS NOT NULL) AND (decided_via_channel IS NOT NULL) AND (decided_at IS NOT NULL)) OR ((status = ANY (ARRAY['expired'::text, 'superseded'::text])) AND (decision IS NULL) AND (decided_by_user_id IS NULL) AND (decided_via_channel IS NULL) AND (decided_at IS NULL)))),
            CONSTRAINT ck_approval_envelopes_status CHECK ((status = ANY (ARRAY['pending'::text, 'decided'::text, 'expired'::text, 'superseded'::text]))),
            CONSTRAINT ck_approval_envelopes_version CHECK (((envelope_version > 0) AND (version > 0)))
        );
    """)
    op.execute(r"""
        CREATE TABLE audit_receipts (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            receipt_version smallint NOT NULL,
            receipt_type text NOT NULL,
            actor_type text NOT NULL,
            actor_user_id uuid,
            trigger_type text NOT NULL,
            run_id uuid,
            invocation_id uuid,
            approval_envelope_id uuid,
            subject_type text,
            subject_id uuid,
            action text NOT NULL,
            outcome text NOT NULL,
            reversible boolean DEFAULT false NOT NULL,
            summary_redacted jsonb NOT NULL,
            source_event_id uuid,
            occurred_at timestamp with time zone NOT NULL,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT ck_audit_receipts_actor CHECK ((((actor_type = 'user'::text) AND (actor_user_id IS NOT NULL)) OR ((actor_type <> 'user'::text) AND (actor_user_id IS NULL)))),
            CONSTRAINT ck_audit_receipts_actor_type CHECK ((actor_type = ANY (ARRAY['user'::text, 'system'::text, 'connector'::text, 'scheduler'::text]))),
            CONSTRAINT ck_audit_receipts_subject CHECK ((((subject_type IS NULL) AND (subject_id IS NULL)) OR ((subject_type IS NOT NULL) AND (subject_id IS NOT NULL)))),
            CONSTRAINT ck_audit_receipts_summary CHECK ((octet_length((summary_redacted)::text) <= 32768)),
            CONSTRAINT ck_audit_receipts_text_bounds CHECK ((((char_length(trigger_type) >= 1) AND (char_length(trigger_type) <= 100)) AND ((char_length(action) >= 1) AND (char_length(action) <= 200)) AND ((char_length(outcome) >= 1) AND (char_length(outcome) <= 100)) AND ((subject_type IS NULL) OR ((char_length(subject_type) >= 1) AND (char_length(subject_type) <= 100))))),
            CONSTRAINT ck_audit_receipts_type CHECK (((char_length(receipt_type) >= 1) AND (char_length(receipt_type) <= 200))),
            CONSTRAINT ck_audit_receipts_version CHECK ((receipt_version > 0))
        );
    """)
    op.execute(r"""
        CREATE TABLE candidates (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            extraction_id uuid NOT NULL,
            generation_id uuid NOT NULL,
            ordinal integer NOT NULL,
            dedupe_key text NOT NULL,
            status text DEFAULT 'pending'::text NOT NULL,
            title character varying(500) NOT NULL,
            description text,
            due_at timestamp with time zone,
            priority text DEFAULT 'medium'::text NOT NULL,
            confidence numeric(5,4) NOT NULL,
            rationale_redacted text,
            source_excerpt_redacted text,
            accepted_todo_id uuid,
            decided_by_user_id uuid,
            decided_at timestamp with time zone,
            version integer DEFAULT 1 NOT NULL,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            updated_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT ck_candidates_confidence CHECK (((confidence >= (0)::numeric) AND (confidence <= (1)::numeric))),
            CONSTRAINT ck_candidates_decision CHECK ((((status = 'pending'::text) AND (accepted_todo_id IS NULL) AND (decided_by_user_id IS NULL) AND (decided_at IS NULL)) OR ((status = ANY (ARRAY['accepted'::text, 'edited'::text])) AND (accepted_todo_id IS NOT NULL) AND (decided_by_user_id IS NOT NULL) AND (decided_at IS NOT NULL)) OR ((status = 'dismissed'::text) AND (accepted_todo_id IS NULL) AND (decided_by_user_id IS NOT NULL) AND (decided_at IS NOT NULL)))),
            CONSTRAINT ck_candidates_dedupe_key CHECK (((char_length(dedupe_key) >= 1) AND (char_length(dedupe_key) <= 512))),
            CONSTRAINT ck_candidates_description_bound CHECK (((description IS NULL) OR (octet_length(description) <= 65536))),
            CONSTRAINT ck_candidates_excerpt_bound CHECK (((source_excerpt_redacted IS NULL) OR (octet_length(source_excerpt_redacted) <= 16384))),
            CONSTRAINT ck_candidates_ordinal CHECK ((ordinal >= 0)),
            CONSTRAINT ck_candidates_priority CHECK ((priority = ANY (ARRAY['low'::text, 'medium'::text, 'high'::text]))),
            CONSTRAINT ck_candidates_rationale_bound CHECK (((rationale_redacted IS NULL) OR (octet_length(rationale_redacted) <= 16384))),
            CONSTRAINT ck_candidates_status CHECK ((status = ANY (ARRAY['pending'::text, 'accepted'::text, 'edited'::text, 'dismissed'::text]))),
            CONSTRAINT ck_candidates_version CHECK ((version > 0))
        );
    """)
    op.execute(r"""
        CREATE TABLE channel_configs (
            tenant_id uuid NOT NULL,
            user_id uuid NOT NULL,
            channel text NOT NULL,
            kind text NOT NULL,
            enabled boolean DEFAULT false NOT NULL,
            app_id text DEFAULT ''::text NOT NULL,
            owner_external_id text DEFAULT ''::text NOT NULL,
            secret_enc bytea DEFAULT '\x'::bytea NOT NULL,
            secret_nonce bytea DEFAULT '\x'::bytea NOT NULL,
            kek_id text DEFAULT ''::text NOT NULL,
            key_version integer DEFAULT 0 NOT NULL,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            updated_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT ck_channel_configs_channel CHECK (((char_length(channel) >= 1) AND (char_length(channel) <= 32)))
        );
    """)
    op.execute(r"""
        CREATE TABLE channel_thread_state (
            tenant_id uuid NOT NULL,
            session_id uuid NOT NULL,
            last_inbound_msg_id text DEFAULT ''::text NOT NULL,
            updated_at timestamp with time zone DEFAULT now() NOT NULL
        );
    """)
    op.execute(r"""
        CREATE TABLE connector_items (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            connector_id uuid NOT NULL,
            provider_item_id text NOT NULL,
            revision text NOT NULL,
            provider_thread_id text,
            received_at timestamp with time zone NOT NULL,
            fetched_at timestamp with time zone DEFAULT now() NOT NULL,
            content_digest bytea NOT NULL,
            content_json jsonb,
            is_latest boolean DEFAULT true NOT NULL,
            deletion_state text DEFAULT 'present'::text NOT NULL,
            source_deleted_at timestamp with time zone,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT ck_connector_items_content_bound CHECK (((content_json IS NULL) OR (octet_length((content_json)::text) <= 262144))),
            CONSTRAINT ck_connector_items_deletion CHECK ((((deletion_state = 'present'::text) AND (source_deleted_at IS NULL)) OR ((deletion_state = 'source_deleted'::text) AND (source_deleted_at IS NOT NULL)) OR ((deletion_state = 'purged'::text) AND (source_deleted_at IS NOT NULL) AND (content_json IS NULL)))),
            CONSTRAINT ck_connector_items_digest CHECK ((octet_length(content_digest) = 32)),
            CONSTRAINT ck_connector_items_ids CHECK ((((char_length(provider_item_id) >= 1) AND (char_length(provider_item_id) <= 512)) AND ((char_length(revision) >= 1) AND (char_length(revision) <= 255))))
        );
    """)
    op.execute(r"""
        CREATE TABLE connectors (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            user_id uuid NOT NULL,
            kind text NOT NULL,
            channel_installation_id text NOT NULL,
            external_account_id text NOT NULL,
            token_enc bytea,
            nonce bytea,
            kek_id text,
            key_version integer,
            token_algorithm text,
            aad_version smallint,
            scopes text[] DEFAULT ARRAY[]::text[] NOT NULL,
            status text DEFAULT 'pending_oauth'::text NOT NULL,
            cursor jsonb DEFAULT '{}'::jsonb NOT NULL,
            refresh_version bigint DEFAULT 0 NOT NULL,
            last_sync_at timestamp with time zone,
            last_error_redacted text,
            rotated_at timestamp with time zone,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            updated_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT ck_connectors_active_has_token CHECK (((status <> ALL (ARRAY['active'::text, 'syncing'::text, 'degraded'::text, 'paused'::text, 'disconnecting'::text, 'error'::text])) OR (token_enc IS NOT NULL))),
            CONSTRAINT ck_connectors_aead_all_or_none CHECK ((((token_enc IS NULL) AND (nonce IS NULL) AND (kek_id IS NULL) AND (key_version IS NULL) AND (token_algorithm IS NULL) AND (aad_version IS NULL)) OR ((token_enc IS NOT NULL) AND (nonce IS NOT NULL) AND (kek_id IS NOT NULL) AND (key_version IS NOT NULL) AND (token_algorithm IS NOT NULL) AND (aad_version IS NOT NULL)))),
            CONSTRAINT ck_connectors_aead_values CHECK (((token_enc IS NULL) OR ((octet_length(token_enc) >= 16) AND (octet_length(nonce) = 12) AND (key_version > 0) AND (token_algorithm = 'AES-256-GCM'::text) AND (aad_version > 0)))),
            CONSTRAINT ck_connectors_cursor_bound CHECK ((octet_length((cursor)::text) <= 65536)),
            CONSTRAINT ck_connectors_error_bound CHECK (((last_error_redacted IS NULL) OR (octet_length(last_error_redacted) <= 16384))),
            CONSTRAINT ck_connectors_kind CHECK ((kind = 'gmail'::text)),
            CONSTRAINT ck_connectors_refresh_version CHECK ((refresh_version >= 0)),
            CONSTRAINT ck_connectors_revoked_has_no_token CHECK (((status <> 'revoked'::text) OR ((token_enc IS NULL) AND (nonce IS NULL) AND (kek_id IS NULL) AND (key_version IS NULL) AND (token_algorithm IS NULL) AND (aad_version IS NULL)))),
            CONSTRAINT ck_connectors_scopes CHECK (((cardinality(scopes) <= 16) AND ((status <> 'active'::text) OR (cardinality(scopes) > 0)))),
            CONSTRAINT ck_connectors_status CHECK ((status = ANY (ARRAY['pending_oauth'::text, 'active'::text, 'syncing'::text, 'degraded'::text, 'paused'::text, 'disconnecting'::text, 'revoked'::text, 'error'::text])))
        );
    """)
    op.execute(r"""
        CREATE TABLE drive_nodes (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            user_id uuid NOT NULL,
            parent_id uuid,
            node_type text NOT NULL,
            name text NOT NULL,
            content_hash bytea,
            size_bytes bigint DEFAULT 0 NOT NULL,
            content_type text DEFAULT 'application/octet-stream'::text NOT NULL,
            version integer DEFAULT 1 NOT NULL,
            trashed_at timestamp with time zone,
            purge_after timestamp with time zone,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            updated_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT ck_dn_name CHECK ((((char_length(name) >= 1) AND (char_length(name) <= 255)) AND (name !~~ '%/%'::text))),
            CONSTRAINT ck_dn_type CHECK ((node_type = ANY (ARRAY['folder'::text, 'file'::text])))
        );
    """)
    op.execute(r"""
        CREATE TABLE drive_versions (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            node_id uuid NOT NULL,
            user_id uuid NOT NULL,
            version integer NOT NULL,
            content_hash bytea NOT NULL,
            size_bytes bigint NOT NULL,
            content_type text NOT NULL,
            created_at timestamp with time zone DEFAULT now() NOT NULL
        );
    """)
    op.execute(r"""
        CREATE TABLE effect_invocations (
            tenant_id uuid NOT NULL,
            invocation_id uuid NOT NULL,
            run_id uuid NOT NULL,
            turn_seq bigint,
            effect_name text NOT NULL,
            idempotency_key text NOT NULL,
            effect_class text NOT NULL,
            retry_policy text NOT NULL,
            args_hash bytea NOT NULL,
            status text DEFAULT 'prepared'::text NOT NULL,
            outcome text,
            attempts integer DEFAULT 0 NOT NULL,
            reconciliation_state text DEFAULT 'not_required'::text NOT NULL,
            result_redacted jsonb,
            external_reference_redacted text,
            last_error_redacted text,
            started_at timestamp with time zone,
            settled_at timestamp with time zone,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            updated_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT ck_effect_invocations_attempts CHECK ((attempts >= 0)),
            CONSTRAINT ck_effect_invocations_class CHECK ((effect_class = ANY (ARRAY['read_only'::text, 'idempotent_write'::text, 'reconcilable_write'::text, 'non_idempotent_write'::text]))),
            CONSTRAINT ck_effect_invocations_error_bound CHECK (((last_error_redacted IS NULL) OR (octet_length(last_error_redacted) <= 16384))),
            CONSTRAINT ck_effect_invocations_external_ref_bound CHECK (((external_reference_redacted IS NULL) OR (octet_length(external_reference_redacted) <= 2048))),
            CONSTRAINT ck_effect_invocations_hash CHECK ((octet_length(args_hash) = 32)),
            CONSTRAINT ck_effect_invocations_key CHECK (((char_length(idempotency_key) >= 1) AND (char_length(idempotency_key) <= 512))),
            CONSTRAINT ck_effect_invocations_name CHECK (((char_length(effect_name) >= 1) AND (char_length(effect_name) <= 200))),
            CONSTRAINT ck_effect_invocations_outcome CHECK (((outcome IS NULL) OR (outcome = ANY (ARRAY['succeeded'::text, 'failed'::text, 'effect_unknown'::text])))),
            CONSTRAINT ck_effect_invocations_reconciliation CHECK ((reconciliation_state = ANY (ARRAY['not_required'::text, 'pending'::text, 'manual_required'::text, 'resolved_succeeded'::text, 'resolved_failed'::text]))),
            CONSTRAINT ck_effect_invocations_result_bound CHECK (((result_redacted IS NULL) OR (octet_length((result_redacted)::text) <= 65536))),
            CONSTRAINT ck_effect_invocations_retry_policy CHECK ((retry_policy = ANY (ARRAY['transient_before_dispatch'::text, 'same_key'::text, 'after_reconcile'::text, 'never'::text]))),
            CONSTRAINT ck_effect_invocations_state CHECK ((((status = ANY (ARRAY['prepared'::text, 'running'::text])) AND (outcome IS NULL) AND (settled_at IS NULL)) OR ((status = 'settled'::text) AND (outcome = ANY (ARRAY['succeeded'::text, 'failed'::text])) AND (settled_at IS NOT NULL)) OR ((status = 'needs_reconciliation'::text) AND (outcome = 'effect_unknown'::text) AND (reconciliation_state = ANY (ARRAY['pending'::text, 'manual_required'::text])) AND (settled_at IS NOT NULL)))),
            CONSTRAINT ck_effect_invocations_status CHECK ((status = ANY (ARRAY['prepared'::text, 'running'::text, 'settled'::text, 'needs_reconciliation'::text]))),
            CONSTRAINT ck_effect_invocations_turn CHECK (((turn_seq IS NULL) OR (turn_seq > 0)))
        );
    """)
    op.execute(r"""
        CREATE TABLE embedding_profiles (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            name text NOT NULL,
            provider text NOT NULL,
            model text NOT NULL,
            dim integer NOT NULL,
            "normalize" text DEFAULT 'cosine'::text NOT NULL,
            privacy text DEFAULT 'local'::text NOT NULL,
            is_active boolean DEFAULT true NOT NULL,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT ck_ep_dim CHECK (((dim >= 1) AND (dim <= 4096))),
            CONSTRAINT ck_ep_privacy CHECK ((privacy = ANY (ARRAY['local'::text, 'external'::text])))
        );
    """)
    op.execute(r"""
        CREATE TABLE event_journal (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            session_id uuid,
            session_seq bigint,
            run_id uuid NOT NULL,
            run_seq bigint NOT NULL,
            event_type text NOT NULL,
            envelope_version smallint NOT NULL,
            durability text DEFAULT 'durable'::text NOT NULL,
            correlation_id uuid,
            causation_event_id uuid,
            payload_redacted jsonb NOT NULL,
            payload_size_bytes integer NOT NULL,
            occurred_at timestamp with time zone NOT NULL,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT ck_event_journal_durability CHECK ((durability = ANY (ARRAY['durable'::text, 'presentation'::text, 'debug'::text]))),
            CONSTRAINT ck_event_journal_envelope CHECK ((envelope_version > 0)),
            CONSTRAINT ck_event_journal_payload CHECK (((payload_size_bytes = octet_length((payload_redacted)::text)) AND ((payload_size_bytes >= 2) AND (payload_size_bytes <= 65536)))),
            CONSTRAINT ck_event_journal_sequences CHECK (((run_seq > 0) AND (((session_id IS NULL) AND (session_seq IS NULL)) OR ((session_id IS NOT NULL) AND (session_seq IS NOT NULL) AND (session_seq > 0))))),
            CONSTRAINT ck_event_journal_type CHECK (((char_length(event_type) >= 1) AND (char_length(event_type) <= 200)))
        );
    """)
    op.execute(r"""
        CREATE TABLE extractions (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            connector_item_id uuid NOT NULL,
            run_id uuid NOT NULL,
            extraction_version integer NOT NULL,
            extractor_version text NOT NULL,
            output_schema_version smallint NOT NULL,
            status text DEFAULT 'pending'::text NOT NULL,
            attempts integer DEFAULT 0 NOT NULL,
            error_redacted text,
            started_at timestamp with time zone,
            completed_at timestamp with time zone,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT ck_extractions_attempts CHECK ((attempts >= 0)),
            CONSTRAINT ck_extractions_completed CHECK ((((status = ANY (ARRAY['pending'::text, 'running'::text])) AND (completed_at IS NULL)) OR ((status = ANY (ARRAY['succeeded'::text, 'failed'::text])) AND (completed_at IS NOT NULL)))),
            CONSTRAINT ck_extractions_error_bound CHECK (((error_redacted IS NULL) OR (octet_length(error_redacted) <= 16384))),
            CONSTRAINT ck_extractions_status CHECK ((status = ANY (ARRAY['pending'::text, 'running'::text, 'succeeded'::text, 'failed'::text]))),
            CONSTRAINT ck_extractions_versions CHECK (((extraction_version > 0) AND (output_schema_version > 0)))
        );
    """)
    op.execute(r"""
        CREATE TABLE generations (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            trace_id uuid NOT NULL,
            run_id uuid NOT NULL,
            extraction_id uuid,
            purpose text NOT NULL,
            provider text NOT NULL,
            model text NOT NULL,
            prompt_version text NOT NULL,
            response_schema_version smallint,
            status text NOT NULL,
            attempt integer DEFAULT 1 NOT NULL,
            input_tokens bigint DEFAULT 0 NOT NULL,
            output_tokens bigint DEFAULT 0 NOT NULL,
            cached_input_tokens bigint DEFAULT 0 NOT NULL,
            cost_usd numeric(20,8) DEFAULT 0 NOT NULL,
            latency_ms integer,
            started_at timestamp with time zone NOT NULL,
            completed_at timestamp with time zone NOT NULL,
            CONSTRAINT ck_generations_extraction_purpose CHECK ((((purpose = 'candidate_extraction'::text) AND (extraction_id IS NOT NULL)) OR ((purpose = ANY (ARRAY['web_chat'::text, 'digest'::text])) AND (extraction_id IS NULL)))),
            CONSTRAINT ck_generations_purpose CHECK ((purpose = ANY (ARRAY['web_chat'::text, 'candidate_extraction'::text, 'digest'::text]))),
            CONSTRAINT ck_generations_schema_version CHECK (((response_schema_version IS NULL) OR (response_schema_version > 0))),
            CONSTRAINT ck_generations_status CHECK ((status = ANY (ARRAY['succeeded'::text, 'failed'::text]))),
            CONSTRAINT ck_generations_usage CHECK (((attempt > 0) AND (input_tokens >= 0) AND (output_tokens >= 0) AND (cached_input_tokens >= 0) AND (cached_input_tokens <= input_tokens) AND (cost_usd >= (0)::numeric) AND ((latency_ms IS NULL) OR (latency_ms >= 0)) AND (completed_at >= started_at)))
        );
    """)
    op.execute(r"""
        CREATE TABLE github_connections (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            user_id uuid NOT NULL,
            auth_kind text NOT NULL,
            account_login text,
            installation_id text,
            token_enc bytea,
            nonce bytea,
            kek_id text,
            key_version integer,
            token_algorithm text,
            aad_version smallint,
            scopes text[] DEFAULT ARRAY[]::text[] NOT NULL,
            status text DEFAULT 'pending'::text NOT NULL,
            last_error_redacted text,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            updated_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT ck_ghc_active_has_token CHECK (((status <> 'active'::text) OR (token_enc IS NOT NULL))),
            CONSTRAINT ck_ghc_aead_all_or_none CHECK ((((token_enc IS NULL) AND (nonce IS NULL) AND (kek_id IS NULL) AND (key_version IS NULL) AND (token_algorithm IS NULL) AND (aad_version IS NULL)) OR ((token_enc IS NOT NULL) AND (nonce IS NOT NULL) AND (kek_id IS NOT NULL) AND (key_version IS NOT NULL) AND (token_algorithm IS NOT NULL) AND (aad_version IS NOT NULL)))),
            CONSTRAINT ck_ghc_auth_kind CHECK ((auth_kind = ANY (ARRAY['pat'::text, 'app_installation'::text]))),
            CONSTRAINT ck_ghc_status CHECK ((status = ANY (ARRAY['pending'::text, 'active'::text, 'revoked'::text, 'error'::text])))
        );
    """)
    op.execute(r"""
        CREATE TABLE identities (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            user_id uuid NOT NULL,
            channel text NOT NULL,
            channel_installation_id text NOT NULL,
            scope_type text NOT NULL,
            external_scope_id text NOT NULL,
            external_actor_id text NOT NULL,
            verified_at timestamp with time zone NOT NULL,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT ck_identities_channel CHECK (((char_length(channel) >= 1) AND (char_length(channel) <= 32))),
            CONSTRAINT ck_identities_external_actor CHECK (((char_length(external_actor_id) >= 1) AND (char_length(external_actor_id) <= 512))),
            CONSTRAINT ck_identities_external_scope CHECK (((char_length(external_scope_id) >= 1) AND (char_length(external_scope_id) <= 512))),
            CONSTRAINT ck_identities_installation CHECK (((char_length(channel_installation_id) >= 1) AND (char_length(channel_installation_id) <= 255))),
            CONSTRAINT ck_identities_scope_type CHECK (((char_length(scope_type) >= 1) AND (char_length(scope_type) <= 32)))
        );
    """)
    op.execute(r"""
        CREATE TABLE knowledge_chunks (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            source_id uuid NOT NULL,
            version_id uuid NOT NULL,
            ordinal integer NOT NULL,
            text_content text NOT NULL,
            token_count integer NOT NULL,
            heading_path text,
            page integer,
            char_offset integer,
            content_hash bytea NOT NULL,
            lexical_text text NOT NULL,
            embedding vector(1024) NOT NULL,
            fts tsvector,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT ck_kc_text_bound CHECK ((octet_length(text_content) <= 65536))
        );
    """)
    op.execute(r"""
        CREATE TABLE knowledge_ingestion_jobs (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            source_id uuid NOT NULL,
            version_id uuid,
            generation integer NOT NULL,
            stage text DEFAULT 'queued'::text NOT NULL,
            lease_owner text,
            lease_expires_at timestamp with time zone,
            attempt integer DEFAULT 0 NOT NULL,
            termination_reason text,
            idempotency_key text NOT NULL,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            updated_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT ck_kij_stage CHECK ((stage = ANY (ARRAY['queued'::text, 'claiming'::text, 'snapshot'::text, 'parse'::text, 'chunk'::text, 'embed'::text, 'activate'::text, 'done'::text, 'failed'::text])))
        );
    """)
    op.execute(r"""
        CREATE TABLE knowledge_retrieval_evidence (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            user_id uuid NOT NULL,
            retrieval_invocation_id uuid NOT NULL,
            run_id uuid,
            tool_call_id text,
            citation_ref text NOT NULL,
            source_id uuid NOT NULL,
            source_version_id uuid NOT NULL,
            chunk_id uuid NOT NULL,
            excerpt text NOT NULL,
            score double precision,
            matched_by text NOT NULL,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            purge_after timestamp with time zone NOT NULL
        );
    """)
    op.execute(r"""
        CREATE TABLE knowledge_source_versions (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            source_id uuid NOT NULL,
            generation integer NOT NULL,
            expected_file_version integer,
            expected_file_hash bytea,
            snapshot_object_key text NOT NULL,
            parser_version text NOT NULL,
            pipeline_version text NOT NULL,
            embedding_profile_id uuid NOT NULL,
            language text,
            status text DEFAULT 'building'::text NOT NULL,
            chunk_count integer DEFAULT 0 NOT NULL,
            failure_code text,
            idempotency_key text NOT NULL,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            activated_at timestamp with time zone,
            CONSTRAINT ck_ksv_status CHECK ((status = ANY (ARRAY['building'::text, 'ready'::text, 'failed'::text, 'superseded'::text])))
        );
    """)
    op.execute(r"""
        CREATE TABLE knowledge_sources (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            user_id uuid NOT NULL,
            source_kind text DEFAULT 'file'::text NOT NULL,
            file_id uuid,
            display_name text NOT NULL,
            visibility text DEFAULT 'private'::text NOT NULL,
            trust_level text DEFAULT 'untrusted'::text NOT NULL,
            status text DEFAULT 'queued'::text NOT NULL,
            active_version_id uuid,
            desired_generation integer DEFAULT 1 NOT NULL,
            tombstoned_at timestamp with time zone,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            updated_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT ck_ks_kind CHECK ((source_kind = 'file'::text)),
            CONSTRAINT ck_ks_status CHECK ((status = ANY (ARRAY['queued'::text, 'parsing'::text, 'chunking'::text, 'embedding'::text, 'ready'::text, 'stale'::text, 'failed'::text, 'deleting'::text]))),
            CONSTRAINT ck_ks_visibility CHECK ((visibility = 'private'::text))
        );
    """)
    op.execute(r"""
        CREATE TABLE memory_passages (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            user_id uuid NOT NULL,
            text_content text NOT NULL,
            embedding vector(1024) NOT NULL,
            embedding_model text NOT NULL,
            content_hash bytea NOT NULL,
            source text DEFAULT 'agent'::text NOT NULL,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            fts tsvector GENERATED ALWAYS AS (to_tsvector('english'::regconfig, text_content)) STORED,
            CONSTRAINT ck_memory_passages_hash CHECK ((octet_length(content_hash) = 32)),
            CONSTRAINT ck_memory_passages_text_bound CHECK ((octet_length(text_content) <= 65536))
        );
    """)
    op.execute(r"""
        CREATE TABLE messages (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            session_id uuid NOT NULL,
            run_id uuid,
            author_user_id uuid,
            seq bigint NOT NULL,
            role text NOT NULL,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            client_message_id uuid,
            CONSTRAINT ck_messages_role CHECK ((role = ANY (ARRAY['user'::text, 'assistant'::text, 'system'::text]))),
            CONSTRAINT ck_messages_seq CHECK ((seq > 0))
        );
    """)
    op.execute(r"""
        CREATE TABLE model_providers (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            user_id uuid NOT NULL,
            kind text NOT NULL,
            display_name text NOT NULL,
            base_url text,
            token_enc bytea,
            nonce bytea,
            kek_id text,
            key_version integer,
            token_algorithm text,
            aad_version smallint,
            models text[] DEFAULT ARRAY[]::text[] NOT NULL,
            default_model text,
            enabled boolean DEFAULT true NOT NULL,
            is_default boolean DEFAULT false NOT NULL,
            status text DEFAULT 'pending'::text NOT NULL,
            last_error_redacted text,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            updated_at timestamp with time zone DEFAULT now() NOT NULL,
            supports_vision boolean DEFAULT true NOT NULL,
            CONSTRAINT ck_mp_aead_all_or_none CHECK ((((token_enc IS NULL) AND (nonce IS NULL) AND (kek_id IS NULL) AND (key_version IS NULL) AND (token_algorithm IS NULL) AND (aad_version IS NULL)) OR ((token_enc IS NOT NULL) AND (nonce IS NOT NULL) AND (kek_id IS NOT NULL) AND (key_version IS NOT NULL) AND (token_algorithm IS NOT NULL) AND (aad_version IS NOT NULL)))),
            CONSTRAINT ck_mp_enabled_has_key CHECK (((enabled = false) OR (token_enc IS NOT NULL))),
            CONSTRAINT ck_mp_kind CHECK ((kind = ANY (ARRAY['openai_compatible'::text, 'anthropic'::text, 'gemini'::text]))),
            CONSTRAINT ck_mp_name CHECK (((char_length(display_name) >= 1) AND (char_length(display_name) <= 200))),
            CONSTRAINT ck_mp_status CHECK ((status = ANY (ARRAY['pending'::text, 'active'::text, 'error'::text])))
        );
    """)
    op.execute(r"""
        CREATE TABLE outbox (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            event_id uuid NOT NULL,
            topic text NOT NULL,
            delivery_key text NOT NULL,
            status text DEFAULT 'pending'::text NOT NULL,
            attempts integer DEFAULT 0 NOT NULL,
            available_at timestamp with time zone DEFAULT now() NOT NULL,
            locked_by text,
            locked_at timestamp with time zone,
            delivered_at timestamp with time zone,
            last_error_redacted text,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT ck_outbox_attempts CHECK ((attempts >= 0)),
            CONSTRAINT ck_outbox_delivery CHECK ((((status = 'delivered'::text) AND (delivered_at IS NOT NULL)) OR ((status <> 'delivered'::text) AND (delivered_at IS NULL)))),
            CONSTRAINT ck_outbox_error_bound CHECK (((last_error_redacted IS NULL) OR (octet_length(last_error_redacted) <= 16384))),
            CONSTRAINT ck_outbox_status CHECK ((status = ANY (ARRAY['pending'::text, 'publishing'::text, 'delivered'::text, 'failed'::text])))
        );
    """)
    op.execute(r"""
        CREATE TABLE parts (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            message_id uuid NOT NULL,
            ordinal integer NOT NULL,
            kind text NOT NULL,
            content_redacted jsonb NOT NULL,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT ck_parts_content_bound CHECK ((octet_length((content_redacted)::text) <= 65536)),
            CONSTRAINT ck_parts_kind CHECK ((kind = ANY (ARRAY['text'::text, 'status'::text, 'image'::text, 'file_ref'::text]))),
            CONSTRAINT ck_parts_ordinal CHECK ((ordinal >= 0))
        );
    """)
    op.execute(r"""
        CREATE TABLE permission_grants (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            user_id uuid NOT NULL,
            tool_name text NOT NULL,
            match_json jsonb NOT NULL,
            created_via text DEFAULT 'manual'::text NOT NULL,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            revoked_at timestamp with time zone,
            CONSTRAINT ck_pg_created_via CHECK ((created_via = ANY (ARRAY['manual'::text, 'always'::text]))),
            CONSTRAINT ck_pg_match_bound CHECK ((octet_length((match_json)::text) <= 8192)),
            CONSTRAINT ck_pg_tool CHECK ((tool_name ~ '^[a-z][a-z0-9_]{0,63}$'::text))
        );
    """)
    op.execute(r"""
        CREATE TABLE project_artifacts (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            project_id uuid NOT NULL,
            working_copy_id uuid,
            run_id uuid,
            user_id uuid NOT NULL,
            name text NOT NULL,
            kind text DEFAULT 'file'::text NOT NULL,
            content_hash bytea,
            size_bytes bigint DEFAULT 0 NOT NULL,
            mime text,
            retention text DEFAULT 'ephemeral'::text NOT NULL,
            retained_at timestamp with time zone,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT ck_part_kind CHECK ((kind = ANY (ARRAY['file'::text, 'log'::text, 'report'::text]))),
            CONSTRAINT ck_part_retention CHECK ((retention = ANY (ARRAY['ephemeral'::text, 'retained'::text, 'expired'::text])))
        );
    """)
    op.execute(r"""
        CREATE TABLE project_change_set_entries (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            change_set_id uuid NOT NULL,
            path text NOT NULL,
            change_kind text NOT NULL,
            old_content_hash bytea,
            new_content_hash bytea,
            size_bytes bigint DEFAULT 0 NOT NULL,
            executable boolean DEFAULT false NOT NULL,
            is_binary boolean DEFAULT false NOT NULL,
            diff_object_key text,
            diff_truncated boolean DEFAULT false NOT NULL,
            selected boolean DEFAULT true NOT NULL,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT ck_pcse_change CHECK ((change_kind = ANY (ARRAY['added'::text, 'modified'::text, 'deleted'::text]))),
            CONSTRAINT ck_pcse_path CHECK ((((char_length(path) >= 1) AND (char_length(path) <= 1024)) AND (path !~~ '/%'::text) AND (path !~~ '%..%'::text)))
        );
    """)
    op.execute(r"""
        CREATE TABLE project_change_sets (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            project_id uuid NOT NULL,
            working_copy_id uuid NOT NULL,
            session_id uuid NOT NULL,
            run_id uuid,
            base_snapshot_id uuid NOT NULL,
            fence_token bigint NOT NULL,
            state text DEFAULT 'open'::text NOT NULL,
            added_count integer DEFAULT 0 NOT NULL,
            modified_count integer DEFAULT 0 NOT NULL,
            deleted_count integer DEFAULT 0 NOT NULL,
            artifact_count integer DEFAULT 0 NOT NULL,
            changed_bytes bigint DEFAULT 0 NOT NULL,
            diff_bytes bigint DEFAULT 0 NOT NULL,
            truncated boolean DEFAULT false NOT NULL,
            created_snapshot_id uuid,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            updated_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT ck_pcs_counts CHECK (((added_count >= 0) AND (modified_count >= 0) AND (deleted_count >= 0) AND (artifact_count >= 0) AND (changed_bytes >= 0) AND (diff_bytes >= 0))),
            CONSTRAINT ck_pcs_state CHECK ((state = ANY (ARRAY['open'::text, 'applied'::text, 'discarded'::text, 'superseded'::text, 'conflicted'::text])))
        );
    """)
    op.execute(r"""
        CREATE TABLE project_import_jobs (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            project_id uuid NOT NULL,
            user_id uuid NOT NULL,
            create_kind text NOT NULL,
            stage text DEFAULT 'queued'::text NOT NULL,
            idempotency_key text NOT NULL,
            staging_object_key text,
            archive_bytes bigint DEFAULT 0 NOT NULL,
            entry_count integer,
            size_bytes bigint,
            termination_reason text,
            attempt integer DEFAULT 0 NOT NULL,
            lease_owner text,
            lease_expires_at timestamp with time zone,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            updated_at timestamp with time zone DEFAULT now() NOT NULL,
            connection_id uuid,
            source_ref_type text,
            source_ref text,
            resolved_oid text,
            CONSTRAINT ck_pij_kind CHECK ((create_kind = ANY (ARRAY['archive'::text, 'github'::text]))),
            CONSTRAINT ck_pij_stage CHECK ((stage = ANY (ARRAY['queued'::text, 'staged'::text, 'activated'::text, 'done'::text, 'failed'::text])))
        );
    """)
    op.execute(r"""
        CREATE TABLE project_snapshot_entries (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            snapshot_id uuid NOT NULL,
            user_id uuid NOT NULL,
            path text NOT NULL,
            entry_kind text NOT NULL,
            content_hash bytea,
            size_bytes bigint DEFAULT 0 NOT NULL,
            executable boolean DEFAULT false NOT NULL,
            symlink_target text,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT ck_pse_file_blob CHECK (((entry_kind <> 'file'::text) OR (content_hash IS NOT NULL))),
            CONSTRAINT ck_pse_kind CHECK ((entry_kind = ANY (ARRAY['file'::text, 'dir'::text, 'symlink'::text]))),
            CONSTRAINT ck_pse_path CHECK ((((char_length(path) >= 1) AND (char_length(path) <= 1024)) AND (path !~~ '/%'::text) AND (path !~~ '%..%'::text)))
        );
    """)
    op.execute(r"""
        CREATE TABLE project_snapshots (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            project_id uuid NOT NULL,
            parent_id uuid,
            reason text NOT NULL,
            entry_count integer DEFAULT 0 NOT NULL,
            size_bytes bigint DEFAULT 0 NOT NULL,
            source_oid text,
            pinned boolean DEFAULT false NOT NULL,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT ck_ps_counts CHECK (((entry_count >= 0) AND (size_bytes >= 0))),
            CONSTRAINT ck_ps_reason CHECK ((reason = ANY (ARRAY['import'::text, 'save'::text, 'checkpoint'::text, 'sync'::text])))
        );
    """)
    op.execute(r"""
        CREATE TABLE project_sources (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            project_id uuid NOT NULL,
            user_id uuid NOT NULL,
            provider text DEFAULT 'github'::text NOT NULL,
            connection_id uuid,
            repo_external_id text NOT NULL,
            owner text NOT NULL,
            repo text NOT NULL,
            ref_type text NOT NULL,
            ref_name text NOT NULL,
            source_oid text,
            status text DEFAULT 'importing'::text NOT NULL,
            imported_at timestamp with time zone,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            updated_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT ck_psrc_provider CHECK ((provider = 'github'::text)),
            CONSTRAINT ck_psrc_ref_type CHECK ((ref_type = ANY (ARRAY['branch'::text, 'tag'::text, 'commit'::text]))),
            CONSTRAINT ck_psrc_status CHECK ((status = ANY (ARRAY['importing'::text, 'imported'::text, 'import_failed'::text])))
        );
    """)
    op.execute(r"""
        CREATE TABLE project_working_copies (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            project_id uuid NOT NULL,
            session_id uuid NOT NULL,
            user_id uuid NOT NULL,
            base_snapshot_id uuid NOT NULL,
            base_head_generation integer NOT NULL,
            state text DEFAULT 'open'::text NOT NULL,
            version integer DEFAULT 0 NOT NULL,
            fence_token bigint DEFAULT 0 NOT NULL,
            lease_owner text,
            lease_expires_at timestamp with time zone,
            reserved_bytes bigint DEFAULT 0 NOT NULL,
            overlay_entry_count integer DEFAULT 0 NOT NULL,
            overlay_bytes bigint DEFAULT 0 NOT NULL,
            last_run_id uuid,
            last_boundary_at timestamp with time zone,
            expires_at timestamp with time zone,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            updated_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT ck_pwc_overlay CHECK (((overlay_entry_count >= 0) AND (overlay_bytes >= 0))),
            CONSTRAINT ck_pwc_reserved CHECK ((reserved_bytes >= 0)),
            CONSTRAINT ck_pwc_state CHECK ((state = ANY (ARRAY['open'::text, 'ready_for_review'::text, 'saved'::text, 'discarded'::text, 'conflicted'::text, 'expired'::text])))
        );
    """)
    op.execute(r"""
        CREATE TABLE project_working_copy_entries (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            working_copy_id uuid NOT NULL,
            user_id uuid NOT NULL,
            path text NOT NULL,
            change_kind text NOT NULL,
            entry_kind text DEFAULT 'file'::text NOT NULL,
            content_hash bytea,
            size_bytes bigint DEFAULT 0 NOT NULL,
            executable boolean DEFAULT false NOT NULL,
            symlink_target text,
            fence_token bigint NOT NULL,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT ck_pwce_blob_presence CHECK ((((change_kind = ANY (ARRAY['added'::text, 'modified'::text])) AND (entry_kind = 'file'::text)) = (content_hash IS NOT NULL))),
            CONSTRAINT ck_pwce_change CHECK ((change_kind = ANY (ARRAY['added'::text, 'modified'::text, 'deleted'::text]))),
            CONSTRAINT ck_pwce_kind CHECK ((entry_kind = ANY (ARRAY['file'::text, 'dir'::text, 'symlink'::text]))),
            CONSTRAINT ck_pwce_path CHECK ((((char_length(path) >= 1) AND (char_length(path) <= 1024)) AND (path !~~ '/%'::text) AND (path !~~ '%..%'::text)))
        );
    """)
    op.execute(r"""
        CREATE TABLE projects (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            user_id uuid NOT NULL,
            name text NOT NULL,
            description text,
            status text DEFAULT 'active'::text NOT NULL,
            current_snapshot_id uuid,
            default_branch_label text DEFAULT 'main'::text NOT NULL,
            source_status text DEFAULT 'unbound'::text NOT NULL,
            used_bytes bigint DEFAULT 0 NOT NULL,
            last_activity_at timestamp with time zone,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            updated_at timestamp with time zone DEFAULT now() NOT NULL,
            head_generation integer DEFAULT 0 NOT NULL,
            CONSTRAINT ck_projects_name CHECK (((char_length(name) >= 1) AND (char_length(name) <= 200))),
            CONSTRAINT ck_projects_source_status CHECK ((source_status = ANY (ARRAY['unbound'::text, 'importing'::text, 'imported'::text, 'import_failed'::text]))),
            CONSTRAINT ck_projects_status CHECK ((status = ANY (ARRAY['active'::text, 'archived'::text, 'deleting'::text]))),
            CONSTRAINT ck_projects_used CHECK ((used_bytes >= 0))
        );
    """)
    op.execute(r"""
        CREATE TABLE runs (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            session_id uuid,
            run_kind text NOT NULL,
            admitted_seq bigint,
            status text DEFAULT 'queued'::text NOT NULL,
            attempt integer DEFAULT 0 NOT NULL,
            fence_token bigint DEFAULT 0 NOT NULL,
            prompt_version text NOT NULL,
            deadline_at timestamp with time zone,
            started_at timestamp with time zone,
            settled_at timestamp with time zone,
            error_redacted text,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            updated_at timestamp with time zone DEFAULT now() NOT NULL,
            heartbeat_at timestamp with time zone,
            lease_expires_at timestamp with time zone,
            worker_id text,
            CONSTRAINT ck_runs_error_bound CHECK (((error_redacted IS NULL) OR (octet_length(error_redacted) <= 16384))),
            CONSTRAINT ck_runs_kind CHECK ((run_kind = ANY (ARRAY['web_chat'::text, 'gmail_sync'::text, 'candidate_extraction'::text, 'schedule_delivery'::text, 'scheduled_task'::text]))),
            CONSTRAINT ck_runs_numbers CHECK (((attempt >= 0) AND (fence_token >= 0) AND ((admitted_seq IS NULL) OR (admitted_seq > 0)))),
            CONSTRAINT ck_runs_session_admission CHECK (((session_id IS NOT NULL) OR (admitted_seq IS NULL))),
            CONSTRAINT ck_runs_settled_time CHECK ((((status = ANY (ARRAY['queued'::text, 'running'::text])) AND (settled_at IS NULL)) OR ((status = ANY (ARRAY['succeeded'::text, 'failed'::text, 'cancelled'::text, 'needs_reconciliation'::text])) AND (settled_at IS NOT NULL)))),
            CONSTRAINT ck_runs_status CHECK ((status = ANY (ARRAY['queued'::text, 'running'::text, 'succeeded'::text, 'failed'::text, 'cancelled'::text, 'needs_reconciliation'::text])))
        );
    """)
    op.execute(r"""
        CREATE TABLE schedule_firings (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            schedule_id uuid NOT NULL,
            firing_key text NOT NULL,
            scheduled_for timestamp with time zone NOT NULL,
            status text DEFAULT 'pending'::text NOT NULL,
            delivery_outcome text,
            delivery_idempotency_key text NOT NULL,
            invocation_id uuid,
            attempts integer DEFAULT 0 NOT NULL,
            available_at timestamp with time zone DEFAULT now() NOT NULL,
            started_at timestamp with time zone,
            settled_at timestamp with time zone,
            last_error_redacted text,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            updated_at timestamp with time zone DEFAULT now() NOT NULL,
            run_id uuid,
            CONSTRAINT ck_schedule_firings_attempts CHECK ((attempts >= 0)),
            CONSTRAINT ck_schedule_firings_error_bound CHECK (((last_error_redacted IS NULL) OR (octet_length(last_error_redacted) <= 16384))),
            CONSTRAINT ck_schedule_firings_key CHECK ((((char_length(firing_key) >= 1) AND (char_length(firing_key) <= 512)) AND ((char_length(delivery_idempotency_key) >= 1) AND (char_length(delivery_idempotency_key) <= 512)))),
            CONSTRAINT ck_schedule_firings_outcome CHECK (((delivery_outcome IS NULL) OR (delivery_outcome = ANY (ARRAY['missed'::text, 'failed'::text, 'unknown'::text, 'delivered'::text])))),
            CONSTRAINT ck_schedule_firings_state CHECK ((((status = ANY (ARRAY['pending'::text, 'running'::text])) AND (delivery_outcome IS NULL) AND (settled_at IS NULL)) OR ((status = 'settled'::text) AND (delivery_outcome IS NOT NULL) AND (settled_at IS NOT NULL)))),
            CONSTRAINT ck_schedule_firings_status CHECK ((status = ANY (ARRAY['pending'::text, 'running'::text, 'settled'::text])))
        );
    """)
    op.execute(r"""
        CREATE TABLE schedules (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            user_id uuid NOT NULL,
            todo_id uuid,
            kind text NOT NULL,
            name character varying(200) NOT NULL,
            reminder_kind text,
            delivery_channel text NOT NULL,
            timezone text NOT NULL,
            local_time time without time zone,
            next_fire_at timestamp with time zone NOT NULL,
            last_fired_at timestamp with time zone,
            misfire_policy text NOT NULL,
            duplicate_policy text NOT NULL,
            status text DEFAULT 'active'::text NOT NULL,
            version integer DEFAULT 1 NOT NULL,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            updated_at timestamp with time zone DEFAULT now() NOT NULL,
            cadence_kind text DEFAULT 'daily'::text NOT NULL,
            cron_expr text,
            interval_seconds integer,
            weekly_days text,
            monthly_day smallint,
            prompt text,
            CONSTRAINT ck_schedules_cadence CHECK ((cadence_kind = ANY (ARRAY['daily'::text, 'cron'::text, 'interval'::text, 'weekly'::text, 'monthly'::text, 'once'::text]))),
            CONSTRAINT ck_schedules_cadence_fields CHECK ((((cadence_kind = 'cron'::text) AND (cron_expr IS NOT NULL)) OR ((cadence_kind = 'interval'::text) AND (interval_seconds IS NOT NULL) AND (interval_seconds >= 60)) OR ((cadence_kind = 'weekly'::text) AND (weekly_days IS NOT NULL) AND (local_time IS NOT NULL)) OR ((cadence_kind = 'monthly'::text) AND ((monthly_day >= 1) AND (monthly_day <= 31)) AND (local_time IS NOT NULL)) OR (cadence_kind = ANY (ARRAY['daily'::text, 'once'::text])))),
            CONSTRAINT ck_schedules_delivery_channel CHECK ((delivery_channel = ANY (ARRAY['web'::text, 'digest_email'::text, 'email'::text, 'qq'::text]))),
            CONSTRAINT ck_schedules_duplicate CHECK ((duplicate_policy = ANY (ARRAY['prefer_no_duplicate'::text, 'eventual_delivery'::text]))),
            CONSTRAINT ck_schedules_kind CHECK ((kind = ANY (ARRAY['todo_reminder'::text, 'daily_digest'::text, 'agent_task'::text]))),
            CONSTRAINT ck_schedules_kind_target CHECK ((((kind = 'todo_reminder'::text) AND (todo_id IS NOT NULL) AND (reminder_kind IS NOT NULL) AND (local_time IS NULL) AND (prompt IS NULL)) OR ((kind = 'daily_digest'::text) AND (todo_id IS NULL) AND (reminder_kind IS NULL) AND (local_time IS NOT NULL) AND (prompt IS NULL)) OR ((kind = 'agent_task'::text) AND (todo_id IS NULL) AND (reminder_kind IS NULL) AND (prompt IS NOT NULL) AND ((char_length(prompt) >= 1) AND (char_length(prompt) <= 8000))))),
            CONSTRAINT ck_schedules_misfire CHECK ((misfire_policy = ANY (ARRAY['skip'::text, 'fire_once'::text]))),
            CONSTRAINT ck_schedules_name CHECK (((char_length((name)::text) >= 1) AND (char_length((name)::text) <= 200))),
            CONSTRAINT ck_schedules_reminder_kind CHECK (((reminder_kind IS NULL) OR (reminder_kind = ANY (ARRAY['due_soon'::text, 'overdue'::text])))),
            CONSTRAINT ck_schedules_status CHECK ((status = ANY (ARRAY['active'::text, 'paused'::text, 'completed'::text, 'disabled'::text]))),
            CONSTRAINT ck_schedules_timezone CHECK (((char_length(timezone) >= 1) AND (char_length(timezone) <= 100))),
            CONSTRAINT ck_schedules_version CHECK ((version > 0))
        );
    """)
    op.execute(r"""
        CREATE TABLE session_search_entries (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            user_id uuid NOT NULL,
            session_id uuid NOT NULL,
            source_kind text NOT NULL,
            source_id text NOT NULL,
            anchor_kind text NOT NULL,
            anchor_id text NOT NULL,
            run_id uuid,
            message_seq bigint,
            event_session_seq bigint,
            channel text NOT NULL,
            content_text text NOT NULL,
            normalized_text text NOT NULL,
            cjk_terms text DEFAULT ''::text NOT NULL,
            occurred_at timestamp with time zone NOT NULL,
            projection_version integer DEFAULT 1 NOT NULL,
            redacted_at timestamp with time zone,
            fts tsvector GENERATED ALWAYS AS (to_tsvector('simple'::regconfig, content_text)) STORED,
            cjk_fts tsvector GENERATED ALWAYS AS (to_tsvector('simple'::regconfig, cjk_terms)) STORED,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            updated_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT ck_sse_anchor_kind CHECK ((anchor_kind = ANY (ARRAY['message'::text, 'event'::text, 'audit'::text, 'session'::text]))),
            CONSTRAINT ck_sse_content_bound CHECK ((octet_length(content_text) <= 32768)),
            CONSTRAINT ck_sse_kind CHECK ((source_kind = ANY (ARRAY['title'::text, 'user_message'::text, 'assistant_message'::text, 'tool'::text, 'action'::text])))
        );
    """)
    op.execute(r"""
        CREATE TABLE sessions (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            user_id uuid NOT NULL,
            identity_id uuid,
            umo_key text NOT NULL,
            channel text NOT NULL,
            channel_installation_id text NOT NULL,
            scope_type text NOT NULL,
            external_scope_id text NOT NULL,
            status text DEFAULT 'open'::text NOT NULL,
            admitted_seq bigint,
            promoted_seq bigint,
            fence_token bigint DEFAULT 0 NOT NULL,
            input_tokens_rollup bigint DEFAULT 0 NOT NULL,
            output_tokens_rollup bigint DEFAULT 0 NOT NULL,
            cost_usd_rollup numeric(20,8) DEFAULT 0 NOT NULL,
            last_activity_at timestamp with time zone,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            updated_at timestamp with time zone DEFAULT now() NOT NULL,
            title text,
            project_id uuid,
            model_provider_id uuid,
            model text,
            CONSTRAINT ck_sessions_rollups CHECK (((fence_token >= 0) AND (input_tokens_rollup >= 0) AND (output_tokens_rollup >= 0) AND (cost_usd_rollup >= (0)::numeric))),
            CONSTRAINT ck_sessions_sequences CHECK ((((admitted_seq IS NULL) AND (promoted_seq IS NULL)) OR ((admitted_seq IS NOT NULL) AND (admitted_seq > 0) AND ((promoted_seq IS NULL) OR ((promoted_seq >= 1) AND (promoted_seq <= admitted_seq)))))),
            CONSTRAINT ck_sessions_status CHECK ((status = ANY (ARRAY['open'::text, 'archived'::text, 'deleted'::text]))),
            CONSTRAINT ck_sessions_title CHECK (((title IS NULL) OR ((char_length(title) >= 1) AND (char_length(title) <= 200)))),
            CONSTRAINT ck_sessions_umo_key CHECK (((char_length(umo_key) >= 1) AND (char_length(umo_key) <= 1024)))
        );
    """)
    op.execute(r"""
        CREATE TABLE storage_accounts (
            tenant_id uuid NOT NULL,
            user_id uuid NOT NULL,
            quota_bytes bigint NOT NULL,
            used_bytes bigint DEFAULT 0 NOT NULL,
            reserved_bytes bigint DEFAULT 0 NOT NULL,
            version integer DEFAULT 1 NOT NULL,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            updated_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT ck_sa_numbers CHECK (((quota_bytes >= 0) AND (used_bytes >= 0) AND (reserved_bytes >= 0)))
        );
    """)
    op.execute(r"""
        CREATE TABLE storage_blobs (
            tenant_id uuid NOT NULL,
            user_id uuid NOT NULL,
            content_hash bytea NOT NULL,
            object_key text NOT NULL,
            size_bytes bigint NOT NULL,
            content_type text DEFAULT 'application/octet-stream'::text NOT NULL,
            ref_count integer DEFAULT 0 NOT NULL,
            unreferenced_at timestamp with time zone,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT ck_sb_size CHECK (((size_bytes >= 0) AND (ref_count >= 0)))
        );
    """)
    op.execute(r"""
        CREATE TABLE tenants (
            tenant_id uuid NOT NULL,
            slug character varying(63) NOT NULL,
            display_name character varying(200) NOT NULL,
            kind text DEFAULT 'personal'::text NOT NULL,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            updated_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT ck_tenants_kind CHECK ((kind = 'personal'::text)),
            CONSTRAINT ck_tenants_slug CHECK (((slug)::text ~ '^[a-z0-9][a-z0-9-]{0,62}$'::text))
        );
    """)
    op.execute(r"""
        CREATE TABLE todos (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            user_id uuid NOT NULL,
            source_candidate_id uuid,
            source text DEFAULT 'gmail_candidate'::text NOT NULL,
            title character varying(500) NOT NULL,
            description text,
            status text DEFAULT 'open'::text NOT NULL,
            due_at timestamp with time zone,
            snoozed_until timestamp with time zone,
            priority text DEFAULT 'medium'::text NOT NULL,
            completed_at timestamp with time zone,
            version integer DEFAULT 1 NOT NULL,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            updated_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT ck_todos_completion CHECK ((((status = 'completed'::text) AND (completed_at IS NOT NULL) AND (snoozed_until IS NULL)) OR ((status = 'open'::text) AND (completed_at IS NULL)) OR ((status = 'cancelled'::text) AND (completed_at IS NULL) AND (snoozed_until IS NULL)))),
            CONSTRAINT ck_todos_description_bound CHECK (((description IS NULL) OR (octet_length(description) <= 65536))),
            CONSTRAINT ck_todos_priority CHECK ((priority = ANY (ARRAY['low'::text, 'medium'::text, 'high'::text]))),
            CONSTRAINT ck_todos_source CHECK ((source = ANY (ARRAY['gmail_candidate'::text, 'agent'::text]))),
            CONSTRAINT ck_todos_source_candidate_link CHECK ((((source = 'gmail_candidate'::text) AND (source_candidate_id IS NOT NULL)) OR ((source = 'agent'::text) AND (source_candidate_id IS NULL)))),
            CONSTRAINT ck_todos_status CHECK ((status = ANY (ARRAY['open'::text, 'completed'::text, 'cancelled'::text]))),
            CONSTRAINT ck_todos_version CHECK ((version > 0))
        );
    """)
    op.execute(r"""
        CREATE TABLE traces (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            parent_trace_id uuid,
            run_id uuid,
            session_id uuid,
            user_id uuid,
            trace_kind text NOT NULL,
            status text DEFAULT 'running'::text NOT NULL,
            tags jsonb DEFAULT '{}'::jsonb NOT NULL,
            started_at timestamp with time zone DEFAULT now() NOT NULL,
            ended_at timestamp with time zone,
            CONSTRAINT ck_traces_ended CHECK ((((status = 'running'::text) AND (ended_at IS NULL)) OR ((status <> 'running'::text) AND (ended_at IS NOT NULL)))),
            CONSTRAINT ck_traces_kind CHECK ((trace_kind = ANY (ARRAY['web_chat'::text, 'gmail_sync'::text, 'candidate_extraction'::text, 'schedule_delivery'::text, 'scheduled_task'::text]))),
            CONSTRAINT ck_traces_status CHECK ((status = ANY (ARRAY['running'::text, 'succeeded'::text, 'failed'::text, 'cancelled'::text]))),
            CONSTRAINT ck_traces_tags_bound CHECK ((octet_length((tags)::text) <= 16384))
        );
    """)
    op.execute(r"""
        CREATE TABLE user_memory (
            tenant_id uuid NOT NULL,
            user_id uuid NOT NULL,
            memory_key character varying(64) NOT NULL,
            value_text text NOT NULL,
            version integer DEFAULT 1 NOT NULL,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            updated_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT ck_user_memory_key CHECK (((memory_key)::text ~ '^[a-z][a-z0-9_.-]{0,63}$'::text)),
            CONSTRAINT ck_user_memory_value_bound CHECK ((octet_length(value_text) <= 16384)),
            CONSTRAINT ck_user_memory_version CHECK ((version > 0))
        );
    """)
    op.execute(r"""
        CREATE TABLE user_settings (
            tenant_id uuid NOT NULL,
            user_id uuid NOT NULL,
            notifications_enabled boolean DEFAULT false NOT NULL,
            web_enabled boolean DEFAULT true NOT NULL,
            email_digest_enabled boolean DEFAULT false NOT NULL,
            timezone text DEFAULT 'UTC'::text NOT NULL,
            digest_time time without time zone DEFAULT '08:00:00'::time without time zone NOT NULL,
            quiet_hours_enabled boolean DEFAULT true NOT NULL,
            quiet_hours_start time without time zone DEFAULT '22:00:00'::time without time zone NOT NULL,
            quiet_hours_end time without time zone DEFAULT '08:00:00'::time without time zone NOT NULL,
            daily_cap integer DEFAULT 6 NOT NULL,
            event_types text[] DEFAULT ARRAY['new_candidate'::text, 'due_soon'::text, 'overdue'::text, 'run_failed'::text] NOT NULL,
            eventual_delivery_kinds text[] DEFAULT ARRAY['overdue'::text] NOT NULL,
            connector_analysis text DEFAULT 'candidate_first'::text NOT NULL,
            todo_promotion text DEFAULT 'manual'::text NOT NULL,
            external_actions text DEFAULT 'approval_required'::text NOT NULL,
            version integer DEFAULT 1 NOT NULL,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            updated_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT ck_user_settings_connector_analysis CHECK ((connector_analysis = ANY (ARRAY['off'::text, 'candidate_first'::text]))),
            CONSTRAINT ck_user_settings_daily_cap CHECK (((daily_cap >= 0) AND (daily_cap <= 100))),
            CONSTRAINT ck_user_settings_event_types CHECK ((event_types <@ ARRAY['new_candidate'::text, 'due_soon'::text, 'overdue'::text, 'run_failed'::text])),
            CONSTRAINT ck_user_settings_eventual_delivery CHECK ((eventual_delivery_kinds <@ ARRAY['due_soon'::text, 'overdue'::text])),
            CONSTRAINT ck_user_settings_external_actions CHECK ((external_actions = 'approval_required'::text)),
            CONSTRAINT ck_user_settings_quiet_hours CHECK ((quiet_hours_start <> quiet_hours_end)),
            CONSTRAINT ck_user_settings_timezone CHECK (((char_length(timezone) >= 1) AND (char_length(timezone) <= 100))),
            CONSTRAINT ck_user_settings_todo_promotion CHECK ((todo_promotion = 'manual'::text)),
            CONSTRAINT ck_user_settings_version CHECK ((version > 0))
        );
    """)
    op.execute(r"""
        CREATE TABLE users (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            email text NOT NULL,
            display_name character varying(200) NOT NULL,
            status text DEFAULT 'active'::text NOT NULL,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            updated_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT ck_users_email CHECK (((char_length(email) >= 3) AND (char_length(email) <= 320))),
            CONSTRAINT ck_users_status CHECK ((status = ANY (ARRAY['active'::text, 'disabled'::text])))
        );
    """)

    # --- ADR-047 + ADR-048 target runtime tables (replace project_sandbox_runs) ------
    # One coding RuntimeSession (open -> exec* -> close). `scratch_ref` is meaningless
    # under tar transport and `warm_until` was never implemented anywhere in the code;
    # the idle TTL is `expires_at` (data-model.md §Projects W3).
    op.execute("""
        CREATE TABLE project_runtime_sessions (
            tenant_id          uuid NOT NULL,
            id                 uuid NOT NULL,
            project_id         uuid,
            working_copy_id    uuid,
            session_id         uuid NOT NULL,
            user_id            uuid NOT NULL,
            scope              text NOT NULL DEFAULT 'project',
            base_snapshot_id   uuid,
            fence_token        bigint,
            state              text NOT NULL DEFAULT 'opening',
            container_ref      text,
            image              text NOT NULL,
            image_digest       text,
            capabilities       jsonb,
            ingress_bytes      bigint,
            entry_count        integer,
            termination_reason text,
            expires_at         timestamptz,
            closed_at          timestamptz,
            created_at         timestamptz NOT NULL DEFAULT now(),
            updated_at         timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_prs PRIMARY KEY (tenant_id, id),
            CONSTRAINT ck_prs_scope CHECK (scope IN ('project','ephemeral')),
            CONSTRAINT ck_prs_state CHECK (
                state IN ('opening','ready','executing','closing','closed','failed')
            ),
            CONSTRAINT ck_prs_scope_binding CHECK (
                (scope = 'project'   AND project_id IS NOT NULL AND working_copy_id IS NOT NULL)
             OR (scope = 'ephemeral' AND project_id IS NULL     AND working_copy_id IS NULL)
            )
        );
    """)
    # One command executed inside a runtime session; `run_id` is the durable model-loop
    # run when agent-driven and NULL when a human pressed Run.
    op.execute("""
        CREATE TABLE project_exec_runs (
            tenant_id             uuid NOT NULL,
            id                    uuid NOT NULL,
            runtime_session_id    uuid NOT NULL,
            run_id                uuid,
            seq                   integer NOT NULL,
            command_preview       text NOT NULL,
            state                 text NOT NULL DEFAULT 'queued',
            exit_code             integer,
            timed_out             boolean NOT NULL DEFAULT false,
            termination_reason    text,
            output_truncated      boolean NOT NULL DEFAULT false,
            spill_ref             text,
            change_set_id         uuid,
            duration_ms           integer,
            persisted_boundary_at timestamptz,
            created_at            timestamptz NOT NULL DEFAULT now(),
            updated_at            timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_per PRIMARY KEY (tenant_id, id),
            CONSTRAINT uq_per_seq UNIQUE (tenant_id, runtime_session_id, seq),
            CONSTRAINT ck_per_state CHECK (
                state IN ('queued','running','persisted','failed','cancelled')
            )
        );
    """)

    # --- primary keys / unique + check constraints -------------------------------
    op.execute(r"""
        ALTER TABLE ONLY approval_envelopes
            ADD CONSTRAINT pk_approval_envelopes PRIMARY KEY (tenant_id, id);
    """)
    op.execute(r"""
        ALTER TABLE ONLY audit_receipts
            ADD CONSTRAINT pk_audit_receipts PRIMARY KEY (tenant_id, id);
    """)
    op.execute(r"""
        ALTER TABLE ONLY candidates
            ADD CONSTRAINT pk_candidates PRIMARY KEY (tenant_id, id);
    """)
    op.execute(r"""
        ALTER TABLE ONLY channel_configs
            ADD CONSTRAINT pk_channel_configs PRIMARY KEY (tenant_id, user_id, channel);
    """)
    op.execute(r"""
        ALTER TABLE ONLY channel_thread_state
            ADD CONSTRAINT pk_channel_thread_state PRIMARY KEY (tenant_id, session_id);
    """)
    op.execute(r"""
        ALTER TABLE ONLY connector_items
            ADD CONSTRAINT pk_connector_items PRIMARY KEY (tenant_id, id);
    """)
    op.execute(r"""
        ALTER TABLE ONLY connectors
            ADD CONSTRAINT pk_connectors PRIMARY KEY (tenant_id, id);
    """)
    op.execute(r"""
        ALTER TABLE ONLY drive_nodes
            ADD CONSTRAINT pk_drive_nodes PRIMARY KEY (tenant_id, id);
    """)
    op.execute(r"""
        ALTER TABLE ONLY drive_versions
            ADD CONSTRAINT pk_drive_versions PRIMARY KEY (tenant_id, id);
    """)
    op.execute(r"""
        ALTER TABLE ONLY effect_invocations
            ADD CONSTRAINT pk_effect_invocations PRIMARY KEY (tenant_id, invocation_id);
    """)
    op.execute(r"""
        ALTER TABLE ONLY embedding_profiles
            ADD CONSTRAINT pk_embedding_profiles PRIMARY KEY (tenant_id, id);
    """)
    op.execute(r"""
        ALTER TABLE ONLY event_journal
            ADD CONSTRAINT pk_event_journal PRIMARY KEY (tenant_id, id);
    """)
    op.execute(r"""
        ALTER TABLE ONLY extractions
            ADD CONSTRAINT pk_extractions PRIMARY KEY (tenant_id, id);
    """)
    op.execute(r"""
        ALTER TABLE ONLY generations
            ADD CONSTRAINT pk_generations PRIMARY KEY (tenant_id, id);
    """)
    op.execute(r"""
        ALTER TABLE ONLY github_connections
            ADD CONSTRAINT pk_github_connections PRIMARY KEY (tenant_id, id);
    """)
    op.execute(r"""
        ALTER TABLE ONLY identities
            ADD CONSTRAINT pk_identities PRIMARY KEY (tenant_id, id);
    """)
    op.execute(r"""
        ALTER TABLE ONLY knowledge_ingestion_jobs
            ADD CONSTRAINT pk_kij PRIMARY KEY (tenant_id, id);
    """)
    op.execute(r"""
        ALTER TABLE ONLY knowledge_chunks
            ADD CONSTRAINT pk_knowledge_chunks PRIMARY KEY (tenant_id, id);
    """)
    op.execute(r"""
        ALTER TABLE ONLY knowledge_sources
            ADD CONSTRAINT pk_knowledge_sources PRIMARY KEY (tenant_id, id);
    """)
    op.execute(r"""
        ALTER TABLE ONLY knowledge_retrieval_evidence
            ADD CONSTRAINT pk_kre PRIMARY KEY (tenant_id, id);
    """)
    op.execute(r"""
        ALTER TABLE ONLY knowledge_source_versions
            ADD CONSTRAINT pk_ksv PRIMARY KEY (tenant_id, id);
    """)
    op.execute(r"""
        ALTER TABLE ONLY memory_passages
            ADD CONSTRAINT pk_memory_passages PRIMARY KEY (tenant_id, id);
    """)
    op.execute(r"""
        ALTER TABLE ONLY messages
            ADD CONSTRAINT pk_messages PRIMARY KEY (tenant_id, id);
    """)
    op.execute(r"""
        ALTER TABLE ONLY model_providers
            ADD CONSTRAINT pk_model_providers PRIMARY KEY (tenant_id, id);
    """)
    op.execute(r"""
        ALTER TABLE ONLY outbox
            ADD CONSTRAINT pk_outbox PRIMARY KEY (tenant_id, id);
    """)
    op.execute(r"""
        ALTER TABLE ONLY project_artifacts
            ADD CONSTRAINT pk_part PRIMARY KEY (tenant_id, id);
    """)
    op.execute(r"""
        ALTER TABLE ONLY parts
            ADD CONSTRAINT pk_parts PRIMARY KEY (tenant_id, id);
    """)
    op.execute(r"""
        ALTER TABLE ONLY project_change_sets
            ADD CONSTRAINT pk_pcs PRIMARY KEY (tenant_id, id);
    """)
    op.execute(r"""
        ALTER TABLE ONLY project_change_set_entries
            ADD CONSTRAINT pk_pcse PRIMARY KEY (tenant_id, id);
    """)
    op.execute(r"""
        ALTER TABLE ONLY permission_grants
            ADD CONSTRAINT pk_permission_grants PRIMARY KEY (tenant_id, id);
    """)
    op.execute(r"""
        ALTER TABLE ONLY project_import_jobs
            ADD CONSTRAINT pk_project_import_jobs PRIMARY KEY (tenant_id, id);
    """)
    op.execute(r"""
        ALTER TABLE ONLY project_snapshots
            ADD CONSTRAINT pk_project_snapshots PRIMARY KEY (tenant_id, id);
    """)
    op.execute(r"""
        ALTER TABLE ONLY project_sources
            ADD CONSTRAINT pk_project_sources PRIMARY KEY (tenant_id, id);
    """)
    op.execute(r"""
        ALTER TABLE ONLY projects
            ADD CONSTRAINT pk_projects PRIMARY KEY (tenant_id, id);
    """)
    op.execute(r"""
        ALTER TABLE ONLY project_snapshot_entries
            ADD CONSTRAINT pk_pse PRIMARY KEY (tenant_id, id);
    """)
    op.execute(r"""
        ALTER TABLE ONLY project_working_copies
            ADD CONSTRAINT pk_pwc PRIMARY KEY (tenant_id, id);
    """)
    op.execute(r"""
        ALTER TABLE ONLY project_working_copy_entries
            ADD CONSTRAINT pk_pwce PRIMARY KEY (tenant_id, id);
    """)
    op.execute(r"""
        ALTER TABLE ONLY runs
            ADD CONSTRAINT pk_runs PRIMARY KEY (tenant_id, id);
    """)
    op.execute(r"""
        ALTER TABLE ONLY schedule_firings
            ADD CONSTRAINT pk_schedule_firings PRIMARY KEY (tenant_id, id);
    """)
    op.execute(r"""
        ALTER TABLE ONLY schedules
            ADD CONSTRAINT pk_schedules PRIMARY KEY (tenant_id, id);
    """)
    op.execute(r"""
        ALTER TABLE ONLY session_search_entries
            ADD CONSTRAINT pk_session_search_entries PRIMARY KEY (tenant_id, id);
    """)
    op.execute(r"""
        ALTER TABLE ONLY sessions
            ADD CONSTRAINT pk_sessions PRIMARY KEY (tenant_id, id);
    """)
    op.execute(r"""
        ALTER TABLE ONLY storage_accounts
            ADD CONSTRAINT pk_storage_accounts PRIMARY KEY (tenant_id, user_id);
    """)
    op.execute(r"""
        ALTER TABLE ONLY storage_blobs
            ADD CONSTRAINT pk_storage_blobs PRIMARY KEY (tenant_id, user_id, content_hash);
    """)
    op.execute(r"""
        ALTER TABLE ONLY tenants
            ADD CONSTRAINT pk_tenants PRIMARY KEY (tenant_id);
    """)
    op.execute(r"""
        ALTER TABLE ONLY todos
            ADD CONSTRAINT pk_todos PRIMARY KEY (tenant_id, id);
    """)
    op.execute(r"""
        ALTER TABLE ONLY traces
            ADD CONSTRAINT pk_traces PRIMARY KEY (tenant_id, id);
    """)
    op.execute(r"""
        ALTER TABLE ONLY user_memory
            ADD CONSTRAINT pk_user_memory PRIMARY KEY (tenant_id, user_id, memory_key);
    """)
    op.execute(r"""
        ALTER TABLE ONLY user_settings
            ADD CONSTRAINT pk_user_settings PRIMARY KEY (tenant_id, user_id);
    """)
    op.execute(r"""
        ALTER TABLE ONLY users
            ADD CONSTRAINT pk_users PRIMARY KEY (tenant_id, id);
    """)
    op.execute(r"""
        ALTER TABLE ONLY approval_envelopes
            ADD CONSTRAINT uq_approval_envelopes_correlation UNIQUE (tenant_id, correlation_id);
    """)
    op.execute(r"""
        ALTER TABLE ONLY approval_envelopes
            ADD CONSTRAINT uq_approval_envelopes_nonce UNIQUE (tenant_id, nonce_hash);
    """)
    op.execute(r"""
        ALTER TABLE ONLY candidates
            ADD CONSTRAINT uq_candidates_chain UNIQUE (tenant_id, id, generation_id, extraction_id);
    """)
    op.execute(r"""
        ALTER TABLE ONLY candidates
            ADD CONSTRAINT uq_candidates_dedupe UNIQUE (tenant_id, dedupe_key);
    """)
    op.execute(r"""
        ALTER TABLE ONLY candidates
            ADD CONSTRAINT uq_candidates_extraction_ordinal UNIQUE (tenant_id, extraction_id, ordinal);
    """)
    op.execute(r"""
        ALTER TABLE ONLY candidates
            ADD CONSTRAINT uq_candidates_todo_link UNIQUE (tenant_id, id, accepted_todo_id);
    """)
    op.execute(r"""
        ALTER TABLE ONLY connector_items
            ADD CONSTRAINT uq_connector_items_id_revision UNIQUE (tenant_id, id, revision);
    """)
    op.execute(r"""
        ALTER TABLE ONLY connector_items
            ADD CONSTRAINT uq_connector_items_revision UNIQUE (tenant_id, connector_id, provider_item_id, revision);
    """)
    op.execute(r"""
        ALTER TABLE ONLY connectors
            ADD CONSTRAINT uq_connectors_external_account UNIQUE (tenant_id, kind, external_account_id);
    """)
    op.execute(r"""
        ALTER TABLE ONLY connectors
            ADD CONSTRAINT uq_connectors_installation UNIQUE (tenant_id, channel_installation_id);
    """)
    op.execute(r"""
        ALTER TABLE ONLY drive_versions
            ADD CONSTRAINT uq_dv_node_version UNIQUE (tenant_id, node_id, version);
    """)
    op.execute(r"""
        ALTER TABLE ONLY effect_invocations
            ADD CONSTRAINT uq_effect_invocations_class_binding UNIQUE (tenant_id, invocation_id, effect_class);
    """)
    op.execute(r"""
        ALTER TABLE ONLY effect_invocations
            ADD CONSTRAINT uq_effect_invocations_idempotency UNIQUE (tenant_id, idempotency_key);
    """)
    op.execute(r"""
        ALTER TABLE ONLY event_journal
            ADD CONSTRAINT uq_event_journal_run_seq UNIQUE (tenant_id, run_id, run_seq);
    """)
    op.execute(r"""
        ALTER TABLE ONLY extractions
            ADD CONSTRAINT uq_extractions_chain UNIQUE (tenant_id, id, connector_item_id, extraction_version);
    """)
    op.execute(r"""
        ALTER TABLE ONLY extractions
            ADD CONSTRAINT uq_extractions_item_version UNIQUE (tenant_id, connector_item_id, extraction_version);
    """)
    op.execute(r"""
        ALTER TABLE ONLY generations
            ADD CONSTRAINT uq_generations_chain UNIQUE (tenant_id, id, extraction_id);
    """)
    op.execute(r"""
        ALTER TABLE ONLY identities
            ADD CONSTRAINT uq_identities_canonical_scope UNIQUE (tenant_id, channel, channel_installation_id, scope_type, external_scope_id);
    """)
    op.execute(r"""
        ALTER TABLE ONLY knowledge_ingestion_jobs
            ADD CONSTRAINT uq_kij_idem UNIQUE (tenant_id, idempotency_key);
    """)
    op.execute(r"""
        ALTER TABLE ONLY knowledge_retrieval_evidence
            ADD CONSTRAINT uq_kre_ref UNIQUE (tenant_id, run_id, citation_ref);
    """)
    op.execute(r"""
        ALTER TABLE ONLY knowledge_source_versions
            ADD CONSTRAINT uq_ksv_idem UNIQUE (tenant_id, source_id, idempotency_key);
    """)
    op.execute(r"""
        ALTER TABLE ONLY memory_passages
            ADD CONSTRAINT uq_memory_passages_dedupe UNIQUE (tenant_id, user_id, content_hash);
    """)
    op.execute(r"""
        ALTER TABLE ONLY messages
            ADD CONSTRAINT uq_messages_session_seq UNIQUE (tenant_id, session_id, seq);
    """)
    op.execute(r"""
        ALTER TABLE ONLY outbox
            ADD CONSTRAINT uq_outbox_delivery_key UNIQUE (tenant_id, topic, delivery_key);
    """)
    op.execute(r"""
        ALTER TABLE ONLY outbox
            ADD CONSTRAINT uq_outbox_event_topic UNIQUE (tenant_id, event_id, topic);
    """)
    op.execute(r"""
        ALTER TABLE ONLY parts
            ADD CONSTRAINT uq_parts_message_ordinal UNIQUE (tenant_id, message_id, ordinal);
    """)
    op.execute(r"""
        ALTER TABLE ONLY project_import_jobs
            ADD CONSTRAINT uq_pij_idem UNIQUE (tenant_id, idempotency_key);
    """)
    op.execute(r"""
        ALTER TABLE ONLY runs
            ADD CONSTRAINT uq_runs_session_binding UNIQUE (tenant_id, id, session_id);
    """)
    op.execute(r"""
        ALTER TABLE ONLY schedule_firings
            ADD CONSTRAINT uq_schedule_firings_delivery_key UNIQUE (tenant_id, delivery_idempotency_key);
    """)
    op.execute(r"""
        ALTER TABLE ONLY schedule_firings
            ADD CONSTRAINT uq_schedule_firings_invocation UNIQUE (tenant_id, invocation_id);
    """)
    op.execute(r"""
        ALTER TABLE ONLY schedule_firings
            ADD CONSTRAINT uq_schedule_firings_key UNIQUE (tenant_id, schedule_id, firing_key);
    """)
    op.execute(r"""
        ALTER TABLE ONLY schedule_firings
            ADD CONSTRAINT uq_schedule_firings_slot UNIQUE (tenant_id, schedule_id, scheduled_for);
    """)
    op.execute(r"""
        ALTER TABLE ONLY sessions
            ADD CONSTRAINT uq_sessions_canonical_scope UNIQUE (tenant_id, channel, channel_installation_id, scope_type, external_scope_id);
    """)
    op.execute(r"""
        ALTER TABLE ONLY sessions
            ADD CONSTRAINT uq_sessions_umo_key UNIQUE (tenant_id, umo_key);
    """)
    op.execute(r"""
        ALTER TABLE ONLY session_search_entries
            ADD CONSTRAINT uq_sse_source UNIQUE (tenant_id, source_kind, source_id, projection_version);
    """)
    op.execute(r"""
        ALTER TABLE ONLY tenants
            ADD CONSTRAINT uq_tenants_slug UNIQUE (slug);
    """)
    op.execute(r"""
        ALTER TABLE ONLY todos
            ADD CONSTRAINT uq_todos_source_candidate UNIQUE (tenant_id, source_candidate_id);
    """)

    # --- indexes ----------------------------------------------------------------
    op.execute(r"""
        CREATE INDEX ix_approval_envelopes_pending_expiry ON approval_envelopes USING btree (expires_at, tenant_id, id) WHERE (status = 'pending'::text);
    """)
    op.execute(r"""
        CREATE INDEX ix_audit_receipts_tenant_occurred ON audit_receipts USING btree (tenant_id, occurred_at DESC);
    """)
    op.execute(r"""
        CREATE INDEX ix_audit_receipts_tenant_type_occurred ON audit_receipts USING btree (tenant_id, receipt_type, occurred_at DESC);
    """)
    op.execute(r"""
        CREATE INDEX ix_candidates_tenant_status_created ON candidates USING btree (tenant_id, status, created_at DESC);
    """)
    op.execute(r"""
        CREATE INDEX ix_connectors_tenant_status ON connectors USING btree (tenant_id, status);
    """)
    op.execute(r"""
        CREATE INDEX ix_dn_parent ON drive_nodes USING btree (tenant_id, user_id, parent_id) WHERE (trashed_at IS NULL);
    """)
    op.execute(r"""
        CREATE INDEX ix_dn_trash ON drive_nodes USING btree (tenant_id, user_id, purge_after) WHERE (trashed_at IS NOT NULL);
    """)
    op.execute(r"""
        CREATE INDEX ix_effect_invocations_unresolved ON effect_invocations USING btree (tenant_id, status, created_at) WHERE (status = ANY (ARRAY['prepared'::text, 'running'::text, 'needs_reconciliation'::text]));
    """)
    op.execute(r"""
        CREATE INDEX ix_event_journal_tenant_correlation ON event_journal USING btree (tenant_id, correlation_id) WHERE (correlation_id IS NOT NULL);
    """)
    op.execute(r"""
        CREATE INDEX ix_event_journal_tenant_type_created ON event_journal USING btree (tenant_id, event_type, created_at DESC);
    """)
    op.execute(r"""
        CREATE INDEX ix_extractions_tenant_status_created ON extractions USING btree (tenant_id, status, created_at);
    """)
    op.execute(r"""
        CREATE INDEX ix_generations_tenant_run ON generations USING btree (tenant_id, run_id, started_at);
    """)
    op.execute(r"""
        CREATE INDEX ix_identities_tenant_user ON identities USING btree (tenant_id, user_id);
    """)
    op.execute(r"""
        CREATE INDEX ix_kc_embedding ON knowledge_chunks USING hnsw (embedding vector_cosine_ops);
    """)
    op.execute(r"""
        CREATE INDEX ix_kc_fts ON knowledge_chunks USING gin (fts);
    """)
    op.execute(r"""
        CREATE INDEX ix_kc_version ON knowledge_chunks USING btree (tenant_id, version_id, ordinal);
    """)
    op.execute(r"""
        CREATE INDEX ix_kij_active ON knowledge_ingestion_jobs USING btree (tenant_id, stage, lease_expires_at) WHERE (stage <> ALL (ARRAY['done'::text, 'failed'::text]));
    """)
    op.execute(r"""
        CREATE INDEX ix_knowledge_sources_file ON knowledge_sources USING btree (tenant_id, file_id) WHERE (file_id IS NOT NULL);
    """)
    op.execute(r"""
        CREATE INDEX ix_knowledge_sources_owner ON knowledge_sources USING btree (tenant_id, user_id, updated_at DESC);
    """)
    op.execute(r"""
        CREATE INDEX ix_kre_gc ON knowledge_retrieval_evidence USING btree (tenant_id, purge_after);
    """)
    op.execute(r"""
        CREATE INDEX ix_ksv_source ON knowledge_source_versions USING btree (tenant_id, source_id, generation DESC);
    """)
    op.execute(r"""
        CREATE INDEX ix_memory_passages_embedding ON memory_passages USING hnsw (embedding vector_cosine_ops);
    """)
    op.execute(r"""
        CREATE INDEX ix_memory_passages_fts ON memory_passages USING gin (fts);
    """)
    op.execute(r"""
        CREATE INDEX ix_memory_passages_tenant_user ON memory_passages USING btree (tenant_id, user_id, created_at DESC);
    """)
    op.execute(r"""
        CREATE INDEX ix_messages_tenant_session_created ON messages USING btree (tenant_id, session_id, created_at);
    """)
    op.execute(r"""
        CREATE INDEX ix_mp_owner ON model_providers USING btree (tenant_id, user_id, updated_at DESC);
    """)
    op.execute(r"""
        CREATE INDEX ix_outbox_ready ON outbox USING btree (available_at, created_at) WHERE (status = ANY (ARRAY['pending'::text, 'publishing'::text]));
    """)
    op.execute(r"""
        CREATE INDEX ix_part_project ON project_artifacts USING btree (tenant_id, project_id, created_at DESC);
    """)
    op.execute(r"""
        CREATE INDEX ix_part_wc ON project_artifacts USING btree (tenant_id, working_copy_id);
    """)
    op.execute(r"""
        CREATE INDEX ix_pcs_project ON project_change_sets USING btree (tenant_id, project_id, created_at DESC);
    """)
    op.execute(r"""
        CREATE INDEX ix_pcs_wc ON project_change_sets USING btree (tenant_id, working_copy_id, created_at DESC);
    """)
    op.execute(r"""
        CREATE INDEX ix_pg_active ON permission_grants USING btree (tenant_id, user_id, tool_name) WHERE (revoked_at IS NULL);
    """)
    op.execute(r"""
        CREATE INDEX ix_pij_project ON project_import_jobs USING btree (tenant_id, project_id);
    """)
    op.execute(r"""
        CREATE INDEX ix_pij_recover ON project_import_jobs USING btree (tenant_id, stage, lease_expires_at) WHERE (stage <> ALL (ARRAY['done'::text, 'failed'::text]));
    """)
    op.execute(r"""
        CREATE INDEX ix_projects_recent ON projects USING btree (tenant_id, user_id, last_activity_at DESC);
    """)
    op.execute(r"""
        CREATE INDEX ix_ps_project ON project_snapshots USING btree (tenant_id, project_id, created_at DESC);
    """)
    op.execute(r"""
        CREATE INDEX ix_pse_blob ON project_snapshot_entries USING btree (tenant_id, user_id, content_hash) WHERE (content_hash IS NOT NULL);
    """)
    op.execute(r"""
        CREATE INDEX ix_pwc_project ON project_working_copies USING btree (tenant_id, project_id, updated_at DESC);
    """)
    op.execute(r"""
        CREATE INDEX ix_pwc_reap ON project_working_copies USING btree (tenant_id, expires_at) WHERE (state = ANY (ARRAY['open'::text, 'ready_for_review'::text]));
    """)
    op.execute(r"""
        CREATE INDEX ix_runs_live_lease ON runs USING btree (tenant_id, lease_expires_at) WHERE (status = 'running'::text);
    """)
    op.execute(r"""
        CREATE INDEX ix_runs_tenant_session_created ON runs USING btree (tenant_id, session_id, created_at DESC) WHERE (session_id IS NOT NULL);
    """)
    op.execute(r"""
        CREATE INDEX ix_runs_tenant_status_created ON runs USING btree (tenant_id, status, created_at);
    """)
    op.execute(r"""
        CREATE INDEX ix_sb_gc ON storage_blobs USING btree (tenant_id, unreferenced_at) WHERE (ref_count = 0);
    """)
    op.execute(r"""
        CREATE INDEX ix_schedule_firings_attention ON schedule_firings USING btree (tenant_id, delivery_outcome, settled_at DESC) WHERE (delivery_outcome = ANY (ARRAY['missed'::text, 'failed'::text, 'unknown'::text]));
    """)
    op.execute(r"""
        CREATE INDEX ix_schedule_firings_ready ON schedule_firings USING btree (available_at, tenant_id, id) WHERE (status = 'pending'::text);
    """)
    op.execute(r"""
        CREATE INDEX ix_schedules_due ON schedules USING btree (next_fire_at, tenant_id, id) WHERE (status = 'active'::text);
    """)
    op.execute(r"""
        CREATE INDEX ix_sessions_project ON sessions USING btree (tenant_id, project_id) WHERE (project_id IS NOT NULL);
    """)
    op.execute(r"""
        CREATE INDEX ix_sessions_tenant_user_activity ON sessions USING btree (tenant_id, user_id, last_activity_at DESC);
    """)
    op.execute(r"""
        CREATE INDEX ix_sse_browse ON session_search_entries USING btree (tenant_id, user_id, occurred_at DESC, id) WHERE (redacted_at IS NULL);
    """)
    op.execute(r"""
        CREATE INDEX ix_sse_cjk_fts ON session_search_entries USING gin (cjk_fts);
    """)
    op.execute(r"""
        CREATE INDEX ix_sse_fts ON session_search_entries USING gin (fts);
    """)
    op.execute(r"""
        CREATE INDEX ix_sse_normalized_trgm ON session_search_entries USING gin (normalized_text gin_trgm_ops);
    """)
    op.execute(r"""
        CREATE INDEX ix_sse_session_anchor ON session_search_entries USING btree (tenant_id, session_id, occurred_at DESC);
    """)
    op.execute(r"""
        CREATE INDEX ix_todos_tenant_status_created ON todos USING btree (tenant_id, status, created_at DESC);
    """)
    op.execute(r"""
        CREATE INDEX ix_traces_tenant_run ON traces USING btree (tenant_id, run_id) WHERE (run_id IS NOT NULL);
    """)
    op.execute(r"""
        CREATE INDEX ix_user_memory_tenant_user ON user_memory USING btree (tenant_id, user_id, updated_at DESC);
    """)
    op.execute(r"""
        CREATE UNIQUE INDEX uq_dn_sibling_name ON drive_nodes USING btree (tenant_id, user_id, parent_id, name) WHERE (trashed_at IS NULL);
    """)
    op.execute(r"""
        CREATE UNIQUE INDEX uq_ghc_owner_active ON github_connections USING btree (tenant_id, user_id) WHERE (status <> 'revoked'::text);
    """)
    op.execute(r"""
        CREATE UNIQUE INDEX uq_messages_client_message_id ON messages USING btree (tenant_id, session_id, client_message_id) WHERE (client_message_id IS NOT NULL);
    """)
    op.execute(r"""
        CREATE UNIQUE INDEX uq_mp_default ON model_providers USING btree (tenant_id, user_id) WHERE is_default;
    """)
    op.execute(r"""
        CREATE UNIQUE INDEX uq_mp_name ON model_providers USING btree (tenant_id, user_id, display_name);
    """)
    op.execute(r"""
        CREATE UNIQUE INDEX uq_pcse_path ON project_change_set_entries USING btree (tenant_id, change_set_id, path);
    """)
    op.execute(r"""
        CREATE UNIQUE INDEX uq_projects_name ON projects USING btree (tenant_id, user_id, name) WHERE (status <> 'deleting'::text);
    """)
    op.execute(r"""
        CREATE UNIQUE INDEX uq_pse_path ON project_snapshot_entries USING btree (tenant_id, snapshot_id, path);
    """)
    op.execute(r"""
        CREATE UNIQUE INDEX uq_psrc_project ON project_sources USING btree (tenant_id, project_id);
    """)
    op.execute(r"""
        CREATE UNIQUE INDEX uq_pwc_live_session ON project_working_copies USING btree (tenant_id, session_id) WHERE (state = ANY (ARRAY['open'::text, 'ready_for_review'::text]));
    """)
    op.execute(r"""
        CREATE UNIQUE INDEX uq_pwce_path ON project_working_copy_entries USING btree (tenant_id, working_copy_id, path);
    """)
    op.execute(r"""
        CREATE UNIQUE INDEX ux_connector_items_latest ON connector_items USING btree (tenant_id, connector_id, provider_item_id) WHERE is_latest;
    """)
    op.execute(r"""
        CREATE UNIQUE INDEX ux_connectors_aead_nonce ON connectors USING btree (tenant_id, kek_id, key_version, nonce) WHERE (nonce IS NOT NULL);
    """)
    op.execute(r"""
        CREATE UNIQUE INDEX ux_event_journal_session_seq ON event_journal USING btree (tenant_id, session_id, session_seq) WHERE (session_id IS NOT NULL);
    """)
    op.execute(r"""
        CREATE UNIQUE INDEX ux_generations_one_per_extraction ON generations USING btree (tenant_id, extraction_id) WHERE (extraction_id IS NOT NULL);
    """)
    op.execute(r"""
        CREATE UNIQUE INDEX ux_schedules_active_digest_channel ON schedules USING btree (tenant_id, user_id, delivery_channel) WHERE ((kind = 'daily_digest'::text) AND (status = 'active'::text));
    """)
    op.execute(r"""
        CREATE UNIQUE INDEX ux_schedules_active_todo_channel ON schedules USING btree (tenant_id, todo_id, reminder_kind, delivery_channel) WHERE ((kind = 'todo_reminder'::text) AND (status = 'active'::text));
    """)
    op.execute(r"""
        CREATE UNIQUE INDEX ux_users_one_active_owner_per_tenant ON users USING btree (tenant_id) WHERE (status = 'active'::text);
    """)
    op.execute(r"""
        CREATE UNIQUE INDEX ux_users_tenant_lower_email ON users USING btree (tenant_id, lower(email));
    """)

    # Runtime-session lookups + the single-writer mirror of the working-copy lease:
    # at most ONE live runtime session per working copy.
    op.execute(
        "CREATE INDEX ix_prs_wc ON project_runtime_sessions "
        "(tenant_id, working_copy_id, created_at DESC);"
    )
    op.execute(
        "CREATE INDEX ix_prs_session ON project_runtime_sessions "
        "(tenant_id, session_id, created_at DESC);"
    )
    op.execute("""
        CREATE UNIQUE INDEX uq_prs_live ON project_runtime_sessions (tenant_id, working_copy_id)
            WHERE state IN ('opening','ready','executing','closing');
    """)
    op.execute("CREATE INDEX ix_per_rs ON project_exec_runs (tenant_id, runtime_session_id, seq);")
    op.execute("CREATE INDEX ix_per_run ON project_exec_runs (tenant_id, run_id);")

    # --- foreign keys -----------------------------------------------------------
    op.execute(r"""
        ALTER TABLE ONLY approval_envelopes
            ADD CONSTRAINT fk_approval_envelopes_authorized_decider FOREIGN KEY (tenant_id, authorized_decider_user_id) REFERENCES users(tenant_id, id) ON DELETE RESTRICT;
    """)
    op.execute(r"""
        ALTER TABLE ONLY approval_envelopes
            ADD CONSTRAINT fk_approval_envelopes_decider FOREIGN KEY (tenant_id, decided_by_user_id) REFERENCES users(tenant_id, id) ON DELETE RESTRICT;
    """)
    op.execute(r"""
        ALTER TABLE ONLY approval_envelopes
            ADD CONSTRAINT fk_approval_envelopes_invocation_class FOREIGN KEY (tenant_id, invocation_id, effect_class) REFERENCES effect_invocations(tenant_id, invocation_id, effect_class) ON DELETE RESTRICT;
    """)
    op.execute(r"""
        ALTER TABLE ONLY approval_envelopes
            ADD CONSTRAINT fk_approval_envelopes_run_session FOREIGN KEY (tenant_id, run_id, session_id) REFERENCES runs(tenant_id, id, session_id) ON DELETE RESTRICT;
    """)
    op.execute(r"""
        ALTER TABLE ONLY approval_envelopes
            ADD CONSTRAINT fk_approval_envelopes_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY audit_receipts
            ADD CONSTRAINT fk_audit_receipts_actor FOREIGN KEY (tenant_id, actor_user_id) REFERENCES users(tenant_id, id) ON DELETE RESTRICT;
    """)
    op.execute(r"""
        ALTER TABLE ONLY audit_receipts
            ADD CONSTRAINT fk_audit_receipts_approval FOREIGN KEY (tenant_id, approval_envelope_id) REFERENCES approval_envelopes(tenant_id, id) ON DELETE RESTRICT;
    """)
    op.execute(r"""
        ALTER TABLE ONLY audit_receipts
            ADD CONSTRAINT fk_audit_receipts_invocation FOREIGN KEY (tenant_id, invocation_id) REFERENCES effect_invocations(tenant_id, invocation_id) ON DELETE RESTRICT;
    """)
    op.execute(r"""
        ALTER TABLE ONLY audit_receipts
            ADD CONSTRAINT fk_audit_receipts_run FOREIGN KEY (tenant_id, run_id) REFERENCES runs(tenant_id, id) ON DELETE RESTRICT;
    """)
    op.execute(r"""
        ALTER TABLE ONLY audit_receipts
            ADD CONSTRAINT fk_audit_receipts_source_event FOREIGN KEY (tenant_id, source_event_id) REFERENCES event_journal(tenant_id, id) ON DELETE RESTRICT;
    """)
    op.execute(r"""
        ALTER TABLE ONLY audit_receipts
            ADD CONSTRAINT fk_audit_receipts_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY candidates
            ADD CONSTRAINT fk_candidates_accepted_todo FOREIGN KEY (tenant_id, accepted_todo_id) REFERENCES todos(tenant_id, id) DEFERRABLE INITIALLY DEFERRED;
    """)
    op.execute(r"""
        ALTER TABLE ONLY candidates
            ADD CONSTRAINT fk_candidates_decider FOREIGN KEY (tenant_id, decided_by_user_id) REFERENCES users(tenant_id, id) ON DELETE RESTRICT;
    """)
    op.execute(r"""
        ALTER TABLE ONLY candidates
            ADD CONSTRAINT fk_candidates_extraction FOREIGN KEY (tenant_id, extraction_id) REFERENCES extractions(tenant_id, id) ON DELETE RESTRICT;
    """)
    op.execute(r"""
        ALTER TABLE ONLY candidates
            ADD CONSTRAINT fk_candidates_generation_extraction FOREIGN KEY (tenant_id, generation_id, extraction_id) REFERENCES generations(tenant_id, id, extraction_id) ON DELETE RESTRICT;
    """)
    op.execute(r"""
        ALTER TABLE ONLY candidates
            ADD CONSTRAINT fk_candidates_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY channel_configs
            ADD CONSTRAINT fk_channel_configs_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY channel_configs
            ADD CONSTRAINT fk_channel_configs_user FOREIGN KEY (tenant_id, user_id) REFERENCES users(tenant_id, id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY channel_thread_state
            ADD CONSTRAINT fk_channel_thread_state_session FOREIGN KEY (tenant_id, session_id) REFERENCES sessions(tenant_id, id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY channel_thread_state
            ADD CONSTRAINT fk_channel_thread_state_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY connector_items
            ADD CONSTRAINT fk_connector_items_connector FOREIGN KEY (tenant_id, connector_id) REFERENCES connectors(tenant_id, id) ON DELETE RESTRICT;
    """)
    op.execute(r"""
        ALTER TABLE ONLY connector_items
            ADD CONSTRAINT fk_connector_items_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY connectors
            ADD CONSTRAINT fk_connectors_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY connectors
            ADD CONSTRAINT fk_connectors_user FOREIGN KEY (tenant_id, user_id) REFERENCES users(tenant_id, id) ON DELETE RESTRICT;
    """)
    op.execute(r"""
        ALTER TABLE ONLY drive_nodes
            ADD CONSTRAINT fk_dn_parent FOREIGN KEY (tenant_id, parent_id) REFERENCES drive_nodes(tenant_id, id) ON DELETE RESTRICT;
    """)
    op.execute(r"""
        ALTER TABLE ONLY drive_nodes
            ADD CONSTRAINT fk_dn_user FOREIGN KEY (tenant_id, user_id) REFERENCES users(tenant_id, id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY drive_versions
            ADD CONSTRAINT fk_dv_node FOREIGN KEY (tenant_id, node_id) REFERENCES drive_nodes(tenant_id, id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY effect_invocations
            ADD CONSTRAINT fk_effect_invocations_run FOREIGN KEY (tenant_id, run_id) REFERENCES runs(tenant_id, id) ON DELETE RESTRICT;
    """)
    op.execute(r"""
        ALTER TABLE ONLY effect_invocations
            ADD CONSTRAINT fk_effect_invocations_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY embedding_profiles
            ADD CONSTRAINT fk_ep_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY event_journal
            ADD CONSTRAINT fk_event_journal_causation FOREIGN KEY (tenant_id, causation_event_id) REFERENCES event_journal(tenant_id, id) ON DELETE RESTRICT;
    """)
    op.execute(r"""
        ALTER TABLE ONLY event_journal
            ADD CONSTRAINT fk_event_journal_run FOREIGN KEY (tenant_id, run_id) REFERENCES runs(tenant_id, id) ON DELETE RESTRICT;
    """)
    op.execute(r"""
        ALTER TABLE ONLY event_journal
            ADD CONSTRAINT fk_event_journal_run_session FOREIGN KEY (tenant_id, run_id, session_id) REFERENCES runs(tenant_id, id, session_id) ON DELETE RESTRICT;
    """)
    op.execute(r"""
        ALTER TABLE ONLY event_journal
            ADD CONSTRAINT fk_event_journal_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY extractions
            ADD CONSTRAINT fk_extractions_item FOREIGN KEY (tenant_id, connector_item_id) REFERENCES connector_items(tenant_id, id) ON DELETE RESTRICT;
    """)
    op.execute(r"""
        ALTER TABLE ONLY extractions
            ADD CONSTRAINT fk_extractions_run FOREIGN KEY (tenant_id, run_id) REFERENCES runs(tenant_id, id) ON DELETE RESTRICT;
    """)
    op.execute(r"""
        ALTER TABLE ONLY extractions
            ADD CONSTRAINT fk_extractions_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY generations
            ADD CONSTRAINT fk_generations_extraction FOREIGN KEY (tenant_id, extraction_id) REFERENCES extractions(tenant_id, id) ON DELETE RESTRICT;
    """)
    op.execute(r"""
        ALTER TABLE ONLY generations
            ADD CONSTRAINT fk_generations_run FOREIGN KEY (tenant_id, run_id) REFERENCES runs(tenant_id, id) ON DELETE RESTRICT;
    """)
    op.execute(r"""
        ALTER TABLE ONLY generations
            ADD CONSTRAINT fk_generations_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY generations
            ADD CONSTRAINT fk_generations_trace FOREIGN KEY (tenant_id, trace_id) REFERENCES traces(tenant_id, id) ON DELETE RESTRICT;
    """)
    op.execute(r"""
        ALTER TABLE ONLY github_connections
            ADD CONSTRAINT fk_ghc_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY github_connections
            ADD CONSTRAINT fk_ghc_user FOREIGN KEY (tenant_id, user_id) REFERENCES users(tenant_id, id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY identities
            ADD CONSTRAINT fk_identities_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY identities
            ADD CONSTRAINT fk_identities_user FOREIGN KEY (tenant_id, user_id) REFERENCES users(tenant_id, id) ON DELETE RESTRICT;
    """)
    op.execute(r"""
        ALTER TABLE ONLY knowledge_chunks
            ADD CONSTRAINT fk_kc_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY knowledge_chunks
            ADD CONSTRAINT fk_kc_version FOREIGN KEY (tenant_id, version_id) REFERENCES knowledge_source_versions(tenant_id, id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY knowledge_ingestion_jobs
            ADD CONSTRAINT fk_kij_source FOREIGN KEY (tenant_id, source_id) REFERENCES knowledge_sources(tenant_id, id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY knowledge_ingestion_jobs
            ADD CONSTRAINT fk_kij_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY knowledge_retrieval_evidence
            ADD CONSTRAINT fk_kre_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY knowledge_sources
            ADD CONSTRAINT fk_ks_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY knowledge_sources
            ADD CONSTRAINT fk_ks_user FOREIGN KEY (tenant_id, user_id) REFERENCES users(tenant_id, id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY knowledge_source_versions
            ADD CONSTRAINT fk_ksv_profile FOREIGN KEY (tenant_id, embedding_profile_id) REFERENCES embedding_profiles(tenant_id, id);
    """)
    op.execute(r"""
        ALTER TABLE ONLY knowledge_source_versions
            ADD CONSTRAINT fk_ksv_source FOREIGN KEY (tenant_id, source_id) REFERENCES knowledge_sources(tenant_id, id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY knowledge_source_versions
            ADD CONSTRAINT fk_ksv_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY memory_passages
            ADD CONSTRAINT fk_memory_passages_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY memory_passages
            ADD CONSTRAINT fk_memory_passages_user FOREIGN KEY (tenant_id, user_id) REFERENCES users(tenant_id, id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY messages
            ADD CONSTRAINT fk_messages_author FOREIGN KEY (tenant_id, author_user_id) REFERENCES users(tenant_id, id) ON DELETE RESTRICT;
    """)
    op.execute(r"""
        ALTER TABLE ONLY messages
            ADD CONSTRAINT fk_messages_run FOREIGN KEY (tenant_id, run_id) REFERENCES runs(tenant_id, id) ON DELETE RESTRICT;
    """)
    op.execute(r"""
        ALTER TABLE ONLY messages
            ADD CONSTRAINT fk_messages_session FOREIGN KEY (tenant_id, session_id) REFERENCES sessions(tenant_id, id) ON DELETE RESTRICT;
    """)
    op.execute(r"""
        ALTER TABLE ONLY messages
            ADD CONSTRAINT fk_messages_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY model_providers
            ADD CONSTRAINT fk_mp_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY model_providers
            ADD CONSTRAINT fk_mp_user FOREIGN KEY (tenant_id, user_id) REFERENCES users(tenant_id, id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY outbox
            ADD CONSTRAINT fk_outbox_event FOREIGN KEY (tenant_id, event_id) REFERENCES event_journal(tenant_id, id) ON DELETE RESTRICT;
    """)
    op.execute(r"""
        ALTER TABLE ONLY outbox
            ADD CONSTRAINT fk_outbox_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY project_artifacts
            ADD CONSTRAINT fk_part_blob FOREIGN KEY (tenant_id, user_id, content_hash) REFERENCES storage_blobs(tenant_id, user_id, content_hash) ON DELETE RESTRICT;
    """)
    op.execute(r"""
        ALTER TABLE ONLY project_artifacts
            ADD CONSTRAINT fk_part_project FOREIGN KEY (tenant_id, project_id) REFERENCES projects(tenant_id, id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY project_artifacts
            ADD CONSTRAINT fk_part_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY project_artifacts
            ADD CONSTRAINT fk_part_user FOREIGN KEY (tenant_id, user_id) REFERENCES users(tenant_id, id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY project_artifacts
            ADD CONSTRAINT fk_part_wc FOREIGN KEY (tenant_id, working_copy_id) REFERENCES project_working_copies(tenant_id, id) ON DELETE SET NULL;
    """)
    op.execute(r"""
        ALTER TABLE ONLY parts
            ADD CONSTRAINT fk_parts_message FOREIGN KEY (tenant_id, message_id) REFERENCES messages(tenant_id, id) ON DELETE RESTRICT;
    """)
    op.execute(r"""
        ALTER TABLE ONLY parts
            ADD CONSTRAINT fk_parts_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY project_change_sets
            ADD CONSTRAINT fk_pcs_base FOREIGN KEY (tenant_id, base_snapshot_id) REFERENCES project_snapshots(tenant_id, id) ON DELETE RESTRICT;
    """)
    op.execute(r"""
        ALTER TABLE ONLY project_change_sets
            ADD CONSTRAINT fk_pcs_project FOREIGN KEY (tenant_id, project_id) REFERENCES projects(tenant_id, id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY project_change_sets
            ADD CONSTRAINT fk_pcs_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY project_change_sets
            ADD CONSTRAINT fk_pcs_wc FOREIGN KEY (tenant_id, working_copy_id) REFERENCES project_working_copies(tenant_id, id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY project_change_set_entries
            ADD CONSTRAINT fk_pcse_cs FOREIGN KEY (tenant_id, change_set_id) REFERENCES project_change_sets(tenant_id, id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY project_change_set_entries
            ADD CONSTRAINT fk_pcse_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY permission_grants
            ADD CONSTRAINT fk_pg_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY permission_grants
            ADD CONSTRAINT fk_pg_user FOREIGN KEY (tenant_id, user_id) REFERENCES users(tenant_id, id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY project_import_jobs
            ADD CONSTRAINT fk_pij_connection FOREIGN KEY (tenant_id, connection_id) REFERENCES github_connections(tenant_id, id) ON DELETE RESTRICT;
    """)
    op.execute(r"""
        ALTER TABLE ONLY project_import_jobs
            ADD CONSTRAINT fk_pij_project FOREIGN KEY (tenant_id, project_id) REFERENCES projects(tenant_id, id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY project_import_jobs
            ADD CONSTRAINT fk_pij_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY projects
            ADD CONSTRAINT fk_projects_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY projects
            ADD CONSTRAINT fk_projects_user FOREIGN KEY (tenant_id, user_id) REFERENCES users(tenant_id, id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY project_snapshots
            ADD CONSTRAINT fk_ps_parent FOREIGN KEY (tenant_id, parent_id) REFERENCES project_snapshots(tenant_id, id) ON DELETE RESTRICT;
    """)
    op.execute(r"""
        ALTER TABLE ONLY project_snapshots
            ADD CONSTRAINT fk_ps_project FOREIGN KEY (tenant_id, project_id) REFERENCES projects(tenant_id, id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY project_snapshots
            ADD CONSTRAINT fk_ps_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY project_snapshot_entries
            ADD CONSTRAINT fk_pse_blob FOREIGN KEY (tenant_id, user_id, content_hash) REFERENCES storage_blobs(tenant_id, user_id, content_hash) ON DELETE RESTRICT;
    """)
    op.execute(r"""
        ALTER TABLE ONLY project_snapshot_entries
            ADD CONSTRAINT fk_pse_snapshot FOREIGN KEY (tenant_id, snapshot_id) REFERENCES project_snapshots(tenant_id, id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY project_snapshot_entries
            ADD CONSTRAINT fk_pse_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY project_sources
            ADD CONSTRAINT fk_psrc_connection FOREIGN KEY (tenant_id, connection_id) REFERENCES github_connections(tenant_id, id) ON DELETE RESTRICT;
    """)
    op.execute(r"""
        ALTER TABLE ONLY project_sources
            ADD CONSTRAINT fk_psrc_project FOREIGN KEY (tenant_id, project_id) REFERENCES projects(tenant_id, id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY project_sources
            ADD CONSTRAINT fk_psrc_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY project_sources
            ADD CONSTRAINT fk_psrc_user FOREIGN KEY (tenant_id, user_id) REFERENCES users(tenant_id, id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY project_working_copies
            ADD CONSTRAINT fk_pwc_base FOREIGN KEY (tenant_id, base_snapshot_id) REFERENCES project_snapshots(tenant_id, id) ON DELETE RESTRICT;
    """)
    op.execute(r"""
        ALTER TABLE ONLY project_working_copies
            ADD CONSTRAINT fk_pwc_project FOREIGN KEY (tenant_id, project_id) REFERENCES projects(tenant_id, id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY project_working_copies
            ADD CONSTRAINT fk_pwc_session FOREIGN KEY (tenant_id, session_id) REFERENCES sessions(tenant_id, id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY project_working_copies
            ADD CONSTRAINT fk_pwc_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY project_working_copies
            ADD CONSTRAINT fk_pwc_user FOREIGN KEY (tenant_id, user_id) REFERENCES users(tenant_id, id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY project_working_copy_entries
            ADD CONSTRAINT fk_pwce_blob FOREIGN KEY (tenant_id, user_id, content_hash) REFERENCES storage_blobs(tenant_id, user_id, content_hash) ON DELETE RESTRICT;
    """)
    op.execute(r"""
        ALTER TABLE ONLY project_working_copy_entries
            ADD CONSTRAINT fk_pwce_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY project_working_copy_entries
            ADD CONSTRAINT fk_pwce_wc FOREIGN KEY (tenant_id, working_copy_id) REFERENCES project_working_copies(tenant_id, id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY runs
            ADD CONSTRAINT fk_runs_admitted_message FOREIGN KEY (tenant_id, session_id, admitted_seq) REFERENCES messages(tenant_id, session_id, seq) DEFERRABLE INITIALLY DEFERRED;
    """)
    op.execute(r"""
        ALTER TABLE ONLY runs
            ADD CONSTRAINT fk_runs_session FOREIGN KEY (tenant_id, session_id) REFERENCES sessions(tenant_id, id) ON DELETE RESTRICT;
    """)
    op.execute(r"""
        ALTER TABLE ONLY runs
            ADD CONSTRAINT fk_runs_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY storage_accounts
            ADD CONSTRAINT fk_sa_user FOREIGN KEY (tenant_id, user_id) REFERENCES users(tenant_id, id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY storage_blobs
            ADD CONSTRAINT fk_sb_user FOREIGN KEY (tenant_id, user_id) REFERENCES users(tenant_id, id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY schedule_firings
            ADD CONSTRAINT fk_schedule_firings_invocation FOREIGN KEY (tenant_id, invocation_id) REFERENCES effect_invocations(tenant_id, invocation_id) ON DELETE RESTRICT;
    """)
    op.execute(r"""
        ALTER TABLE ONLY schedule_firings
            ADD CONSTRAINT fk_schedule_firings_schedule FOREIGN KEY (tenant_id, schedule_id) REFERENCES schedules(tenant_id, id) ON DELETE RESTRICT;
    """)
    op.execute(r"""
        ALTER TABLE ONLY schedule_firings
            ADD CONSTRAINT fk_schedule_firings_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY schedules
            ADD CONSTRAINT fk_schedules_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY schedules
            ADD CONSTRAINT fk_schedules_todo FOREIGN KEY (tenant_id, todo_id) REFERENCES todos(tenant_id, id) ON DELETE RESTRICT;
    """)
    op.execute(r"""
        ALTER TABLE ONLY schedules
            ADD CONSTRAINT fk_schedules_user FOREIGN KEY (tenant_id, user_id) REFERENCES users(tenant_id, id) ON DELETE RESTRICT;
    """)
    op.execute(r"""
        ALTER TABLE ONLY sessions
            ADD CONSTRAINT fk_sessions_admitted_message FOREIGN KEY (tenant_id, id, admitted_seq) REFERENCES messages(tenant_id, session_id, seq) DEFERRABLE INITIALLY DEFERRED;
    """)
    op.execute(r"""
        ALTER TABLE ONLY sessions
            ADD CONSTRAINT fk_sessions_identity FOREIGN KEY (tenant_id, identity_id) REFERENCES identities(tenant_id, id) ON DELETE RESTRICT;
    """)
    op.execute(r"""
        ALTER TABLE ONLY sessions
            ADD CONSTRAINT fk_sessions_model_provider FOREIGN KEY (tenant_id, model_provider_id) REFERENCES model_providers(tenant_id, id) ON DELETE SET NULL;
    """)
    op.execute(r"""
        ALTER TABLE ONLY sessions
            ADD CONSTRAINT fk_sessions_project FOREIGN KEY (tenant_id, project_id) REFERENCES projects(tenant_id, id) ON DELETE RESTRICT;
    """)
    op.execute(r"""
        ALTER TABLE ONLY sessions
            ADD CONSTRAINT fk_sessions_promoted_message FOREIGN KEY (tenant_id, id, promoted_seq) REFERENCES messages(tenant_id, session_id, seq) DEFERRABLE INITIALLY DEFERRED;
    """)
    op.execute(r"""
        ALTER TABLE ONLY sessions
            ADD CONSTRAINT fk_sessions_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY sessions
            ADD CONSTRAINT fk_sessions_user FOREIGN KEY (tenant_id, user_id) REFERENCES users(tenant_id, id) ON DELETE RESTRICT;
    """)
    op.execute(r"""
        ALTER TABLE ONLY schedule_firings
            ADD CONSTRAINT fk_sf_run FOREIGN KEY (tenant_id, run_id) REFERENCES runs(tenant_id, id) ON DELETE SET NULL;
    """)
    op.execute(r"""
        ALTER TABLE ONLY session_search_entries
            ADD CONSTRAINT fk_sse_session FOREIGN KEY (tenant_id, session_id) REFERENCES sessions(tenant_id, id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY session_search_entries
            ADD CONSTRAINT fk_sse_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY todos
            ADD CONSTRAINT fk_todos_source_candidate_backlink FOREIGN KEY (tenant_id, source_candidate_id, id) REFERENCES candidates(tenant_id, id, accepted_todo_id) DEFERRABLE INITIALLY DEFERRED;
    """)
    op.execute(r"""
        ALTER TABLE ONLY todos
            ADD CONSTRAINT fk_todos_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY todos
            ADD CONSTRAINT fk_todos_user FOREIGN KEY (tenant_id, user_id) REFERENCES users(tenant_id, id) ON DELETE RESTRICT;
    """)
    op.execute(r"""
        ALTER TABLE ONLY traces
            ADD CONSTRAINT fk_traces_parent FOREIGN KEY (tenant_id, parent_trace_id) REFERENCES traces(tenant_id, id) ON DELETE RESTRICT;
    """)
    op.execute(r"""
        ALTER TABLE ONLY traces
            ADD CONSTRAINT fk_traces_run FOREIGN KEY (tenant_id, run_id) REFERENCES runs(tenant_id, id) ON DELETE RESTRICT;
    """)
    op.execute(r"""
        ALTER TABLE ONLY traces
            ADD CONSTRAINT fk_traces_session FOREIGN KEY (tenant_id, session_id) REFERENCES sessions(tenant_id, id) ON DELETE RESTRICT;
    """)
    op.execute(r"""
        ALTER TABLE ONLY traces
            ADD CONSTRAINT fk_traces_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY traces
            ADD CONSTRAINT fk_traces_user FOREIGN KEY (tenant_id, user_id) REFERENCES users(tenant_id, id) ON DELETE RESTRICT;
    """)
    op.execute(r"""
        ALTER TABLE ONLY user_memory
            ADD CONSTRAINT fk_user_memory_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY user_memory
            ADD CONSTRAINT fk_user_memory_user FOREIGN KEY (tenant_id, user_id) REFERENCES users(tenant_id, id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY user_settings
            ADD CONSTRAINT fk_user_settings_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY user_settings
            ADD CONSTRAINT fk_user_settings_user FOREIGN KEY (tenant_id, user_id) REFERENCES users(tenant_id, id) ON DELETE CASCADE;
    """)
    op.execute(r"""
        ALTER TABLE ONLY users
            ADD CONSTRAINT fk_users_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE;
    """)

    op.execute("""
        ALTER TABLE ONLY project_runtime_sessions
            ADD CONSTRAINT fk_prs_tenant FOREIGN KEY (tenant_id)
                REFERENCES tenants (tenant_id) ON DELETE CASCADE;
    """)
    op.execute("""
        ALTER TABLE ONLY project_runtime_sessions
            ADD CONSTRAINT fk_prs_project FOREIGN KEY (tenant_id, project_id)
                REFERENCES projects (tenant_id, id) ON DELETE CASCADE;
    """)
    op.execute("""
        ALTER TABLE ONLY project_runtime_sessions
            ADD CONSTRAINT fk_prs_wc FOREIGN KEY (tenant_id, working_copy_id)
                REFERENCES project_working_copies (tenant_id, id) ON DELETE CASCADE;
    """)
    op.execute("""
        ALTER TABLE ONLY project_runtime_sessions
            ADD CONSTRAINT fk_prs_session FOREIGN KEY (tenant_id, session_id)
                REFERENCES sessions (tenant_id, id) ON DELETE CASCADE;
    """)
    op.execute("""
        ALTER TABLE ONLY project_runtime_sessions
            ADD CONSTRAINT fk_prs_user FOREIGN KEY (tenant_id, user_id)
                REFERENCES users (tenant_id, id) ON DELETE CASCADE;
    """)
    op.execute("""
        ALTER TABLE ONLY project_exec_runs
            ADD CONSTRAINT fk_per_tenant FOREIGN KEY (tenant_id)
                REFERENCES tenants (tenant_id) ON DELETE CASCADE;
    """)
    op.execute("""
        ALTER TABLE ONLY project_exec_runs
            ADD CONSTRAINT fk_per_rs FOREIGN KEY (tenant_id, runtime_session_id)
                REFERENCES project_runtime_sessions (tenant_id, id) ON DELETE CASCADE;
    """)


def downgrade() -> None:
    """Empty-development-database downgrade only (data-model.md §Alembic item 5).
    Drops every object this revision created; it is never a backup/restore substitute."""
    op.execute(
        """
        DROP TABLE IF EXISTS
        approval_envelopes,
        audit_receipts,
        candidates,
        channel_configs,
        channel_thread_state,
        connector_items,
        connectors,
        drive_nodes,
        drive_versions,
        effect_invocations,
        embedding_profiles,
        event_journal,
        extractions,
        generations,
        github_connections,
        identities,
        knowledge_chunks,
        knowledge_ingestion_jobs,
        knowledge_retrieval_evidence,
        knowledge_source_versions,
        knowledge_sources,
        memory_passages,
        messages,
        model_providers,
        outbox,
        parts,
        permission_grants,
        project_artifacts,
        project_change_set_entries,
        project_change_sets,
        project_exec_runs,
        project_import_jobs,
        project_runtime_sessions,
        project_snapshot_entries,
        project_snapshots,
        project_sources,
        project_working_copies,
        project_working_copy_entries,
        projects,
        runs,
        schedule_firings,
        schedules,
        session_search_entries,
        sessions,
        storage_accounts,
        storage_blobs,
        tenants,
        todos,
        traces,
        user_memory,
        user_settings,
        users
        CASCADE;
        """
    )
    op.execute("DROP TEXT SEARCH CONFIGURATION IF EXISTS sherpa_text;")
