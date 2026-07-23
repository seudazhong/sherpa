# 07 · 可观测 / 部署

> **评审修订说明（2026-07-19）**：事件溯源式可观测现与 [ADR-016](decisions.md) 的 event journal 对齐；本页保留长期部署设计，已确认的 v1 子集见下方修订。

## 可观测（大部分白嫖自事件溯源）

core 已在向总线发归一化事件（见 [04](04-core-loop.md)），**这些事件本身就是可观测性原语**（OpenCode："事件存储即观测原语"）。trace 几乎白送。

```
TRACE(一次 run: tenant_id·user_id·session_id·tags)
  ├─ OBSERVATION generation  → model·in/out tokens·cost·latency·prompt_version
  ├─ OBSERVATION tool        → run_code / connector_gmail
  ├─ OBSERVATION retriever   → 向量检索
  └─ SCORE ×N                → 反馈/评估/标注
```

| 要素 | 落地 |
|---|---|
| trace/observation | 从事件流投影（`traces` / `generations` 表） |
| 成本核算 | 滚进 `sessions.cost_rollup`；子 agent 成本→父 |
| 评估（练手够用） | 先攒 trace → 挑失败案例 → 回归数据集；judge/断言后加 |
| 可观测后端 | **先用自己的 events 表**；专业化再接 OTLP 后端。**[ADR-033](decisions.md) 修订默认：自托管 Phoenix**（单容器、复用现有 Postgres、OTLP 原生）优先于 Langfuse（现为 6 服务含 ClickHouse，单用户过重）；线格式用 **OpenTelemetry `gen_ai`**，后端可换。 |

> **评审修订（2026-07-19）**：按 [ADR-016](decisions.md)，上文「事件流 / events 表」在 v1 特指 PostgreSQL 有序 append-only event journal + 事务性 outbox；Redis Streams 只加速投递，pub/sub 不承担正确性。

> **评审修订（2026-07-19）**：按 [ADR-021](decisions.md)，另建稳定、脱敏、append-only 的**审计回执**，与可能含密钥且 schema 易变的原始 debug/telemetry 事件分离；原始遥测不得成为公共审计 API，仅授权诊断可按受控引用追溯。

> 铁律：每个 generation 记 model/tokens/cost/latency/**prompt 版本**；实验要 pin 数据集+prompt+model 版本。练手阶段先埋点，评估飞轮后置。

## 部署编排（docker-compose 一键）

```yaml
services:
  web          # FastAPI: 认证·REST/SSE/WS·webhook入站·入队·事件回推   [无状态,可多副本]
  worker       # agent runner: 出队→core循环→沙箱/连接器             [无状态,可多副本]
  scheduler    # leader: cron tick·at-most-once·主动推送              [单副本,Redis选主]
  channels     # QQ WebSocket / IMAP 轮询 长连接监听
  sandbox-orch # 编排隔离容器(持 docker socket,只跑我方可信代码)
  postgres     # durable: 用户/会话/todos/连接器令牌/记忆/遥测 + pgvector
  redis        # 队列 + 事件总线 + 锁 + 选主
  minio        # 对象存储: 每用户文件(S3 兼容)
  frontend     # TS: 登录·看板·会话·文件
  # langfuse   # 可选,后加
```

> **评审修订（2026-07-19）**：按 [ADR-012/022](decisions.md)，v1 受支持后端栈为 **Postgres + Redis + web + worker**（另含 frontend）；**MinIO、pgvector/RAG、sandbox-orch 与 QQ/IM、agentic email 等额外 channels 均推迟**。上表保留的是长期编排蓝图。

> **评审修订（2026-07-19）**：按 [ADR-017](decisions.md)，上表 scheduler 的 at-most-once 语义已由「唯一 firing + outbox + at-least-once + 幂等/对账投递」取代；missed/failed/unknown 必须可见。

**一个 core，多 surface**：FastAPI 暴露核心，frontend + channels 都是它的客户端。

## 存储选型（为"一键"服务，能少一个服务是一个）

| 需求 | 选型 | 理由 |
|---|---|---|
| 向量库 | **pgvector**（复用 Postgres） | 少起一个服务；Letta 同款 |
| 对象存储 | **MinIO** | S3 兼容，compose 友好 |
| 队列/总线/锁 | **Redis** | 三合一 |
| 关系库 | **Postgres** | 多租户主库 |

将来量大再把向量拆到 Qdrant、队列换独立 broker——**无状态设计让这些都能后换不改上层**。

## 配置 / 密钥 / 迁移 / 选主

- **配置分层**：env + config 文件，secrets 分离（master key 从 env/KMS）。

> **评审修订（2026-07-19）**：按 [ADR-019](decisions.md)，OAuth secret 必须在 callback 当场逐记录 AEAD 加密，并由可轮换 KEK 包裹；仅连接器拥有解密权，日志必须脱敏。v1 单 owner 可推迟托管 KMS，但不能推迟此密文契约。

- **迁移单一 owner**：Alembic + 备份（保留 N 份）。
- **选主**：scheduler 用 Redis `SET key host EX ttl NX`。
- **沙箱 socket 安全**：docker socket 只给 `sandbox-orch`；不可信代码在它派生的隔离容器里，不接触 socket。
