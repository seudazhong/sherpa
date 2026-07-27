# Sherpa

> 一个**多租户云端 Agent 运行时**——个人助理兼小团队协作。为你背负、为你向导。

Sherpa 是一个可 **Docker 一键部署**、支持**多用户登录**的云端 AI Agent。它给每个用户一块**个人存储空间**、一个**代码沙箱**，能连接 **Gmail / GitHub** 等外部服务，拥有**自己的 agentic email**，能登录 **QQ 等 IM**，能设置并执行**定时任务**，能分析连接器内容**智能生成待办**，并通过邮件 / IM **主动给你发通知**。

## 当前状态

🟢 **可运行的代码库**——后端（Python core + workers + api）、前端（Vite + React + TS SPA）、基础设施（docker-compose）与 CI 均已就绪，可 `docker compose` 一键起栈。

上面的介绍是**长期愿景**。经三方评审（PM/UI/架构师）并由负责人拍板，**v1 收窄为**：

> **自托管、单实例、单用户的 Gmail → Action 助理**——只读 Gmail → 私有候选待办（带来源）→ accept/edit/dismiss → opt-in 提醒；保留 Web 聊天为次要界面。

**已落地**：可持久化的 core 双循环（事件日志 + outbox + effect 幂等）、真实 provider 接入（OpenAI-兼容，默认 `claude-sonnet-4.6`；`mock` 为离线默认）、REST 认证与会话/消息面、AEAD 密钥保险箱、run trace + 结构化日志，以及 Web 聊天与各功能页（收件箱 / 活动 / 定时任务 / 审批 / 记忆 / 会话库 / 个人网盘 / 项目）。里程碑 **M1–M5 已完成并端到端验证**；post-v1 若干方向（分层记忆、个人网盘、知识库、项目工作空间、通用 cron、审批闭环、可观测）也已推进。

沙箱/代码执行、GitHub 写动作、QQ/IM 入站、agentic email、团队协作等**仍属后续里程碑**。**最新进度以 [`docs/STATUS.md`](docs/STATUS.md) 为准**；范围与决策见 ⭐[设计评审汇总](docs/reviews/README.md)、[里程碑](docs/09-roadmap.md) 与 [ADR-022](docs/decisions.md)。

## 快速开始

先复制环境模板（`.env` 已 gitignore，切勿提交）：

```bash
cp .env.example .env    # 按需填写 provider / 密钥；默认 PROVIDER_KIND=mock 可离线跑
```

一键起栈（Postgres + Redis + web + worker + 前端）：

```bash
docker compose -f infra/docker-compose.yml --env-file .env up --build -d
```

本地开发：

```bash
# 后端（backend/，使用 uv）
uv sync
uv run alembic upgrade head               # 迁移
uv run uvicorn app.main:app --reload      # web
uv run arq app.worker.WorkerSettings      # worker
uv run pytest                             # 测试

# 前端（frontend/，使用 npm）
npm ci
npm run dev
```

> 开发约定、Definition of Done 与提交规范见 [`AGENTS.md`](AGENTS.md)。

## 文档导航

| 文档 | 内容 |
|---|---|
| ⭐ [STATUS 项目状态](docs/STATUS.md) | **当前阶段 · 已完成/下一步 · 实时进度（先看这个）** |
| [AGENTS.md 开发约定](AGENTS.md) | 命令 · Definition of Done · 提交规范 · 护栏 |
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
| [决策记录 ADR](docs/decisions.md) | 所有已锁定的架构决策 + 理由 |
| ⭐ [设计评审汇总](docs/reviews/README.md) | **PM / UI / 架构师三方评审结论 + 待办改动清单（编码前必读）** |
| ⭐ [UI 设计图样 · Daybreak 亮色](docs/design-bright/index.html) | 8 屏离线原型 + [范围说明 v1/后续](docs/design-bright/README.md) |
| [UI 设计图样 · Alpine 深色（暗色主题）](docs/design/index.html) | 初版 5 屏离线原型 · [对比](docs/reviews/ui-comparison.md) |
| [生产 UI · Quiet Work](docs/design-refined/README.md) | 落地的 Notion 风克制设计系统（当前前端方向） |

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
