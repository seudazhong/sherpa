# persistence

SQLAlchemy 模型 + Alembic 迁移。多租户 Postgres（+pgvector）。

## 铁律
- **每张表带 `tenant_id`**（行级隔离，可 RLS 兜底）。
- OAuth 令牌加密落库。
- **迁移单一 owner** + 备份。

## 主要表
`tenants · users · identities · sessions · runs · messages · parts · connectors · connector_items · candidates · todos · schedules · schedule_firings · event_journal · outbox · effect_invocations · approval_envelopes · permission_grants · audit_receipts · storage_blobs · drive_nodes · user_memory · memory_passages · knowledge_* · projects · project_snapshots · project_working_copies · project_change_sets · project_artifacts · project_runtime_sessions · project_exec_runs · model_providers`。

> **单一 baseline（ADR-045，2026-07-30）**：`migrations/versions/` 只有 `0001_baseline.py`，
> 由原 32 条 revision squash 而成；`files` 表与 `project_sandbox_runs` 已删除
> （见 [../../docs/contracts/data-model.md](../../docs/contracts/data-model.md) §Alembic）。

完整 schema 见 [../../docs/08-data-model.md](../../docs/08-data-model.md)。
