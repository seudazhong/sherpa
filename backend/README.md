# backend

Python 后端：core + workers + adapters。对应架构四层的 GATEWAY / CORE / CAPABILITIES / DURABLE STATE。

见 [../docs/01-architecture.md](../docs/01-architecture.md)。

## 子目录职责

| 目录 | 职责 | 文档 |
|---|---|---|
| `gateway/` | 认证 · 身份链接 · 租户解析 · 事件归一化 · 路由（`resolve_inbound`） | [02](../docs/02-identity-session-memory.md) |
| `channels/` | SURFACES 适配器：web/rest · email · im(qq) · webhook(github) | [02](../docs/02-identity-session-memory.md) [03](../docs/03-runtime-async-jobs.md) |
| `core/` | 双循环 · turn 状态机 · 上下文组装 · 压缩 · 流式事件 | [04](../docs/04-core-loop.md) |
| `providers/` | 模型/provider 层 · failover · 路由 | [04](../docs/04-core-loop.md) |
| `tools/` | 工具接口 · 内置工具箱 · 权限闸（四道闸） | [05](../docs/05-tools-permissions-sandbox.md) |
| `sandbox/` | 代码执行编排（起隔离容器） | [05](../docs/05-tools-permissions-sandbox.md) |
| `connectors/` | gmail · github · agentic_email（统一抽象） | [06](../docs/06-connectors-autonomy.md) |
| `scheduler/` | cron · at-most-once jobs · 主动推送 | [06](../docs/06-connectors-autonomy.md) |
| `memory/` | core / recall / archival 三层记忆 | [02](../docs/02-identity-session-memory.md) |
| `persistence/` | SQLAlchemy 模型 + Alembic 迁移 | [08](../docs/08-data-model.md) |
| `observability/` | trace / 成本 / 事件投影 | [07](../docs/07-observability-deployment.md) |
| `workers/` | Celery/arq 任务（消费 job → 跑 core） | [03](../docs/03-runtime-async-jobs.md) |
| `api/` | FastAPI（SSE/WS 入口，无状态） | [03](../docs/03-runtime-async-jobs.md) |
