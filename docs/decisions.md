# 决策记录（ADR）

本项目讨论中**已锁定**的架构决策，及其理由与替代方案。新决策追加到末尾。

> ✅ **v1 定位已确认（2026-07-19，项目负责人拍板）**：v1 = **自托管、单实例、单用户**的 **Gmail → Action** 助理；**保留 Web 聊天**为次要界面（Candidate Inbox 为主）。据此，下列受影响 ADR 已按[设计评审汇总](reviews/README.md) §4 落地修订（见各 ADR 下「评审修订」块，以及文末新增 ADR-015~023）。
>
> 仍待确认的**实现参数**（不阻塞架构，编码时定）：Gmail OAuth 运营模式、Gmail 数据保留范围、初始 model/provider、通知默认值。见 reviews/README.md §5。

## 决策日志（Decisions Log）

| 日期 | 决策点 | 答复 | 影响 |
|---|---|---|---|
| 2026-07-19 | v1 产品定位 | ✅ 接受「自托管、单用户 Gmail→Action」 | 新增 ADR-022 |
| 2026-07-19 | 多租户 vs 单实例单人 | ✅ **单实例、单用户**；不追求托管多租户 | 新增 ADR-015（RLS 降级为"后加"） |
| 2026-07-19 | Web 聊天界面 | ✅ **v1 保留**（次要界面；Candidate Inbox 为主） | 新增 ADR-022 |
| 2026-07-20 | agent 是否应能自主驱动全部 UI 功能 | ✅ **应**——凡 UI 可见功能都要有对应 agent 工具（共享能力层） | 新增 ADR-023；新增 [docs/11](11-agent-tool-surface.md) + IMPLEMENTATION M-tools |
| 2026-07-21 | M3 抽取质量门是否留在 v1 | ⏸️ **推迟出 v1** → post-v1 #11（评估飞轮）；单用户自托管期不设外部质量门 | 新增 ADR-024；更新 roadmap/STATUS/IMPLEMENTATION |
| 2026-07-22 | 代码执行沙箱如何隔离 | ✅ 落地 ADR-007（Docker 硬化一次性容器：断网/掉权/非root/只读/资源+时间上限）；worker 挂 docker.sock（单用户自托管的信任让步，已记录） | 新增 ADR-025 |
| 2026-07-22 | QQ/IM 入站如何接入 + 审批如何渲染 | ✅ 通道适配器（OneBot v11/aiocqhttp）：webhook(HMAC+owner allowlist) → 复用 admit_prompt 有界循环 → 出站回推；审批复用 v1 基座（IM `approve/reject` → 同一信封） | 新增 ADR-026 |
| 2026-07-22 | agentic email 如何落地 + 统一发信接缝 | ✅ AgentMail 自有邮箱：单一 `build_email_sender()` 发信接缝（`send_email` 工具 + 日报都走它，真实发信）；入站 email(Svix 验签+owner allowlist) 复用同一有界循环 + 审批基座 | 新增 ADR-027；实现 roadmap 统一发信 note |
| 2026-07-22 | QQ 用官方平台还是 OneBot | ✅ **改用腾讯官方 api-v2（qq-botpy / WebSocket），弃用 OneBot**（自建 bridge + 非官方登录，封号/合规风险）；复用现有入站管线+审批基座；配置进复活的 Connectors 页 | 新增 ADR-028（**部分取代 ADR-026**）|
| 2026-07-23 | 记忆机制重构 + embedding 用自带 ollama | ✅ **分层记忆**（核心 blocks + 自动形成语义层 + 会话搜索情景层）+ **确定性 ADD/UPDATE/INVALIDATE/NOOP 写合并** + **双时态软失效** + **缓存稳定注入**；embedding 走**自带 ollama**(bge-m3 1024d)，与聊天 provider 解耦 | 新增 ADR-032（扩展 ADR-004、修订 ADR-012；源自 R-MEMORY）|
| 2026-07-23 | Agent 可观测性 + 是否用 OpenTelemetry | ✅ 用 **OTel `gen_ai` span** 作 ADR-016 日志之上的**薄诊断层**（日志仍真相源；内容默认不采集；`InMemorySpanExporter` 确定性测试）；后端首选自托管 **Phoenix**（复用现有 Postgres），**修订 docs/07 的 Langfuse 默认**；补上 STATUS item0 的 LLM 调用级观测 | 新增 ADR-033（源自 R-OBSERVABILITY）|

---

### ADR-001 · 做云端 agent，不做 local agent
- **决策**：Sherpa 是多租户云端 Agent 运行时，非本地 CLI agent。
- **理由**：需求包含多用户登录、云端沙箱、定时任务、主动推送、IM/邮箱通道——本质是长驻服务。
- **来源**：用户明确要求。
> **评审修订（2026-07-19）**：长期愿景仍是云端（多租户）运行时；**v1 是其第一个可交付切片 = 自托管、单实例、单用户**（见 ADR-022）。多入口/多租户/团队为后续里程碑。

### ADR-002 · 技术栈 = Python core + TS 前端
- **决策**：后端主语言 Python；前端 TypeScript。
- **理由**：对本项目的重资产（QQ/IM 适配=AstrBot、记忆/RAG=Letta、多租户沙箱+连接器=Dify、agentic email+cron+failover=Hermes）Python 生态可白嫖最多。FastAPI+asyncio+Celery/arq 撑住个人+小团队规模。
- **替代**：全 TS（借 OpenClaw 网关，但 QQ/记忆生态弱）；Go（参考代码少）。
- **窄腰兜底**：IM 适配器/沙箱执行器可后续用别的语言做独立服务。

### ADR-003 · 身份/会话 = tenant/user/identity + UMO 会话键
- **决策**：四层概念分离；会话键格式 `channel:type:external_id`；`identities` 表做身份链接；所有入口汇入 `resolve_inbound()`。
- **理由**：解决"一人多入口"——同一人从 Web/QQ/邮箱进来归到同一 user、同一记忆、同一 todos。
- **来源**：AstrBot UMO + OpenClaw identity links。
> **评审修订（2026-07-19）**：规范键扩展为 内部 UUID + 唯一 `(tenant_id, channel, channel_installation_id, scope_type, external_scope_id)`；群体 actor 身份与 session 身份分离；原始 provider ID 仅留作审计。v1 单用户下 `tenant_id` 恒为单值，但字段/复合键保留（近乎零成本的前向兼容，避免团队/托管成为单向门）。详见 ADR-015。

### ADR-004 · 两层记忆（user 私有 + tenant 共享）
- **决策**：记忆分 user 级私有 block 与 tenant 级共享 block；个人=单人工作区，与团队同一套 schema。
- **理由**：个人助理与小团队协作统一，不必来回改 schema。共享 block 保持小（编辑会 rebuild 所有成员 prompt）。
- **来源**：Letta memory blocks（many-to-many attach）。
> **评审修订（2026-07-19）**：v1 单用户 → 只保留 **user 私有记忆**；tenant 共享 block 随团队功能一并推迟（schema 仍留 owner 维度以便未来）。此外 v1 的 memory/RAG（pgvector）整体推迟出 v1（见 ADR-012），先用最简候选/待办数据即可。
> **Post-v1 实现注（2026-07-22）**：user 私有 core memory 与手工语义笔记 `memory_passages` 已实现；它们属于长期记忆，不等于有来源/版本/切块/引用的文档知识库。后者仍未批准实现，见 [`research/knowledge-base.md`](research/knowledge-base.md)。

### ADR-005 · 运行时 = 异步 job 优先 + 事件总线流式
- **决策**：所有 agent 运行都是异步 job；交互式靠 SSE/WS 从事件总线回推。web 进程永不跑 core 循环。
- **理由**：长任务/定时/主动推送/多用户并发都要求非阻塞、无状态 web、可水平扩。
- **铁律**：调模型前先持久化输入（durable prompt admission）。
- **来源**：OpenCode/Kilo 事件总线 + durable admission。
> **评审修订（2026-07-19，重要）**：明确 **PostgreSQL 规范运行态 + 有序事件日志（event journal）+ 事务性 outbox 为恢复/重放/流式的真相源**；Redis **Streams** 仅加速投递；Redis **pub/sub 永不作为正确性关键**（原文「事件总线」不得读作 pub/sub）。SSE 客户端用 cursor 断线补齐。详见 ADR-016。

### ADR-006 · core 循环 = 递归/生成器 ReAct + turn 粒度持久化（方案 A）
- **决策**：双循环（外层 agency / 内层 resilience）；stop-reason 闸；每个 turn 落库，崩溃从最后完成的 turn 重跑。
- **理由**：练手项目要够健壮又不过度工程；intra-turn 可恢复（方案 B，显式状态机）代码量大得多，后置。
- **替代**：方案 B（Claude Code 风格显式状态机，intra-turn 可恢复）。
> **评审修订（2026-07-19）**：turn 粒度恢复可能重跑一个工具 → 每个副作用需 **幂等键 + effect 分类**；结果未知（`effect_unknown`）时 **停下对账，绝不盲重试**。详见 ADR-017。

### ADR-007 · 沙箱 = ephemeral 每次一容器 + 持久 workspace 卷 + 默认断网 + Docker 后端
- **决策**：文件持久（对象存储/命名卷）；每次代码执行起一个全新隔离容器；默认 `--network none`；后端先用 Docker-per-run。
- **理由**：干净（无跨运行状态泄漏）、安全、compose 友好。启动开销用容器池预热缓解。
- **替代/演进**：persistent 每用户常驻容器（跑 dev server 时后加）；gVisor/Firecracker（跑不可信第三方代码时加固）。
> **评审修订（2026-07-19）**：沙箱/代码执行 **移出 v1**（降级为 deferred）。重启用前提：后端中立执行契约 + 对不相关租户用 gVisor/Firecracker（或专用节点）+ 出口策略 + 聚合配额 + 威胁评审。v1 不含 `run_code`。

### ADR-008 · 权限 `ask` = 异步 HITL（走事件总线）
- **决策**：工具授权走四道闸；`ask` 发 `permission.asked` 事件到任意 surface 渲染审批（correlation-id 协议）。权限代数：last-match 胜，`deny>ask>allow`，默认 `ask`。
- **理由**：cloud 多端场景下，一个远程 QQ 就能批准 Worker 里的操作，跨端一致。
- **来源**：OpenClaw/Kilo 权限引擎 + Ch11 approval bus。
> **评审修订（2026-07-19）**：**现在冻结**版本化语义审批信封契约，但**在首个 `ask` 动作进入范围前不建任何渲染器**（候选确认是独立业务流，不是审批）。详见 ADR-020。

### ADR-009 · 信任分级工具集（SAFE vs FULL）
- **决策**：不可信入口（email/webhook）的会话只发 SAFE 工具集（只读，无 shell/沙箱）；已认证用户（web/QQ）发 FULL。工具集 turn 开始时定死、中途不变。
- **理由**：防提示注入升级为代码执行；同时保住 prompt 缓存。
- **来源**：Hermes webhook 平台"受限安全工具集"。
> **评审修订（2026-07-19）**：连接器内容改用**专用 `CONNECTOR_ANALYSIS` 无工具结构化抽取**能力（不给通用 SAFE 工具、不读工作区/记忆、无副作用），只输出候选。原「按来源发 SAFE/FULL 工具集」仅用于已认证用户的交互式会话。

### ADR-010 · 自治边界 = 读+建todo+通知 全自动，对外代表用户走审批
- **决策**：连接器读取、建 todo、发通知全自动；代表用户发邮件/回复/建 issue 等对外动作走 `ask` 审批；沙箱写/跑代码走权限闸。
- **理由**：低风险可撤销的自动化提效；高风险对外可见动作需人确认。
- **来源**：Ch11 autonomy ladder。
> **评审修订（2026-07-19）**：改为 **candidate-first**：连接器内容**只自动建「候选（candidate）」**；正式 todo 需用户 accept/edit；通知 **opt-in + 策略门控**（安静时段/配额）。对外代表用户的动作仍走 `ask`。

