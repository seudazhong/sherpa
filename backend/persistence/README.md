# persistence

SQLAlchemy 模型 + Alembic 迁移。多租户 Postgres（+pgvector）。

## 铁律
- **每张表带 `tenant_id`**（行级隔离，可 RLS 兜底）。
- OAuth 令牌加密落库。
- **迁移单一 owner** + 备份。

## 主要表
`tenants · users · memberships · identities · sessions · messages · parts · permissions · connectors · schedules · sent_log · sandbox_runs · memory_blocks · memory_passages · files · todos · todo_deps · traces · generations · scores`。

完整 schema 见 [../../docs/08-data-model.md](../../docs/08-data-model.md)。
