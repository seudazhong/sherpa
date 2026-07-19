# 07 · 可观测 / 部署

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
| 可观测后端 | **先用自己的 events 表**；专业化再接 **Langfuse**（TS+docker，同栈） |

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
- **迁移单一 owner**：Alembic + 备份（保留 N 份）。
- **选主**：scheduler 用 Redis `SET key host EX ttl NX`。
- **沙箱 socket 安全**：docker socket 只给 `sandbox-orch`；不可信代码在它派生的隔离容器里，不接触 socket。