### ADR-011 · 调度 = at-most-once（先推进游标 + 原子领取）+ 推送幂等
- **决策**：scheduler 单 leader（Redis `SET NX`）；领取到期任务时在同一事务推进 `next_run_at`；`FOR UPDATE SKIP LOCKED` 防双领；主动推送用 `sent_log` 幂等键。
- **理由**：即使崩溃也绝不重复触发/重复发通知。
- **来源**：Hermes cron at-most-once + Ch15。
> **评审修订（2026-07-19）**：**用 at-least-once 取代 at-most-once**：持久化唯一 firing + outbox + 至少一次 worker + 幂等/对账投递；按任务类型定「漏发 vs 重发」策略（摘要偏不重发；重要提醒偏最终必达）；显式暴露 missed/failed/unknown，**绝不静默丢失**。详见 ADR-017。

### ADR-012 · 存储选型 = Postgres(+pgvector) + Redis + MinIO
- **决策**：主库 Postgres；向量用 pgvector（复用 PG）；队列/总线/锁用 Redis；对象存储用 MinIO。
- **理由**：为"docker 一键部署"服务，尽量少起服务。无状态设计让将来拆分（Qdrant/独立 broker）不改上层。
> **评审修订（2026-07-19）**：v1 **推迟 MinIO/文件 与 pgvector/RAG**；受支持部署栈 = **Postgres + Redis + web + worker**（单实例单用户）。对象存储/向量随对应功能再加。
> **Post-v1 实现注（2026-07-22）**：MinIO Files 与 pgvector 手工语义笔记均已落地。来源型文档 Knowledge 若获批准，继续复用 Postgres FTS + pgvector；当前无规模证据支持新增独立向量数据库。

### ADR-013 · agentic email 与用户 Gmail 是两种信任级别
- **决策**：Gmail 连接器 = 读用户账户数据（OAuth 最小 scope、只读优先）；agentic email = agent 自有通信身份（收指令/发通知）。两者内容都不可信→SAFE 工具集。
- **理由**：区分"账户访问可信"与"内容可信"；agent 自有邮箱天然隔离、不碰用户私人号。
> **评审修订（2026-07-19）**：**agentic email 移出 v1** 与导航；v1 只用普通「摘要邮件」出站。重启用需 provider + 发件人/认证模型 + 信誉 owner + 滥用预算 + 增量价值证据。用户 Gmail 连接器（只读）保留在 v1。

### ADR-014 · 项目命名 Sherpa
- **决策**：项目名为 Sherpa。
- **理由**：为你背负/向导的助手，贴合"个人+团队全能云助理"定位。
- **来源**：用户选择。

---

## 评审后新增 ADR（2026-07-19，落实设计评审汇总 §4）

### ADR-015 · 租户隔离模型（v1 单用户简化）
- **决策**：v1 单实例单用户 → 应用以**单一 tenant** 运行；但所有表保留 `tenant_id` 列 + 复合外键（近乎零成本的前向兼容）。**强制 RLS、最小权限 DB 角色、KMS** 推迟到团队/托管里程碑。
- **理由**：用户确认 v1 单实例单人 → 无需托管多租户的运维门槛（KMS/RBAC/事故响应）；但保留租户维度键，避免未来团队/托管成为单向门。
- **权衡**：架构师 Phase-1 倾向「RLS-now」（防历史数据跨租户泄漏）；因确认单用户降级为「列在、RLS 后加」。**任何团队/托管模式启用前必须先补齐 RLS + 角色 + KMS**。
- **来源**：评审 §2.1 / cross-arch §5 单向门 #1；用户决策「单实例单人」。

### ADR-016 · 事件日志 + outbox 为真相源
- **决策**：PostgreSQL append-only **event journal**（带 session/run 序号、版本化信封、有界/脱敏 payload）+ 事务性 **outbox** 是恢复/重放/投影的真相源；Redis **Streams** 加速投递；**pub/sub 永不正确性关键**。SSE 用 cursor/reset 断线补齐。
- **理由**：pub/sub fire-and-forget 会丢事件，与 durable-first 冲突；重连客户端需补齐。Sherpa 不必全事件溯源——业务态仍存普通表，日志用于恢复/流式/审计。
- **单向门**：上线后从 pub/sub 换真相源会留下不可恢复的历史空洞 → 现在定。
- **来源**：评审 §2.1 / architect-review §4；修订 ADR-005。

### ADR-017 · 副作用/幂等/effect 契约（含调度投递）
- **决策**：每个副作用**先持久化 invocation 身份**再执行；带**幂等键**；分类可重试性；结果分 `succeeded/failed/effect_unknown`；**unknown 时停下对账，绝不盲重试**。调度改 **at-least-once**：唯一 firing + outbox + 幂等投递，显式 missed/failed/unknown。
- **理由**：turn 粒度恢复与崩溃会导致重复/未知副作用；统一契约避免每个工具各自发明重试。
- **来源**：评审 §2.1 / architect-review §5–6；修订 ADR-006、ADR-011。

### ADR-018 · 候选/来源 provenance 链
- **决策**：稳定链路 连接器 item/revision → 抽取版本 → generation → **candidate** → 已接受 todo；保留来源出处、去重键、thread 更新对账、删除语义。
- **理由**：已接受待办、反馈、去重、来源更新、删除都依赖它；事后补做会毁用户信任与评估完整性。
- **来源**：评审 §2.1 / cross-arch §5 单向门 #5。

### ADR-019 · 密钥加密（AEAD / KEK）
- **决策**：OAuth 令牌**逐记录 AEAD** + 可轮换 **KEK**；OAuth 回调即刻加密；仅连接器有解密权；刷新串行化；日志脱敏并有金丝雀测试。
- **理由**：明文令牌会渗入备份/日志/事件，事后加密无法擦除副本；宽泛解密权会嵌入各服务。
- **来源**：评审 §2.1 / cross-arch §5 单向门 #7。

### ADR-020 · 语义审批信封（冻结契约，渲染器后置）
- **决策**：现在**冻结**版本化语义审批载荷（correlation ID + 绑定 tenant/run/invocation + 规范化参数 hash + 预览 + policy 版本 + 过期 + nonce + 决策者/渠道；first-valid-response-wins）；**在首个 `ask` 动作进入范围前不建任何渲染器**。候选确认是独立业务流。
- **理由**：一旦 Web/QQ/email 客户端编码了请求字段，改 scope/一次性语义就可能批错动作。
- **来源**：评审 §2.1 / cross-arch §5 单向门 #6；修订 ADR-008。

### ADR-021 · 审计回执 vs 调试事件边界
- **决策**：稳定、脱敏的**语义回执**存于 append-only 审计模型，与原始 debug/telemetry 事件（可能含密钥、schema 易变）**分离**；仅授权诊断可链到原始数据；保留/删除独立定义。
- **理由**：把易变的原始遥测当公共审计 API 会冻结内部实现并增大隐私风险。
- **来源**：评审 §2.1 / cross-arch §5 单向门 #9。

### ADR-022 · v1 范围定义（纳入 / 排除）
- **决策（用户已确认 2026-07-19）**：v1 = **自托管、单实例、单用户的 Gmail→Action 助理**，**保留 Web 聊天**为次要界面。
  - **纳入**：只读 Gmail（受限 scope）→ 持久同步分析 → 私有候选（带来源+不确定性）→ accept/edit/dismiss → 基础 todo → opt-in Web/摘要邮件提醒（安静时段+配额+可见投递态）→ 暂停/断开/导出/删除 → job 状态/失败/用量成本/审计回执。**次要界面**：基础 Web 聊天。
  - **排除**（各带 tracking issue，属后续里程碑）：代码执行/沙箱、文件/MinIO、GitHub、QQ/IM、agentic email、团队/共享记忆、memory/RAG、对外写动作、通用 cron、多 provider failover、跨渠道审批渲染器、token 级流式打磨。
- **来源**：用户决策 + 评审 §1/§4 / cross-pm §4。

### ADR-023 · Agent 能力层 + 双适配器（REST 人用 / Tool agent 用）
- **决策（用户要求 2026-07-20）**：凡用户在 UI 上能看到/能做的功能，agent 都必须能通过**工具**自主驱动。为此确立**单一能力层 + 双适配器**：
  - 业务逻辑集中在 `app/services/`（**能力层**，传输无关：入参 `CallerContext` + 领域参数，做领域校验/变更/抛 typed `ServiceError`，**不 commit**）；
  - **REST = 人的适配器，Tool = agent 的适配器**，两者都只"解析入参 → 调同一 service → 组织出参/错误"，**不重复业务逻辑**；共用同一 `CallerContext` 与同一**四道闸权限引擎**；
  - **按能力纵切开发**（service→REST→Tool→权限→测试→浏览器验收），不横切。
- **例外/边界（一次性门，不弱化）**：
  - 不可信内容分析仍是**无工具隔离 pipeline**（ADR-009 不动）；仅 FULL（已认证用户）会话拿数据工具；
  - **对外写动作**（`send_email` 等）仍走语义审批（ADR-020）：策略引擎判 `ask` → 审批信封；
  - **审批的"解决"是人的职责**——agent 不获得批准自己动作的工具；
  - **破坏性数据操作**（导出/删除导入数据）判 `ask` 或仅限人工。
- **理由**：REST 与 Tool 各写一遍业务逻辑必然漂移、双倍 bug、权限不一致；共享 service 让"UI 能做 = agent 能做"成为结构性保证。
- **落地缺口（→ [docs/11](11-agent-tool-surface.md) + IMPLEMENTATION M-tools）**：`Tool.execute` 需注入 `ToolContext`（当前 `base.py` 缺）；ALLOWED 策略引擎需实现（当前仅 VISIBLE 闸 + 极简 ask）；输出 spill 需落地（api.md §7.2）；候选/待办/连接器/日程/通知/活动均需补 service 抽取 + 工具（`create_todo`/`create_schedule`/日程 REST 连 REST 都缺）。
- **来源**：用户输入「我认为 agent 肯定要有能力自主控制用户在 UI 上能看到的一切功能」；对齐 api.md §7、docs/05。

### ADR-024 · M3 抽取质量门推迟出 v1（折入 post-v1 评估飞轮）
- **决策（用户确认 2026-07-21）**：v1 收尾 **不含** M3 抽取精度质量门（goldens + 50–100 封脱敏邮件精度基准 + 回归数据集）。**v1 收尾 = 上下文忠实性修复（跨-run 工具历史 bug）+ 审批闭环**。评估 harness 折入 **post-v1 里程碑 #11（评估飞轮增强）**。
- **理由**：v1 是自托管、单用户（ADR-022）。抽取质量门的目的是"证明精度够好、值得让**外部用户**接入真实 Gmail" + 防回归；但当前**唯一用户即 owner 本人**，其本身就是评估闭环，无外部用户需保护。50–100 封邮件的**标注成本高**（人工判断为瓶颈），此刻收益低。roadmap 本就把 #11 定义为"贯穿式持续投入"。
- **边界 / 重启条件（不弱化，只是推迟）**：**在 onboard 任何外部 beta 用户之前必须重新引入**该质量门（精度基准 + 回归集）。若期间改动抽取路径（如上记忆/RAG），建议先补一条**便宜的确定性回归泳道**（mock + 小 golden 集，锁 parser/dedupe/字段映射）作为最小护栏。
- **影响**：更新 [09-roadmap.md](09-roadmap.md)（v1 收尾定义 + M3 行 + #11）、[STATUS.md](STATUS.md)（Next-ready）、[IMPLEMENTATION.md](IMPLEMENTATION.md)（cross-cutting eval 行）。
- **来源**：用户输入「M3 要花多大 effort，可不可以跳过 M3？因为我比较期待尽快完成 v1 收尾，开始 09-roadmap.md 内容的开发」；选择「跳过 M3 评估门（推荐）：v1 收尾 = item 0 修 bug + 审批闭环，评估折进 post-v1 #11」。

