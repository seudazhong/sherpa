# 03 · 运行时 / 异步 job

> **评审修订（2026-07-19）**：异步 job 优先的长期设计不变；事件总线语义按 [ADR-016](decisions.md) 进一步明确。

这是"云端"区别于"local"的本质：长任务、定时、主动推送、多用户并发。

## 根本形状：异步 job 优先

Local agent 是"请求→阻塞→回答"。Sherpa 的场景**逼你走异步**：

- QQ 一句话可能触发 5 分钟的沙箱任务 → 不能阻塞连接。
- 定时任务、主动推送 → 天生没有"请求方"在等。
- 多用户并发 → web 进程必须无状态、能水平扩。

> **结论**：所有 agent 运行都是**异步 job**。交互式会话靠**事件总线 + SSE/WS 流式**回推，让它"感觉"实时（OpenCode/Kilo 范式）。一套 job 模型同时覆盖交互、长任务、定时、主动。

> 铁律：**调模型前先持久化输入**（OpenCode durable prompt admission）——入站先写库拿 `admitted_seq` 再入队；崩溃了任务还在、可重试。

## 进程/服务拆分（web 永不跑 core 循环）

| 进程 | 职责 | 无状态? |
|---|---|---|
| **Web/API** | 认证、REST/SSE/WS、入站 webhook、入队、回推事件 | ✅ 可水平扩 |
| **Channels listeners** | QQ WebSocket、IMAP 轮询这类**长连接**入站 | 部分有状态 |
| **Workers** | 出队 → 跑 **core 双循环** → 调沙箱/连接器/记忆 → 发事件 | ✅ 可水平扩 |
| **Scheduler** | cron tick、at-most-once、入队 job、主动推送 | ⚠️ **单 leader** |
| **Sandbox** | 代码执行的容器编排（每次运行一个隔离容器） | 隔离 |

**Redis 三用途**：队列（BullMQ/arq）+ 事件总线（pub/sub）+ 锁（session 串行、scheduler 选主）。

> **评审修订（2026-07-19）**：按 [ADR-016](decisions.md)，**PostgreSQL event journal + transactional outbox 是恢复 / 重放 / 流式的真相源**；Redis Streams 仅加速投递，**pub/sub 永不承担正确性关键职责**。SSE 客户端重连时通过 cursor 对 journal 补齐事件。

## 一条消息的完整生命周期

```
① 入站 (web/qq/email/webhook/cron)
      │
② gateway.resolve_inbound → 命中 user/tenant/session（见 02）
      │
③ 持久化输入 (durable admission: 写 messages, 拿 admitted_seq)   ← 崩溃可恢复
      │
④ 入队 run job → 立即返回 (202 / channel ack)                    ← 不阻塞
      ┄┄┄┄┄┄┄┄┄┄┄ 边界：以下在 Worker ┄┄┄┄┄┄┄┄┄┄┄
⑤ Worker claim job (session 锁：同会话串行)
      │
⑥ 跑 core 双循环：装配上下文 → 调模型 → 工具(沙箱/连接器) → 循环
      │   每步 publish 归一化事件 → Redis → Web → SSE/WS → 前端    ← UI 是流的客户端
      │
⑦ 完成：持久化最终结果、滚动 token/成本、（如需）主动 push
```

- **交互式**（Web 聊天）：用户在等 → 前端订阅 SSE 实时看到 ⑥ 的事件流，体验=实时。
- **非交互式**（定时/邮件触发）：没人等 → ⑥ 结束后走 ⑦ 主动推送。
- 同一套 job 机制，区别只在"有没有人订阅流"。

## 并发模型

- **session 内串行**：同一 `umo_key` 同时只跑一个循环（Redis 锁），否则两循环改同一份 transcript 会撕裂。
- **跨 session 并行**：全局并发上限（如 main 4 / subagent 8）。
- 队列 + 锁天然实现"排队 + 不双跑"。
- **原则：serialize in-session, parallelize across sessions.**

## 部署形态

- **docker-compose 单机起步**（"一键部署"）：全部 service 在 compose 里。
- **架构 scale-out-ready**：web/worker 无状态、状态全在共享层、scheduler 用 Redis `SET NX` 选主 → 加机器直接扩 worker，不改代码。
