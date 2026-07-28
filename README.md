# Sherpa

> 一个**多租户云端 Agent 运行时**——个人助理兼小团队协作。为你背负、为你向导。

Sherpa 是一个可 **Docker 一键部署**、支持**多用户登录**的云端 AI Agent。它给每个用户一块**个人存储空间**、一个**代码沙箱**，能连接 **Gmail / GitHub** 等外部服务，拥有**自己的 agentic email**，能登录 **QQ 等 IM**，能设置并执行**定时任务**，能分析连接器内容**智能生成待办**，并通过邮件 / IM **主动给你发通知**。

## 当前状态

🟢 **可运行的代码库**——后端（Python core + workers + api）、前端（Vite + React + TS SPA）、基础设施（docker-compose）与 CI 均已就绪，可 `docker compose` 一键起栈。

上面的介绍是**长期愿景**。经三方评审（PM/UI/架构师）并由负责人拍板，**v1 收窄为**：

> **自托管、单实例、单用户的 Gmail → Action 助理**——只读 Gmail → 私有候选待办（带来源）→ accept/edit/dismiss → opt-in 提醒；保留 Web 聊天为次要界面。

**已落地**（均已端到端验证）：

- **core 与数据面**：可持久化的双循环（事件日志 + outbox + effect 幂等 + 有界恢复）、跨 run 的工具历史重建、REST 认证与会话/消息面、AEAD 密钥保险箱、Redis Streams + SSE 流式。
- **模型接入**：用户可配置的**多来源模型 provider**（ADR-041；OpenAI-兼容 / Anthropic / Gemini，设置页管理 + 每个会话切换模型），`mock` 为离线默认。
- **v1 主线**：Gmail 只读同步 → 无工具抽取候选 → accept/edit/dismiss → 待办与提醒；四道闸权限 + 审批页 + **预授权 grants**（ADR-034）。
- **post-v1 里程碑 M1–M5**：分层记忆（core blocks + 本地 ollama/bge-m3 向量笔记）、个人网盘（Drive，内容寻址 + 配额 + 回收站）、代码沙箱、**QQ 官方平台入站**（ADR-028）、**agentic email**（AgentMail 收发 + 邮件内审批）。
- **通用定时任务**（ADR-031）：cron/interval/weekly/monthly，`agent_task` 每次独立会话触发 + 投递结果 + 运行历史。
- **知识库**（ADR-036，`/library`）：Drive 文件 → 不可变快照 → 解析/切块/嵌入 → zhparser+pgvector 混合检索 + 引用；聊天内 `[K:…]` 引用角标。
- **工作空间项目**（ADR-037/038/039/040，`/work/projects`）：空白/模板/归档导入、GitHub 一次性只读导入、任务工作副本（租约 + fence + CAS）、只挂一次性 scratch 的硬化沙箱、变更评审（diff → Save/Discard，人工专属）。
- **可观测**（ADR-033）：OTel `gen_ai` span + 每次模型调用的结构化日志与脱敏 journal 事件；可选 Phoenix 容器查看完整 prompt。
- **Web 前端**：`Quiet Work` 设计系统，桌面 + 390px 移动端；页面含 Today / 审批 / 会话库 / 活动 / 定时任务 / 记忆 / 知识库 / 网盘 / 项目 / 消息 / 连接器 / 设置。

**仍未做**：GitHub 写动作与 PR（W4）、团队/多用户协作、插件与 MCP、多 provider 故障切换与子 agent、eval 飞轮。**最新进度以 [`docs/STATUS.md`](docs/STATUS.md) 为准**，手工测试发现的未排期问题见 [`docs/backlog.md`](docs/backlog.md)；范围与决策见 ⭐[设计评审汇总](docs/reviews/README.md)、[里程碑](docs/09-roadmap.md) 与 [ADR-022](docs/decisions.md)。

## 快速开始

先复制环境模板（`.env` 已 gitignore，切勿提交）：

```bash
cp .env.example .env    # 按需填写 provider / 密钥；默认 PROVIDER_KIND=mock 可离线跑
```

一键起栈（Postgres + Redis + MinIO + web + worker + 前端）：

```bash
docker compose -f infra/docker-compose.yml --env-file .env up --build -d
```

前端 http://localhost:5173 · API http://localhost:8000。可选 profile：

```bash
# 本地嵌入模型（知识库 / 记忆检索需要）：ollama + bge-m3
docker compose -f infra/docker-compose.yml --env-file .env --profile embeddings up -d
# 可观测：自托管 Phoenix，查看每次 LLM 调用的完整 prompt（UI http://localhost:6006）
docker compose -f infra/docker-compose.yml --env-file .env --profile observability up -d
```

本地开发：

```bash
# 后端（backend/，使用 uv）
uv sync
uv run alembic upgrade head               # 迁移（当前 head 0031）
uv run uvicorn app.main:app --reload      # web
uv run arq app.worker.WorkerSettings      # worker
uv run pytest                             # 测试（⚠️ 见 docs/backlog.md B-9：目前会清空开发库的 owner 租户，跑之前先停 worker）
uv run ruff check . && uv run ruff format --check . && uv run mypy app

# 前端（frontend/，使用 npm）
npm ci
npm run dev
```

> 开发约定、Definition of Done 与提交规范见 [`AGENTS.md`](AGENTS.md)。

## 界面地图

SPA 路由刻意避开 API 代理前缀（如 `/sessions`、`/knowledge`），因此名字与后端资源不同名：