### ADR-025 · 代码执行沙箱实现（Docker 硬化容器，落地 ADR-007）
- **决策（2026-07-22）**：落地 [ADR-007](#adr-007) 的 Docker 后端沙箱。每次 `run_code` 在一个**临时、一次性**容器里执行，硬化项：`network_disabled`（断网）、`cap_drop=ALL` + `no-new-privileges`、非 root（`nobody`）、`read_only` rootfs + `tmpfs /tmp`、内存/pids/CPU 上限、墙钟超时、执行后 `--rm`、`python -I -B`（隔离、不写字节码）。输出限长（loop 的 bound/spill）。默认 `SANDBOX_KIND=disabled`（离线/测试返回明确的 not-enabled）；栈内 `=docker`。
- **威胁模型 + 缓解（履行 roadmap 里程碑3 的"威胁评审"前置）**：
  - *任意代码执行* → 隔离在一次性容器（无网 · 非 root · 掉全部 caps · 只读 rootfs · 资源上限 · 超时），逃逸面最小化；
  - *数据外泄* → `network_disabled` 断网；v1 首版**不挂 workspace/MinIO 文件**（纯计算），无本地数据可读；
  - *资源耗尽/DoS* → mem/pids/CPU/墙钟上限，每 run 强制回收；
  - **已记录的信任让步**：worker 挂载 `docker.sock` ≈ 对宿主的 root 等价访问。仅在**自托管、单用户** v1 可接受；**生产/多用户前**须换 gVisor/Firecracker、rootless Docker 或 socket-proxy（后续里程碑）。
- **排除（后置）**：workspace 文件挂载进沙箱、gVisor/Firecracker、跨-run 聚合配额（当前每 run 上限）、多语言（v1 仅 Python）。
- **来源**：ADR-007；roadmap 里程碑3 前置（中立执行契约 + 隔离 + 出口策略 + 聚合配额 + 威胁评审）。

### ADR-026 · QQ / IM 入站通道 + IM 审批渲染器（落地 roadmap 里程碑4）
- **决策（2026-07-22）**：以**通道适配器**形态接入 IM，首个后端为自托管的 **OneBot v11 / aiocqhttp** HTTP API（go-cqhttp / Lagrange / AstrBot）。入站与 Web 提示**同源**：`POST /channels/qq/webhook` 收事件 → **复用 `admit_prompt`** 持久化后再调模型 → worker 跑同一有界循环 → 出站 `send_private_msg` 回推最终 assistant 文本。IM 线程**直接映射到既有 `sessions`**（`channel='qq'` + `external_scope_id=<qq user id>`，`umo_key=qq:<inst>:<uid>`）——**不新增表、不改冻结契约**；`run_kind` 仍用 `web_chat`（`runs` CHECK 不动，session.channel 才是判别键）。
- **审批复用 v1 基座（ADR-020）**：gated 工具在 IM 会话里同样触发 `permission.asked`；回推消息带审批预览 + 短 correlation id，用户回 `approve <id>` / `reject <id>`（中英文动词皆可）→ webhook 用**服务端可信 verify**（服务端持有信封 + owner 已由 HMAC 鉴权）调用同一 `resolve_approval(channel='qq')` → `enqueue_approval_resume` → 复用同一 run 恢复 + 对外写动作执行。无跨渠道前向依赖。
- **安全**：入站 body 走 **HMAC-SHA1 常数时间校验**（`X-Signature`, `qq_webhook_secret`）；**owner-id allowlist**（单用户 v1 仅 `qq_owner_id` 放行，其余 403）；secrets 只来自 env、不记录。`qq_kind='disabled'`（默认）→ `RecordingQQClient`（离线/测试不触网）。
- **可验证性（无真实账号也能走人工路径）**：`POST /channels/qq/simulate`（owner+CSRF）以 owner 身份注入一条"入站"消息；`GET /channels` 出状态 + 线程；`GET /channels/threads/{id}` 出线程转录，供 Messaging 页做人工点检验收。真实 QQ 账号接入留作**手动验收**（用户备注）。
- **排除 / 后置（本里程碑显式不做）**：① **定时提醒/日报路由到 QQ**——`schedules.delivery_channel` 受冻结 CHECK 限制（`web`/`digest_email`），需契约迁移，推迟（agent 可在 run 内主动推 QQ，审批也已在 IM）；② 群消息（仅私聊）；③ 官方 `qq-botpy`(WebSocket) 适配器——留作真实账号接入时的第二后端；④ 富媒体/at/图片段（仅纯文本段）；⑤ 出站投递去重（best-effort，post-commit，失败不影响 run 持久性）。
- **来源**：roadmap 里程碑4（AstrBot/aiocqhttp「在 QQ 里跟 agent 对话、收通知、在 QQ 上批准/拒绝」）；用户备注 QQ python SDK 文档 + 「真实账号接入无法完成的验证部分直接跳过、手动验收」。

### ADR-027 · agentic email（AgentMail 自有邮箱）+ 统一发信接缝（落地 roadmap 里程碑5）
- **决策（2026-07-22）**：agent 拥有独立邮箱身份（AgentMail inbox，如 `cloudysample676@agentmail.to`）。
  - **统一发信接缝（落实 roadmap 2026-07-21 note）**：`build_email_sender()` 是**唯一**出站发信口——`send_email` 工具（原内联 stub「email sent to …」）改为调用它，日报/提醒投递本就走它。`email_kind='agentmail'` 时 `AgentMailEmailSender` 经 `AgentMailClient`（`POST /v0/inboxes/{inbox}/messages/send`，Bearer）真实发信；默认 `recording`（离线记录，测试/开发不触网）。两条路径共用同一集成 + 脱敏 + 审计，杜绝行为漂移。
  - **入站 agentic email**：`POST /channels/email/webhook` 收 AgentMail `message.received`（Svix HMAC-SHA256 验签，`svix-id/timestamp/signature`，secret 为 `whsec_` 后 base64）→ **复用与 QQ 相同的通用入站路径**（`ensure_channel_session(channel='email')` + `admit_prompt` → 有界循环）→ 出站回推 assistant 文本（+ 待审批预览）。**邮件侧审批复用 v1 基座**：回信 `approve/reject <id>` → `resolve_approval(channel='email')` → run 恢复。
  - **信任 / 工具层级（ADR-013）**：邮件内容不可信。v1 用 **owner-email allowlist**（`agentmail_owner_email`，设了就只放行 owner，FULL 层级；未设=放行任意发件人）。**向任意发件人开放需降到 SAFE 工具层级**（ADR-013），需把 per-run tier 下沉进 `run_job`——**推迟**（记录为后续）。
- **可验证性**：真实**发信**已用 owner 提供的 API key 验证（自测邮件返回 `message_id`）。**入站真实投递**需公网 webhook（AgentMail→本地 localhost，需 ngrok/隧道）——**留作手动验收**；人工路径用 `POST /channels/email/simulate`（owner+CSRF）注入一封"入站"邮件 + Messaging 页 email 段做点检。
- **排除 / 后置**：任意发件人 + SAFE 层级下沉；邮件线程/引用抽取（Talon）；附件；富 HTML；出站去重（best-effort）；官方 SDK（用 httpx，零新依赖）。
- **来源**：ADR-013；roadmap 里程碑5 + 2026-07-21 统一发信 note；用户提供 AgentMail 账号 + API key（本地文件）+「真实账号接入无法完成的验证直接跳过、手动验收」。

### ADR-028 · QQ 接入改用腾讯官方平台（api-v2 / qq-botpy / WebSocket），弃用 OneBot（部分取代 ADR-026）
- **决策（用户拍板 2026-07-22）**：QQ 入站改走**腾讯 QQ 机器人开放平台官方接口（api-v2）**，用官方 SDK **`qq-botpy`（WebSocket 网关）**接入；**弃用 ADR-026 的 OneBot v11/aiocqhttp 传输**。
- **理由**：
  - OneBot 需自建 bridge（NapCat/go-cqhttp/Lagrange 等）+ **非官方协议登录 QQ**，有**封号/合规风险**，且多一个运维组件；官方平台用 **AppID + AppSecret**、合规、且 **WebSocket 模式 bot 主动外连网关、无需公网 URL/反代**，最契合自托管单用户。
  - 生态验证：Hermes / OpenClaw / **AstrBot**（开源，已读其官方适配器源码）均用此路径。
- **连接模式选择**：**WebSocket**（自托管友好，无需公网）而非 Webhook（需公网 IP+域名+HTTPS+反代、Ed25519 验签）。Webhook 留作后续可选。
- **接入 UX（复活 Connectors 页）**：**手动 AppID/Secret 优先**（永远可用）；**扫码一键**（✅ 官方对第三方开放，已确认 2026-07-22）——腾讯公开 SDK `@tencent-connect/qqbot-connector` + wiki「第三方 Agent 接入」，`create_bind_task` 只需客户端自生成 key、无合作方 token，`source` 留空显示"第三方机器人"。流程：`POST q.qq.com/lite/create_bind_task {key:base64 AES-256}` → 二维码 `q.qq.com/qqbot/openclaw/connect.html?task_id=..&source=..` → `POST /lite/poll_bind_result {task_id}` → 返回 `bot_appid` + `bot_encrypt_secret`（AES-256-GCM，用 key 解）+ **`user_openid`（扫码人=owner，用于 owner 绑定）**。Python 直连即可（官方 SDK 端点与 AstrBot 逆向逐字一致）。Secret → **AEAD 凭据保险库（ADR-019）**，永不日志、响应脱敏（仅显示 set/last4）。
- **运行位置**：botpy WS 客户端作为 **worker 的有界重连后台任务**（类比 `_relay_loop`），优雅关闭（参考 AstrBot `ManagedBotWebSocket`/`shutdown`）。
- **复用（不重造）**：通道无关入站管线（`ensure_channel_session(channel='qq', external_id=<user_openid>)` → `admit_prompt` → 有界循环）、**审批基座**（`resolve_approval(channel='qq')`）、`/channels/qq/simulate`（人工验收）、Messaging UI 全部保留。**删除**现有 M4 的 OneBot 部分：`app/channels/qq.py` 的 `OneBotQQClient` + HMAC-SHA1 `verify_signature` + `/channels/qq/webhook` 的 OneBot 事件解析。
- **发送约束**：**被动回复**用入站 `msg_id`（+`msg_seq`），需按会话保存最近 msg_id（异步 worker 回复可能延迟，超回复窗兜底走**主动推送**，受配额限制）。API：`post_c2c_message`（单聊）等；`msg_type` 0=文本。
- **owner 归属（单用户 v1）**：仅 owner 的 QQ openid 走 FULL 层级——首绑 openid 或 allowlist 字段。
- **新依赖**：`qq-botpy`（WS+发送）+ `pycryptodome`（扫码 AES-GCM 解密）。（Webhook 的 Ed25519 依赖 `cryptography`——不做 webhook 则免。）
- **排除 / 后置**：Webhook 模式；群消息（先私聊/C2C）；富媒体段（先纯文本）；定时提醒路由到 QQ（冻结 `schedules` CHECK，同 ADR-026）。

---

## Post-v1 产品线 ADR（2026-07-23，落实 R-SESSION-SEARCH / R-WORKSPACE-PRODUCT 调研）

### ADR-029 · Session Library + 会话搜索（落地 P0/P1，源自 R-SESSION-SEARCH）
- **决策（用户拍板 2026-07-23，实现到 P2 的一部分）**：把"浏览/搜索/恢复历史会话"做成一等产品 **Session Library**，取代当前 Chat 顶部的会话下拉。**保留"可重放 canonical history + 可重建 search projection"的分层，但云端不引入 JSONL/SQLite**（区别于 Copilot CLI / Codex 的本地实现；见 [`research/session-search-report.md`](research/session-search-report.md)）。
- **真相源不变（ADR-016）**：`sessions`/`runs`/`messages`/`parts`/`event_journal`/`approval_envelopes`/`effect_invocations`/`audit_receipts` 仍是 Postgres canonical；搜索用**派生、可重建**的投影表 `session_search_entries`，损坏即从 canonical 重放重建，永不作为唯一副本。
- **P0（Session Library 浏览+恢复，先做，无内容搜索）**：
  - **会话标题持久化**：`sessions.title text NULL`（当前仅在 create 回显、未落库）；未设置时由首条用户消息派生（脱敏截断），可改名。
  - **`last_activity_at` 维护**：列已存在且已建索引但从未写入；P0 起在每次消息落库/运行状态变化时更新，作为浏览排序键。
  - **run 存活判定（liveness）**：`runs.status='running'` **不等于** worker 活着。新增 Postgres 心跳/租约（`runs.heartbeat_at`、`runs.lease_expires_at`、`runs.worker_id`）：worker 每 15s 续租、45s 过期；**Reconnect 仅在租约新鲜时可用**，过期归为 **Interrupted / Recover run**。Redis 心跳仅加速展示、非真相源。
  - **状态化恢复语义**（不再用单一 "Resume" 按钮）：idle→Resume session；running(lease fresh)→Reconnect；running(lease stale)→Recover run；pending approval(`now()<expires_at`)→Review approval；expired approval→Dismiss；interrupted-safe→Continue from checkpoint；`effect_unknown`/`needs_reconciliation`→Resolve outcome（**绝不盲重试**，ADR-017）；failed→Review failure；archived→Open/Restore；deleted→不可恢复。审批过期是惰性判定，API **不得**暴露点了必失败的动作。
  - **恢复语义拆分**：Open（只读加载 transcript/活动/状态）、Resume（对同一 durable session 提交新 prompt）、Reconnect（附着现有 SSE 流、不新建 run）、Recover（先对账 interrupted/stale run）。
  - **授权**：所有 browse/search/timeline/resume-state/recover 查询同时校验 `tenant_id` **与** `user_id`（为未来多用户租户，现有 `/sessions/{id}/messages` 仅查 owner-is-None 的缺口一并收口）。
- **P1（会话内容搜索）**：
  - **派生投影 `session_search_entries`**（tenant/user 作用域，见 data-model 契约）：每个可索引单元一行（title / user_message / assistant_message / tool / action）。
  - **两条独立计数轴不可混用**：`messages.seq` 与 `event_journal.session_seq` 是**各自 per-session 独立**的计数器，绝不能当同一条时间轴比较。投影用 **typed anchor**（`anchor_kind` ∈ message/event/audit + `anchor_id` + `run_id`），deep-link 时后端把 event/tool 命中映射回其 `run_id` 与相邻消息 turn。
  - **检索**：Postgres FTS（`simple` 配置，空白分词精确 token/短语）+ **应用层 Unicode 字符 bigram** 索引处理中文/CJK + `pg_trgm`（≥3 码点的模糊/子串）。字段加权 title>user>assistant>tool/action + 轻量 recency。**不上独立向量库/OpenSearch**；语义检索（pgvector hybrid RRF）留 Phase C，且需 golden 集证明 MRR 提升≥10% 才保留。
  - **投影更新不能只靠 event_journal**：prompt admission **不发用户消息事件**（`core/history.py` 已注明），title/status/删除也非 append 事件。故用**同事务投影 outbox**：消息/标题/归档/删除/脱敏在 canonical 写入同事务里写投影作业行；tool/run 事件与 audit 继续从 `event_journal` 消费；删除/脱敏产生**显式 tombstone**（清空 `content_text` + 置 `redacted_at`，生成列 fts 变空，永不命中）。live 投影与全量重建读同一 canonical 集合。
  - **快照式游标**：browse 与 ranked search 用不同 opaque cursor（browse=snapshot 时刻+`(last_activity_at, id)`；search=query hash+`(score, last_activity_at, id)`），避免翻页时被新活动插入打乱。
- **分阶段**：A=浏览+状态化恢复（P0）；B=lexical/CJK/trigram 搜索+精确跳转+投影重建/保留删除传播（P1）；C=语义召回+从某 turn 分支+lineage+托管多租户分区/RLS（**后置，本次不做**）。分支创建新 session（`parent_session_id` + `branched_from_message_id`），原 session 与 journal 不变。
- **验收关键**：零跨租户/用户结果；索引永不是唯一副本；删/过期 ≤1min 从结果移除；重建产生同一可搜索集合；无 raw reasoning/明文密钥进投影；`ts_headline` 输出**必须**转义后再返回（非可信 HTML）；恢复保真覆盖 idle/live/stale/approval/expired/interrupted/effect_unknown/failed/archived/双端并发 十态，人需对账者绝不静默变 ready。
- **不改的**：ADR-016/017 真相源与 effect 语义；ADR-020/021 审批与审计边界；ADR-023 能力层+双适配器（搜索/浏览同样 service→REST+Tool）。

### ADR-030 · Personal Drive 基础（落地 P2 / Workspace W1，源自 R-WORKSPACE-PRODUCT）
- **决策（用户拍板 2026-07-23，方向已于 2026-07-22 确认）**：把当前扁平的 **Files**（`/workspace`，仅上传/下载/永久删）升级为面向用户的 **Personal Drive**——W1 只做 Drive 基础，**不**暴露 Projects 导航、**不**把 Drive 挂进 sandbox、**不**做 GitHub 同步（那些是 W2/W3/W4）。
- **产品词汇（防"workspace"一词多义）**：Personal workspace=所有权/配额容器；Drive=通用私有文件；后续 Projects/Source/Sandbox/Task working copy 等见 [`research/workspace-product-report.md`](research/workspace-product-report.md)。SPA 未来路由 `/work`、`/work/drive`（避开 API 前缀冲突）；W1 先在现有 `/workspace` 内落地，不新增顶层 Projects 项。
- **存储真相源（ADR-012 延伸）**：Postgres 存**元数据/所有权/配额/版本/回收站/对账状态**；MinIO 存**不可变、租户作用域的字节 blob**；Redis 仅加速。**不**引入独立 Git 存储（那是被否的 Option 2）。这是 Option 1 + 向 Option 3 的可控演进，不是一上来就 Merkle 平台。
- **新表（见 data-model 契约）**：`storage_accounts`（每 user 配额账户：quota_bytes、used_bytes、reserved_bytes）、`storage_blobs`（不可变，按 content_hash 去重，引用计数）、`drive_nodes`（folder/file 为一等记录，非纯路径前缀，支持 move/rename 事务、subtree、trash 状态）、`drive_versions`（每次覆写保留旧版本指向旧 blob，可恢复）。均带 `tenant_id`+复合键（ADR-015 前向兼容）。
- **配额**：**部署可配置的每人 5 GiB 默认**（非 schema 常量）；另设 tenant 上限与部署硬顶。**记账**：每个 owner 对每个**不同 durable blob** 只计一次；多版本/多快照指向同一未变字节**不翻倍**；去重 credit **不跨 user/tenant 边界**。大写入前先 `reserved_bytes` 预留，失败释放。
- **跨库一致性修正（当前 bug）**：现 `put_file` 先写新对象再 commit DB、`delete_file` 先删对象再 commit（`services/files.py`），崩溃/失败会留孤儿对象或删掉已提交行的旧对象。W1 改为 **DB 先行 + 不可变 blob + 引用计数 + 后台对账 GC**：写入=先 upsert blob 记录与节点（同事务），对象已存在则复用；删除=只解引用/进回收站，实际字节由 **对账/GC worker** 在无引用且过保留期后清理。孤儿/校验和/配额/GC 各有 reconciliation 作业。
- **回收站**：以**永久删**替换为 **trash（软删+保留期+restore+显式 purge）**；purge 仅人工或审批门（agent 工具不得永久清除）。
- **流式**：上传/下载不再整对象读入进程内存；大上传走可续传/分块（W1 至少去掉全量 buffer）。
- **迁移**：现有 `files` 行迁入个人 Drive root，不暴露 object key，保留 version/hash；旧 REST/Tool 行为在过渡期保持可用（ADR-023 能力层不破坏）。
- **Agent 平权（ADR-023）**：agent 经同一 service 可 list/search/建文件夹/写上传（限量）/rename/move/看版本/恢复版本/进回收站/恢复；**永久 purge 人工专属**。
- **W1 明确不做**：Projects 导航占位、Drive 挂 sandbox、GitHub 同步/新 Git service（分别 W2/W3/W4）。
- **验收关键**：配额/预留正确且不跨界翻倍；崩溃不产生孤儿对象或丢字节（对账可收敛）；trash 可 restore、purge 需授权；每页 390px 无横向滚动；迁移后旧文件可见可下载、版本/hash 保留。契约（data-model/api）**先于代码**更新（本 ADR 同批）。
- **可验证性**：真实端到端需真实 bot 账号（+ 可能的 IP 白名单）→ **手动验收**（用户已同意此类留手动）；开发期用 `/channels/qq/simulate` + Messaging 页走人工路径。
- **与 ADR-026 关系**：**部分取代**——保留 ADR-026 的"IM 线程映射到既有 `sessions`（`channel='qq'`）、审批复用 v1 基座、无新表/无冻结契约变更"结论；**只替换传输层**（OneBot → 官方 botpy/WS）。
- **来源**：用户输入「既然要用官方 bot，OneBot 就不需要了」；调研见会话工作区 `qq-official-bot-research.md`（AstrBot 源码 + 腾讯 api-v2 + 官方 SDK `@tencent-connect/qqbot-connector@1.2.0` 确认扫码端点对第三方开放）。

#### 构建任务拆解（评审后再写代码；真实端到端留手动验收）
1. **ADR + 依赖**（本条已含）：加 `qq-botpy` + `pycryptodome` 到 `backend/pyproject.toml`；`uv sync`；`uv.lock` 提交。
2. **清理 OneBot**：删 `OneBotQQClient` + HMAC-SHA1 `verify_signature` + `/channels/qq/webhook` OneBot 解析；保留通用入站管线/simulate/Messaging。
3. **配置持久化 + 保险库**：channel 配置表（tenant+user，非密钥字段：appid/enabled/enable_group_c2c/owner_openid）；AppSecret 走 AEAD vault；`build_*` 从存储读取（env 兜底）。
4. **官方适配器**（`app/channels/qq_official.py`）：botpy `Client(intents).start(appid, secret)` 封装；入站 C2C→`ensure_channel_session`+`admit_prompt`+`enqueue_run`；存每会话 `msg_id`。
5. **worker 后台任务**：有界重连的 botpy WS 生命周期（启动/关闭），仅当 QQ 已配置时启动。
6. **回复投递**：`_deliver_im_reply` 的 qq 分支改为 botpy 被动回复（`post_c2c_message` + 存储的 msg_id），超窗兜底主动推送。
7. **REST + 复活 Connectors 页**：GET/PUT QQ 配置（owner+CSRF、密钥脱敏）、POST 测试连接；手动 AppID/Secret 表单 + 状态卡（与 `connectors-editable-email` 同页）。
8. **扫码一键（fast-follow，端点授权确认后）**：create_bind_task/poll + AES-GCM 解密 + 二维码 + 轮询 UI。
9. **测试**：签名/解密单测、入站路由、配置/vault、回复投递（mock botpy，离线）；`uv run pytest`/`ruff`/`mypy` 全绿。
10. **验证**：`/simulate` + Messaging 人工路径；真实 bot 端到端 → 手动验收。

### ADR-031 · 通用定时任务 cron / Schedules 增强（**已接受**，源自 roadmap #6「通用定时任务 cron」）

> **状态：已接受（owner 拍板 2026-07-23 开工）。** 契约增量已写入冻结契约（data-model「Recurring schedules / general cron」+ api §4.5，migration `0023`）；实现见 IMPLEMENTATION「Phase CRON」。先契约后代码（AGENTS.md §1）。

- **动机 / 现状痛点**：现有 Schedules 只是「每日提醒器」——`schedules.kind` 被冻结 CHECK 锁死为 `todo_reminder` / `daily_digest`；推进逻辑 `scheduler/tick.py:_advance` 写死「每天 +1 天」；触发只往 Web 收件箱/摘要邮件投一条**静态文本**（`delivery.py` 的 `"Reminder for {name}."`），**从不启动 agent**。无法表达「每周一 9 点」「每 2 小时」「工作日早上跑一个自主任务」。
- **决策**：把 Schedules 升级为**面向 agent 的通用 cron**——「给 AI 助手用的 crontab」。三处通用化 + 一个新动作：
  1. **通用重复规则（cadence）**：cron 表达式 / interval（每 N 秒-分-时）/ weekly / monthly / once；带时区（IANA）+ DST 正确;沿用现有 misfire（skip / fire_once）与 duplicate 策略。
  2. **新动作类型 `agent_task`（最大变化）**：schedule 保存一段 **prompt**，到点由 worker **自动 enqueue 一个 run**、跑现有有界 agent 循环、产出结果并投递。让「每天 8 点：读未读邮件→列今日要回的→草拟回复」这类**自主任务**成为可能，而不只是提醒。
  3. **通用投递目标**：结果按 `delivery_channel` 路由到 **web / email / qq / …**，复用现有 channels/notifications 层，而非锁死两个渠道。
- **真相源 / 安全不变（复用 v1 基座）**：
  - **触发幂等（ADR-016/017）**：仍是「先进游标再触发、每 slot 唯一 `schedule_firings` 行」的 at-most-once slot + outbox + 幂等投递。`agent_task` 的 run enqueue **必须**用 firing slot key 作幂等键，worker 崩溃/重放不重复跑。
  - **定时 ≠ 放权**：`agent_task` 里的外部副作用（发邮件等）**仍走审批闸（ADR-019/020/021）**；无人值守时挂起为收件箱/渠道审批卡，绝不因为「是定时任务」而自动执行外部动作。
  - **绝不盲重试**：run 失败/`effect_unknown` 沿用 ADR-017 语义，firing 以诚实的 `failed`/`needs_reconciliation` settle，收件箱可见。
- **契约增量（经批准后写入 data-model/api；此处仅设计，不改冻结文件）**：
  - `schedules` 放宽 `ck_schedules_kind` 增加 `agent_task`；新增 cadence 列：`cadence_kind`(daily/cron/interval/weekly/once)、`cron_expr text`、`interval_seconds int`、（可选）`rrule text`；`agent_task` 用 `prompt text`（有界长度）+ 可选 `session_binding`（专用 scheduled session 或每次新建）。
  - 调整 `ck_schedules_kind_target`：`agent_task` 需 `prompt` 非空、`todo_id/reminder_kind` 为空、cadence 字段与 `cadence_kind` 自洽（cron 需 `cron_expr`，interval 需 `interval_seconds`）。
  - `ck_schedules_delivery_channel` 扩展为 `web / digest_email / email / qq`（按已上线 channels）。
  - `schedule_firings` 增加到 run 的关联（`run_id` 可空，agent_task 触发后回填），便于运行历史/结果展示。
  - api.md §4.4 增补：新建/编辑通用排程、`run_now`（立即试跑一次）、运行历史。
- **调度引擎**：`_advance` 从「每日」泛化为 cadence-aware「下一次严格晚于 now」的计算——cron 用 `croniter`（或等价库，走 uv 依赖 + ADR）、interval 用步进、weekly/monthly 用日历推进、once 触发后置 `completed`；DST 经时区换算。**护栏**：cron/interval 有**最小频率下限**（如 ≥1 分钟，可部署配置），创建时校验表达式合法性，防误配高频。
- **`agent_task` 执行**：到点在专用 `run_kind='scheduled_task'` 下 enqueue run，seed 保存的 prompt（进一个「定时任务」系统会话或每次新建会话，绑定可配置）；复用 worker run 循环、事件流、trace。**成本护栏**：每用户并发上限 + 频率下限，避免定时任务把模型调用打爆。结果经 firing→delivery 投递到目标渠道。
- **Agent 平权（ADR-023）**：`schedule_*` 工具泛化——agent 能建/列/暂停/恢复/立即试跑**通用**排程（含 `agent_task`），与 REST/UI 同一 service。
- **前端**：Schedules 页（SPA 路由仍 `/reminders`，避开 API 前缀）从「提醒列表」升级为**调度台**：新建任务（选 cadence + 动作：提醒 / 摘要 / **跑 agent 任务**+ prompt + 投递渠道）、下次运行时间、**运行历史**（每次 slot 跑了什么/成败/产出）、暂停/恢复、**立即试跑**。
- **本阶段明确不做**：多步工作流 / DAG 编排；外部事件（webhook）作触发器；跨任务依赖链；长运行/常驻服务。**只做「时间触发的单 prompt 自主任务」**，把工作流编排留后续 ADR。
- **验收关键**：cron/interval/weekly 的下一次计算在 DST 边界正确；`agent_task` 每 slot **恰好跑一次**（firing 幂等，worker 重放不重复）；外部动作仍需审批；频率下限/并发上限生效；零跨租户/用户泄漏；`run_now` 可手动验证；双路径 Playwright（人工建一个 cron agent_task + agent 经工具建一个，观察到点自动跑并投递）。
- **依赖 / 不改的**：不依赖 GitHub（roadmap #6 的「#7」是原需求编号非构建依赖）；不改 ADR-016/017 真相源与 effect 语义、ADR-019/020/021 审批与密钥边界、ADR-023 能力层双适配器；不引入独立工作流引擎。
- **修订（2026-07-24，源自 owner 实测反馈 + R-SCHED-CONTEXT 调研）**：`agent_task` 的会话模型从"每 schedule 一个长期会话"改为**每次触发一个全新会话**（per-firing）。**动机**：原实现让同一 cron 的多次触发累积到同一会话，`assemble_provider_history` 把全量历史（含上次的 `tool_calls` / 审批占位）当 provider 消息回放 → 第二次起 provider **400 Bad Request**（同一 `tool_call_id` 出现两条 tool result + 重复 user 消息），且污染 Chat/Sessions。**调研印证**：ChatGPT Tasks / OpenHands Automations / n8n / CrewAI / Hermes 均为"每次触发全新上下文";AstrBot 甚至把历史渲染成 system-prompt 文本后 `req.contexts=[]` 清空 provider 消息。**决策**：每次触发新建会话，key=firing slot（`scheduled:{id}:{firing_key}`），`scope_type='scheduled_task'`；**Session Library / Chat 排除 `scheduled_task`**，仍可经 `/reminders`→firing 历史（`schedule_firings.run_id`）查该次 transcript。跨 run 若要传状态，走 memory block（ADR-032）而非裸 transcript。ADR-031 原文已列"每次新建 session（可配置）"为选项,此为落实正确默认。**run_now** 改为**立即派发**(不等周期 tick)。**编辑/删除**:`agent_task` 支持 UI 编辑(名/prompt/cadence/渠道,乐观版本)与真删(硬删 firings+schedule)。
- **P4 预授权预检(简单设计,已记录,深入留后续)**：见 [`research/scheduled-permission-prehint.md`](research/scheduled-permission-prehint.md)——建定时任务时预检将用的外部权限并提示"是否加入预授权";首版倾向轻量启发式(扫 prompt 里的邮箱地址→提示加 send_email 白名单),精确的模型 dry-run 分析留后续调研。

### ADR-032 · 记忆机制重构：分层记忆 + 确定性写合并 + 自带 ollama embedding（源自 R-MEMORY；扩展/部分取代 ADR-004，修订 ADR-012）

> **状态：方向已由 owner 拍板（2026-07-23）；本批次先契约后代码——只写 ADR + 冻结契约增量（data-model/api/events/config），不写业务代码，落地顺序待定（AGENTS.md §1）。** 完整调研见 [`research/memory.md`](research/memory.md)。

- **背景（触发）**：复现到一个记忆 bug——`user_memory` 是**自由命名 key 的精确匹配 KV**，整表拼进 system prompt；`personal.email` 写入、后续按 `personal_email` 读取，`.`≠`_` 精确不匹配 → miss，模型反而否认已注入的事实。**已从 journal + DB 实锤**：失败会话 `f04f8b3f` / run `c31b69b3`（02:48:05Z 启动），三条记忆（含 `personal.email` @ 02:46:58Z）在启动前均已存在且 `session.user_id`=owner ⇒ `_load_core_memory` 必然注入——**问题不是"没加载"，是"自由 key 精确匹配 + 冗余查找覆盖了已注入上下文"**，且记忆被塞进缓存前缀违反 docs/04 铁律⑤。

- **决策（用户拍板 2026-07-23）**：把记忆做成**能力维度分层**（与 ADR-004 的"归属维度两层 user/tenant"正交；tenant 共享层继续后置）：
  1. **核心记忆 core = 命名、有界、恒在上下文的 blocks**（取代 `user_memory` 整表注入）。固定少量 block：`profile`（身份：姓名/邮箱/时区/角色）、`preferences`（行为偏好）、`agent_notes`（可选）。每块 free-text（markdown），字符上限**写时**强制（超限返回"先整合"结构化错误，Hermes 式），agent 用 `append/replace/remove` 外科式编辑；乐观 `version` 并发锁。**永远整块注入 ⇒ 无 key 可猜、无查找会 miss**，从根上消除该 bug。
  2. **语义记忆 archival = 复用 `memory_passages` + pgvector + FTS + RRF**，新增**自动形成**与写入去重（SHA-256 精确 + 语义近重 → NOOP/UPDATE）。
  3. **情景记忆 episodic = 复用 Session Library 搜索（ADR-029）**，作为"相关历史对话"按需召回，**不新建并行存储**。

- **写入两路**：① **热路径**——agent 工具轮内显式写（core block 编辑 / archival note）；② **后台形成**（新，异步于回复之后，复用 arq + event journal）——读完结 run 的 transcript，抽取候选事实，跑**确定性合并**写回 core+archival；可配触发节奏（run settle / 每 N 轮）+ kill-switch。**mock 模式必须走确定性分支**（铁律：测试不调真模型，一如 `embeddings._fake_embedding`）。

- **冲突消解（本 bug 的根治）**：每次写入（热/后台）对既有记忆判定 **ADD / UPDATE / INVALIDATE / NOOP**（Mem0 双阶段 + Sydney 增量合并）。core 每主题单块 ⇒ 无 key 冲突；**变化型事实用双时态软失效**（Zep）：新事实置旧行 `invalid_at`、不硬删——保留历史 + 可时点查询 + 测试确定。mock 下合并退化为规则（规范化匹配→UPDATE、hash 重复→NOOP、否则 ADD）。

- **缓存稳定注入（修 docs/04 铁律⑤）**：core 记忆**移出静态 `SYSTEM_PROMPT` 缓存前缀**，作为独立层、**run 开始快照**、run 内不变（Hermes 冻结快照）。archival/episodic 不预注入——按需 `memory_search` 或 top-k 拼到消息**尾部**（Mem0），绝不进前缀。注入前对 block 内容做**威胁扫描**（记忆可能源自不可信邮件，ADR-009/019）。纠正 docs/02「`<memory-context>` 注入用户消息、绝不进 system prompt」被实现偏离的问题。

- **embedding = 自带 ollama 固定模型（用户拍板 2026-07-23）**：默认 **`ollama/bge-m3`（多语言含中文，1024 维）**，`infra/docker-compose.yml` 增 `ollama` 服务。理由：**维度稳定**（换模型=全量重建、非开关）、**隐私**（个人记忆不出机，ADR-019）、**可用性**（聊天 provider 未必供 `/v1/embeddings`）。故 `embedding_dim` 与 `memory_passages.embedding` 由 **1536 → 1024**；**现在做零成本**（`memory_passages` 实测 0 行，已核）。外部 provider embedding 降为**可选高级覆盖**，**与聊天 provider 解耦**。另记：聊天 provider 端点/密钥应转为用户 Setting（UI 已有模型选择器，端点/密钥配置尚无）。

- **可观测性**：run 注入 core 记忆时发 `core_memory.loaded`（labels + 字符数 + block 版本），让每次运行**自证**"看到了哪些记忆"（今天只能靠时间戳反推）；与 STATUS item0 的 `model.request`/`generations` 捕获协同。

- **契约先行（本 ADR 同批，仅改契约文档不写代码）**：`data-model`（新 `memory_blocks`；`memory_passages` 加 `origin/importance/valid_at/invalid_at` + embedding 1024；`user_memory` 处置；补 ADR-032 子节说明 supersede 冻结不变量#13）、`events`（`memory.formed` op 化 + `core_memory.loaded`）、`api`（core block CRUD + 历史，REST/Tool 双适配 ADR-023）、`config`（`EMBEDDING_*` + 形成节奏 + kill-switch）。均带 `tenant_id` 复合键（ADR-015）。

- **`user_memory` 处置**：迁移现有行 → 种入 `profile` block；表**保留为遗留只读**（Phase A 兼容，写入改走 blocks），后续迁移可移除。REST/Tool 双适配同步改，不破坏 ADR-023。

- **分阶段**：Phase 0=（暂缓的）最小补丁（key 规范化 + get 回退 + 写合并去重 + "core 已在上下文、勿重查"提示）；**Phase A**=core blocks + run 快照 + 缓存稳定层 + 外科编辑工具 + ollama/1024 切换 + Memory UI blocks；**Phase B**=后台形成 + importance/recency 评分；**Phase C**=证据门（双时态历史 UI、混合评分检索、reflection；需 golden 集 MRR/答准提升≥10% 才留，比照 ADR-029 Phase C）。

- **取代/修订关系**：**扩展 ADR-004**（user 私有记忆升级为分层；tenant 共享层仍后置）；**修订 ADR-012**（pgvector 已部分落地，此处确立 embedding 走自带 ollama、**不引入独立向量库**）；纠正 docs/02 注入点偏差。**不改**：ADR-015 租户键、ADR-016/017 真相源与 effect 语义、ADR-019 密钥、ADR-023 能力层双适配。

- **验收关键**：写 `personal.email` 后**新会话直接可用**（不再 miss）；core 记忆**不破坏缓存前缀**；后台形成在 mock 下**确定性可回归**；embedding **全本地、维度=1024、零残留**；`user_memory` 迁移后旧事实在 `profile` 可见；UI Memory 页展示 blocks + archival 来源(你/自动) + 失效历史。契约**先于代码**（本 ADR 同批）。

- **来源**：R-MEMORY 调研 [`research/memory.md`](research/memory.md)（Letta/MemGPT · Hermes · Sydney · Mem0 · Zep/Graphiti · Generative Agents · LangMem · Anthropic/OpenAI）；用户输入「我倾向先用ollama」「3」（先起草 ADR + 契约再定实现顺序）；bug 实锤见本会话 journal+DB 反推。

### ADR-033 · Agent 可观测性 = OpenTelemetry `gen_ai` span（ADR-016 日志之上的薄诊断层）+ 可选自托管 Phoenix 后端（源自 R-OBSERVABILITY）

> **状态：已接受（owner 拍板 2026-07-24）。** 5 个开放问题按推荐锁定（见下）。先契约后代码:契约(config/events)已写;实现从 **Phase A** 起(AGENTS.md §1)。完整调研见 [`research/observability.md`](research/observability.md)。
>
> **锁定的 5 个决策**:①后端=**自托管 Phoenix**(复用现有 Postgres,可选、默认关);②耐久记录=**span + 有界脱敏 `model.request/response` 事件**都要(span 深度、事件耐久 ADR-021);③内容采集=**默认全关**,dev flag 打开且脱敏;④打点=**手写**循环/工具/chat span(Sherpa 用原生 httpx 流式 + 自研循环/工具,主流 auto-instrumentation 覆盖不到,OpenLLMetry 留作未来引入主流 SDK 时的备选);⑤排序=**独立先做**(与 ADR-032 协同但不互相阻塞;它正好帮调试包括记忆在内的一切)。

- **背景**：Sherpa 已有很强的**领域级**可观测（ADR-016 事件日志=真相源；`traces`/`generations` 投影；结构化日志 + 关联 id）。但**缺 LLM 调用级**观测：chat 循环不写 `generations`，没有"装配后的真实 prompt"、没有 per-call token/延迟（现 token 靠 ~4 字符估算、cost=0）。这正是 STATUS item0 推迟的一半，也是本会话查 memory bug 时只能靠时间戳反推的盲点。docs/07 早已预留"专业化再接 Langfuse"。

- **决策**：引入 **OpenTelemetry GenAI 语义约定（`gen_ai.*`）作为线格式的薄诊断层**，而**非**新平台：
  1. **打点有界循环**（`core/loop.py`）：每 run 一个 `invoke_agent` 根 span；每次模型调用一个 `chat` span（provider/model、temperature/max_tokens、`response.finish_reasons`=循环 `stop_reason`、`usage.input/output_tokens`、延迟）；每个工具一个子 `execute_tool` span（`gen_ai.tool.name`、`gen_ai.tool.call.id`；工具返回错误观测时 `status=ERROR`+`success=false`——**错误即观测**，契合铁律）。根 span 带 `agent.loop_count`/`agent.total_cost_usd`/`agent.stop_reason`。
  2. **日志仍是真相源（ADR-016/021）**：OTel span 是**派生、易失、可采样**的诊断面，用 `run_id`/`session_id` 关联，**绝不替代**日志；后端挂了不影响 run，span 可日后从日志重投。
  3. **补上 STATUS item0**：`chat` span 即"generation record"（model/prompt_version/tokens/stop_reason）；可选再落一条**有界、脱敏的 `model.request`/`model.response` 日志事件**（durability=debug；ADR-021 有界脱敏；只存装配输入的 **sha-256 摘要**、不存内容）作耐久记录，而**完整装配 prompt 只进 span**、受内容开关控制、短保留。
  4. **内容默认不采集**（隐私，ADR-019）：`gen_ai.input/output.messages`、`tool.call.arguments/result` 等内容属性 opt-in，由 `OTEL_CAPTURE_MESSAGE_CONTENT`（默认 false）控制；开启也经 `security/redaction.py` 脱敏，密钥永不入 span。
  5. **确定性测试**（铁律：测试不调真模型）：`InMemorySpanExporter` + mock provider，断言 span 树结构/属性/错误状态，快照回归。

- **后端选型**：**首选自托管 Arize Phoenix**——单容器、OTLP 原生（gRPC+HTTP）、**复用现有 Postgres**（单独 db/schema，不新增数据库）、自动把 `gen_ai.*` 转 OpenInference。**修订 docs/07 的"接 Langfuse"默认**：Langfuse 现为 6 服务栈（含 ClickHouse ≥4G），对单用户过重；因线格式是 OTLP，后端可随时换回 Langfuse（若要其 prompt 管理/评估 DX）。**后端默认关闭、可选**；无后端时 SDK 用 console/in-memory exporter 即可。

- **与 ADR-032 协同**：与记忆的 `core_memory.loaded` 事件互补——那是领域事件，这是 span 树 + `model.request/response` 耐久记录。

- **契约先行（本 ADR 同批）**：`config`（`OTEL_*` 键）、`events`（可选 `model.request`/`model.response` debug 事件）。`generations` 表已存在，Phase A 让 chat 循环也写它即可，**无需新表**——span 后端持有树。均带 `tenant_id`（ADR-015）。

- **分阶段**：**Phase A**=OTel SDK + `gen_ai` 属性包一层（隔离 semconv 改名）+ 循环打点（chat/execute_tool）+ 真实 per-call token 取代字符估算 + `InMemorySpanExporter` 测试；补上 item0；**无新基座**。**Phase B（owner 2026-07-25 选定为「看完整 prompt」的实现路径，取代自研 ADR-035）**=**内容采集进 span**（`otel_capture_message_content` 开时，把装配后的 system+memory+transcript messages、tool schemas、response 写成 **OpenInference span 属性**（`llm.input_messages`/`llm.output_messages`/`llm.tools` + `openinference.span.kind`），结构化部分经 `security/redaction` 脱敏 + size cap）＋**可选 Phoenix 容器**（复用 Postgres、compose profile、默认不随核心栈起）＋ OTLP exporter（config 开关）＋保留期。借 Phoenix 现成 UI 看每次调用完整 prompt/response/瀑布，免自研 inspector。**Phase C**=评估/飞轮（judge + 人工分；失败 run（`stop_reason=error`/`loop_count>N`）导出 `datasets/regression.jsonl`，CI 跑 mock）——**证据门**，比照 ADR-029 Phase C。

- **护栏**：单用户 **100% 采样、无需 Collector**（SDK 直连本地后端）；span 名**低基数**（`chat gpt-4o`，高基数值进属性）；GenAI semconv 处 **Development** 状态、会改名（`gen_ai.system`→`gen_ai.provider.name` 已发生）→ 所有 `set_attribute` 收拢进**一个 wrapper 模块**隔离变动。

- **取代/修订关系**：**修订 docs/07** 可观测后端默认（Langfuse→Phoenix，footprint 理由）；**落实 STATUS item0** 推迟的 LLM 调用级观测。**不改**：ADR-016 日志真相源、ADR-021 审计/调试事件边界（内容进 span 非审计）、ADR-019 密钥脱敏、ADR-015 租户键。

- **验收关键**：每次 LLM 调用可见装配输入/token/finish_reason（不再靠反推）；默认无内容泄漏、无密钥进 span；日志仍是真相源、后端挂不丢 run；测试用 `InMemorySpanExporter` 确定性通过；Phoenix 复用现有 Postgres、默认关闭、一开关接通。

- **来源**：R-OBSERVABILITY 调研 [`research/observability.md`](research/observability.md)（OTel GenAI semconv · Langfuse · Arize Phoenix/OpenInference · OpenLLMetry · landscape）；用户输入「起草 ADR-033 + 契约 diff」；STATUS item0 推迟的可观测记录。

### ADR-034 · 待审批入口 + 可配置预授权（grants）——让后台/定时任务的外部动作既安全又可自动化（源自定时发邮件用例）

- **决策（用户拍板 2026-07-23）**：把审批从"只在 Chat 页随 SSE 弹出"扩成两件事：**(A) 独立的「待审批」前台入口**（列出所有 pending 审批、可 Approve/Reject，覆盖后台/定时任务产生的审批）；**(B) 可配置的「预授权」grants**（用户配置的自动放行规则，如**收件人白名单**：给自己个人/工作邮箱发信免逐次审批直接发）。二者组合覆盖绝大多数定时/自主场景（定时发邮件、发 QQ、写码跑码、未来能力）：**能预授权的直接自动做，不能预授权的进待审批入口等人工**。
- **动机**：现有实现（recon 2026-07-23）——`permissions/policy.evaluate(tool)` **只看 `tool.flags`**（看不到 args/收件人/用户配置），外部动作一律 `ask`；审批 **nonce 只随 `permission.asked` SSE 事件下发**、`GET /permissions` 不含 nonce，故**后台/定时任务的审批在没有开着的 Chat 流时无法被批准**（InboxView 只能「Review in chat」）；`allow_session`/`always` 选项存在但 **resume 对所有 allow 一视同仁、grants 未落地**。定时发邮件因此每次都卡在无法操作的审批上。
- **(A) 待审批入口 —— Web 解析改为 nonce 可选（**协调 ADR-020**）**：
  - **决策**：`POST /permissions/{id}/resolve` 对 `channel='web'` 的 owner 解析，**以 会话Cookie(HttpOnly) + CSRF + `authorized_decider` 相等 + 完整 binding 校验（correlation/run/invocation/tool/scope/session/effect/args_hash/policy_version）** 为准，**nonce 变为可选**（提供则仍校验）；**非 web 渠道（QQ/邮件）仍要求 nonce**（那里 actor 身份更弱）。理由：nonce 的初衷是"确保决策者看到了那次 ask + 单次防重放"，对已强认证的 owner Web 会话，session+CSRF+binding 已等价达成；DB 仍只存 `nonce_hash`（不落明文）。这落实了 STATUS 早已标注的"web 的 nonce 交付需决策"。
  - 前端新增 **Approvals 页（SPA 路由 `/approvals`，避开 API 前缀 `/permissions`）**：列 pending（工具、预览、来源 run/session、到期），Approve(`allow_once`/`always`)/Reject，直接调 `/permissions/{id}/resolve`（web、无需 nonce）；侧栏入口带未决计数；InboxView 的审批项也接上真正的解析（不再只跳 chat）。
- **(B) 预授权 grants —— 让 policy 变 args/上下文感知**：
  - **新表 `permission_grants`**（见 data-model 契约；tenant+user 作用域，ADR-015 复合键）：`tool_name` + `match_json`（工具专属匹配规则，如 send_email 的收件人白名单 `{"recipients":["a@x.com"]}`）+ `scope` + 审计字段。**owner 专属配置，agent 不得自建 grant**（不给 agent 工具、无 agent 可写 REST）。
  - **匹配器登记表**（每 `tool_name` 一个纯函数 `matches(match_json, args)->bool`）：首发 **send_email**（`args["to"].lower() ∈ recipients`，精确、默认不通配）；结构可扩展到 QQ 目标、代码执行等。
  - **循环集成**：`core/loop.py` 命中 `ask` 后、建审批信封**之前**，查匹配的 grant → 命中则**自动放行**（照常 `begin_invocation` 记 effect + 执行 + **写审计回执**，**不建 envelope、不停机**）。未命中才建审批（现有行为）。审计上标注 `auto_approved_by_grant`，留痕可查（ADR-021）。
  - **`always` 落地为建 grant**：用户在待审批入口选 `always` 时，从该动作 scope 派生并**持久化一条 grant**（如 send_email→把本次收件人加入白名单），实现"批一次、以后自动"。`allow_session` 仍限本 session（v1 可先等同 `allow_once` + 建议用 `always`；session 级持久留后续）。
- **安全不变量（不得违反，复用 ADR-019/020/021）**：grant 仅 owner 配置、tenant+user 作用域、**精确匹配不默认通配**；自动放行**仍记 effect_invocation + 审计回执**（有据可查）；grant 绝不含密钥；解析仍做完整 binding + args_hash + policy/actor 校验，`permission_scope` 不可被客户端放大；非 web 渠道 nonce 不放松；审批预览仍为语义纯文本、渲染端转义。
- **明确不做（本阶段）**：通配/正则 grant、跨 user 共享 grant、时间窗/次数限额 grant、per-session `always`（session 持久）、把 grant 开放给 agent 自建——留后续 ADR。
- **验收关键**：后台/定时任务产生的审批能在 `/approvals` 页被 owner 批准并真正执行；给白名单收件人的定时邮件**免审批自动发**、非白名单仍进待审批；`always` 建出可见 grant、之后同类动作自动放行；自动放行有审计回执；非 web 渠道仍需 nonce；零跨 user/tenant 泄漏；契约（data-model `permission_grants` + api §4.7/新 grants 段）先于代码。双路径 Playwright：人工（批一个后台审批 + 配一条邮箱白名单 → 定时邮件自动发）+ agent（定时任务命中白名单自动执行，agent 无法自建 grant）。
- **来源**：用户输入「1.待审批入口，适用场景:实在不能预授权但是又想让它定时做的…需要我人工审批。2.可设置的预授权，例如设置邮箱地址白名单，给我自己的个人邮箱工作邮箱发邮件都不需要我审批，直接就能发…定时发QQ消息、写代码跑代码以及未来…都能被这两种覆盖」；recon 见本会话审批/策略架构调研。

### ADR-035 · 内建「调试快照」inspector = provider-call 边界抓完整装配 prompt + response，短保留、owner-only、默认关（扩展 ADR-033 §决策3；源自微软 Copilot `/debug` 参照 + R-DEBUG-CAPTURE 调研）

> **状态：暂缓 / 备选（owner 2026-07-25 选择走 ADR-033 Phase B「Phoenix」路线）。** 「在 provider-call 边界捕获完整装配内容 + 脱敏」这一**核心工作两条路共用**，已并入 **ADR-033 Phase B**（写成 OpenInference span 属性 → 自托管 Phoenix UI）；本 ADR 的自研 `llm_call_debug` 表 + `/inspector` 页**后置**，仅在未来需「Sherpa 内嵌 / 去容器化 / 完全自控保留（TTL、不进 journal）」时再启用。理由：效果相同下 Phoenix UI 更强（瀑布/搜索/聚合/prompt 重放）且免自研 UI，代价仅多一个可选容器。调研见 [session `files/debug-ui-research.md`]（8 产品，含源码出处）。

- **决策**：加一个**内建的、短保留的「LLM 调用调试快照」+ dev-only inspector 页**，让 owner 能看到 agent 循环内**每次 LLM 调用的完整装配输入**（system prompt + 注入的 memory 块 + tool schema 列表 + 全部 user/assistant/tool 消息）＋完整 response ＋ token/延迟/工具步骤——即微软 Copilot `/debug` 那种深度，但**短保留、默认关、脱敏、owner-only**。
- **动机 / 现状缺口**：你在 OTEL trace 里看到 `invoke_agent/chat/execute_tool` 却看不到具体 message/tool 内容，是 ADR-033「内容默认不采集」的**有意**结果。ADR-033 §决策3 让**完整装配 prompt 只进 span**（Phoenix）、journal 的 `model.request` 只存 **sha-256 摘要（no content）**。但那要求 owner **先立起 Phoenix** 才能看 prompt。本 ADR 补一个**内建的第三存储层**（短保留 DB 调试 store），让完整 prompt **在 Sherpa 内就能看，无需任何 trace 后端**——**扩展而非取代 ADR-033**。
- **调研（R-DEBUG-CAPTURE，`files/debug-ui-research.md`）**：LangSmith / Phoenix·OpenInference / Langfuse / OpenHands / Cline·Roo / Vercel AI SDK / Semantic Kernel·AutoGen / aider **8 个产品无一例外都在同一处捕获——单一 provider-call 边界**，快照 `messages[](system/memory/history) + tools[] + response + tokens + latency`。共同模式＝**捕获边界 → keyed record `{run_id, turn, messages, tools, system, response, tokens, latency}` → 短保留（gated）→ per-call inspector**。gate 多为 opt-in（`DEBUG=1` / SDK init / per-call flag）；短保留靠 TTL / ring buffer；隐私最佳＝OpenInference 分字段脱敏 flag ＋ Semantic Kernel 的 Presidio PII 过滤。
- **设计要点**：
  1. **捕获点**：`core/loop.py` 的 provider-call 边界（即现在写 `model.request` 事件那一处）。`debug_capture_enabled` 开时，快照该次调用的 `{messages(system+memory+transcript+tool-results)、tools schemas、response(text+tool_calls+finish)、input/output_tokens、latency_ms}`——`provider_messages` ＋ `schemas` 本身就是完整装配输入。
  2. **存储＝新表 `llm_call_debug`，不进不可变 journal**：内容是 PII/易失，必须**可删＋TTL**；ADR-016 journal 是**真相源**，不塞 PII 调试内容（守 ADR-016/021 边界）。带 `tenant_id`＋复合键（ADR-015）、`expires_at`；内容经 `security/redaction` 脱敏、每字段大小上限截断（`…[truncated]`）。
  3. **保留**：TTL（`debug_capture_ttl_hours` 默认 48h）＋**后台 GC**（复用 Drive maintenance cron 模式）＋每 session 调用数上限（`debug_capture_max_calls_per_session`，ring-buffer 丢最旧）——**短保留是设计要求**（正合「数据量大、保留时间短」）。
  4. **gate**：新 config `debug_capture_enabled`（默认 **false**），**独立于 OTEL**（这是 DB inspector，不是 span；OTEL 的 `otel_capture_message_content` 走 span 内容那条路，二者正交）。
  5. **隐私（ADR-019 铁律）**：owner-only API＋UI；内容脱敏；size cap；TTL；**不进 journal**；数据明确标为 PII 调试；密钥永不入（API key 在 header 不在 messages，仍防御性脱敏）；**不给 agent 工具**（agent 不读也不建调试快照）。
  6. **UI**：dev-only inspector（**SPA 路由 `/inspector`，避开 API 前缀 `/debug`**）。按 run 列每次 chat 调用；点开分区看 **System / Memory（高亮 system 内的记忆块）/ Tools（schema）/ Messages（role+content）/ Response ＋ token/延迟 ＋ 工具步骤瀑布**。导航入口 owner/dev 门控。
  7. **API**：owner-only `GET /debug/sessions/{id}/llm-calls` ＋ `GET /debug/runs/{id}/llm-calls`（列表＋详情）。
- **契约先行（本 ADR 同批草案，DBG.0 冻结）**：
  - `config-and-secrets`：`debug_capture_enabled`(false) ＋ `debug_capture_ttl_hours`(48) ＋ `debug_capture_max_calls_per_session`(200) ＋ `debug_capture_max_bytes`(单条上限, 如 256 KiB)。
  - `data-model`：新表 `llm_call_debug`（`tenant_id,id,run_id,session_id,call_index,provider,model,request_redacted jsonb, response_redacted jsonb, input_tokens,output_tokens,finish_reason,latency_ms,created_at,expires_at`）＋ TTL/GC 索引 ＋ 复合键 ＋ `tenant_id`（ADR-015）。
  - `api.md`：新 owner-only debug 段。
  - `events` §2.7 **不变**（`model.request/response` 仍 digest-only 耐久）——内容版是**新表**、非 journal 事件（守 ADR-016/021）。
- **护栏/不变量**：journal 仍真相源、不塞 PII；默认关、owner-only；脱敏＋size cap＋TTL；SPA 路由避开 API 前缀；`llm_call_debug` 带 `tenant_id`＋复合键（forward-compat，勿删）。
- **明确不做（本阶段）**：把内容采进 span（那是 ADR-033 的 OTEL 路径，仍可选并存）；prompt playground 重放（后置）；跨 user 共享调试；无限保留；自定义脱敏规则（用现有 `security/redaction`）。
- **取代/扩展关系**：**扩展 ADR-033 §决策3**（新增内建短保留内容 store，使完整 prompt 无需 Phoenix 即可查）；**不改** ADR-016 日志真相源、ADR-021 审计/调试事件边界、ADR-019 密钥脱敏、ADR-015 租户键。
- **分阶段**：单阶段 **Phase OBS-DEBUG**（DBG.0–DBG.V，见 IMPLEMENTATION.md）。
- **验收关键**：开 flag 后 owner 在 `/inspector` 能看某 run **每次 LLM 调用的完整装配 prompt（system+memory+tools+messages）＋ response ＋ token/延迟**；关 flag 时**零捕获零开销**；快照 TTL 到期被 GC 删除、每 session 有上限；API/UI **owner-only**；内容脱敏、密钥不入、超限截断；**不进 journal**；双路径 Playwright（agent：开 flag 跑聊天→inspector 显示装配 prompt；人工：点 `/inspector` 页并做 UX 评审）。
- **来源**：用户输入「微软 Copilot `/debug`… 几乎能看到 agent 循环内每一步的详细内容，能看到发给 LLM 的完整 prompt，包括 system prompt, memory, tool list, assistant and user messages… 因为过于详细数据量比较大，保留时间比较短… 我想知道我们能不能做到类似效果？代价是什么？已知的开源 agent 有没有这样做的，怎么做的」；R-DEBUG-CAPTURE 调研 [`files/debug-ui-research.md`]（8 产品 + 源码出处）。

### ADR-036 · 来源型个人知识库（Knowledge）——文件型来源 + 版本化异步摄取 + zhparser/pgvector 混合检索 + 结构化引用（源自 R-KNOWLEDGE-BASE；与 ADR-004/032 记忆并列而非扩展，复用 ADR-012 pgvector 与 ADR-030 Drive/ADR-023 能力层）

> **状态：方向 + 5 项 go-gate 已由 owner 拍板（2026-07-26）；先契约后代码——本批次写 ADR + 冻结契约增量（data-model/api/events/config）+ 能力矩阵行 + 检索 golden 集，不写业务代码，落地顺序 KB0→KB5（AGENTS.md §1）。** 完整调研见 [`research/knowledge-base.md`](research/knowledge-base.md)；静态 UI 稿见 [`design-knowledge/index.html`](design-knowledge/index.html)。

- **背景（为何要、为何独立）**：现有 RAG（`memory_passages`）是**手工语义笔记本**，不是文档知识库——一个 passage 既是来源又是检索单元，没有父文档/多 chunk、没有解析/切块、没有异步索引状态、没有来源版本/原子激活/重建、没有结构化引用、词法分支硬编码 `english`（中文只能靠向量）。**决策：另建 `knowledge_*` 子系统**，与 core/archival 记忆**平行且独立**（笔记 vs 来源型文档的**归属/版本/切块/引用/删除语义互不兼容**，§9 调研已否决"扩 `memory_passages`"）。四能力边界：core=关于你的稳定事实（KV/块）· archival=你/agent 写的语义笔记 · **Knowledge=外部文档，带出处检索** · episodic=会话搜索。

- **决策（owner 拍板 2026-07-26，5 项 go-gate 全过）**：
  1. **首版 = 私有、文件型知识库**（单个隐式私有库）。**不做**：爬虫 / 连接器同步 / 多命名库 / 团队共享 / OCR / 独立向量库（均后置，各带后续 ADR）。
  2. **embedding = 复用 ADR-032 自带 `ollama/bge-m3`（1024 维，本地、文档不出机）**，固化为一个**已审 embedding profile**；换模型/维度 = 新 profile 下**整库重建**，绝不在一个索引里混两个向量空间。
  3. **中文词法 = `zhparser`**（Postgres CJK 分词扩展）藏在**稳定逻辑配置名 `sherpa_text`** 之后；app 层 jieba 分词为**文档化回退**（KB0 先做 timeboxed spike 验证 zhparser 能在 `pgvector:pg16` 上装起来 + 小 golden 集中文召回；不过则走回退）。与 pgvector 向量分支经 RRF 混合；`pg_trgm` 仅作语言无关模糊信号。**同 bug 也在 `memory_passages`**，可先做独立小修当试点。
  4. **先出静态 UI 流 + 检索 golden 集，再动后端**（研究强制前置；静态稿已交付、owner 已过目）。
  5. **进路线图**（强用户价值，owner 认可排在 #7 GitHub 之前的候选；最终插位仍由 owner 定）。

- **数据模型（canonical 与派生分离，§5.1/§5.2）**：`knowledge_sources`（用户可见来源，绑一个 Drive `file_id`，`status`、`active_version_id`、单调 `desired_generation`、可选 tombstone）→ `knowledge_source_versions`（每版一份**不可变对象存储快照** `snapshot_object_key`——因 Drive overwrite 会删旧 blob，快照对解析/预览/引用是 canonical，直到该知识版本被清除；带期望 file 版本/哈希、解析器/pipeline 版本、embedding-profile、语言、状态、计数、有界失败码、幂等键）→ `knowledge_chunks`（源版本 FK、稳定 ordinal、原文、token 数、heading path、page/offset 定位、内容哈希、版本化 `sherpa_text` 词法 `tsvector`、pinned profile 下的向量）。另：`embedding_profiles`（provider/model/dim/normalize/privacy，首版一条已审 active）· `knowledge_ingestion_jobs`（源/版本/generation 绑定、stage、lease/claim owner、attempt、具名终止原因——支持有界重试/恢复/fencing）· `knowledge_retrieval_evidence`（retrieval 调用 ID、run/tool-call 绑定、全局唯一引用 ref、源/版本/chunk ID、有界摘录、保留/tombstone——可重放但可删，**文档正文不进 append-only journal**）。**每表带 `tenant_id` + 复合租户键**（ADR-015；团队共享/RLS 随通用多用户门）。

- **摄取管线（durable + 分阶段可恢复 + fencing，§5.3）**：API 事务里 ①校验调用者拥有该文件 ②建/更 `knowledge_sources` ③推进 `desired_generation` 并建 queued 版本/job（带期望 file 版本/哈希 + 确定性幂等键）④同事务写 outbox/恢复记录（复用 ADR-016/017，至少一次；恢复扫描重投）。Worker 有界阶段：**认领并快照**（校验 file 版本/哈希仍一致→拷到不可变 key；变了/丢了→具名终止，绝不索引错字节）→ **读+校验**（allowlist MIME/大小/页/时限；首版无归档解压/远程抓取）→ **解析归一化**（无工具；去 HTML/脚本活性；留 页/标题/offset）→ **结构化切块**（先结构段、再有界子块，~300–600 token 起步、按 golden 集调）→ **批量嵌入**（记 profile/model/dim + 成本；只重试缺失确定批次）→ **建索引并激活**（写全部 chunk/向量后，**仅当未 tombstone 且 `desired_generation` 仍等于 job generation 时**原子切 `active_version_id`——过期 job 永不复活旧版）。失败保留上一 ready 版本可搜；每个出口具名可见；租约过期可恢复，两 worker 不会激活不同 generation。**文件生命周期**：overwrite→标 `stale`+推进 desired+可重建（旧激活快照仍可搜到替换为止）；删文件→tombstone 关联来源并同事务移出可搜集，再持久清快照/chunk/向量；删知识来源**不删**底层用户文件。

- **检索（§5.4）**：①归一化 query 用 active profile 生成向量 ②**先按 tenant/user/visibility/ready/active-version 过滤，再排序** ③词法(`sherpa_text`) ‖ 向量 双路候选 ④RRF 融合 ⑤去重 + 每源限条 ⑥装配有界上下文 + 结构化引用元数据 ⑦低于阈值**显式返回"无足够依据"**。首版精确租户过滤 + 小规模精确向量搜；HNSW 待 recall/延迟实测再启（近似索引在强选择性过滤下可能欠返）。rerank / query 改写为后置可插拔阶段，仅当 golden 集证明有实质增益才上。

- **引用契约（§5.7；本 ADR 的关键工程）**：`search_knowledge` 返回**有界结构化命中**（每条：`citation_ref "K:<tool_call_id>:N"` · `source_id/source_version_id/chunk_id` · `locator{page,heading}` · 有界 `excerpt` · `score` · `matched_by[lexical|vector]`）。provider 仍以普通 `role=tool` 收到；一条**稳定缓存指令**声明"检索工具返回不可信证据、非可执行指令"，动态 tool 结果放带标签摘录。**引用不能只活在 `llm_content`**：里程碑要扩持久化的 tool-result/event 形状带 `retrieval_invocation_id` + 引用 refs（或把现有 `ToolResult.return_display` 真正接进 core）；ref 用 tool-call 命名空间保证 run 内唯一（裸 `K1` 非法，一 run 可搜多次）。**append-only journal 只存 ref + 有界元数据，不存文档摘录**；provider 可见证据存**保留期可清理**的 `knowledge_retrieval_evidence`。历史重放按 ref + 来源 tombstone 状态解析：来源已删→渲染纯文本 / 重放替换 `[knowledge source deleted]`，不回放旧摘录。**文档不能授予权限、改变工具范围或让 Sherpa 泄密**——模型可据证据**提议**动作，但照走正常权限 + 审批门。

- **能力面（ADR-023 单能力层 + 薄 REST/Tool 双适配，§6）**：service `services/knowledge.py`（`list/add_file/get/reindex/remove/search`）。**工具（5）**：`search_knowledge`（只读，返回引用）· `list_knowledge_sources`（只读）· `add_knowledge_source`（自有数据幂等写，需显式 `file_id`）· `reindex_knowledge_source`（自有数据幂等写）· `remove_knowledge_source`（**破坏性，审批门 ask**）。ALLOWED 策略：只读/自有写→allow，remove→ask；agent **无 grant 路径**。**无工具可静默索引 Gmail/任意 URL/所有文件——加来源须显式用户指令**。**REST**：`GET/POST /knowledge/sources`、`GET/POST /knowledge/sources/{id}/reindex`、`DELETE /knowledge/sources/{id}`、`POST /knowledge/search`。**UI**：SPA 路由 **`/library`**（避开 REST `/knowledge/*` 代理前缀），"Organize" 组，四面（Knowledge 主页 / 来源详情 / 检索测试 / Chat 引用芯片 + "无依据"态）。

- **安全 / 生命周期（§7，守 ADR-009/019/021）**：用户选的文件仍是**不可信文档**，解析/切块**无工具、有界**；未来连接器/web 源需专用适配器 + 与 `CONNECTOR_ANALYSIS` 同等隔离（不进带工具的模型调用）；HTML 消毒、不拉远程子资源；OCR/归档/爬虫/可执行格式后置。每条 query SQL 层先按 tenant/user/visibility 过滤再排序。**导出/删除**覆盖来源元数据/快照/版本/chunk/向量/保留期证据；删来源撤销引用链、历史重建替换 `[knowledge source deleted]`，但**已持久化的过往助手文本仍归 session 保留/删除治理**（UI 要讲清这个区分，别暗示删源会改写每条旧答）。审计收据记 add/reindex/remove + 哪些被引来源支撑了某次 grounded 回答（不存思维链）。

- **评估 / 发布门（§8，ADR-024 单用户姿态）**：**不建通用 RAGAS 平台**——先小确定性回归 lane，广评估留 roadmap #11。**上线前最小 golden 集**：20–30 代表文件 · ≥30 条中/英/混查询（精确名/编号/日期 + 语义改写 + 无答问）· 期望 源/chunk 标注。**门**：Recall@5 ≥ 0.85；每条渲染引用可解析到 active 源版本；service + raw-query 隔离测试**零跨用户/租户**结果；重复 add/reindex 幂等；失败 reindex 保留上一版本可搜；删除即时移出检索且最终无孤儿 chunk/向量；**中文精确词走词法信号而非向量兜底**；有界检索/上下文输出 + 显式无依据。trace/audit 记检索延迟/候选数/源多样性/active profile/引用是否存在。

- **取代/修订关系**：**与 ADR-004/032 记忆并列**（Knowledge 是独立能力，不扩 `memory_passages`）；**复用 ADR-012 pgvector**（确认仍不引独立向量库）、**ADR-030 Drive**（来源来自 Drive 文件、快照进 MinIO）、**ADR-023 能力层双适配**、**ADR-016/017 journal+outbox 真相源/effect 语义**、**ADR-009/019 不可信内容 + 密钥**、**ADR-015 租户键**。**微调 ADR-021**：核心执行须**把 provider 可见 `llm_content` 与脱敏耐久事件载荷解耦**（引用/元数据进 journal，摘录进保留期证据表）——为引用契约所必需。中文词法配置名 `sherpa_text` 同时用于修 `memory_passages` 的 `english` 偏差。

- **分阶段（KB0→KB5，见 IMPLEMENTATION.md）**：**KB0**（本批）= ADR + 契约增量 + 能力矩阵行 + 静态 UI（✅）+ 检索 golden 集 + zhparser/embedding spike；**KB1** = 源/版本/job schema + 文件型生命周期；**KB2** = 有界解析器 + 结构化切块 + embedding profile + 异步索引；**KB3** = 混合检索 + 引用 + 中英文 golden 测试；**KB4** = services + REST + 工具 + 权限策略；**KB5** = Knowledge UI + chat 引用 + agent/人工 Playwright 双通道。

- **验收关键**：加一个 Drive 文件为来源→看到诚实的索引阶段与有界失败原因→检索看到带 源/页/标题 的摘录→chat 里 `search_knowledge` 作答并给**可点回原文精确位置**的引用；覆盖文件→源转 `stale` 可重建到新版本而不暴露半成品索引；删来源→即时消失且派生行持久清除；中文精确词命中**词法分支**；无依据**显式**说明；每表带 `tenant_id` + 复合键。契约**先于代码**（本 ADR 同批）。

- **来源**：R-KNOWLEDGE-BASE 调研 [`research/knowledge-base.md`](research/knowledge-base.md)（OpenAI file search · Anthropic Contextual Retrieval · Open WebUI · Dify · pgvector/PostgreSQL）；用户输入「用zhparser吧，其他4个问题按你的建议来。开始起草，然后我想先看看静态UI什么样」「ok，我对UI也没太大问题」。
