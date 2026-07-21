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
