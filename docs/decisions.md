# 决策记录（ADR）

本项目讨论中**已锁定**的架构决策，及其理由与替代方案。新决策追加到末尾。

---

### ADR-001 · 做云端 agent，不做 local agent
- **决策**：Sherpa 是多租户云端 Agent 运行时，非本地 CLI agent。
- **理由**：需求包含多用户登录、云端沙箱、定时任务、主动推送、IM/邮箱通道——本质是长驻服务。
- **来源**：用户明确要求。

### ADR-002 · 技术栈 = Python core + TS 前端
- **决策**：后端主语言 Python；前端 TypeScript。
- **理由**：对本项目的重资产（QQ/IM 适配=AstrBot、记忆/RAG=Letta、多租户沙箱+连接器=Dify、agentic email+cron+failover=Hermes）Python 生态可白嫖最多。FastAPI+asyncio+Celery/arq 撑住个人+小团队规模。
- **替代**：全 TS（借 OpenClaw 网关，但 QQ/记忆生态弱）；Go（参考代码少）。
- **窄腰兜底**：IM 适配器/沙箱执行器可后续用别的语言做独立服务。

### ADR-003 · 身份/会话 = tenant/user/identity + UMO 会话键
- **决策**：四层概念分离；会话键格式 `channel:type:external_id`；`identities` 表做身份链接；所有入口汇入 `resolve_inbound()`。
- **理由**：解决"一人多入口"——同一人从 Web/QQ/邮箱进来归到同一 user、同一记忆、同一 todos。
- **来源**：AstrBot UMO + OpenClaw identity links。

### ADR-004 · 两层记忆（user 私有 + tenant 共享）
- **决策**：记忆分 user 级私有 block 与 tenant 级共享 block；个人=单人工作区，与团队同一套 schema。
- **理由**：个人助理与小团队协作统一，不必来回改 schema。共享 block 保持小（编辑会 rebuild 所有成员 prompt）。
- **来源**：Letta memory blocks（many-to-many attach）。

### ADR-005 · 运行时 = 异步 job 优先 + 事件总线流式
- **决策**：所有 agent 运行都是异步 job；交互式靠 SSE/WS 从事件总线回推。web 进程永不跑 core 循环。
- **理由**：长任务/定时/主动推送/多用户并发都要求非阻塞、无状态 web、可水平扩。
- **铁律**：调模型前先持久化输入（durable prompt admission）。
- **来源**：OpenCode/Kilo 事件总线 + durable admission。

### ADR-006 · core 循环 = 递归/生成器 ReAct + turn 粒度持久化（方案 A）
- **决策**：双循环（外层 agency / 内层 resilience）；stop-reason 闸；每个 turn 落库，崩溃从最后完成的 turn 重跑。
- **理由**：练手项目要够健壮又不过度工程；intra-turn 可恢复（方案 B，显式状态机）代码量大得多，后置。
- **替代**：方案 B（Claude Code 风格显式状态机，intra-turn 可恢复）。

### ADR-007 · 沙箱 = ephemeral 每次一容器 + 持久 workspace 卷 + 默认断网 + Docker 后端
- **决策**：文件持久（对象存储/命名卷）；每次代码执行起一个全新隔离容器；默认 `--network none`；后端先用 Docker-per-run。
- **理由**：干净（无跨运行状态泄漏）、安全、compose 友好。启动开销用容器池预热缓解。
- **替代/演进**：persistent 每用户常驻容器（跑 dev server 时后加）；gVisor/Firecracker（跑不可信第三方代码时加固）。

### ADR-008 · 权限 `ask` = 异步 HITL（走事件总线）
- **决策**：工具授权走四道闸；`ask` 发 `permission.asked` 事件到任意 surface 渲染审批（correlation-id 协议）。权限代数：last-match 胜，`deny>ask>allow`，默认 `ask`。
- **理由**：cloud 多端场景下，一个远程 QQ 就能批准 Worker 里的操作，跨端一致。
- **来源**：OpenClaw/Kilo 权限引擎 + Ch11 approval bus。

### ADR-009 · 信任分级工具集（SAFE vs FULL）
- **决策**：不可信入口（email/webhook）的会话只发 SAFE 工具集（只读，无 shell/沙箱）；已认证用户（web/QQ）发 FULL。工具集 turn 开始时定死、中途不变。
- **理由**：防提示注入升级为代码执行；同时保住 prompt 缓存。
- **来源**：Hermes webhook 平台"受限安全工具集"。

### ADR-010 · 自治边界 = 读+建todo+通知 全自动，对外代表用户走审批
- **决策**：连接器读取、建 todo、发通知全自动；代表用户发邮件/回复/建 issue 等对外动作走 `ask` 审批；沙箱写/跑代码走权限闸。
- **理由**：低风险可撤销的自动化提效；高风险对外可见动作需人确认。
- **来源**：Ch11 autonomy ladder。

### ADR-011 · 调度 = at-most-once（先推进游标 + 原子领取）+ 推送幂等
- **决策**：scheduler 单 leader（Redis `SET NX`）；领取到期任务时在同一事务推进 `next_run_at`；`FOR UPDATE SKIP LOCKED` 防双领；主动推送用 `sent_log` 幂等键。
- **理由**：即使崩溃也绝不重复触发/重复发通知。
- **来源**：Hermes cron at-most-once + Ch15。

### ADR-012 · 存储选型 = Postgres(+pgvector) + Redis + MinIO
- **决策**：主库 Postgres；向量用 pgvector（复用 PG）；队列/总线/锁用 Redis；对象存储用 MinIO。
- **理由**：为"docker 一键部署"服务，尽量少起服务。无状态设计让将来拆分（Qdrant/独立 broker）不改上层。

### ADR-013 · agentic email 与用户 Gmail 是两种信任级别
- **决策**：Gmail 连接器 = 读用户账户数据（OAuth 最小 scope、只读优先）；agentic email = agent 自有通信身份（收指令/发通知）。两者内容都不可信→SAFE 工具集。
- **理由**：区分"账户访问可信"与"内容可信"；agent 自有邮箱天然隔离、不碰用户私人号。

### ADR-014 · 项目命名 Sherpa
- **决策**：项目名为 Sherpa。
- **理由**：为你背负/向导的助手，贴合"个人+团队全能云助理"定位。
- **来源**：用户选择。
