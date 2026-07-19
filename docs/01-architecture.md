> **评审修订（2026-07-19）**：下文四层模型与完整进程拓扑描述的是**长期目标**；v1 只运行 **web + worker + scheduler + Postgres + Redis**，不含 MinIO/对象存储与 sandbox，channel 仅含 Web 与出站摘要邮件。详见 [ADR-022](decisions.md)。

# 01 · 架构

## 四层模型（UI 永不碰模型）

```
SURFACES（全部是"不可信入站 + 出站"）
  Web UI(登录/看板) · REST/SSE · 邮件(agentic收件箱 + Gmail推送) · IM(QQ…) · Webhook(GitHub)
      │  ▲ 出站：主动通知走邮件/IM
GATEWAY（多租户前门）
  认证/会话 · 身份链接(web+QQ+邮箱 → 同一用户) · 归一化成一套事件词汇 · 每租户限流 · 路由
AGENT CORE（每会话一个有界循环）
  双循环(agency/resilience) · turn 状态机 · 上下文组装 · 压缩 · 流式事件 · 中断/steer
CAPABILITIES
  工具+权限闸 · provider层(多模型+failover) · 代码沙箱(每次执行一个容器) · 连接器 · 子agent · 调度器(cron,at-most-once)
DURABLE STATE（多租户）
  Postgres(用户/租户/会话/todos/权限/连接器令牌[加密]) · Redis(队列/总线/锁) · 对象存储(每用户文件) · 向量库(记忆/RAG) · 遥测(trace/成本)
```

> **评审修订（2026-07-19）**：按 [ADR-015](decisions.md)，v1 是**单实例、单 owner**；持久状态仍保留 `tenant_id` 列与复合键，作为低成本的前向兼容，但强制 RLS、最小权限数据库角色与 KMS 延后到团队/托管里程碑。

**依赖只能向下。** 这条边界（UI 永不直接调模型；core 不依赖任何 UI）让**一个 core 同时服务 Web + 邮件 + QQ**。违反它 = 每个 surface 重写一遍 agent。

## 进程拓扑（异步 job 优先，详见 [03](03-runtime-async-jobs.md)）

> **评审修订（2026-07-19）**：按 [ADR-016](decisions.md)，**PostgreSQL event journal + transactional outbox 是恢复、重放与流式的真相源**；Redis Streams 仅用于加速投递，**Redis pub/sub 永不承担正确性关键职责**。下图中的“事件总线”应据此理解。

```
                    ┌──────────── SHARED STATE ────────────┐
                    │ Postgres · Redis(队列/事件总线/锁) ·    │
                    │ 对象存储 · 向量库                       │
                    └───────────────────────────────────────┘
  入站                    ▲          ▲            ▲
 ┌──────────┐ webhook/HTTP │          │            │
 │ Web/API  │──────────────┤          │            │
 │(无状态)   │  SSE/WS 出 ◀─┼──订阅事件─┘            │ claim job
 └──────────┘              │ enqueue               │ ▼
 ┌──────────┐ 长连接        │ ▼                ┌──────────┐
 │ Channels │ (QQ WS /     │  [Redis 队列] ───│ Workers  │ 跑 core 双循环
 │ listeners│  IMAP 轮询)──┤                  │ (agent)  │──┐调用
 └──────────┘              │                  └──────────┘  ▼
 ┌──────────┐ cron tick    │                        ┌──────────┐
 │Scheduler │──────────────┘  at-most-once           │ Sandbox  │ 每次一个隔离容器
 │(leader)  │                                        └──────────┘
 └──────────┘
```

## 目录结构（monorepo，1:1 对应四层）

```
sherpa/
├─ docs/                       # 本设计文档
├─ backend/                    # Python core + workers + adapters
│  ├─ gateway/                 # 认证 · 身份链接 · 租户解析 · 事件归一化 · 路由
│  ├─ channels/                # SURFACES: web/rest · email · im(qq) · webhook(github)
│  ├─ core/                    # 双循环 · turn 状态机 · 上下文组装 · 压缩 · 流式事件
│  ├─ providers/               # 模型/provider 层 · failover · 路由
│  ├─ tools/                   # 工具接口 · 内置工具箱 · 权限闸
│  ├─ sandbox/                 # 代码执行编排（起隔离容器）
│  ├─ connectors/              # gmail · github · agentic_email
│  ├─ scheduler/               # cron · at-most-once jobs · 主动推送
│  ├─ memory/                  # core / recall / archival
│  ├─ persistence/             # SQLAlchemy 模型 + Alembic 迁移
│  ├─ observability/           # trace / 成本
│  ├─ workers/                 # Celery/arq 任务
│  └─ api/                     # FastAPI（SSE/WS 入口）
├─ frontend/                   # TS（Next 或 Vite）：登录 · 看板 · 会话 · 文件
├─ sandbox-runner/             # 独立执行镜像（窄腰隔离）
└─ infra/                      # docker-compose · env 模板 · 迁移脚本
```

## 窄腰原则（narrow waist）

保持 core 小；把多样性推到边缘。**内置工具、MCP 工具、子 agent 全部呈现为"吃 JSON 吐 JSON 的工具"**；每个 surface、每个连接器、每个模型 provider 都是 core 之外的适配器。这样**加能力永远不碰循环**。

## 数据面 vs 控制面

- **数据面** = 发给模型的确切字节（system prompt、transcript、工具 schema、记忆）。必须稳定/缓存友好，动态值走尾部。
- **控制面** = 决定发什么、拿回复做什么的代码（循环、预算、权限、failover、压缩、中断）。**正确性在这里，这是我们的代码。**