| 路由 | 页面 | 说明 |
|---|---|---|
| `/` | Chat | 会话、SSE 流式、审批卡片、知识引用、每会话模型切换 |
| `/today` | Today | 候选建议、待办跟进、通知汇总（原 Inbox，`/inbox` 会重定向） |
| `/approvals` | 审批 | 待审批动作 + 预授权 grants 管理 |
| `/history` | 会话库 | 会话浏览/搜索（含中文分词）+ Resume/Reconnect/Recover |
| `/library` | 知识库 | 从 Drive 添加来源、索引状态、检索测试与引用 |
| `/workspace` | 网盘 Drive | 文件夹/上传/版本/回收站/配额 |
| `/work/projects` | 项目 | 创建/导入项目（含 GitHub 只读导入与连接管理）、只读文件树、Open-in-Chat、变更评审 |
| `/remember` | 记忆 | core memory 块 + 语义笔记 |
| `/reminders` | 定时任务 | cadence 配置、Run now、暂停/恢复、运行历史 |
| `/data` | 活动 | 事件回执、数据导出/删除 |
| `/messaging` | 消息 | QQ / 邮件渠道 |
| `/integrations` | 连接器 | Gmail 授权与同步、QQ 绑定 |
| `/preferences` | 设置 | 模型来源（provider）、通知偏好等 |

## 文档导航

| 文档 | 内容 |
|---|---|
| ⭐ [STATUS 项目状态](docs/STATUS.md) | **当前阶段 · 已完成/下一步 · 实时进度（先看这个）** |
| [IMPLEMENTATION 任务分解](docs/IMPLEMENTATION.md) | 按阶段的可执行任务清单与依赖 |
| [BACKLOG 待排期](docs/backlog.md) | 手工测试发现、尚未排期的问题（B-1…B-9） |
| [AGENTS.md 开发约定](AGENTS.md) | 命令 · Definition of Done · 提交规范 · 护栏 |
| ⭐ [冻结契约 contracts/](docs/contracts/) | 数据模型 · 事件与副作用 · API · 配置与密钥（实现以此为准） |
| [00 总览](docs/00-overview.md) | 定位、目标、需求清单、家族归属 |
| [01 架构](docs/01-architecture.md) | 四层架构 · 进程拓扑 · 目录结构 · 窄腰原则 |
| [02 身份/会话/记忆](docs/02-identity-session-memory.md) | 租户/用户/身份链接 · UMO 会话键 · 两层记忆 · 一人多入口 |
| [03 运行时/异步 job](docs/03-runtime-async-jobs.md) | 异步 job 优先 · 事件总线 · 请求生命周期 · 并发 |
| [04 core 循环](docs/04-core-loop.md) | 双循环 · stop-reason 闸 · 上下文装配 · 护栏 · 恢复 |
| [05 工具/权限/沙箱](docs/05-tools-permissions-sandbox.md) | 工具接口 · 四道闸 · 异步 HITL · 多租户沙箱 |
| [06 连接器/自治](docs/06-connectors-autonomy.md) | 连接器 · agentic email · at-most-once 调度 · 主动推送 |
| [07 观测/部署](docs/07-observability-deployment.md) | 事件溯源即观测 · compose 编排 · 存储选型 |
| [08 数据模型](docs/08-data-model.md) | 多租户 schema |
| [09 路线图](docs/09-roadmap.md) | P0–P6 分阶段构建 |
| [10 技术栈](docs/10-tech-stack.md) | 语言/框架/存储选型锁定 |
| [11 Agent 工具面](docs/11-agent-tool-surface.md) | 工具清单 + §9 能力矩阵（REST / 工具 / **UI** 三列） |
| [决策记录 ADR](docs/decisions.md) | 所有已锁定的架构决策 + 理由（当前至 ADR-041） |
| ⭐ [设计评审汇总](docs/reviews/README.md) | **PM / UI / 架构师三方评审结论 + 待办改动清单（编码前必读）** |
| [生产 UI · Quiet Work](docs/design-refined/README.md) | 落地的 Notion 风克制设计系统（当前前端方向） |
| [静态设计稿](docs/design-bright/README.md) | [Daybreak 亮色](docs/design-bright/index.html) · [知识库](docs/design-knowledge/README.md) · [工作空间/项目](docs/design-workspace/README.md) · [会话库](docs/design-session-library/README.md) · [模型来源设置](docs/design-settings-models/README.md) |
| [研究报告 research/](docs/research/) | 会话搜索 · 工作空间产品 · 知识库 · 记忆 · 可观测 · 模型来源 |

## 一句话架构

```
① 定位     多租户云端 Agent 运行时(运行时/网关 + 记忆server + 应用平台 + 沙箱 混血)
② 技术栈    Python core + TS 前端
③ 身份/会话  tenant/user/identity/UMO会话键 + 两层记忆
④ 运行时     异步 job 优先 + 事件总线流式 + web/worker/scheduler/sandbox 拆分
⑤ core循环   双循环 + stop-reason闸 + turn粒度持久化 + 分层缓存上下文
⑥ 工具/沙箱  四道闸 + 异步HITL权限 + ephemeral容器/断网/Docker + 信任分级工具集
⑦ 连接器/自治 统一连接器 + agentic email + at-most-once调度 + 自治边界
⑧ 观测/部署  事件溯源即观测 + docker-compose(pgvector+MinIO+Redis) 一键
```
