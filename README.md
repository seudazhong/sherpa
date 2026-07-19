# Sherpa

> 一个**多租户云端 Agent 运行时**——个人助理兼小团队协作。为你背负、为你向导。

Sherpa 是一个可 **Docker 一键部署**、支持**多用户登录**的云端 AI Agent。它给每个用户一块**个人存储空间**、一个**代码沙箱**，能连接 **Gmail / GitHub** 等外部服务，拥有**自己的 agentic email**，能登录 **QQ 等 IM**，能设置并执行**定时任务**，能分析连接器内容**智能生成待办**，并通过邮件 / IM **主动给你发通知**。

## 当前阶段

🟡 **设计阶段（评审已完成，v1 定位已确认）**。本仓库目前只含**设计文档**与**目录骨架**，尚未开始编码。

上面的介绍是**长期愿景**。经三方评审（PM/UI/架构师）并由负责人拍板，**v1 已收窄为**：

> **自托管、单实例、单用户的 Gmail → Action 助理**——只读 Gmail → 私有候选待办（带来源）→ accept/edit/dismiss → opt-in 提醒；保留 Web 聊天为次要界面。

沙箱、GitHub、QQ、agentic email、团队协作等**推迟到后续里程碑**。详见 ⭐[设计评审汇总](docs/reviews/README.md)、[里程碑](docs/09-roadmap.md) 与 [ADR-022](docs/decisions.md)。

## 文档导航

| 文档 | 内容 |
|---|---|
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
| [UI 设计图样](docs/design/index.html) | 可离线预览的 HTML 原型（chat / todo / connectors / dashboard） |

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

## 设计出处

架构综合自对 19 个开源 AI Agent 项目的源码级 deep-dive（见 `../ai-docs/`），特别参考：
**Hermes**（运行时/agentic email/at-most-once 调度/failover）、**OpenClaw**（网关/channels/SQLite-first/工具策略）、**Dify**（多租户/沙箱/连接器/SSRF 代理）、**Letta**（记忆分层/agent 即实体）、**AstrBot**（QQ 适配/UMO 键）、**OpenCode**（durable prompt admission/事件溯源）、**Langfuse/Phoenix**（可观测/评估）。
