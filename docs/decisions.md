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
| 2026-07-27 | Workspace 产品模型 + Projects 上线顺序 | ✅ **Workspace 为总入口**，Projects 与 Drive 并列；实现顺序 **W2a→W2b→W3→W4**；**W2a=空/模板/归档导入（不含 GitHub）**，GitHub 一次性导入放 W2b；W3 沙箱**仅挂一次性 scratch 副本**、绝不挂项目真相源，且 ADR-025 正式修订在 W3 开始前经隔离评审后进行；W3 前先加固 docker.sock/多用户隔离。本批次**只做契约与设计先行**（不写生产代码/迁移/不暴露 Projects 导航） | 新增 ADR-037（延伸 ADR-030；源自 R-WORKSPACE-PRODUCT）|
| 2026-07-27 | Projects W2b = GitHub 一次性导入的契约与设计先行 | ✅ **一次性导入**（选 repo + ref：**branch/tag/commit 三者皆首版**，先 ref→OID 解析并**钉住 OID**）→ **有界归档获取**（GitHub `tarball/{ref}` 只含内容、无 git 历史，复用 W2a 内存安全解压器，**不用 git clone/不落 .git**）→ 记录 source repo/ref/OID provenance → 物化为不可变初始快照；**导入后项目独立存活、远端非权威源**。凭据 = **AEAD vault 内的 GitHub connection**（首版 fine-grained PAT `contents:read`，`auth_kind` 可扩展到 GitHub App 安装令牌），**绝不进树/快照/prompt/日志/工具结果/沙箱**。只读拉取**幂等**、无 `effect_unknown` 远端对账（那是 W4 push）。GitHub 导入**不给 agent**（人工，跨凭据+不可信外部内容边界）。**本批次只做契约与设计先行**（无生产代码/迁移/不暴露 W2b 导航） | 新增 ADR-038（延伸 ADR-037；复用 ADR-019/030；源自 R-WORKSPACE-PRODUCT §9）|
| 2026-07-27 | W3 前置：沙箱隔离安全评审（docker.sock/多用户威胁模型 + 隔离方案选型） | ✅ **独立安全评审结论**：worker 挂 `docker.sock` ≈ 宿主 root（OWASP Rule#1；只读挂载无用）；当前「socket 只给可信编排进程、不可信代码只在派生容器、容器绝不碰 socket」的**专用 sandbox 编排**模式正确、须保持。W3 首版**仅**给沙箱加**一个一次性 scratch 只读拷贝的 RW 挂载**（`nosuid,nodev` + 拷贝前剔除/断言无凭据 + 路径校验 + 编排方原子清理 + 孤儿扫除），保留全部 ADR-025 硬化，且**只在自托管单用户**可接受（推荐补 rootless Docker + patched runc）。**明确禁止上线条件（多用户/真不可信第三方代码前必须先做）**：gVisor(`runsc`) 或 microVM(Kata/Firecracker) 运行时 + 不共享 docker.sock/每租户 scratch 隔离 + 租户级出口策略 + 聚合配额 + 威胁评审——未实施的缓解**绝不写成已安全**。socket-proxy 对本编排角色是**假安全**（需放行 create/exec 即等于放行逃逸），不采用 | 新增 ADR-039（落地 ADR-037 §决策4 前置硬门；证据见 R-WORKSPACE-PRODUCT + 独立评审）|
| 2026-07-27 | W3 = Project Chat 任务工作副本 + 一次性 scratch 沙箱 + 变更评审的契约与设计先行 | ✅ 首条变更动作**惰性开**跨 turn **持久任务工作副本**（base=当前 head 快照）；**真相源 = Sherpa 快照 head**，工作副本 overlay 是**持久任务态**，scratch 卷/热容器是**可丢缓存**；每次执行**物化一次性 scratch 拷贝**、有界批次后**持久化 overlay**；**Change Review** 展示 added/modified/deleted + artifacts；用户 **Save selected / Save+checkpoint / Discard**（**Save 不给 agent**，人工评审闸）；head 移动 → **stale Save 用 head_generation CAS 拒绝**（`conflicted`，须重评审 rebase）；**single-writer lease + fence**（stale fence 不能发布 overlay）；缺依赖 → 显式 `environment_missing_dependencies`（绝不联网装包）；内置 file/edit/run/test 工具在 scratch 上工作，**不嵌 coding agent**；**不做 git init/history/commit/branch、不做 GitHub sync/push/PR（W4）**。**ADR-025 正式修订**为「仅挂一次性 scratch，永不挂真相源」（受 ADR-039 门控）。**本批次只做契约与设计先行**（无生产代码/迁移/无真实挂载/不暴露 W3 导航） | 新增 ADR-040（延伸 ADR-037/ADR-030；复用 ADR-016/017/023/015/009/019；**正式修订 ADR-025**；受 ADR-039 门控；源自 R-WORKSPACE-PRODUCT §10）|
| 2026-07-28 | 多来源模型 provider（用户在设置里配置多个 model 来源） | ✅ 把 env 单一 provider 升级为 **DB 支持、用户可配的多 provider 注册表**：一行 = 一个来源（`kind`/`api_mode` + `base_url` + **AEAD 密钥** + model 列表 + 默认），复用现有 `Provider.stream` 抽象。首版 3 个 wire 适配器：增强 **`openai_compatible`**（覆盖 DeepSeek/Qwen/Moonshot/Mistral/xAI/Groq/OpenRouter/Ollama/Gemini-OAI…）+ 原生 **`anthropic`** + 原生 **`gemini`**；forward `kind`（bedrock/vertex/openai_responses）留而不建。密钥复用 `github_token.py` 的 **KEK 直封** + connector-vault capability 门控，**仅在 `stream()` 边界解密，绝不进日志/事件/prompt/工具输出**。**全局默认 + 每会话可切 model**（会话绑定携带 provider 引用，避免用旧端点/协议）。**跨-provider failover / MoA / 成本 ledger / Bedrock·Vertex·Responses / 子 agent 后置**（各自后续 ADR）。provider 配置 = **人工设置**（Settings「Models」面 + REST，**不给 agent** —— 跨凭据边界，同 GitHub 连接）。**本批次只做 ADR + 契约与设计先行**（无生产代码/迁移） | 新增 ADR-041（延伸 ADR-008；复用 ADR-019/015/033；源自 R-MODEL-PROVIDER）|
| 2026-07-29 | Drive 能否上传文件夹（backlog B-5） | ✅ **客户端有界展开**：`multiple` + `webkitdirectory` + 拖拽目录遍历 → 先逐层建目录（`POST /drive/folders`）再逐个上传（`POST /drive/files`），**不新增 batch/zip 端点、服务端零改动**；有界（≤200 文件 / ≤200 MiB / 并发 3）+ 逐文件状态与诚实的部分失败；archive 上传方案留作后续 | 新增 ADR-042（落地 backlog B-5；复用 ADR-030 契约）|
| 2026-07-29 | Chat 能否上传/粘贴图片 + 从 Drive 附加文件（backlog B-6） | ✅ **字节只存 Drive**（粘贴/上传先落 `Chat uploads/`，附件只存 `drive_node_id`+`version` 引用，绝不字节复制进 `parts`）；`parts.kind` 扩为 `text\|status\|image\|file_ref`；**装配期**读字节 → user turn 变 OpenAI 形状 content 数组（纯文本 turn 仍是字符串，缓存前缀不变）；三个 provider 各自翻译（Anthropic image block / Gemini inlineData / OpenAI 直通）；**每来源 `supports_vision` 标志**，为假时图片诚实降级为文本占位而非 400；非图片文本类做有界抽取、二进制只给指针 | 新增 ADR-043（落地 backlog B-6；扩展 ADR-005/008/030；延伸 ADR-041）|
| 2026-07-29 | 测试套件清空开发库、并与 worker 死锁（backlog B-9） | ✅ **进程级数据面隔离**，不是「把 20 处 DELETE 写好看点」：测试进程在 `app.config` 建单例**之前**改写 `DATABASE_URL`→`<应用库>_test`、`REDIS_URL`→逻辑库 15、`OWNER_EMAIL`→合成 owner；专用库由会话钩子自动建库+`alembic upgrade head`+盖**标记表**；**标记表是允许破坏性写入的唯一凭据**（fail-closed，绝不降级到应用库）；全部清场收敛到唯一入口 `drop_tenant()`（`lock_timeout` + 单次重试）。**worker 无需停机**即可跑全量 | 新增 ADR-044（落地 backlog B-9；复用 ADR-015/019/022）|
| 2026-07-30 | backlog B-2（52 个扁平工具）与 B-8（`project_run` 必失败）是分开修还是一起修 | ✅ **一起修，且按 clean break 做**：二者是同一缺陷的两面——工具面是静态全量注入的，而"在项目里改/跑/测代码"天然需要上下文相关、还会继续变大的工具集。**明确放弃向后兼容**：不做别名/弃用期、不保留历史工具名、不保留 `/files` 遗留栈、不保留 `project_run` 行为、**不做任何数据迁移**；32 条 alembic revision **squash 成单一 baseline**，开发库与卷**销毁重建**（现有数据全部是可抛弃的测试数据，负责人已确认）。本批次**只做 ADR + 契约与设计先行**（无生产代码/迁移/前端/基础设施改动） | 新增 ADR-045（伞；统领 046/047/048；**取代** ADR-023 的工具面落地口径、**取代** ADR-040 §决策8 的执行器口径）|
| 2026-07-30 | 工具面如何在持续变大的同时保持可理解、省 token、权限安全、可发现 | ✅ **分层工具目录 + 上下文作用域可见集 + 渐进式披露**：全部工具统一 `domain.verb` 命名；旁挂 `ToolDescriptor`（namespace/toolset/version/requires/surfaces/summary）**不改窄腰 `Tool` Protocol**；`ToolsetResolver` 成为**真正的 VISIBLE 闸**（按 trust tier / surface / session kind / runtime 求解，turn 边界冻结，确定性排序，core 恒为缓存前缀真前缀）；core 常驻 ~15 个 + 一行式目录摘要 + `tools.search`/`tools.load` 两个元工具；**不做动词巨型工具**（`drive(op=…)` 会塌成 `oneOf`、削弱校验、破坏权限粒度与审批 scope）；策略引擎升级为 **args 感知** `evaluate(ctx, descriptor, args, scope)` | 新增 ADR-046（落地 backlog B-2；受 ADR-045 统领；具体化 ADR-009 的 VISIBLE 闸；扩展 ADR-008 权限代数） |
| 2026-07-30 | 项目字节如何进出沙箱（B-8 的 bind mount 在 DooD 下结构性失败） | ✅ **改用 tar ingress/egress，彻底删除 bind mount**：`put_archive`/`get_archive` 完全不涉及任何文件系统路径语义 → 宿主守护进程无需解析 worker 容器内路径（B-8 整类问题消失）、`src=` 路径注入面消失、Windows/Linux/CI/DinD 同码同行为、可用 fake docker client 完整单测。这是对 **ADR-025/ADR-039 挂载口径的收窄性修订**（从"只挂一次性 scratch"收窄为"**只注入**一次性 scratch，**永不挂任何宿主路径**"），**不是放松**。同批交付**真自建 `sandbox-runner` 镜像**（python+pytest+ruff+`capabilities.json`），否则挂载修好也无产品价值 | 新增 ADR-047（落地 backlog B-8；受 ADR-045 统领；**收窄性修订** ADR-025/ADR-039） |
| 2026-07-30 | file/shell 是宿主一等工具、沙箱路由工具、还是委派子 agent | ✅ **混合分层 + 显式 RuntimeSession**：`fs.*` 走**宿主侧**直接读写工作副本 overlay（无需容器、确定性、可完整单测、且严格强于旧 `project_tree`/`project_read`——后者只看 head，看不见 agent 刚写的内容）；`sh.*` **必经 RuntimeSession** 进沙箱。产品后果：**沙箱不可用只损失「跑」，不损失「改」**。`RuntimeSession` 从 v1 起就是**显式一等对象**（`runtime.open/close` + `scope=project|ephemeral`），未来「沙箱内专用 coding agent」只是加一个 sub-agent provider（`delegate.code_task(runtime_session_id, …)`），**不需重写编排**。`project_run`/`project_tree`/`project_read`/`run_code` **全部删除**；`project_sandbox_runs`（含从未实现的 `warm_until`/在 tar 下无意义的 `scratch_ref`）拆为 `project_runtime_sessions` + `project_exec_runs` | 新增 ADR-048（落地 backlog B-8 的产品面；受 ADR-045 统领；**修订** ADR-040 §决策8「不嵌 coding agent」为「v1 不嵌、接缝预留」） |
| 2026-07-30 | 先建工具目录还是先压缩工具本身；`domain(action,…)` 横向合并是否可行；CLI agent 模式能否借鉴 | ✅ **修订 ADR-046（修订 A）**：负责人复审指出「引入渐进式披露前尚未做过廉价压缩」——实测成立（47 工具 / 17,432 B，其中**描述散文占 38%**，含从未被审视的死工具，`drive_restore` 甚至**结构性不可调用**：无任何工具吐 node id）。故 **P2 先瘦身再建目录**（删死工具 + 描述字节上限进启动强制）。但压缩有硬地板（瘦身后 ~12,137 B，仍是 6 KiB 预算的 2×，且 P4 加 11 个工具即吃回），**替代不了目录**。横向 `domain(action)` 合并**维持否决但理由收窄**：ADR-046 原理由②③被其自身 §决策6（args 感知策略）推翻，仅存①（`validate.py` 非全量 JSON-Schema 引擎，合并后 `if_version` 等条件 required 校验**消失**而非削弱）+ ④（模型对判别式联合偏弱）。新增 **§决策10：合并只沿纵向（工作流）轴**（Anthropic `get_customer_context` 判据），跨域 `list(kind)` 明确禁止。CLI 模式最大省钱来源是**预训练先验**，对自有数据域**完全不转移**；可转移的「带内发现」就是 `tools.search`/`tools.load`（GitHub MCP Server + Copilot CLI 均为「渐进披露 + 工具保持独立」的一线先例） | **修订 ADR-046**（§决策1 补注 · §决策5 收窄 · **新增 §决策10** · 基线数字更正）；新增 backlog **B-10**（瘦身 + 纵向合并候选）与 **B-11**（工具使用评测缺失，Phoenix 已具备采集基础） |

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
> **落地口径修订（2026-07-30，见 [ADR-045](#adr-045)/[ADR-046](#adr-046)）**：**原则不变**（单一能力层 + REST/Tool 双适配、共享 `CallerContext` 与四道闸、按能力纵切）。修订的是**工具面的呈现方式**：从「所有已注册工具平铺发给每个会话」改为「**分层工具目录 + 上下文作用域可见集 + 渐进式披露**」（`domain.verb` 命名、`ToolDescriptor` 旁挂、`ToolsetResolver` 落地 VISIBLE 闸、`tools.search`/`tools.load`）。"UI 能做 = agent 能做"仍是结构性保证 —— 只是 agent **按上下文取用**而非一次全拿。上文§落地缺口中「`Tool.execute` 需注入 `ToolContext`」**已完成**（`app/tools/base.py:74`）；「ALLOWED 策略引擎」由 [ADR-046] §决策6 升级为 **args 感知**；「输出 spill」的类型化 `ToolOutputSpillReference`（api §7.2）仍**未落地**，排在 Phase TR。

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

> **正式修订（2026-07-27，W3 前置，经 ADR-039 隔离评审后进行；落实 ADR-037 §决策3）**：把上文「**数据外泄 → 不挂 workspace/MinIO 文件（纯计算）**」与「**排除：workspace 文件挂载进沙箱**」两条**收窄修订为**——
> - **允许且仅允许挂载一次性 scratch 副本，永不挂载真相源。** W3 起沙箱可 RW 挂载**一份一次性、节点本地、可丢弃的 scratch 拷贝**（`project` 工作副本物化出来的），**绝不**挂载 Sherpa 快照 / `storage_blobs`/MinIO 对象存储 / 其它 project 或工作副本 / Drive / `WORKSPACE_ROOT` / `TOOL_OUTPUT_ROOT` / 任何凭据。真相源（`project_snapshots` head + 工作副本 overlay）**永不被 RW 挂载**（OpenHands「任何 RW 挂载都可被 agent 修改」→ 只挂可丢副本）。
> - **保留全部既有硬化不变**：`network_disabled`、`cap_drop=ALL`、`no-new-privileges`、非 root、只读 rootfs + tmpfs、mem/pids/cpu/墙钟上限、`--rm`、**无任何密钥注入**。scratch 挂载额外加 `nosuid,nodev`；编排方在**拷贝前剔除/断言无凭据**、校验 scratch 源路径、`finally` 原子清理 + 启动扫除孤儿 scratch。
> - **信任让步与门控不变**：worker 挂 `docker.sock` ≈ 宿主 root（OWASP Docker Cheat Sheet Rule#1；**只读挂 socket 无用**）——引入「用项目字节喂容器」后风险上升，故该修订**仅在自托管、单用户**下生效（推荐补 **rootless Docker** + **patched runc ≥1.1.12**，CVE-2024-21626）。**多用户 / 真不可信第三方代码的禁止上线条件见 [ADR-039](#adr-039)**（须 gVisor/`runsc` 或 microVM + 不共享 socket + 每租户 scratch/出口/配额隔离 + 威胁评审）。**socket-proxy 对本编排角色是假安全**（需放行 `containers/create`/`exec` 即等于放行逃逸），不采用。
> - 本条修订的完整威胁模型、方案比较与禁止上线条件在 [ADR-039](#adr-039)；W3 产品/数据/生命周期在 [ADR-040](#adr-040)。**本批次只修订本 ADR 正文的挂载口径 + 新增 ADR-039/040 + 契约增量，不落地任何生产代码/迁移/真实挂载。**

> **再次收窄修订（2026-07-30，见 [ADR-047](#adr-047)）**：上文「**RW 挂载**一份一次性 scratch 副本」进一步**收窄为**「以 **tar 流注入**一次性 scratch 副本进容器内的**匿名卷**，**永不挂载任何宿主路径**」。原因：bind mount 在 Docker-out-of-Docker 下结构性失败（backlog B-8），且 `src=` 路径参数本身是需要当不可信输入校验的攻击面。**其余全部硬化与控制一字不改**（断网 / `cap_drop=ALL` / `no-new-privileges` / 非 root / 只读 rootfs+tmpfs / 资源+时限 / `--rm` / **无密钥注入** / `nosuid,nodev` / 拷贝前剔除凭据 / 原子清理 + 孤儿扫除 / 沙箱绝不接触 socket）。**这是收窄，不是放松**；[ADR-039] 的禁止上线条件完全不变。

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

### ADR-037 · Workspace 产品模型 + Projects 上线顺序（W2a→W2b→W3→W4）——落地 W2a「空/模板/归档项目」的契约与设计先行（延伸 ADR-030；源自 R-WORKSPACE-PRODUCT）

> **状态：方向已由负责人拍板（2026-07-27）；本批次先契约后代码——只写 ADR + 冻结契约增量（data-model/api/events/config）+ 能力矩阵行 + W2a 静态 UI 稿，不写业务代码、不做迁移、不暴露生产 Projects 导航（AGENTS.md §1/§2）。** 完整调研见 [`research/workspace-product-report.md`](research/workspace-product-report.md)；研究态未来全景静态稿见 [`research/workspace-product-prototype/index.html`](research/workspace-product-prototype/index.html)；本批 W2a 生产设计系统静态稿见 [`design-workspace/index.html`](design-workspace/index.html)。

- **背景（为何要、承接谁）**：ADR-030（Personal Drive / W1）已把扁平 Files 升级为 Drive，并**有意不暴露 Projects 导航、不把 Drive 挂进 sandbox、不做 GitHub 同步**（那些是 W2/W3/W4）。R-WORKSPACE-PRODUCT 调研确立了完整词汇与信息架构（Workspace 为所有权/配额umbrella；Drive=通用私有文件；Project=可开发的持久状态，带文件树/快照/活动/可选源绑定/沙箱动作；Sandbox=一次性执行层；Project Chat=不可变绑定一个 Project 的会话）。负责人现批准**分四步落地 Projects**，并要求**先做契约与设计先行**，W2a 的实现待本批评审通过后再开始。

- **决策（负责人拍板 2026-07-27，5 条）**：
  1. **Workspace 是总入口**；其下 **Projects 与 Drive 并列**为两个不同的产品名词（Drive 面向文件管理；Projects 增加源/快照/活动/执行语义）。SPA 未来路由 `/work`、`/work/projects`、`/work/drive`（避开 REST 前缀 `/projects`、`/drive`、`/storage`）。**W2a 阶段生产导航仍不暴露 Projects 项**——只落契约 + 静态稿。
  2. **实现顺序固定为 W2a → W2b → W3 → W4**，逐步交付、每步独立评审：
     - **W2a**（本 ADR 契约化）= **空项目 / 模板 / 归档导入**三条创建路径 + Projects 库 + Project 详情（文件树/活动/存储/快照）+ **Open in Chat**（创建一个**不可变绑定**该 Project 的会话，`sessions.project_id`）。**W2a 明确不含 GitHub**。
     - **W2b** = **GitHub 一次性分支导入**（浅拉取分支头 → 初始快照 + 记录稳定 repo id/branch/source OID；不保留全量远程历史、不后台合并/推送）。**GitHub 一次性导入放 W2b**。需**后续 ADR**（`project_sources` 契约 + GitHub App 凭据边界）。
     - **W3** = **Project Chat 任务工作副本 + sandbox 变更评审**。**W3 允许且仅允许 Sandbox 挂载一次性 scratch 副本，绝不挂载项目真相源**；持久权威=`project head snapshot` + 任务工作副本 overlay，scratch 卷/热容器都是可重建缓存。需**后续 ADR**（`project_working_copies`/`project_change_sets`/`project_artifacts` 契约）。
     - **W4** = **GitHub 同步 + 对外写**（后台 fetch 只更新状态；apply/merge 显式；push/建远程分支/建 PR 走 ADR-020 审批信封、带期望远程 OID、首版不 force push）。需**后续 ADR**。
  3. **W3 的 ADR-025 修订受隔离评审门控**：W3 会**有意 supersede ADR-025 现在的「不挂 workspace 卷」排除项**——但**仅**放开「挂一次性 scratch 副本」这一点，同时保留 ADR-025 的断网/掉权/非 root/只读 rootfs/资源+时间上限。**ADR-025 的正式修订必须在 W3 开始前、经一次隔离的安全评审后进行**，不在本批次内改 ADR-025 正文。
  4. **W3 前置硬门**：在 W3 动工前，**必须先评审并加固 `docker.sock` 挂载 + 多用户隔离边界**。当前单用户自托管下 worker 挂 `docker.sock`（ADR-025 记录的信任让步）在引入「用项目字节喂容器」后风险上升（容器逃逸=宿主 root、跨会话/跨用户 scratch 串扰）。这是 W3 的进入条件，不是 W3 内的一个任务。
  5. **本批次=契约与设计先行**：**只**写 ADR-037 + 冻结契约增量（data-model/api/events/config）+ 能力矩阵行（Projects UI 仍 ⬜）+ W2a 生产设计系统静态稿。**不**写生产后端/前端代码、**不**做迁移、**不**暴露生产 Projects 导航。研究建议不得写成已实现能力。

- **数据模型（W2a；canonical 与派生分离；名义命名，见 data-model 契约）**：
  - `projects`（用户可见的持久开发状态：name/description/owner/status、`current_snapshot_id`、`default_branch_label`、`source_status`（W2a 恒 `unbound`）、存储 rollup、`last_activity_at`）。
  - `project_snapshots`（**不可变**、父链接的快照，`reason ∈ import|save|checkpoint|sync`；W2a 只产生 `import`（blank/template/archive 的初始快照）；带 manifest/tree 引用、size rollup、pinned 状态；源 OID 留给 W2b）。
  - `project_snapshot_entries`（快照内条目：归一化 path、entry kind、**blob 引用**（复用 ADR-030 `storage_blobs` 不可变去重字节）、可执行位、受限安全相对 symlink；后续可用紧凑 manifest/tree 表示替换该投影而不改 Project 语义）。
  - `sessions.project_id`（**契约扩展**）：可空，`null`=General chat；**首条已 admit 的用户消息后不可变**；建会话时与每次 Project 操作都校验 Project 归属；换 Project = **新建会话**，绝不原地改 transcript/工具上下文。
  - **W2a 明确不建的表**（留后续 ADR）：`project_sources`（W2b）、`project_working_copies`/`project_change_sets`/`project_artifacts`（W3）。均带 `tenant_id`+复合键（ADR-015 前向兼容）。

- **存储真相源（复用 ADR-030/ADR-012）**：Postgres 存 project 元数据/快照/条目/存储 rollup；快照条目指向 **ADR-030 的不可变、内容寻址、引用计数 `storage_blobs`**（同一未变字节多快照共享、不翻倍计费；配额记账复用 Drive 的「每 owner 每不同 durable blob 计一次」规则）；MinIO 存字节；**不引入独立 Git 存储**（被否的 Option 2）。归档导入在**隔离 staging**中安全解压（有界大小/文件数/膨胀比/嵌套深度/时限；拒绝绝对/穿越路径、设备、FIFO、硬链接、逃逸 symlink），验证后才进初始快照。

- **能力面（ADR-023 单能力层 + 薄 REST/Tool 双适配）**：service `services/projects.py`（`list/create/get/tree/open_in_chat`；W2a 只读+自有幂等写）。**REST**（见 api §10.5）：`GET/POST /projects`、`POST /projects/imports`（模板/归档；GitHub 留 W2b 返回 `not_implemented`）、`GET /projects/{id}`、`GET /projects/{id}/tree`、`POST /projects/{id}/chats`（建 Project 绑定会话）、`GET /sessions/{id}/project-context`。**Tool（W2a）**：`project_list`/`project_create`/`project_tree`/`project_read`（只读或自有幂等写→allow）。**W2a 不给 agent** 的工具：无破坏性 purge、无 `project_run`（W3）、无 `project_push`（W4）。**UI**：SPA 路由 `/work/projects`——**W2a 只交付静态稿，生产导航不暴露、能力矩阵 UI 列保持 ⬜**。

- **事件/幂等/outbox（复用 ADR-016/017）**：项目创建/导入/快照发一条 `project.lifecycle` 事件（stage：`created|import_staged|snapshot_activated|failed`，带 `project_id`/`snapshot_id`/具名 `termination_reason`）；归档导入是**durable job**（认领→隔离 staging 解压校验→建初始不可变快照→原子激活 `current_snapshot_id`）——同事务写 outbox/恢复记录，至少一次、幂等键=`(project_id, import_idempotency_key)`，worker 崩溃/重放不产生半成品或重复项目。失败保留无快照的 `failed` project（可见、可删），绝不激活错误字节。文档正文/文件字节**不进** append-only journal（journal 只存引用+有界元数据）。

- **安全边界（守 ADR-009/019/021）**：路径/名字归一化 + 拒绝绝对/`..`/NUL/设备/保留名 + 深度/长度/兄弟数上限；上传/归档不信任扩展名或 client Content-Type、服务端生成对象键、隔离 staging 解压、有界解压、扫描接口 `pending|clean|rejected|unavailable`；**Project 源凭据（W2b+）永远留在 vault/connector 边界，绝不进 Project 树/快照/sandbox/prompt/日志/工具结果**；**W2a 不涉及沙箱**（Open in Chat 只读/讨论 Project，不建工作副本、不挂 sandbox——那是 W3）。

- **W2b/W3/W4 非目标与后续 ADR 边界（明确不做）**：
  - **W2a 不做**：GitHub 导入/源绑定（W2b）；任务工作副本/sandbox 变更评审/`project_run`（W3）；GitHub 同步/push/PR/对外写（W4）；生产 Projects 导航暴露；后台合并、force push、submodule、Git LFS、多 remote、全历史镜像、网络化开发环境、依赖安装策略、prebuild、live preview、团队/共享 Drive、内嵌 coding-agent 执行器（均 later）。
  - **后续每步各带自己的 ADR + 契约先行**：W2b（`project_sources` + GitHub App 凭据边界）、W3（`project_working_copies`/`project_change_sets`/`project_artifacts` + **ADR-025 修订** + docker.sock/多用户隔离加固评审）、W4（对外 Git 写 + ADR-020 审批集成）。

- **取代/延伸关系**：**延伸 ADR-030**（Workspace 从「只有 Drive」扩为「Projects + Drive 并列」，Projects 快照复用 ADR-030 的不可变去重 blob 与配额记账）；**复用** ADR-012 存储选型（不引独立 Git/向量库）、ADR-023 能力层双适配、ADR-016/017 journal+outbox 真相源与 effect 语义、ADR-015 租户键、ADR-009/019/021 不可信内容/密钥/审计边界。**预告将修订 ADR-025**（W3 放开「一次性 scratch 副本」挂载，但正式修订在 W3 开始前经隔离评审）。**不改**任何既有 ADR 正文（本批只新增 ADR-037 + 契约增量）。

- **实现说明（W2a 落地，2026-07-27，本 ADR 的实现修订）**：迁移 `0028` 建 `projects`/`project_snapshots`/`project_snapshot_entries` + `sessions.project_id`，并新增**运维基础设施表 `project_import_jobs`**——这是 events §2.9「durable job（outbox + lease）」在实现层的落地：项目生命周期**非 run 作用域**，冻结的 `event_journal`（run_id NOT NULL）不适用，故用该 job 的 `stage`/`termination_reason` + 显式 enqueue + 恢复 tick（对齐 `knowledge_ingestion_job`）给出同等持久保证；文件字节只进 ADR-030 `storage_blobs`（与 Drive 共享去重/引用计数/配额，Drive 的 blob 引用计数与孤儿清扫已扩展为兼顾项目引用与 `project-import/` staging 前缀）。归档在**有界内存**中解压（不落盘，杜绝路径/symlink 攻击），拒绝绝对/穿越/设备/FIFO/硬链接/逃逸 symlink 并强制 `PROJECT_MAX_*`。`ProjectSummary` 增补派生 `import_status`/`import_failure_reason`（`status` 仍恒 active/archived/deleting），另加只读 `GET /projects/templates`、`GET /projects/{id}/snapshots` 支撑 UI。`project_snapshot_entries` 的 NUL CHECK 移除（Postgres text 本就禁止 NUL）。生产 Projects 导航与 `/work/projects` 页面本阶段**已交付**，能力矩阵 UI 列转 ✅。

- **验收关键（本契约先行批次）**：ADR-037 被接受；data-model 有 `projects`/`project_snapshots`/`project_snapshot_entries` + `sessions.project_id` 不可变绑定（canonical vs 派生清晰、快照不可变、每表 `tenant_id`+复合键）；api 有 §10.5 Projects REST + schema；events 有项目生命周期事件 + 幂等/outbox；config 有 `PROJECT_*` + 安全边界；能力矩阵有 Projects 行且 **UI 列 ⬜**；W2a 静态稿（Projects 列表/新建/详情/Open in Chat）落在生产 Quiet Work 设计系统、桌面与 390px 均合理；**无生产代码/迁移/导航暴露**；W2b/W3/W4 非目标与后续 ADR 边界写清。契约**先于代码**（本 ADR 同批）。

- **来源**：R-WORKSPACE-PRODUCT 调研 [`research/workspace-product-report.md`](research/workspace-product-report.md)（Google Drive · Dropbox · OneDrive · Notion · GitHub Codespaces · Replit · Gitpod/Ona · Devin · OpenAI Codex · Firebase Studio）；负责人输入「批准 Workspace 建议，执行第一阶段：顺序 W2a→W2b→W3→W4；Workspace 总入口、Projects 与 Drive 并列；W2a 仅空/模板/归档导入不含 GitHub，GitHub 一次性导入放 W2b；W3 仅挂一次性 scratch 副本、绝不挂真相源、ADR-025 修订在 W3 前经隔离评审；W3 前先加固 docker.sock/多用户隔离；本阶段只做契约与设计先行」。

---

### ADR-038 · Projects W2b = GitHub 一次性导入（选 repo/ref + 有界归档获取 + 记录 source OID + 物化不可变初始快照）——契约与设计先行（延伸 ADR-037；复用 ADR-019/030；源自 R-WORKSPACE-PRODUCT §9）

> **状态：方向由负责人拍板（2026-07-27）；契约先行批次已完成，随后负责人批准 W2b **生产实现**（2026-07-27）——本 ADR 的契约增量（data-model/api §10.6/events §2.10/config §1.6）已落地为代码：migration `0029`（`github_connections`/`project_sources`/`source_status` 扩展/`project_import_jobs` github 列）+ `security/github_token.py`（AEAD）+ `services/github_source.py` + `services/projects_import.py` github 分支 + `api/connections.py` + `api/projects.py` §10.6 + 生产 `/work/projects` GitHub UI，两栈验证通过。** 完整调研见 [`research/workspace-product-report.md`](research/workspace-product-report.md) §9（Project 与 Git 语义）；W2b 生产设计系统静态稿见 [`design-workspace/github-import.html`](design-workspace/github-import.html)。

- **背景（承接谁、为何要）**：ADR-037 把 Projects 分四步落地并在 §决策2 明确「**W2b = GitHub 一次性分支导入**（浅拉取分支头 → 初始快照 + 记录稳定 repo id/branch/source OID；不保留全量远程历史、不后台合并/推送），需**后续 ADR**（`project_sources` 契约 + GitHub App 凭据边界）」。W2a（空/模板/归档）已上线（migration `0028`、`/work/projects`）。本 ADR 就是 ADR-037 预告的那个「后续 ADR」，把 GitHub 一次性导入的**契约与设计**冻结下来，实现待本批评审通过再开始。

- **决策（负责人方向 + 仓库研究证据收敛，2026-07-27）**：

  1. **W2b 严格限定为「GitHub 一次性导入」**：选择 repository + ref → 有界获取该 ref 的**内容树**（无 git 历史）→ 记录 source repo/ref/OID provenance 元数据 → 物化为 Sherpa **不可变初始快照**（`project_snapshots.reason='import'`，`source_oid=<resolved commit OID>`）。**导入后项目独立存活**：远端**不是**权威源，Sherpa 快照才是；即便远端被改名/删除/转移/掉权，项目仍可用。

  2. **首版 ref 范围 = branch + tag + commit SHA（三者皆首版）**——由仓库研究证据收敛：
     - GitHub REST 归档端点 `GET /repos/{owner}/{repo}/tarball/{ref}`（及 `zipball`）对 **branch / tag / commit SHA** 三种 ref **统一**接受，返回该 ref 处**只含文件内容、不含 git 历史**的 gzip tar，归档根目录名 `repo-<SHA>/` 已带解析出的 commit OID（[Downloading source code archives](https://docs.github.com/en/repositories/working-with-files/using-files/downloading-source-code-archives)）。
     - ref→OID 解析统一：`GET /repos/{owner}/{repo}/git/ref/heads/{branch}`、`.../git/ref/tags/{tag}`（[REST · Git refs](https://docs.github.com/en/rest/git/refs)）、commit SHA 用 `GET /repos/{owner}/{repo}/commits/{sha}` 校验。
     - 因此三种 ref 的边际成本近乎为零，且都能**先解析成具体 OID 再获取并钉住**——branch/tag 导入等于钉在导入当刻的那个 commit，天然满足「远端非权威」。→ 首版即支持三者，`ref_type ∈ (branch, tag, commit)`，默认 branch（默认分支）。

  3. **获取机制 = 有界归档获取（tarball），不是 `git clone`**——由证据收敛：
     - 归档端点天然满足 ADR-037「不保留全量远程历史」，产物是一个 **tar（gzip）**——恰好可**复用 W2a 的内存内有界安全解压器**（`services/archive.py`：拒绝绝对/穿越/NUL/设备/FIFO/硬链接/逃逸 symlink，强制 `PROJECT_MAX_*` 大小/条目/膨胀比/深度）。**不引入 git 二进制/子进程、不落 `.git`、不建工作副本**（工作副本是 W3）。
     - 相较 `git clone --depth 1`：归档路径无需在 worker 内跑 git、无需处理 packfile/`allowAnySHA1InWant`、无网络化开发环境，攻击面与运维面都更小；代价是拿不到 git 对象（submodule/LFS/历史）——这些本就都是 later（W4+）非目标。私有仓需带凭据；归档重定向 URL 短时（约 5 分钟）失效，worker 端即时跟随。
     - 若未来需要 submodule/LFS/增量 fetch，再在 W4 引入受控 git 传输（各带 ADR），不影响本 provenance 与快照语义。

  4. **凭据 = AEAD vault 内的「GitHub connection」，复用 connector/credential/approval 架构**——由证据收敛：
     - 首版凭据 = **fine-grained PAT（`contents:read`）**：自托管单用户最低门槛（无需注册/托管 GitHub App），足以只读克隆/归档私有仓；`auth_kind` 设计为可扩展，**GitHub App 安装令牌（`contents:read`，≤8h 短时、非用户绑定）**为推荐/前向路径，加它**不需要改表**。公共仓导入可不带凭据。
     - **凭据边界（守 ADR-019/037 §安全边界）**：GitHub token **只**存在 AEAD vault（复用 `connectors` 的 `token_enc/nonce/kek_id/key_version/token_algorithm/aad_version` 加密列形态），**只**由导入 worker 在 connector 边界内解密使用，**绝不**进入项目文件树 / 快照 / prompt / 日志 / 工具结果 / （W3）沙箱。凭据也不进 append-only journal。
     - 复用 approval 架构：**W2b 无对外写**（无 push/PR），故导入本身不需要 ADR-020 审批信封（那是 W4）；但建立/删除 GitHub connection、以及导入一个私有仓的动作是 owner 用户级操作（Session + CSRF），不给 agent（见决策6）。

  5. **持久化 / 幂等 / effect 语义（复用 ADR-016/017、承接 W2a 的 durable job）**：
     - GitHub 导入是 **durable job**（复用 `project_import_jobs`，`create_kind='github'`）：认领（lease）→ ref→OID 解析 → 有界归档获取到隔离 staging → 安全解压校验 → 建初始不可变快照 → 原子置 `projects.current_snapshot_id` 并置 `source_status='imported'`。幂等键 `(project_id, import)`（一项目一次导入），crash/replay 不产生半成品或重复项目。失败保留无快照的 `failed`/`import_failed` 项目（可见、可删），绝不激活错误字节。
     - **只读拉取 ⇒ 幂等，无 `effect_unknown` 远端对账**：GitHub 归档获取**不改远端**，与 W4 push 的对外写不同——失败/部分获取可安全重试（按解析出的 OID 重取 → 同一字节）。`effect_unknown`/远端 ref 对账语义属于 **W4**（push/PR），本 ADR 不引入。
     - 事件：复用 events §2.10（延伸 §2.9 形状）`project.lifecycle`，`create_kind` 增 `github`，stage 复用 `created|import_staged|snapshot_activated|failed` + 具名 `termination_reason`（`done|source_resolve_failed|auth_required|repo_unavailable|unsafe_archive|too_large|expansion_ratio|error:...`）。文档/文件字节**不进** journal（只存引用 + 有界元数据）。

  6. **能力面（ADR-023 单能力层 + 薄 REST/Tool 双适配）**：
     - service `services/projects.py` + `services/github_source.py`（ref→OID 解析、归档获取、connection 凭据取用）。**REST**（见 api §10.6 增量）：`POST /projects/imports` 的 `kind='github'` 由 **501 改为 202**（body 带 `GithubImportSpec`：repo + ref_type + ref + 可空 connection 引用，默认取 owner 的 active connection）；失败态经 `import_status='failed'` + `source_status='import_failed'`（`projects.status` 仍恒 active）暴露，`POST /projects/{id}/imports/retry` 重跑幂等 durable job；新增只读选择端点 `GET /projects/github/repos?query=`（列出该 connection 可见仓库）与 `GET /projects/github/refs?repo_external_id=`（列出分支/标签），二者**服务端**经 connection 凭据代理 GitHub（`502` 透传上游、`409` 无 active connection），**凭据不下发前端**；GitHub connection 管理端点 `GET/POST/DELETE /connections/github`（状态含连接 id、授权、断开）。W2b 首版**要求 active connection**（无连接的公共仓直连为后续放宽）。
     - **Tool（W2b）= 不新增 agent 写工具**：GitHub 导入**不给 agent**（人工触发，理由：跨凭据边界 + 拉取不可信外部内容；与 W2a「归档上传不给 agent」一致，也避免 agent 枚举用户私有仓的隐私面）。导入完成后 agent 用**既有** W2a 只读工具（`project_tree`/`project_read`）读取项目内容（项目文件仍是**不可信内容**，ADR-009）。**不给** agent：`project_push`（W4）、任何破坏性 purge、`project_run`（W3）。
     - **UI**：SPA 路由复用 `/work/projects`——**W2b 只交付静态稿**，能力矩阵 UI 列保持 ⬜，**本阶段不暴露 W2b 生产入口/导航**。

- **数据模型（W2b；canonical 与派生分离；名义命名，见 data-model 契约增量）**：
  - **新增 `project_sources`（canonical，一项目一行、导入后即 provenance）**：`provider='github'`、稳定 `repo_external_id`（GitHub 数字 repo id）、`owner`/`repo` 显示名、`ref_type ∈ (branch,tag,commit)`、`ref_name`、解析出的 `source_oid`、`connection_id`（→ vault 凭据引用，**不存 token 本体**）、`imported_at`、`status`。W4 再在此表**扩展** fetch/sync 字段（`source_base_oid`/最近 remote OID/`last_fetched_at`/sync 态）——本 ADR 不建那些列。带 `tenant_id`+复合键（ADR-015）。
  - **新增 `github_connections`（凭据记录，复用 AEAD 列形态）**：`auth_kind ∈ (pat,app_installation)`、`account_login`、可空 `installation_id`、AEAD 加密列（`token_enc/nonce/kek_id/key_version/token_algorithm/aad_version`）、`scopes`、`status`、时间戳。带 `tenant_id`+复合键。
  - **`projects.source_status` CHECK 扩展**：W2a 恒 `unbound` → W2b 增 `importing`、`imported`、`import_failed`；更丰富的同步态（`clean/remote_ahead/local_ahead/diverged/conflicted/auth_required/remote_unavailable/sync_error`）**留 W4**。
  - **`project_import_jobs` 扩展**：`create_kind` CHECK 增 `github`；增可空 github 列（`connection_id`、`source_ref_type`、`source_ref`、`resolved_oid`）；`termination_reason` 词表增 github 具名原因。
  - **`project_snapshots.source_oid`**：W2a 已预留（W2b GitHub commit OID）——本 ADR 起 github 导入填该列。
  - **W2b 明确不建的表**（留后续 ADR）：`project_working_copies`/`project_change_sets`/`project_artifacts`（W3）；W4 的对外写/同步字段。

- **安全边界（守 ADR-009/019/021/037 §1.5）**：归档是**不可信输入**——隔离 staging + 有界内存解压 + 拒绝绝对/`..`/NUL/设备/FIFO/硬链接/逃逸 symlink + 强制 `PROJECT_MAX_*`（tar gzip 同样受 zip-bomb 膨胀比守卫）；服务端生成对象键，不信任 client `Content-Type`/文件名。**GitHub 凭据永远留 vault/connector 边界**，绝不进树/快照/prompt/日志/工具结果/沙箱。**W2b 不涉及沙箱**（Open in Chat 只读/讨论，工作副本+沙箱是 W3）。`source_oid` 是 provenance；远端非权威，导入后不追远端。

- **W3/W4 非目标与后续 ADR 边界（W2b 明确不做）**：后台 fetch/sync、working copy、`git init/commit/branch`、merge、push、建远程分支、PR、force push、submodule、Git LFS、多 remote、全历史镜像、网络化开发环境、依赖安装、sandbox、把凭据喂进项目内容——**全部 later**。W3（工作副本 + 一次性 scratch 沙箱 + 变更评审 + **ADR-025 修订** + docker.sock/多用户隔离加固）与 W4（GitHub 同步/push/PR，走 ADR-020，带期望远程 OID、首版不 force push）各自**再带自己的 ADR + 契约先行**。

- **取代/延伸关系**：**延伸 ADR-037**（把 §决策2 的 W2b 从预告落成契约）；ADR-037 §决策2 把 W2b 粗描为「浅拉取分支头」——**本 ADR 收敛并取代该处的 ref/机制细节**：首版 ref = branch **+ tag + commit**（三者），机制 = 有界**归档（tarball）获取**而非 `git clone`（先解析 OID 再钉住）。**复用** ADR-019 密钥 AEAD/KEK 边界、ADR-030 不可变去重 blob + 配额记账（GitHub 归档字节走同一 `storage_blobs`）、ADR-023 能力层双适配、ADR-016/017 journal+outbox 真相源与 durable job、ADR-015 租户键、ADR-009 不可信内容。**不改**任何既有 ADR 正文（本批只新增 ADR-038 + 契约增量）；不动 ADR-025（W3 的事）。

- **验收关键（本契约先行批次）**：ADR-038 被接受；data-model 有 `project_sources` + `github_connections` + `projects.source_status` 扩展 + `project_import_jobs` github 扩展（canonical vs 派生清晰、凭据不入表本体、每表 `tenant_id`+复合键）；api §10.6 有 github 导入 202 + repo/ref 选择端点 + connection 端点 + schema；events §2.10 有 `create_kind=github` + durable job/幂等/恢复 tick + 「只读拉取无 effect_unknown」说明；config 有 `GITHUB_*` + 安全边界增补；能力矩阵有 GitHub 导入 + connection 行且 **UI 列 ⬜**；W2b 静态稿（连接状态/repo·ref 选择/导入进度/成功来源元数据/失败·重试）落在生产 Quiet Work 设计系统、桌面与 390px 均合理，且**明标设计稿、不冒充已实现**；**无生产代码/迁移/新入口暴露**；W3/W4 非目标与后续 ADR 边界写清。契约**先于代码**（本 ADR 同批）。

- **来源**：R-WORKSPACE-PRODUCT 调研 [`research/workspace-product-report.md`](research/workspace-product-report.md) §9（Project 与 Git 语义：一次性导入、shallow-by-default、稳定 repo id + 期望 OID、凭据留 connector/vault 边界）；GitHub 官方文档 [Downloading source code archives](https://docs.github.com/en/repositories/working-with-files/using-files/downloading-source-code-archives)、[REST · Git refs](https://docs.github.com/en/rest/git/refs)、[Permissions required for GitHub Apps](https://docs.github.com/en/rest/authentication/permissions-required-for-github-apps)、[Fine-grained PAT 介绍](https://github.blog/security/application-security/introducing-fine-grained-personal-access-tokens-for-github/)；负责人输入「按 W2a→W2b→W3→W4 正常顺序继续，W2b 严格限定 GitHub 一次性导入，复用现有 connector/credential/approval，凭据不进 sandbox/prompt/tree/snapshot/日志，本阶段只做契约与设计先行，不写生产代码/迁移/不暴露导航」。

---

### ADR-039 · W3 沙箱隔离安全架构 = 一次性 scratch RW 挂载 + 保留全部硬化 + docker.sock/多用户威胁模型与禁止上线条件（W3 前置硬门；落地 ADR-037 §决策4；此门控 ADR-025 修订与 ADR-040）

> **状态：负责人批准按正常顺序进入 W3 并先执行「安全评审 + ADR/契约/设计先行」（2026-07-27）。本 ADR 是 ADR-037 §决策4 预告的 W3 前置硬门——一次独立的沙箱隔离安全评审，先于任何 W3 生产实现。本批次不写生产代码/迁移/不做真实挂载/不暴露 W3 导航。** 完整证据见 [独立安全评审](#adr-039)（下述引用为一手来源）与 [`research/workspace-product-report.md`](research/workspace-product-report.md) §10–§11；产品/数据/生命周期见 [ADR-040](#adr-040)；本 ADR 门控的 ADR-025 挂载口径修订见 [ADR-025 正式修订（2026-07-27）](#adr-025)。

- **背景（承接谁、为何是硬门）**：ADR-037 §决策4 规定「**W3 动工前必须先评审并加固 `docker.sock` 挂载 + 多用户隔离边界**」，§决策3 规定「**ADR-025 的正式修订必须在 W3 开始前、经一次隔离的安全评审后进行**」。W3 会让沙箱**首次挂载项目字节**（一次性 scratch 拷贝），风险相对 ADR-025 的纯计算沙箱上升。本 ADR 就是那次隔离评审：给出威胁模型、隔离方案比较、W3 首版可实施的**最小安全架构**，以及**明确的禁止上线条件**；并**不把未实施的缓解写成已安全**。

- **独立安全评审结论（一手证据）**：
  1. **`docker.sock` ≈ 宿主 root（不可回避）**：挂载 Docker socket 进容器 = 对宿主的**无限制 root**（OWASP Docker Security Cheat Sheet Rule#1）；**只读挂 socket 也无用**（同源）。逃逸只需一步：`docker run -v /:/host --privileged … chroot /host`。**当前 worker 挂 `docker.sock` 就是这个信任让步**（ADR-025 已记录）。shared-kernel runc 的内核攻击面**不可用 per-container flag 关闭**：CVE-2024-21626（"Leaky Vessels"，CVSS 8.6，2024-01）即便非 root + 掉全 caps + seccomp 仍可逃逸（缓解=runc≥1.1.12）。
  2. **专用 sandbox 编排模式是对的、须保持**：Sherpa 已正确实现「**socket 只给可信编排进程（只跑我方代码），不可信代码只在其派生的隔离容器里、绝不接触 socket**」（`sandbox-runner`/docs 05 §sandbox socket 安全）——这与 E2B/Daytona/Judge0/OpenHands 的「orchestrator-spawns-container」一致。**关键不变量**：socket 持有进程是 TCB，任何让 agent/项目内容影响 `docker run` 参数（镜像名/卷路径/env/scratch 源路径构造）的路径都是 critical——**须把所有 container-create 参数当不可信输入校验**。
  3. **socket-proxy 对本编排角色是假安全**：Tecnativa `docker-socket-proxy` 只按 URL 前缀过滤、不看请求体；编排必须放行 `POST /containers/create`（还常需 `exec`），而 `create` 一旦放行即可传 `{"HostConfig":{"Binds":["/:/host"],"Privileged":true}}` 完成逃逸。→ **不采用**（对只读监控消费者才有意义）。
  4. **rootless Docker = 单用户推荐加固**：守护/编排/容器全跑进用户命名空间，容器 root 映射为非特权宿主 UID；socket 被盗/容器逃逸只得非特权用户权限（OWASP Rule#11）。**`--network none` + 资源上限在 rootless 下可用**（需 cgroup v2 + 内核≥5.11 + systemd≥244）；限制：不能加载自定义 AppArmor、无 `--network host`。它**降低 blast radius 但不消除内核攻击面**。
  5. **gVisor（`runsc`）= 多用户实用最低标准**：用户态应用内核（Sentry）拦截**全部**应用 syscall，宿主内核只被 Sentry 以极小集合触达 → 关闭 CVE-2024-21626 一类内核逃逸；**无已知宿主逃逸 CVE**（历史仅 DoS/内部提权）；`--runtime=runsc` 逐容器 opt-in、与 `--network none` + Python/Node 兼容；代价：syscall 密集型 1.5–3× + ~200–500ms 冷启动 + 少量兼容性缺口。
  6. **Kata/Firecracker microVM = 真不可信第三方代码必需**：硬件 VM 边界，每负载独立内核 → 跨租户内核逃逸结构性不可能；Firecracker ~125ms 启动、~5MB 开销，AWS Lambda 多租户量产参照，E2B 开源参照；代价：需 KVM/containerd、运维复杂、部分云主机嵌套虚拟化受限。
  7. **Sysbox**：用户命名空间 + 虚拟化 `/proc`·`/sys` + 免 privileged 的 DinD——适合半可信 CI/DinD，**仍共享宿主内核**（无 VM 边界），且 Docker 收购后**非官方支持**——**不作为不可信代码的隔离层**。
  8. **RW 挂载 = 双向通道，只挂可丢副本**：任何 RW bind mount 都可被 agent 读写/外泄（OpenHands 明确「挂进 `/workspace` 的 RW 内容都可被 agent 改」；其 `~/.ssh` 默认挂载曾致私钥暴露）。故**永不挂真相源**，只挂**一次性拷贝**；**拷贝前剔除/断言无凭据**（`.env`/`*.pem`/`*.key`/`.git/config` 凭据/SSH key），挂载加 `nosuid,nodev`，副本随容器销毁、不归档不快照。Portainer bind-mount 限制绕过（GHSA-7fw3-x4r2-g7wc，2024）证明「声明式别挂敏感路径」不足以替代**结构性隔离**。

- **威胁模型**：
  - **单用户现状（v1 自托管）**：威胁主体 = 被提示注入/被恶意项目内容驱动的 agent，或项目里的恶意代码。既有硬化把「容器内任意代码」的逃逸面压到很小（断网、非 root、掉全 caps、只读 rootfs、资源+时限、`--rm`、socket 不进容器）。W3 新增面 = **scratch 副本读到意外凭据 / 用 scratch 内 suid 提权 / scratch 残留跨 run 泄漏**——均可用「拷贝前剔除凭据 + `nosuid,nodev` + 编排原子清理」缓解。**残余风险** = CVE-2024-21626 一类 runc 内核逃逸（缓解=patched runc + 可选 rootless）；单用户下宿主即用户自己的机器，风险自担、可接受。
  - **未来多用户 / 真不可信第三方代码**：威胁主体 = 会**主动尝试逃逸**的其它租户。shared-`docker.sock`/shared-kernel runc **结构性不安全**（一个租户的内核 0-day 危及全宿主与所有租户）——**per-container flag 不能缓解**。这是**禁止上线条件**的根据。

- **隔离方案比较（威胁 / 残余风险 / 单用户运维成本 / 多用户适用性）**：

  | 方案 | 缓解的威胁 | 残余风险 | 单用户运维成本 | 多用户适用性 |
  |---|---|---|---|---|
  | 现有基线（runc + 全硬化 flags） | 常规容器逃逸/网络/资源耗尽 | 内核逃逸（CVE-2024-21626 类）；shared-kernel | 零（已部署） | ❌ 不适用 |
  | **W3 scratch 加固**（`nosuid,nodev` + 拷贝前剔除凭据 + 源路径校验 + 原子清理/孤儿扫除） | scratch 凭据捕获 / suid 提权 / scratch 残留 | 同上，内核面不变 | 极低（仅编排代码） | ❌ 仍不适用（未换运行时） |
  | rootless Docker | 守护/编排/socket 被盗 → 宿主 root | 内核逃逸仍达宿主（但仅非特权 UID）；无 AppArmor | 低（一次性，内核≥5.11） | ⚠️ 更好但对抗性多租户不足 |
  | docker-socket-proxy | 只读消费者的 API 面 | 需放行 create/exec 即无意义 | 低 | ❌ 对编排角色不适用（假安全） |
  | **gVisor（`runsc`）** | 内核攻击面（Sentry 拦截全 syscall）；CVE-2024-21626 类 | 侧信道；syscall 密集慢；少量兼容缺口；自身 CVE 仅 DoS | 中（装 runsc + daemon.json） | ✅ 多用户+可信镜像最低标准 |
  | **Kata + Firecracker** | 硬件 VM 隔离；跨租户内核逃逸 | 侧信道（共享硬件）；运维复杂 | 高（KVM/containerd） | ✅✅ 对抗性第三方代码必需 |
  | Sysbox | 用户命名空间 + proc/sys 虚拟化；免特权 DinD | 仍共享宿主内核；非官方支持 | 中 | ⚠️ CI/DinD 可，不适对抗性 |

- **决策**：
  1. **W3 首版最小安全架构（自托管单用户，可实施）**：在保留 [ADR-025] 全部硬化（断网 / `cap_drop=ALL` / `no-new-privileges` / 非 root / 只读 rootfs+tmpfs / mem·pids·cpu·墙钟上限 / `--rm` / **无密钥注入**）的前提下，**只**新增「**一份一次性 scratch 只读拷贝的 RW 挂载**」，并附六条编排级控制：① 挂载 `nosuid,nodev`；② 物化 scratch **前剔除/断言无凭据**（项目字节 only，绝不写入任何 token）；③ 编排方**校验 scratch 源路径**在 `SANDBOX_SCRATCH_ROOT` 内、无穿越（把构造参数当不可信输入）；④ 沙箱**只**挂该一次性 scratch，**绝不**挂 Sherpa 快照 / `storage_blobs`/MinIO / 其它 project/工作副本 / Drive / `WORKSPACE_ROOT` / `TOOL_OUTPUT_ROOT` / socket / 凭据；⑤ 编排 `finally` 原子清理 + 启动扫除孤儿 scratch；⑥ **保持沙箱绝不接触 `docker.sock`**（TCB 只在编排进程）。**强烈推荐**同时上 **rootless Docker** + **patched runc ≥1.1.12**。
  2. **明确禁止上线条件（多用户 / 真不可信第三方代码之前，必须先做，缺一不可上线）**：① **不共享 `docker.sock`**、跨租户不共享守护；② 不可信容器改用 **gVisor（`runsc`）或 microVM（Kata/Firecracker）** 运行时（对抗性第三方代码必须 microVM）；③ **每租户 scratch 物理隔离**（不同租户 scratch 互不可达）；④ **租户级出口策略 + SSRF 代理**（现单用户 SSRF 代理须变租户感知 + 域名白名单 + 限速）；⑤ **每租户聚合资源配额**（cgroup v2 per-tenant slice，防 DoS）；⑥ 权限引擎 `deny>ask>allow` **无跨租户策略泄漏**；⑦ **多用户 ADR 前完成威胁评审**（威胁主体 / 隔离保证 / 残余风险 / 事件响应）。
  3. **不把未实施缓解写成已安全**：本 ADR 与所有 W3 文档**只**声称「单用户自托管下、加上上述编排控制、可接受地挂一次性 scratch」；**不**声称多用户安全、**不**声称已具备 gVisor/microVM 隔离（均为**尚未实施**的禁止上线前提）。就绪状态须在 readiness/docs 中如实报告，**绝不过度声称**。
  4. **socket-proxy 不采用**（对本编排角色是假安全）。

- **取代/延伸关系**：**门控并触发 [ADR-025] 的正式修订**（把「不挂 workspace」收窄为「仅挂一次性 scratch，永不挂真相源」，见 ADR-025 正文的 2026-07-27 修订）；**门控 [ADR-040]**（W3 产品/数据/生命周期只能在本 ADR 的隔离前提下实现）；落地 [ADR-037] §决策3/4 的两道前置；复用 [ADR-019] 密钥边界、[ADR-009] 不可信内容边界、[ADR-015] 租户键（多用户隔离的前向依据）。**不改** ADR-007/016/017/020/023 正文。

- **验收关键（本评审批次）**：ADR-039 被接受；docker.sock≈宿主 root 与 shared-kernel 内核面用一手来源确证；socket-proxy/rootless/gVisor/Firecracker/Kata/Sysbox/专用 sandbox 编排逐一给出「缓解/残余/成本/多用户适用性」；给出 W3 首版最小安全架构（保留全硬化 + 只挂一次性 scratch + 六条编排控制）与**明确禁止上线条件**；未实施缓解不写成已安全；config §1.7 与 ADR-025 修订与本 ADR 一致；**无生产代码/迁移/真实挂载/导航暴露**。

- **来源（一手证据）**：OWASP Docker Security Cheat Sheet（Rule#1 socket=宿主 root、只读挂无用；Rule#11 rootless）<https://cheatsheetseries.owasp.org/cheatsheets/Docker_Security_Cheat_Sheet.html>；CVE-2024-21626 "Leaky Vessels" <https://nvd.nist.gov/vuln/detail/CVE-2024-21626> · <https://github.com/advisories/GHSA-xr7r-f8xq-vfvv>；Tecnativa docker-socket-proxy <https://github.com/Tecnativa/docker-socket-proxy>；Rootless Docker（官方 + Known Limitations）<https://docs.docker.com/engine/security/rootless/>；gVisor 安全架构 <https://gvisor.dev/docs/architecture_guide/security/> + Docker 快速上手 <https://gvisor.dev/docs/user_guide/quick_start/docker/> + CVE 列表 <https://app.opencve.io/cve/?product=gvisor&vendor=google>；Firecracker 设计/威胁模型 <https://github.com/firecracker-microvm/firecracker/blob/main/docs/design.md>；Kata 架构 <https://github.com/kata-containers/kata-containers/blob/main/docs/design/architecture/README.md>；Sysbox <https://github.com/nestybox/sysbox>；OpenHands 沙箱 RW 挂载告警 <https://docs.openhands.dev/openhands/usage/sandboxes/docker>；Portainer bind-mount 绕过 <https://github.com/advisories/GHSA-7fw3-x4r2-g7wc>；E2B Firecracker 编排 <https://deepwiki.com/e2b-dev/infra>；R-WORKSPACE-PRODUCT §10–§11；Sherpa 现状（`infra/docker-compose.yml` worker 挂 `docker.sock`、`backend/app/sandbox/runner.py` 硬化容器、`docs/05` §sandbox socket 安全）；负责人输入「第一优先完成独立安全评审……给出 W3 首版可实施的最小安全架构与明确禁止上线条件；不得把未实施缓解写成已安全」。

> **收窄修订（2026-07-30，见 [ADR-047](#adr-047)）**：§决策1 的「新增**一份一次性 scratch 只读拷贝的 RW 挂载**」收窄为「以 **tar 流注入**一次性 scratch 副本进容器内**匿名卷**」。控制①（`nosuid,nodev`）、②（拷贝前剔除/断言无凭据）、④（绝不挂快照/blob store/其它 project/Drive/`WORKSPACE_ROOT`/`TOOL_OUTPUT_ROOT`/socket/凭据）、⑤（`finally` 原子清理 + 启动扫除孤儿）、⑥（沙箱绝不接触 `docker.sock`）**逐条保留**；控制③（校验 scratch 源路径在 `SANDBOX_SCRATCH_ROOT` 内、无穿越）因**不再存在 `src=` 参数**而**不再适用** —— 攻击面被结构性移除而非被信任替代。**§决策2 的禁止上线条件（多用户 / 真不可信第三方代码前须 gVisor 或 microVM + 不共享 socket + 每租户 scratch 隔离 + 租户级出口策略 + 聚合配额 + 权限无跨租户泄漏 + 威胁评审）完全不变**，且仍是 roadmap 上的硬门；§决策3「不把未实施缓解写成已安全」继续适用于本次修订本身。

---

### ADR-040 · Projects W3 = Project Chat 任务工作副本 + 一次性 scratch 沙箱变更评审（契约与设计先行；延伸 ADR-037；受 ADR-039 门控；正式修订 ADR-025）

> **状态：方向由负责人拍板（2026-07-27）——按 W2a→W2b→W3→W4 正常顺序进入 W3，先执行「安全评审 + ADR/契约/设计先行」。本 ADR 把 W3（Project Chat 任务工作副本 + 一次性 scratch 沙箱 + 变更评审）的**产品/数据/工具/生命周期**冻结为契约，实现待本批评审通过再开始。本批次不写生产代码/迁移/不做真实沙箱挂载/不暴露 W3 导航（AGENTS.md §1/§2）。** 隔离前提见 [ADR-039](#adr-039)；ADR-025 挂载口径修订见 [ADR-025 正式修订（2026-07-27）](#adr-025)；调研见 [`research/workspace-product-report.md`](research/workspace-product-report.md) §10；W3 静态设计稿见 [`design-workspace/w3-change-review.html`](design-workspace/w3-change-review.html)。

- **背景（承接谁）**：ADR-037 §决策2 把 W3 粗描为「**Project Chat 任务工作副本 + sandbox 变更评审**；**仅允许 Sandbox 挂载一次性 scratch 副本，绝不挂载项目真相源**；持久权威=`project head snapshot` + 任务工作副本 overlay，scratch 卷/热容器都是可重建缓存」，并要求 W3 前置 [ADR-039] 隔离评审 + ADR-025 修订。W2a（空/模板/归档）、W2b（GitHub 一次性导入）已上线。本 ADR 就是 ADR-037 预告的 W3「后续 ADR」。

- **决策（负责人方向 + R-WORKSPACE-PRODUCT §10 收敛，2026-07-27）**：
  1. **真相源层级（不可动摇）**：`project_snapshots` head（**持久、已保存、用户可见的真相源**）→ **任务工作副本**（durable pending overlay，**跨 chat turn 的持久任务态**）→ *scratch 卷*（节点本地缓存，可重建）→ *sandbox 容器*（短 TTL、可选热态、随时可杀）。**系统维护的是任务工作副本，不是某个容器**：热容器只降延迟，**对正确性/恢复从不必需**。只有 Postgres/MinIO/journal 态是权威；scratch 树/热容器/预备镜像是**可丢缓存，绝不是恢复真相源**（从 `base 快照 + 持久 overlay` 重建）。
  2. **Project Chat 首次变更动作 → 惰性开持久工作副本**：Project 绑定 Chat 初始只读 head；**首个变更动作**（`project_run`/edit）**原子创建**任务工作副本（`base_snapshot_id=current_snapshot_id`，记 `base_head_generation`）。General chat 无工作副本。一个 Project 的多个 Chat 得到**互相隔离**的工作副本（绝不共享可写 scratch 树）。
  3. **每次执行物化一次性 scratch 拷贝**：取工作副本的 single-writer lease/fence → 把 `base 快照 + 持久 overlay` 物化进**一份全新一次性 scratch 树** → 起 [ADR-039] 的**硬化断网**沙箱，**只**挂该 scratch（**绝不**挂快照/blob store/凭据/其它 project/Drive）。**有界批次后、等用户前、拆容器前**把 scratch delta **持久化进 overlay**（+ change-set 投影），并盖 fence。**未持久化的 scratch 写入绝不算完成的工作**；容器/节点丢失 → 从最后持久边界**重物化**。
  4. **single-writer lease + fence**：一个工作副本一个活动写者租约（互斥）+ 单调 `fence_token`（每次(重)取租约自增），盖在每次 overlay/change-set/sandbox-run 发布上。**stale 沙箱（fence 落后）绝不能发布 overlay/change set**，即使重投递让其租约看似 live。
  5. **Change Review 展示 added/modified/deleted + artifacts**：每个执行边界比对 scratch↔持久 overlay↔已保存 base，拒绝路径逃逸/不安全 symlink/设备/socket/`.git` 凭据或 config 泄漏，并**有界**（改动文件数/字节/artifact 字节/diff·输出大小，`WORKING_COPY_MAX_*`）。**超界 ⇒ 显式 truncated 局部评审，绝不静默给一份看似完整的 diff**。文本 diff 溢出到 MinIO（有界）；二进制/超大只摘要不内联。
  6. **用户 Save selected / Save + checkpoint / Discard（Save 不给 agent）**：*Save selected* 应用被选子集 → 建**新不可变快照**（`reason='save'`）、原子推进 `current_snapshot_id` **并**自增 `head_generation`；未选留在工作副本。*Save + checkpoint* 再 pin（`reason='checkpoint'`, `pinned=true`）+ 命名/备注，chat 可从新 head 开新工作副本。*Discard* 删 overlay/暂存字节、释放配额预留，head 保持与 base **逐字节相同**。**推进 Project head 是显式人工评审决定，绝非 agent 自动应用**（agent-save 留后续 ADR + grant 门控）。
  7. **head 移动 → stale Save 必须拒绝（head_generation CAS）**：`projects.head_generation` 在推进 `current_snapshot_id` 的**同一事务**自增。Save 是对 `(current_snapshot_id==base_snapshot_id AND head_generation==base_head_generation)` 的 **compare-and-set**：head 若移动（另一 chat Saved / W4 apply-remote）→ Save **失败为 conflict**（`409 head_moved`）、**什么都不应用**，须**重评审 rebase** change set；**绝不对错误 base 应用**。
  8. **内置 file/edit/run/test 工具在 scratch 上工作，不嵌 coding agent**：W3 执行器 = **Sherpa 内置工具**（`project_run` 驱动 file/edit/run/test）；**不内嵌 Copilot CLI/Claude Code 等专用 coding agent**；沙箱**断网、无 model/provider 凭据**。
  9. **缺依赖显式 `environment_missing_dependencies`；不做包安装**：命令**只**用已在批准基础镜像里的 runtime/工具 + 已在项目快照里的依赖运行；缺依赖 → 明确 `environment_missing_dependencies`，**绝不**私自联网装包/开网。
  10. **W3 明确不做（留 W4/later）**：依赖安装/包管理器；`git init/history/commit/branch`、merge、push、建远程分支、PR、force push（**GitHub sync/push/PR 全是 W4**）；长驻 dev server/托管预览/进程复活；网络化开发环境；内嵌 coding-agent 执行器。

- **数据模型（W3；canonical 与派生分离；名义命名，见 data-model §Projects W3）**：`projects.head_generation`（CAS token）；`project_working_copies`（持久 pending 态：base 快照/`base_head_generation`/state/`version`/`fence_token`/`lease_owner`/`lease_expires_at`/`reserved_bytes`/overlay rollup/`last_boundary_at`/`expires_at`）；`project_working_copy_entries`（**持久 overlay**：path/added·modified·deleted/blob 引用/fence）；`project_change_sets` + `project_change_set_entries`（有界可评审投影 + `selected`/有界 diff spill/`truncated`）；`project_artifacts`（run 产物，`retention='ephemeral'`，仅 Keep/Export 才计配额）；`project_sandbox_runs`（沙箱执行链接 run+工作副本 + **非权威**操作元数据 `scratch_ref`/`container_ref`/`warm_until` + 具名 `termination_reason` + `persisted_boundary_at`）。均带 `tenant_id`+复合键（ADR-015）；文件字节复用 [ADR-030] 不可变去重 `storage_blobs`，**绝不进 journal/change-set 行本体**。

- **能力面（ADR-023 单能力层 + 薄 REST/Tool 双适配；见 api §10.7）**：service `services/projects.py` + `services/project_changes.py` + `services/sandbox`（编排 [ADR-039] 硬化容器 + 一次性 scratch）。**REST**：`GET /sessions/{id}/working-copy`、`POST /projects/{id}/sandbox-runs`、`GET …/working-copies/{wc}`、`GET …/change-sets/{cs}` + `…/entries/{e}/diff`、`POST …/change-sets/{cs}/apply`（Save selected/checkpoint，`409 SaveConflict`）、`…/discard`、`…/working-copies/{wc}/discard`、`GET …/artifacts` + `…/keep`/`…/export`。**Tool（W3）**：`project_run`（内置 file/edit/run/test on working copy → `idempotent_write`、**allow**，仅 Project 绑定 chat + 仅 ADR-039 硬化 scratch-only 挂载）、`project_review_changes`（读 change set → `read_only`、**allow**）。**不给 agent（人工评审闸）**：`project_save`/`project_checkpoint`/`project_discard`/artifact `keep`·`export`（**user-only**）、`project_push`（W4）、任何破坏性 purge、依赖安装。项目文件 + 沙箱输出仍是**不可信内容**（ADR-009）。**UI**：SPA 路由复用 `/work/projects`——**W3 只交付静态稿**，能力矩阵 UI 列保持 ⬜、**不暴露 W3 生产导航**。

- **事件/幂等/crash recovery（复用 ADR-016/017；见 events §2.11）**：W3 执行跑在 Project 绑定 chat 的**持久 run**里（复用 `run`/`event_journal`，`project_run` 是普通 tool-call/result），**不新增 run 事件类型**，项目侧持久记录 = W3 表 + 结构化日志。**① 沙箱执行无对外副作用 ⇒ 该 run 无 `effect_unknown`**（断网、只挂一次性 scratch，不改任何外部系统/远端/真相源）；容器/节点/重投递丢失只触发**重物化**续跑。**② 唯一持久副作用 = fence 守护的 overlay/change-set 持久（幂等）**：同 fence+边界重放产出相同 overlay（内容寻址去重），stale fence 被拒；`persisted_boundary_at` 未置前不报「durably 完成」。**③ Save = head_generation CAS（幂等）**：head 移动即 conflict 不应用；重放已应用 change set 是 no-op。**④ 具名 termination（每个出口）**：`done|environment_missing_dependencies|wall_timeout|mem_limit|pids_limit|output_limit|changeset_bounds|path_escape|fence_lost|sandbox_unavailable|error:...`。**⑤ 凭据绝不进** scratch/overlay/change-set/artifact/快照/journal/日志（ADR-019/039）。

- **安全边界**：完全遵从 [ADR-039]（硬化容器 + 仅挂一次性 scratch + 拷贝前剔除凭据 + `nosuid,nodev` + 编排原子清理/孤儿扫除 + 单用户前提 + 多用户禁止上线条件）与 [ADR-025 修订]（永不 RW 挂真相源）。config §1.7 冻结 mount/lifecycle/resource/network/credential 边界与 `SANDBOX_*`/`WORKING_COPY_*` 设置。

- **取代/延伸关系**：**延伸 ADR-037**（把 §决策2 的 W3 从预告落成契约）；**受 [ADR-039] 门控**（隔离前提）；**正式修订 [ADR-025]**（挂载口径）；**复用** ADR-030 不可变去重 blob + 配额记账、ADR-016/017 journal+outbox 真相源与 durable 语义、ADR-023 能力层双适配、ADR-015 租户键、ADR-009 不可信内容、ADR-019 密钥边界；**不改** ADR-020（W4 push 才走审批信封）。**W4** = GitHub 同步/push/PR，另带自己的 ADR。

- **验收关键（本契约先行批次）**：ADR-040 被接受；data-model 有 `project_working_copies`/`project_working_copy_entries`/`project_change_sets`/`project_change_set_entries`/`project_artifacts`/`project_sandbox_runs` + `projects.head_generation`（canonical vs 派生清晰、lease+fence、head-gen CAS、每表 `tenant_id`+复合键、字节不入 journal）；api §10.7 有 working-copy/sandbox-run/change-review/Save selected·checkpoint·Discard/artifacts REST + Tool schema（`project_run`/`project_review_changes` allow、Save 系列 user-only）；events §2.11 有「沙箱无 effect_unknown」+ fence 幂等持久 + head-gen CAS + crash recovery；config §1.7 有 mount/lifecycle/resource/network/credential 边界 + `SANDBOX_*`/`WORKING_COPY_*`；能力矩阵有 W3 行且 **UI 列 ⬜**；W3 静态稿（Project Chat 执行态/diff change review/artifacts/Save·checkpoint·Discard/stale·conflict/390px）落在生产 Quiet Work 设计系统、桌面与 390px 均合理，且**明标设计稿、不冒充已实现**；**无生产代码/迁移/真实挂载/W3 导航暴露**；W4 非目标写清。契约**先于代码**（本 ADR 同批）。

- **来源**：R-WORKSPACE-PRODUCT 调研 [`research/workspace-product-report.md`](research/workspace-product-report.md) §10（真相源层级、Project Chat 生命周期、持久边界、并发与恢复、change set out、用户动作、初始执行器边界）；ADR-037 §决策2/3/4；[ADR-039] 隔离评审；Gitpod/Ona 工作区快照凭据边界、OpenHands「RW 挂载可被 agent 改」；负责人输入「按正常顺序进入 W3……W3 目标：Project-bound Chat 首次变更动作创建跨 turn 持久 working copy；Sherpa snapshot/head 是真相源；working copy/overlay 是持久任务态；每次执行只物化一次性 scratch 副本，sandbox 绝不挂 project snapshot/blob store/凭据；内置 file/edit/run/test 工具在 scratch 上工作；有界批次后持久化 overlay；Change Review 展示 added/modified/deleted/artifacts；用户可 Save selected、Save+checkpoint、Discard；head 移动时 stale save 必须拒绝；single-writer lease/fence；容器是短 TTL 可丢缓存；缺依赖显式 environment_missing_dependencies；不做包安装、不嵌 coding agent、不做 git init/history/commit/branch、不做 GitHub sync/push/PR（W4）」。

> **执行器口径修订（2026-07-30，见 [ADR-048](#adr-048)；clean break，[ADR-045](#adr-045) 统领）**：§决策8「内置 file/edit/run/test 工具在 scratch 上工作，不嵌 coding agent」修订为——
> - **执行器分层**：`fs.*` 走**宿主侧**直接读写工作副本 effective tree（无需容器；严格强于被删的 `project_tree`/`project_read`，后者只看 head、看不见 agent 刚写的内容）；`sh.*`/`run.*` 经**显式 `RuntimeSession`** 进沙箱。**沙箱不可用只损失「跑」，不损失「改」。**
> - **`project_run` / `project_tree` / `project_read` 删除**（无 shim、无别名）；`project_sandbox_runs`（含从未实现的 `warm_until`、tar 传输下无意义的 `scratch_ref`）拆为 `project_runtime_sessions` + `project_exec_runs`；`POST /projects/{id}/sandbox-runs` 由 `POST /projects/{id}/runtime` + `POST /runtime/{rid}/exec`（202 + SSE + cancel）取代，且**改由 worker 执行**。
> - **"不嵌 coding agent"→"v1 不嵌，接缝预留"**：未来沙箱内专用 coding agent 以 **sub-agent 适配器**（`delegate.code_task(runtime_session_id, …)`）形态接入同一容器、同一 overlay、同一预算与审计路径，**不需重写编排**。这是 `RuntimeSession` 必须在 v1 就是显式一等对象的根本原因。
> - **不变**：真相源层级、single-writer lease + fence、`head_generation` CAS Save（`409 head_moved`）、Save/checkpoint/Discard **人工专属**、change set 有界 + 显式 `truncated`、artifacts 默认 `ephemeral`、**无包安装、无开网**、缺依赖显式 `environment_missing_dependencies`、W4 边界。

---

### ADR-041 · 多来源模型 provider = DB 支持、用户可配的多 provider 注册表（OpenAI 兼容 + 原生 Anthropic/Gemini；AEAD 密钥；全局默认 + 每会话可切）——契约与设计先行（延伸 ADR-008；复用 ADR-019/015/033；源自 R-MODEL-PROVIDER）

> **状态：方向由负责人拍板（2026-07-28）——下一步先做「多来源模型 provider，用户在 Settings 里配置」。本 ADR 把它的**数据/能力面/密钥/选择/归一化**冻结为契约，实现待本批评审通过再开始。本批次只做 ADR + 契约与设计先行（无生产代码/迁移；roadmap #8 的「多 provider」那一半，failover/子 agent 后置）。** 调研见 [`research/model-provider.md`](research/model-provider.md)（深读 AstrBot `AstrBotDevs/AstrBot`、hermes-agent `NousResearch/hermes-agent`、PI-agent `earendil-works/pi` + provider landscape）；静态 Settings「Models」稿见 [`design-settings-models/index.html`](design-settings-models/index.html)。

- **背景（承接谁）**：现状是单一、**env 配置**的 provider（`app/providers/factory.py` 从全局 `settings` 选 mock / `openai_compatible`），`build_provider()` 被 worker/connector 各调一次。roadmap #8「多 provider failover + 子 agent」——本 ADR 只落地其中「**用户可配多来源 model**」这一半；failover/ensemble/子 agent 另开 ADR。三个参考项目一致收敛出「**声明式 provider 配置 + 少数几个 wire 适配器**」，且**三者密钥都明文存**——Sherpa 用 **AEAD 存 DB** 是差异化点。

- **决策（负责人方向 + R-MODEL-PROVIDER 收敛，2026-07-28）**：
  1. **DB 支持的多 provider 注册表取代 env 单一 provider**：新增 `model_providers` 表，一行 = 一个用户配置的来源。owner 在 **Settings「Models」面**增删配置。**env `PROVIDER_*` 保留**为「无 DB 配置时兜底 + 测试 mock」（不破坏离线/CI）。
  2. **声明式配置 + 现有 `Provider.stream` 之下的行为适配器**：配置行携带 `kind`/`api_mode`（`openai_compatible` | `anthropic` | `gemini`；forward：`bedrock`/`vertex`/`openai_responses` 留而不建）+ `base_url`（可空→用 kind 默认）+ AEAD 密钥 + `models` 列表 + `default_model`。**「加一个 OpenAI 兼容来源 = 加一条配置行」**（DeepSeek/Qwen/Moonshot/Mistral/xAI/Groq/OpenRouter/Ollama/Gemini-OAI 等同一 `openai_compatible` 适配器 + 换 `base_url`）。
  3. **首版 3 个 wire 适配器**（全在 `Provider.stream` 之下）：**增强 `openai_compatible`**（加兼容层坑处理：`reasoning_content`/`reasoning` 变体、`<think>` 标签剥离、tool-call delta 缺 `index`/`type` 修补、空 assistant 消息过滤、per-choice usage）+ **原生 `anthropic`**（Messages API：system 顶层、`input_schema` 工具、`tool_result` 入 user、连续同角色合并、`max_tokens` 必填正数、block SSE→`Text/Reasoning/ToolCall/Finish`、Claude 4.7+ 略采样参数、thinking 签名回传、`refusal`/`end_turn` 空 content 合法不重试）+ **原生 `gemini`**（`generateContent`：`functionDeclarations` + schema 收敛[type-list→单一/去 additionalProperties/array.items 必填]、parts 流归一、`thought_signature` 回传、tool 参数单块不流式）。
  4. **密钥 = AEAD 存 DB，仅连接边界解密**：复用 `security/github_token.py` 的 **KEK 直封**（AES-256-GCM，AAD 由行身份重算）+ connector-vault capability 门控；`model_providers` 用 `github_connections` 同款列形态（`token_enc`/`nonce`/`kek_id`/`key_version`/`token_algorithm`/`aad_version`）。密钥**只**在 `build_provider(db, ctx)` 构造适配器时解密，**绝不**进日志/事件/prompt/工具输出/前端（写只入不出；`test`/`models` 拉取全服务端）。
  5. **全局默认 + 每会话可切 model**：全局默认 = `model_providers.is_default` 行 + 其 `default_model`（`uq` 保证唯一活跃默认）；**每会话覆盖** = `sessions` 加不可空绑定 `model_provider_id`(nullable) + `model`(nullable)，chat 顶栏切换器持久化到该会话。**切换必须连带 provider 引用**（避免 hermes #25106：用旧 `base_url`/`api_mode`）。首消息后是否冻结绑定不强制（可随时切，下条消息生效）。
  6. **工具/流式/推理归一**：canonical 内部工具 schema（`{name,description,input_schema}`，已是 Anthropic 形状）→ 每格式序列化器 `to_openai_tools`/`to_anthropic_tools`/`to_gemini_tools`；各适配器把各家流归一回**现有** `TextDelta/ReasoningDelta/ToolCall/Finish`；**opaque 推理签名原样回传**（Anthropic thinking signature、Gemini `thought_signature`），否则多轮 HTTP 400。
  7. **retry 就地、failover 后置**：适配器/loop 层做 429/5xx 指数退避（区分 fail-fast 的 `insufficient_quota`/`quota exceeded`）；**跨-provider failover 三方都不在 provider 层做** → 本期不做，另开 ADR。
  8. **本期明确不做（留后续 ADR）**：跨-provider failover；MoA/ensemble；子 agent；成本 ledger + prompt-cache 计量；Bedrock/Vertex/OpenAI-Responses/Codex wire；多 key 轮换环；provider 配置的 agent 工具（配置是**人工**动作）。

- **数据模型（本 ADR；见 data-model §Model providers）**：`model_providers`（`tenant_id`+`id` 复合键、`user_id`、`kind`、`display_name`、`base_url`、AEAD 密钥列、`models text[]`、`default_model`、`enabled`、`is_default`、`status`[pending/active/error]、`last_error_redacted`、时间戳；`uq` 唯一活跃默认 + 唯一名）；`sessions` 加 `model_provider_id`(nullable, FK) + `model`(nullable)。文件/密钥字节复用既有 AEAD 边界，**绝不进 journal**。

- **能力面（人工配置；无 agent 工具；见 api §10.8）**：service `services/model_providers.py`（增删改 / 测试连接 / 列 model / 选默认，AEAD 封装/解封于连接边界）。**REST**：`GET/POST /providers`、`GET/PATCH/DELETE /providers/{id}`、`POST /providers/{id}/test`（测试连接：拉 `/models` 或极小 chat）、`GET /providers/{id}/models`、`POST /providers/{id}/default`（选默认）、`GET/POST /sessions/{id}/model`（读/切会话 model）。写需 CSRF；密钥只入不出。**不新增 agent 工具**（provider 配置跨凭据边界 = 人工，同 GitHub 连接；model 选择是设置，非 agent 动作）。审计：create/delete/select 落 `audit_receipts`。**UI**：Settings 新增「Models」面 + chat 顶栏 model 切换器——**本批次只交付静态稿**，能力矩阵 UI 列保持 ⬜。

- **事件/幂等（复用 ADR-016/019）**：provider 配置是**配置态**，不产生 run 事件、无 `effect_unknown`（不改任何外部真相源；`test` 是只读拉取）。密钥封装/解封走 AEAD vault 语义（AES-GCM 认证失败 = 终态完整性错误）。

- **安全边界**：完全遵从 [ADR-019]（密钥 env/AEAD、连接边界解密、绝不日志/沙箱）；密钥经 `security/redaction.py` 脱敏兜底；`test`/`models` 于服务端用密钥、绝不下发前端。多 provider 不改沙箱边界（[ADR-039]）——沙箱仍**无** model/provider 凭据。

- **取代/延伸关系**：**延伸 [ADR-008]**（保留 `Provider.stream` narrow 接口，把「选 provider」从 env 单一升级为 DB 多来源 + 3 wire 适配器）；**复用** [ADR-019] 密钥/AEAD、[ADR-015] 租户键、[ADR-033] 观测（新适配器发同样的 `gen_ai` span + `llm call` 日志）、[ADR-023] 能力层（但 provider 配置是 human-only REST，**无** agent 工具）；**不改** ADR-016/017/020/025。roadmap #8 的 **failover/子 agent** 另开 ADR。

- **验收关键（本契约先行批次）**：ADR-041 被接受；data-model 有 `model_providers`（AEAD 列 + `models`/`default_model`/`is_default`/唯一默认，`tenant_id` 复合键）+ `sessions` model 绑定；api §10.8 有 providers CRUD + `test`/`models`/`default` + session-model REST（写需 CSRF、密钥只入不出、**无** agent 工具）；config 有 `PROVIDER_*` 兜底说明 + 无新 env 密钥（密钥进 vault）；能力矩阵有「模型 provider 配置」「会话 model 切换」行且 **UI 列 ⬜**；静态 Settings「Models」稿（来源列表/加来源[kind 下拉+base_url+password key，永不回显]/测试连接/列 model/选默认/会话切换器）落在生产 Quiet Work 设计系统、桌面 1280 + 390px 均无横向滚动、**明标设计稿**；**无生产代码/迁移**。契约**先于代码**。

- **来源**：R-MODEL-PROVIDER 调研 [`research/model-provider.md`](research/model-provider.md)（AstrBot `AstrBotDevs/AstrBot@3f9aa74`、hermes-agent `NousResearch/hermes-agent@7100e8d`、PI-agent `earendil-works/pi@c820aa2` 的 provider 层深读 + provider landscape + 三方真实 quirk 目录）；负责人输入「下一步先做 model provider，支持多个来源的 model，由用户在设置里配置」「需要支持 OpenAI/Anthropic/Gemini/DeepSeek/Qwen 等全部主流 provider，参考 hermes-agent/AstrBot/PI-agent」「做全局默认 + 每会话可切」；Sherpa 现状 `app/providers/*` + `app/security/{vault,github_token,keyring}.py`。

---

### ADR-042 · Drive 文件夹/批量上传 = 客户端有界展开（复用现有 `POST /drive/folders` + `POST /drive/files`），不新增 batch/zip 端点（落地 backlog B-5；复用 ADR-030 契约）

> **状态：已接受（2026-07-29）。** 源自手工测试发现 [`backlog.md` B-5](backlog.md#b-5-drive-cannot-upload-a-folder)：Drive 一次只能传**一个**文件，文件夹无法上传（`WorkspaceView.tsx` 是裸 `<input type="file">`，handler 只取 `files?.[0]`）。

- **背景**：ADR-030 的 Drive 契约（api §10.2）已经具备文件夹（`POST /drive/folders`）与单文件上传（`POST /drive/files`，multipart + `parent_id`），配额 `507` / 单文件上限 `413` / 同名 `409` 语义齐备。缺的**只是客户端**：没有 `multiple`、没有 `webkitdirectory`、没有目录拖拽遍历。

- **决策**：
  1. **采用「客户端有界展开」（B-5 方案 a）**：`<input multiple webkitdirectory>` + 拖拽的 `DataTransferItem.webkitGetAsEntry()` 目录遍历 → 先按相对路径**逐层建目录**（`POST /drive/folders`；同名 `409` 视为「已存在，复用」），再逐个上传文件（`POST /drive/files`）。**服务端零改动、契约不变**。
  2. **明确不做「archive 上传」（B-5 方案 b）**：它需要新端点 + 复用 ADR-037 的有界解压器（那是**项目导入**路径，配额/租户语义不同），而且要求用户先自己打包——对「把一个文件夹拖进来」这个诉求是绕路。若将来出现万级小文件场景再另开 ADR。
  3. **有界**（防一次拖入 `node_modules` 打爆浏览器与后端）：单次 ≤ **200** 个文件、总计 ≤ **200 MiB**、上传并发 **3**；超界在**发起前**拒绝并明示原因。单文件仍受服务端 `413` 与配额 `507` 约束——客户端不复制这些阈值，只诚实转述服务端的回答。
  4. **部分失败诚实呈现**：批量上传**没有事务性**（Drive 无批量端点，也不引入伪回滚）。每个文件一行状态（等待/上传中/完成/失败+原因），失败项可单独重试；遇到 `507`（配额耗尽）**立即停止**后续排队项，而不是刷出一屏同样的错误。
  5. **无新 REST、无新 agent 工具**：这是纯客户端能力；agent 侧已可用 `drive_make_folder` + `drive_write` 组合达成同一效果（ADR-023 能力对等仍成立）。

- **契约影响**：`docs/contracts/api.md` §10.2 增加一条说明（文件夹上传 = 客户端展开，服务端仍是单文件端点），**无 schema 变更、无迁移**。

- **验收关键**：能选择/拖入一个**嵌套**文件夹并在 Drive 中重建出同样的树；超界（>200 文件或 >200 MiB）在上传前被拒绝且原因可读；单个文件失败不影响其余文件；`507` 停止后续排队；390 px 无横向溢出；`npm run lint` + `npm run build` 绿；能力矩阵（docs/11 §9）Drive 上传行的 UI 列补齐。

- **来源**：backlog B-5（2026-07-28 手工测试）；现状 `frontend/src/views/WorkspaceView.tsx`、`backend/app/api/drive.py`、api §10.2。

---

### ADR-043 · Chat 附件 = 类型化 message parts（`image`/`file_ref`）+ Drive 作唯一字节存储 + provider 多模态翻译 + 每来源 `supports_vision` 标志（落地 backlog B-6；扩展 ADR-005/008/030；延伸 ADR-041）

> **状态：已接受（2026-07-29）。** 源自负责人诉求 [`backlog.md` B-6](backlog.md#b-6-chat-attachments-image-uploadpaste--attach-from-drive)：在 Chat 里 (a) 上传/粘贴图片，(b) 从 Drive 附加已有文件。现状整条链路是**纯文本**：composer 无附件、admission 只落一个 text part、`core/history.py` 把每个 user turn 压成字符串 `content`，任何多模态内容都到不了 provider。

- **决策**：
  1. **字节只存 Drive，`parts` 只存引用**：composer 上传/粘贴的图片先写入 Drive（自动目录 **`Chat uploads/`**），拿到 `drive_node_id` + `version`；从 Drive 选择的文件直接引用。**绝不**把字节复制进 `parts`/journal。→ 免费继承配额（`507`）、单文件上限（`413`）、版本、回收站与 GC（ADR-030），附件可被再次引用，agent 也能用 `drive_read` 读到同一份东西。
  2. **类型化 parts**：`parts.kind` 由 `('text','status')` 扩展为 **`('text','status','image','file_ref')`**，`content_redacted = {drive_node_id, version, name, content_type, size_bytes}`。data-model DDL 注释 5 相应修订：**仍然不存 chain-of-thought**——新增的是**用户输入的引用**，不是模型推理。
  3. **admission 携带引用**（api §4）：`PromptRequest.attachments: [{drive_node_id, version?}]`，≤ **8** 个。服务端解析时校验属主、非 trashed、按 `content_type` 判定 `image` 还是 `file_ref`、钉住 `version`（缺省 = 当前版本），与 text part 在**同一事务**落库（ADR-005 的「先持久化输入」不变）。幂等：同 `client_message_id` 的重放必须 text **与**附件集合都相同，否则 `409`。
  4. **装配期读字节，且有界**：`assemble_provider_history` 在**每次装配**时从 Drive 读附件字节（而不是 admission 时 inline base64），把带附件的 user turn 变成 OpenAI 形状的 content 数组；**纯文本 turn 保持字符串原样**（既有会话的缓存前缀字节不变）。上限：单图 ≤ **5 MiB**、单次装配图片总量 ≤ **15 MiB**，超出者降级为文本占位。附件块位于 user turn（前缀**尾部**），系统层前缀不受影响 → docs/04 不变式⑤保持。
  5. **非图片文件**：文本类（`text/*`、json/csv/md/xml/yaml）→ **有界文本抽取**（≤ 32 KiB，截断显式标注）内联为文本块；二进制 → 只给「名称/类型/大小 + 可用 `drive_read` 读取」的诚实指针，**绝不假装模型看得到**。
  6. **vision 能力 = 每来源标志**（延伸 ADR-041）：`model_providers` 增列 **`supports_vision`**（默认 `true`，用户可在 Settings「Models」按来源关闭）。为 `false` 时图片**降级为文本占位**并说明原因（提示切换来源/模型），而不是把图片硬塞给不支持的端点再吃 400。无 DB 来源时的 env 兜底 provider 视为 `true`。
  7. **provider 翻译**（ADR-008 narrow waist 不变）：`openai_compatible` 直通 content 数组；`anthropic` 把 `image_url` data URL 翻成 `{"type":"image","source":{"type":"base64",…}}`；`gemini` 翻成 `{"inlineData":{"mimeType","data"}}`；`mock` 容忍数组（取其中文本）。
  8. **本期明确不做**：模型**产出**图片、音频/视频、附件 OCR/向量化（那是 Knowledge ADR-036 的活）、附件级共享/权限、把 Drive 之外的外链当附件、`assistant` 侧多模态输出。

- **数据模型**：迁移 **0032** = 放宽 `ck_parts_kind` + `model_providers.supports_vision boolean NOT NULL DEFAULT true`。无新表。

- **能力面**：无新 agent 工具——附件是**人在 composer 里的输入动作**；agent 侧读同一批字节的能力已由 `drive_read`/`drive_list` 覆盖（ADR-023 对等成立）。REST：`POST /sessions/{id}/prompt` 增 `attachments`；`GET /sessions/{id}/messages` 的 part 增附件元数据（供转录渲染缩略图/下载）；`PATCH /providers/{id}` 增 `supports_vision`。

- **安全边界**：附件字节走既有 Drive 属主校验（tenant + user 双重作用域，跨用户结构性不可达）；附件**不进日志/事件 payload**（journal 只留引用与大小）；不可信来源（如邮件抽取）**不会**自动变成附件——附件只能由人在 composer 产生或从自己的 Drive 选择（ADR-009 的不可信内容边界不变）。

- **验收关键**：迁移 0032 可升可降；粘贴一张图片 → 落 `Chat uploads/`、chip 可见、发送后模型**真的**描述出图片内容（agent lane）；`supports_vision=false` 时同一图片得到诚实占位而非报错；纯文本会话的 provider 消息形状**逐字节不变**（回归测试）；转录能渲染缩略图与文件 chip；390 px 无溢出；后端 `pytest`/`ruff`/`mypy` 与前端 `lint`/`build` 全绿；能力矩阵新增「Chat 附件」行且 UI 列为 ✅。

- **来源**：backlog B-6（2026-07-28 负责人诉求）；现状 `backend/app/core/{admission,history}.py`、`app/providers/*`、`frontend/src/views/ChatView.tsx`；复用 ADR-030 Drive、ADR-041 provider 注册表。

---

### ADR-044 · 测试套件数据面隔离 = 专用 `<应用库>_test` + 独立 Redis 逻辑库 + 合成 owner + 标记表 fail-closed 守卫（落地 backlog B-9；复用 ADR-015/019/022）

> **状态：已接受（2026-07-29）。** 源自负责人诉求 [`backlog.md` B-9](backlog.md#b-9-the-test-suite-deletes-the-owner-tenant-in-the-dev-database)：一次 `uv run pytest` **摧毁了开发工作区**（`model_providers`=0、`projects`=0、会话被清空），并且在开发 worker 运行时随机打挂一个 API 用例。

- **根因（代码确证，不是猜测）**：三件事叠加，缺一不可。
  1. `backend/` 下没有 `.env`，`Settings.database_url` 落到默认 `postgresql+asyncpg://sherpa:sherpa@localhost:5432/sherpa`，而 compose 把 postgres 映射到宿主 `5432:5432` —— **测试进程与开发栈是同一个库**。Redis 同理（都是 `/0`）。
  2. `owner_ids()` 由 `OWNER_EMAIL` 确定性派生 uuid5，**测试用的就是运行中的栈登录的那个身份**；20 个测试文件靠 `DELETE FROM tenants WHERE tenant_id = <真 owner>` 取得干净起点。
  3. 所有租户表都带 `ForeignKeyConstraint(tenant_id → tenants, ondelete="CASCADE")`（ADR-015 的前向兼容租户键），于是那一行 DELETE **级联删掉整个工作区**。
  - 死锁是同一根因的第二症状：worker 的 `project_workcopy_maintenance` cron（`expire_idle()` 先改 `storage_accounts` 再改 `project_working_copies`）与测试 DELETE 的级联加锁顺序相反 → `DeadlockDetectedError`。
  - CI 没暴露，只是因为 CI 的库本来就是一次性的、**且不跑 worker**。

- **决策**：
  1. **隔离数据面，而不是修 20 处 DELETE**。把 20 处写好看并不能阻止下一处写错，也不能解决 Redis 串扰与 worker 抢跑。分四层，层层独立可失效：
     - **L0 环境垫片**：`tests/__init__.py`（Python 执行 `tests` 包的第一个模块，早于 `conftest.py`）改写 `DATABASE_URL` / `REDIS_URL` / `OWNER_EMAIL` 与 scratch 根目录，**必须早于 `app.config` 建成 `settings` 单例**——`app/db.py` 的 engine 就是从那个单例派生的。垫片会显式重建单例，使其对导入顺序不敏感。
     - **L1 供给**：会话钩子建库 → 子进程跑 `alembic upgrade head` → 盖标记表。
     - **L2 fail-closed 守卫**：**标记表 `_sherpa_test_marker` 是允许破坏性写入的唯一凭据**。没有它就中止整次运行，**绝不**降级为 skip、更不会退回应用库。
     - **L3 唯一破坏性入口**：`drop_tenant()`，带 `lock_timeout` + 单次重试。
  2. **合成 owner = 纵深防御**：`OWNER_EMAIL` 固定为 `test-owner@sherpa.test`。因为 `owner_ids()` 从它派生，**即使 L1/L2 全部失效，被删的租户在数学上也不可能是真 owner**（uuid5 不同）。20 个测试文件全部符号引用 `settings.owner_email`，因此零改动兼容。
  3. **库名派生 + 显式覆盖**：默认 `<应用库>_test`（`sherpa` → `sherpa_test`），`TEST_DATABASE_URL` 可覆盖；Redis 默认逻辑库 **15**，`TEST_REDIS_URL` 可覆盖。**解析结果等于应用库名 → 直接中止**（含用 `TEST_DATABASE_URL` 显式指过去的情况）。
  4. **逃生舱是显式的，不是隐式的**：已存在但**无标记**的库 → 报错并给出补救命令；`SHERPA_TEST_DB_ADOPT=1` 一次性收编，`SHERPA_TEST_DB_RESET=1` 重建。跑完**保留**测试库（首跑约 20–60 s，之后为空跑）。
  5. **安全 fail-closed，可用性 best-effort**：目标不安全或供给失败 → 整次运行中止；Postgres **连不上**只打一行提示，既有的 `ping_db()` 自跳过继续生效（这是 CI 无服务/本地栈没起时仍然绿的机制，不能破坏）。
  6. **陈旧数据预检**：`ensure_owner` 用 `ON CONFLICT DO NOTHING` 且 `tenants.slug` 唯一，因此测试库里若残留一个**别的身份**持有的 `personal` 租户，owner 播种会**静默变成 no-op**，随后每个 API 用例死在无关的外键报错上。供给阶段显式点名这种情况并给出重建命令，而不是让人去读一屏 FK 栈。
  7. **不做的事**：不新增 alembic 迁移、不改任何 `app/` 生产代码、不把 `TEST_*` 升为一等 `Settings` 字段（保持冻结的配置契约与生产配置面不变）、不改 CI（CI 的 role 有 CREATEDB，自动建 `sherpa_test`；保留原有 `alembic upgrade head` 步骤作迁移冒烟）。

- **不选的替代方案**：
  - *只把 owner 换成每模块合成租户*：能防误删，但**防不住 Redis 串扰与 worker 抢跑测试入队的 job**，且要改 20 个文件。→ 降级为 L0 的一行全局覆盖，作纵深防御而非主方案。
  - *per-run schema / template database*：更快，但与 `search_path`、`CREATE EXTENSION`（`vector`/`pg_trgm`/`zhparser`）、alembic 假设耦合太深，收益不抵复杂度。
  - *仅在文档里写「跑测试前先停 worker」*：这是 B-9 记录的**临时规避**，不是修复——它把正确性寄托在人的记性上。

- **不变式**：标记表**不进** `Base.metadata`，因此**绝不能**对测试库跑 `alembic revision --autogenerate`（否则 alembic 会提议把它删掉）。`tenant_id` 复合键与级联（ADR-015）保持原样——问题从来不是级联，而是测试不该待在那个库里。密钥边界（ADR-019）不变：测试库不持有任何真实凭据，KEK 仍只从 env 来。

- **验收关键**：开发 worker **保持运行**时 `uv run pytest` 全绿（这是与旧行为的分水岭：以前必须停 worker）；连跑两次结果一致；把 `TEST_DATABASE_URL` 指向应用库时**在导入期就中止且不发出任何 DELETE**；跑完开发库各表计数与基线**逐项相同**；`ruff check` / `ruff format --check` / `mypy app` 全绿；README/AGENTS/STATUS 里「测试会毁开发数据」的告警与规避说明一并撤除（陈述与现实不允许分叉）。

- **来源**：backlog B-9（2026-07-28 发现于 B-3 验证过程）；现状 `backend/tests/conftest.py`、`backend/app/{config,db,redis_client}.py`、`app/auth/owner.py`、`app/worker.py::project_workcopy_maintenance`、`app/services/project_workcopy.py::expire_idle`。

---

### ADR-045 · 伞 ADR：Agent 工具面 v2 + 执行工作区统一架构（clean break，无兼容层、无数据迁移；统领 ADR-046/047/048）

> **状态：架构决策已由负责人批准（2026-07-30）；实现代码仍须等待详细执行计划获批。本批次只做 ADR + 冻结契约增量 + 实现计划，不写生产代码/迁移/前端/基础设施（AGENTS.md §1/§2）。** 落地任务见 [`IMPLEMENTATION.md` Phase TR](IMPLEMENTATION.md)；下属决策见 [ADR-046](#adr-046)（工具目录）、[ADR-047](#adr-047)（tar 传输 + 安全模型）、[ADR-048](#adr-048)（RuntimeSession 与 Project 编码模型）。

- **背景（两个 backlog 是同一个问题）**：
  - [`backlog.md` B-2](backlog.md#b-2-built-in-tool-surface-is-too-large-53-tools)：工具面**实测 52 个**（backlog 原记 53，偏差 1），schema JSON **19,848 字节 ≈ 4,962 token**，在 `backend/app/core/loop.py:527` 于 while 循环内每轮重建、**每次模型调用全量重发**。
  - [`backlog.md` B-8](backlog.md#b-8-project_run-always-fails-with-sandbox_unavailable)：`project_run` **必然失败**。
  - 二者是同一缺陷的两面：工具面是**扁平、静态、全局注入**的，而"在项目里改/跑/测代码"天然需要**上下文相关、按运行时路由、还会继续长到 70+ 个**的工具集。只修 B-8（补 file/shell 工具集）会把 52 变成 70+，把设计债变成上下文事故；只修 B-2（砍工具）会挡住 B-8 必需的工具面扩张。**必须一次设计。**

- **根因（代码确证）**：
  1. **VISIBLE 闸事实上没实现**。`backend/app/tools/registry.py` 只有 SAFE/FULL 二元，SAFE 只含 `echo`/`get_time`（`app/tools/builtin.py`），且 `loop.py:412/437/449` 的 `tier` 全流程恒为 `FULL`。一个闲聊会话被迫携带 `project_run`(1142B)、`create_scheduled_task`(889B)、8 个 `drive_*`、5 个 `knowledge_*`。契约 [api.md §7.1](contracts/api.md) 说的"turn 构造时按 profile/source 决定可见集"在代码里是空的。
  2. **无命名空间/版本/工具集/发现机制**。名字是扁平字符串，命名法三代混杂（`todo_write` / `update_todo` / `complete_todo`；`memory_user_set` / `memory_note`）。
  3. **真实功能重复**。`file_*`(4) 走遗留 `files` 表（`app/services/files.py` + `app/api/files.py`，前端**已无 Files 页面**），`drive_*`(8) 走 ADR-030 Drive —— 对模型是两套语义相同的文件系统。
  4. **B-8 = bind mount 在 Docker-out-of-Docker 下结构性失败**。`sandbox_scratch_root=".sherpa/scratch"`（`app/config.py:154`，**相对路径**）在 worker 容器内解析为 `/app/.sherpa/scratch/<run>`，却被当作 bind mount 的 `source` 交给**宿主** Docker 守护进程（worker 挂宿主 `docker.sock`，`infra/docker-compose.yml:178`），宿主上不存在；compose 也**无任何共享 scratch 卷**（`docker-compose.yml:239-243`）。→ `DockerException` → `sandbox_start_failed`（`app/sandbox/project_sandbox.py:269-270`）→ 被 `app/services/project_sandbox.py:141-144` **无差别塌缩成 `sandbox_unavailable` 且不打日志**。

- **本次新发现的三个次生问题（backlog 未记录）**：
  1. **人工泳道根本不存在**：`frontend/src/api.ts:1293` 定义了 `createSandboxRun`，**全前端零调用点**。能力矩阵（[docs/11](11-agent-tool-surface.md) §9）W3 行的 UI ✅ 是错的。
  2. **即使挂载修好，REST 泳道仍会失败，且失败原因不同**：`app/api/projects.py::create_sandbox_run` **在 web 进程内同步执行** `sbx_svc.run_sandbox(...)`，而 `SANDBOX_KIND=docker` **只配在 worker**（`docker-compose.yml:163`），web 默认 `"disabled"`（`config.py:141`）且无 docker.sock；同时它**同步阻塞 HTTP 最长 120s**，而契约本就写 202。
  3. **测试结构性看不见它**：`backend/tests/test_project_sandbox.py` 把 `psbx._execute_in_scratch` monkeypatch 掉，`test_sandbox.py` 把 `_execute` patch 掉，**全仓无一个真 Docker 集成用例**。这就是它带着 297 个绿测上线的机制性原因。
  4. **镜像现实**：`sandbox_image = "python:3.11-slim"`（`config.py:142`），**无 pytest / ruff / node / git**；`sandbox-runner/` 目录**只有 README.md，没有 Dockerfile**。即使挂载修好，`project_run` 也跑不了任何有意义的测试。
  5. **`warm` 从未实现**：`sandbox_warm_ttl_seconds`（config）、契约 `SandboxRunState.warm`、DB 列 `project_sandbox_runs.warm_until` 都存在，`app/sandbox/` 与 `app/services/` 代码里 **零实现**。

- **决策（负责人批准 2026-07-30）**：
  1. **B-2 与 B-8 合并为一个架构/产品程序**，一次拍板共同前提：**工具面是上下文相关的目录，执行工作区是运行时挂载的能力**。
  2. **Clean break：明确不为旧架构做兼容优化，明确不做数据迁移。** 取消别名表、弃用周期、历史工具名保留、`/files` 保留期、`project_run` shim、双写。**理由**：现有 Sherpa 数据全部是可抛弃的测试数据（负责人确认）；为一个单用户自托管、尚未 onboard 任何外部用户的系统建兼容层，是纯粹的复杂度税。
  3. **32 条 alembic revision squash 成单一 `0001_baseline`**，开发库与 docker 卷**销毁重建**。这不是"数据迁移"，是它的反面：`backend/migrations/versions/` 里 0001→0032 的累积史本身就是对旧架构的兼容包袱。
  4. **窄腰不动**：[api.md §7](contracts/api.md) 的 `Tool` Protocol、四道闸（REGISTERED→VISIBLE→ALLOWED→EXECUTABLE）、`ToolResult` 双面、"错误即观察"、`begin_invocation`/审批/审计路径**原样保留**。新增的一切都在**注册表之上**，不在窄腰之内 —— 这正好兑现 api.md §7.4「built-in / MCP / sub-agent 走同一条路」。
  5. **三条下属决策**：[ADR-046] 工具目录 / [ADR-047] tar 传输 + 安全模型 / [ADR-048] RuntimeSession 与 Project 编码模型。
  6. **明确留 roadmap（不在本程序内）**：生产 runner（gVisor/`runsc` 或 microVM + 每租户隔离，即 [ADR-039] 的禁止上线条件）、沙箱内专用 coding agent、MCP/plugin provider、dev-server 托管预览、W4 GitHub sync/push/PR。

- **clean break 之下仍必须做的三件事，及其理由（这些不是兼容工作）**：
  | 项 | 为什么仍要做 |
  |---|---|
  | **事件日志里的历史工具名不动** | `event_journal` 是 append-only 真相源（[ADR-016]），**物理上不可改写**。而 `backend/app/core/history.py:133-165` 重建历史时**直接读事件 payload 里的 `name` 字符串、从不查注册表** —— 故删除工具**不会**破坏跨 run 历史重建。**结论：什么都不用做。**（这条推翻了"必须保留 `project_run` 以维持历史忠实性"的早期论据。） |
  | **Alembic 链线性且空库 `upgrade head` 可跑** | **仓库一致性**，不是数据兼容。不能从中间抽掉 revision；squash 必须产出一条完整可跑的 baseline。 |
  | **凭据边界 / 容器硬化 / 权限四道闸不变** | **安全**（[ADR-019]/[ADR-025]/[ADR-039]/[ADR-009]）。clean break 不等于降低安全成本 —— 这是 [ADR-022] "为真正上线的每个能力付全额安全成本"的直接推论。 |

- **被否的替代方案**：
  - **A · 最小修复**（把 bind 路径对齐 + 拆错误码 + 补一个 Run 按钮，B-2 不动）：成本最低（~2 天），但**不关闭 B-2**；保留 `src=` 路径注入面与"宿主绝对路径进配置"（Windows/Linux/CI/DinD 各一套配置，正是 B-8 逃过 297 个测试的原因）；保留"沙箱挂 = 改不了代码"的产品脆弱性；无 `RuntimeSession` 对象，未来沙箱内 coding agent 必须重写编排。**同一段编排代码要动两次。**
  - **C · 直接上持久远程 runner + 沙箱内 coding agent**：终局最优，但**不解决 B-2**（工具面问题原样存在，且内嵌 agent 反而**新增**工具面复杂度）；一次性引入新服务 + 新协议 + KVM/containerd 依赖 + 新信任边界，违背 [ADR-022] 单用户自托管定位。**它是终局而非起点** —— [ADR-048] 的 `RuntimeSession` 抽象正是通往它的路径，故排进 roadmap 而非现在建。

- **取代/延伸关系**：**取代** [ADR-023] 的工具面**落地口径**（能力层 + 双适配器的原则不变，但"所有工具平铺给所有会话"被目录 + 作用域可见集取代）；**修订** [ADR-040] §决策8（"内置 file/edit/run/test 工具 … 不嵌 coding agent" → "v1 不嵌，但 `RuntimeSession` 接缝预留"）；**收窄性修订** [ADR-025]/[ADR-039] 的挂载口径（见 [ADR-047]）；**具体化** [ADR-009] 的 VISIBLE 闸；**扩展** [ADR-008] 权限代数为 args 感知。**不改** [ADR-016]/[ADR-017]（journal + outbox 真相源与 effect 语义）、[ADR-015]（租户键）、[ADR-019]（密钥边界）、[ADR-020]（审批信封）、[ADR-030]（Drive 字节存储）、[ADR-037]/[ADR-038]（Projects W2a/W2b）。

- **验收关键（本设计批次）**：ADR-045/046/047/048 被接受；契约增量落地且**明标 target vs current 实现状态**（绝不把未实现写成已实现）；`IMPLEMENTATION.md` 有可被**无对话记忆的新 Copilot 进程**独立执行的 Phase TR（P0–P5）；`STATUS.md`/`backlog.md` 只记录"架构已批准 + 下一实现阶段"，**B-2/B-8 不标 done**；**无生产代码/迁移/前端/基础设施改动**。

- **来源**：backlog B-2 + B-8；本批次只读代码勘察（`app/tools/*`、`app/core/loop.py`、`app/permissions/*`、`app/sandbox/*`、`app/services/project_*`、`app/api/projects.py`、`frontend/src/{api.ts,views/ProjectsView.tsx,components/ChangeReview.tsx}`、`infra/docker-compose.yml`、`backend/tests/test_project_sandbox.py`）；实测 `build_default_registry().schemas("full")` = 52 工具 / 19,848 B；负责人输入「不要为旧架构做兼容优化、不要规划数据迁移；现有数据均为可抛弃测试数据，接受 baseline squash 与销毁重建；架构批准，实现代码仍需先批执行计划」。

---

### ADR-046 · 工具目录 = `domain.verb` 命名空间 + ToolDescriptor/ToolsetResolver + 渐进式披露（落地 backlog B-2；受 ADR-045 统领；具体化 ADR-009 的 VISIBLE 闸；扩展 ADR-008）

> **状态：已批准（2026-07-30），契约与设计先行；实现待 Phase TR 执行计划获批。**
>
> **修订 A（2026-07-30，负责人主持的 P2 设计复审）**：三处更正，详见 [backlog B-10](backlog.md#b-10-tool-surface-slimming-dead-tools-prose-diet-and-vertical-workflow-consolidation)。
> ① **§决策5 的否决理由②③被本 ADR 的 §决策6 自我推翻**，已收窄（见该条的「修订」块）；
> ② **实测基线数字更正**：19,848 B 是 **P1 前 52 工具**的值，P1 后实测 **47 工具 / 17,432 B（紧凑分隔符）/ 18,303 B（默认分隔符）**，两种口径不得混用；
> ③ 新增 **§决策10「合并只沿纵向（工作流）轴，不沿横向（CRUD 动词）轴」** —— 这是本 ADR 原先完全缺失的判据，也是 §决策5 的正面表述。
> 另记：命名前缀 vs 后缀（`domain.verb` vs `verb_domain`）是**实测题**，见 §决策1 补注与 [backlog B-11](backlog.md#b-11-no-tool-use-evaluation-harness-decisions-are-argued-not-measured)。

- **决策**：
  1. **统一命名 `domain.verb`**：全部工具一律 `^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$`（`drive.read` / `project.list` / `fs.write` / `sh.exec` / `tools.search`）。**这需要修订 [api.md §7](contracts/api.md) 的名称正则**（现为 `^[a-z][a-z0-9_]{0,63}$`，不含点）。点号在 OpenAI / Anthropic / Gemini 三家 wire 协议上都合法（`backend/app/providers/tools.py` 的三个适配器只做透传/裁剪）。clean break 之下**不存在新旧混排**，故 `ToolDescriptor` **不设 `stability`/`deprecated` 字段**。
     - **实测动机（修订 A 补）**：47 个工具里 **28 个 `action_domain` · 15 个 `domain_action` · 4 个都不是**，且**同一个域内部就混用**（`todo_write` ↔ `list_todos`；`project_read` ↔ `list_projects`；`search_knowledge` ↔ `add_knowledge_source`），连局部模式匹配都不成立。drive 是唯一自洽的一组。
     - **但方向本身待实测**：Anthropic《[Writing effective tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents)》示例用域前缀（`asana_search`/`jira_search`），却明说前缀式与后缀式命名 *"have non-trivial effects on our tool-use evaluations. Effects vary by LLM and we encourage you to choose a naming scheme according to your own evaluations."* 故 `domain.verb` 先按本 ADR 落地，**其相对 `verb_domain` 的优劣列为 [B-11](backlog.md#b-11-no-tool-use-evaluation-harness-decisions-are-argued-not-measured) E3 的一个变体去测**，不作为已证结论。
  2. **`ToolDescriptor` 旁挂，不改窄腰**：`Tool` Protocol（`backend/app/tools/base.py:68-74`）一字不改；目录元数据在**注册时**以独立 dataclass 提供：
     ```python
     @dataclass(frozen=True)
     class ToolDescriptor:
         tool: Tool
         namespace: str            # core|inbox|todo|schedule|memory|knowledge|drive|project|fs|sh|run|tools
         toolset: str              # 目录条目 id，如 "knowledge" / "fs.edit"
         version: int              # 破坏性 schema 变更 ⇒ 升版（clean break 下无并存期）
         requires: frozenset[str]  # "project_binding" | "runtime_session" | "gmail_connected"
         surfaces: frozenset[str]  # "chat" | "scheduled_task"（"connector_analysis" 恒为空集）
         summary: str              # ≤80 字符，只进目录摘要，不进完整 schema
     ```
  3. **`ToolsetResolver` = 真正的 VISIBLE 闸**（[api.md §7.1](contracts/api.md) 第 2 闸的落地）。输入 profile `(trust_tier, surface, session_kind, runtime, loaded_toolsets)`，输出 `core` / `catalog` / `loaded`。**两条硬约束**：
     - **确定性排序 + core 恒为真前缀**：按 `(namespace, name)` 排序，`core` 永远在数组最前 —— 于是 `resolve(general).core` 在字节层面是 `resolve(project_bound)` 工具数组的**真前缀**，加载新工具集只让**尾部**失配。这是 [docs/04](04-core-loop.md) 不变量⑤（前缀字节稳定 + 动态数据在尾部）在工具数组上的落地。
     - **turn 边界冻结**：可见集在 turn 开始时定死、turn 内不变（[ADR-009] 原文）；`tools.load` 的效果**在下一个 turn 生效**，并写一条 `toolset.resolved` 事件。
  4. **渐进式披露**：`core` 常驻约 15 个 + **一行式目录摘要**渲染进 system 消息的能力层（位于全局前缀之后、per-user 记忆与 per-session ambient 之前，保持 `loop.py:483-491` 的分层顺序）+ 两个元工具（合计 <500 B）：
     - `tools.search(query)` → 返回匹配的 toolset id + summary + 工具数（`read_only`，allow）
     - `tools.load(toolsets[≤3])` → 本会话后续 turn 获得其完整 schema（`read_only` 语义，allow，但**记审计**）
  5. **不做动词巨型工具**（`drive(op=…)`）。理由：会把 8 个精确 JSON Schema 塌成带 `oneOf` 的巨型 schema，**削弱 `app/tools/validate.py` 的参数校验**、**破坏权限粒度**（`drive.trash` 与 `drive.read` 的 `effect_class` 不同）、**污染审批 scope**（`permission_scope="tool:drive"` 太粗）。分组 + 按需加载已拿到 token 收益的绝大部分，风险却低一个数量级。
     - **修订 A（2026-07-30）——否决结论不变，但理由必须收窄**：
       - **理由②③作废**（自我矛盾）。它们假定策略引擎看不见参数；而**本 ADR 的 §决策6** 恰恰把引擎升级为 args 感知 `evaluate(ctx, descriptor, args, scope)`。一旦看得见 args，`drive(action="trash")` 的 effect class 与审批 scope **可以和 `drive.trash` 一样精确**。用一条被自己下一条决策消除的理由去否决，站不住。
       - **理由①成立，且比原文更重**。原文说 schema「塌成 `oneOf`」；实际是**连 `oneOf` 都写不了**——`app/tools/validate.py` 自述 *"Not a full JSON-Schema engine … checks required keys are present and primitive types match"*，不认 `enum`/`oneOf`/条件 `required`。合并后校验不是被削弱而是**消失**。实锤：`update_todo` 现声明 `"required": ["todo_id","if_version"]`（`app/tools/todo_tools.py:112`），create+update 一旦合并，`if_version` 无法再 required，**乐观并发的 schema 级护栏直接丢失**。
       - **补一条新理由④**：模型对判别式联合（discriminated union）经验上偏弱；服务端换成全量 JSON-Schema 校验器也补不了模型选错的账。
       - **因此**：否决**仅**立于①+④，并且是**可实测的**（[B-11](backlog.md#b-11-no-tool-use-evaluation-harness-decisions-are-argued-not-measured) E3）。若将来评测数据反转结论，需先换掉 `validate.py` 再重开此议。
  6. **策略引擎升级为 args 感知**：`app/permissions/policy.py` 的 `evaluate(tool)` → `evaluate(ctx, descriptor, args, scope) -> "allow"|"ask"|"deny"`，last-match 胜、`deny > ask > allow`（[ADR-008] 代数）。审批 scope 从裸 `"tool:{name}"` 升为 `"tool:{name}"` + 结构化 `scope`（如 `{"command_class":"shell","paths":["src/"]}`），使 [ADR-020] 信封能渲染**确切命令 + 目标路径**。`app/permissions/grants.py` 的 `_MATCHERS` 从只支持 `send_email` 扩到 `sh.exec`（命令白名单）与 `fs.write`（路径前缀）。
  7. **删除**：`app/tools/file_tools.py`（4 个 `file_*`）、`app/services/files.py`、`app/api/files.py` 及 `app/main.py` 的 `include_router(files_router)`、`files` 表、`app/tools/sandbox_tools.py`（`run_code`）。**保留**并建议改名 `app/files/` → `app/objectstore/`（它是对象存储适配层，Drive/Projects 都依赖，名字与被删的 files 栈重名纯属误导）。
  8. **命名统一（clean break，无别名）**：`memory_user_get`/`memory_user_list` → `memory.recall`（key 可选）；`todo_write`/`update_todo`/`complete_todo` → `todo.create`/`todo.update`（含 status）；其余按 `domain.verb` 逐一改名。旧名**直接消失**；模型若照抄历史 transcript 调用旧名，得到 `unknown tool` 观察 —— **错误即观察，不崩循环**（api.md §7），可接受。
  9. **provider 概念前向预留**：目录层统一 `ToolProvider`（`BuiltinProvider` 今天唯一实现；`RuntimeProvider` 由 [ADR-048] 引入；`McpProvider`/`SubAgentProvider` 留 roadmap）。外部 provider 的工具默认 `surfaces={"chat"}` + 全部 `ask` + **永不进 core 集**（必须显式 `tools.load`）—— 这是 roadmap #9「两遍信任 + footprint ladder」在目录层的落地。
  10. **合并只沿纵向（工作流）轴，绝不沿横向（CRUD 动词）轴**（修订 A 新增；§决策5 的正面表述）。判据来自 Anthropic《[Writing effective tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents)》：

      > By selectively implementing tools whose names reflect **natural subdivisions of tasks**, you simultaneously reduce the number of tools **and offload agentic computation from the agent's context back into the tool calls themselves**.

      | 轴 | 形态 | 减少了什么 |
      |---|---|---|
      | **横向**（按 CRUD 动词分组） | `sherpa.list(kind)`、`todo(action=…)` | 只减工具数/字节；**offload 的 agentic computation 为零**，还多一个「kind/action 填什么」的决策。它是**分发器**不是工具 |
      | **纵向**（按工作流分组）✅ | `schedule_event`、`search_logs`、`get_customer_context` | 工具数 **+ round trip + 中间输出占用的上下文** |

      推论：**跨域的 `list(kind=…)` 明确禁止**；原文更进一步质疑 `list_*` 工具本身该不该建（*"implement a `search_contacts` … **instead of** a `list_contacts` tool"*）。Sherpa 的纵向候选（`todo.create(…, remind_at?)` / `inbox.accept(…, patch?, remind_at?)` / `today()` / `knowledge.add(query_or_path)`）记在 [B-10](backlog.md#b-10-tool-surface-slimming-dead-tools-prose-diet-and-vertical-workflow-consolidation)，**每条都需 [B-11](backlog.md#b-11-no-tool-use-evaluation-harness-decisions-are-argued-not-measured) 的评测证据才可实施**。

      **一线产品旁证**：GitHub MCP Server（`list_available_toolsets`/`get_toolset_tools`/`enable_toolset`）与 GitHub Copilot CLI（31 个 MCP 工具延迟加载于一个 tool-search 工具之后，且 `browser_click`/`browser_navigate`/`browser_type` 等**仍是 24 个独立工具**）都选择「**渐进式披露 + 保持工具独立**」；未发现横向合并的一方产品先例。

      **CLI 模式可借鉴的边界**：CLI agent 便宜有三源——① 一个 schema 无限动词（= 横向合并，见上，代价高）；② **预训练先验**（模型天生会 `ls`/`git`/`pytest`，教学成本为 0）；③ 带内发现（`--help`）。其中**②是最大来源且完全不转移**——`sherpa todo list` 与 `todo.list` 对模型一样陌生；③正是 `tools.search`/`tools.load`。另：沙箱内跑 `sherpa` CLI 操作自有数据被 [ADR-019]/[ADR-047] 硬阻断（凭据不进沙箱 + 断网，够不到 DB）。反向地，CLI agent 手握 shell 仍刻意保留独立的 `Read`/`Write`/`Edit`/`Glob`/`Grep`（结构化 diff、避开 shell 引号、权限分级、路径边界可强制），四条**全部适用**于 [ADR-048] 的 `fs.*` —— 即 CLI 实践**支持**而非否定 `fs.*` 独立；但 [ADR-048] 计划中的 `run.test`/`run.lint` 是 `sh.exec` 的纯语法糖（Claude Code 无 `run_test`），建议 P4 删除。

- **token 预算**（原写「实测基线 19,848 B / ~4,962 token」——**修订 A 更正**：那是 **P1 前 52 工具**的值。P1 后实测 **47 工具 / 17,432 B 紧凑 / 18,303 B 默认分隔符**；下表的「相对今天」按旧基线估算，重算前只作数量级参考。另实测：普通 chat 的 core（10 工具）**今天就只有 3,627 B**，即 `TOOL_CATALOG_CORE_MAX_BYTES=6144` 是**带 2,517 B 余量的棘轮**而非冲刺目标——真正的收益来自**另外 37 个工具不发**，不是把 core 压小。描述散文占全量 **38%**（6,625 B），故 [B-10](backlog.md#b-10-tool-surface-slimming-dead-tools-prose-diet-and-vertical-workflow-consolidation) 要求 P2 先做**瘦身**（删死工具 + 每工具描述字节上限，与名称正则同处启动强制），再建目录）：

  | 场景 | core | 目录摘要 | 合计 | 相对今天 |
  |---|---|---|---|---|
  | 普通 chat | ~15 工具 ≈ 5.5 KB | ~0.5 KB | **~6 KB / ~1.5k tok** | **−70%** |
  | Project-bound（无 runtime） | core + `project` ≈ 9 KB | ~0.5 KB | ~9.5 KB | −52% |
  | Project-bound + runtime | core + project + `fs` + `sh` ≈ 13 KB | ~0.5 KB | ~13.5 KB | −32%（**工具总数 52→66**） |
  | `CONNECTOR_ANALYSIS` | **0**（[ADR-009] 无工具） | 0 | 0 | 不变 |

  即：**工具总数增长 27%，普通会话上下文反而降 70%。**

- **不变式**：`CONNECTOR_ANALYSIS` 永远零工具（`surfaces` 不含它，[ADR-009] 不弱化）；解决审批、破坏性 purge、model provider 配置、GitHub 凭据、Save/checkpoint/Discard 仍**不给 agent**（[docs/11](11-agent-tool-surface.md) §12 边界不变）；工具错误仍是观察不是异常。

- **验收关键**：普通 chat 的工具 JSON ≤ **6 KiB**（回归断言 `TOOL_CATALOG_CORE_MAX_BYTES`）；`resolve(general).core` 是 `resolve(project_bound)` 的**字节级真前缀**（断言）；同一 profile 连调两次**字节相同**；mock provider 脚本证明 `tools.search → tools.load → 下一 turn 调用成功`；`CONNECTOR_ANALYSIS` 仍零工具；名称正则 + 唯一性 + 版本单调断言通过；`toolset.resolved` 事件记录 core 摘要 / 加载集 / `tools_offered` / `catalog_bytes`。

- **来源**：backlog B-2；实测 `app/tools/registry.py` + `app/core/loop.py:527`；[ADR-045] §根因；[ADR-008]/[ADR-009]/[ADR-020]/[ADR-023]/[ADR-034]；负责人拍板 O-1（点号命名）、O-2（不做动词巨型工具）、O-7（模型可自主 `tools.load`）。

---

### ADR-047 · 执行工作区传输 = tar ingress/egress（删除 bind mount）+ 自建 runner 镜像 —— **收窄性修订 ADR-025/ADR-039 的挂载口径**（落地 backlog B-8；受 ADR-045 统领）

> **状态：已批准（2026-07-30），契约与设计先行；实现待 Phase TR 执行计划获批。本 ADR 收窄（而非放松）既有隔离口径。**

- **传输方案比较**：

  | 方案 | 机制 | Windows host + DooD（当前 dev 栈） | 安全面 | 生产演进 |
  |---|---|---|---|---|
  | **A. bind mount**（现状） | `Mount(type="bind", source=<path>)` | ❌ **结构性坏**：source 在宿主解析，worker 容器内路径不存在（B-8） | 需把 `src=` 当不可信输入校验（[ADR-039] §决策1③）；宿主路径进配置 | 生产必须重做 |
  | **B. named volume** | worker 建/复用具名卷，sandbox 挂同名卷 | ✅ 名字由守护进程解析 | 无路径注入面；但卷是持久对象，需生命周期管理 | 好；warm 容器天然契合 |
  | **C. `put_archive`/`get_archive`（tar 流）** | worker 用 docker API 把 tar 直接写进容器 `/work`，跑完 `get_archive` 取回 | ✅ **完全不涉及任何文件系统路径语义** | **最小**：无 bind、无卷、无 `src=` 参数 | 好；是 gVisor/microVM/远程 runner 的**天然通用接口** |
  | D. git clone/worktree | sandbox 内 clone 内部 git 仓 | 需 sandbox 内有 git + 一个 git 服务端 | 引入 git 传输攻击面 | 与 [ADR-038]「不引入独立 Git 存储」直接冲突 |
  | E. 持久远程 runner | 独立 runner 服务 + session API | ✅ | 最好（[ADR-039] 禁止上线条件的正解） | **生产终局** |

- **决策**：
  1. **v1 = C（tar ingress/egress）**，`app/sandbox/` 中所有 `Mount(type="bind", ...)` **删除**；容器 `/work` 用**匿名卷**（`nosuid,nodev`）。四条理由：
     - **消灭整类问题**：A 的修法要求宿主绝对路径以**同一路径**挂进 worker，在 Windows + Docker Desktop 上尤其脆弱，且 Linux / CI / DinD 各有一套不同的正确配置 —— 这正是 B-8 逃过 297 个测试的原因。**C 没有配置可配错。**
     - **安全净收益**：[ADR-039] §决策1③ 要求"把构造的 scratch `src=` 路径当不可信输入校验"—— C 让这条要求**不再适用**（根本没有 `src=`）。[ADR-025] 修订的"永不 RW 挂真相源"依然成立且**更强**：**根本不挂任何宿主路径**。
     - **通往生产 runner 的正确接口**：远程 runner 天然就是"上传工作区 → 执行 → 取回 delta"。今天写成 tar，明天换 runner 只换传输实现，[ADR-048] 的 `RuntimeSession` 抽象不变。
     - **可测**：tar 往返可用 fake docker client 完整单测；bind mount 的正确性**只能**靠真 Docker 验证。
  2. **对 [ADR-025]/[ADR-039] 的收窄性修订**：把"允许且仅允许 RW **挂载**一份一次性 scratch 副本"收窄为 —— **允许且仅允许把一次性 scratch 副本以 tar 流 *注入* 容器内的匿名卷；永不挂载任何宿主路径、永不挂载真相源。** [ADR-039] 的其余全部控制**逐条保留**：拷贝前剔除/断言无凭据、`nosuid,nodev`、编排 `finally` 原子清理 + 启动孤儿扫除、沙箱绝不接触 `docker.sock`、socket 只在可信编排进程（TCB）。[ADR-025] 的全部硬化**一字不改**：`network_disabled`、`cap_drop=ALL`、`no-new-privileges`、非 root（`nobody`）、只读 rootfs + tmpfs、mem/pids/cpu/墙钟上限、`--rm`、**无任何密钥注入**。
  3. **[ADR-039] 的禁止上线条件完全不变**：多用户 / 真不可信第三方代码之前，仍必须先做 gVisor(`runsc`) 或 microVM + 不共享 `docker.sock` + 每租户 scratch/出口/配额隔离 + 威胁评审。**tar 传输不改变这个门；它只是把单用户 dev 栈从"结构性坏"修成"结构性对"。未实施的缓解绝不写成已安全。**
  4. **同批必须交付真自建 `sandbox-runner` 镜像**：今天用 stock `python:3.11-slim`（无 pytest/ruff/node/git），`sandbox-runner/` 只有 README。v1 镜像 = 非 root、只读 rootfs 友好、**python + pytest + ruff**、版本 pin、**不含 git、不含网络工具**，并自带 `/opt/sherpa/capabilities.json` 供启动时能力探测 —— 让 `environment_missing_dependencies` 能给出"本镜像有什么"的可操作观察，而不是靠 exit 127 猜。**node 作为可选 profile 留后（O-6）。**
  5. **传输协议**：
     - **ingress**：`base snapshot + persisted overlay` → **内存 tar** → `put_archive("/work")`。物化前**剔除/断言无凭据**（`.env*`、`*.pem`、`*.key`、`.git/config`、`id_*`），受 `SANDBOX_SCRATCH_MAX_BYTES` 约束。
     - **egress**：`get_archive("/work")` → 与 ingress manifest 比对出 delta（added/modified/deleted）→ **fence 守护地**持久进 overlay + 投影 change set。超界 ⇒ 显式 `truncated`，**绝不给看似完整的假 diff**。
     - **tar 本身是不可信输入**：解包时拒绝绝对路径、`..` 穿越、NUL、设备/FIFO、硬链接、逃逸 symlink（复用 `app/services/archive.py` 的既有安全解包语义）。
  6. **演进路径**：v1 = tar；**v1.5 = 具名卷**（触发条件：项目 > ~50 MB，或需要同一 runtime session 跨多次 exec 复用工作区而 tar 全量进出成为瓶颈）；**v2 = 持久远程 runner**（roadmap，[ADR-039] 禁止上线条件的正解）。
  7. **删除 `warm` 概念**：`sandbox_warm_ttl_seconds` / `SandboxRunState.warm` / `project_sandbox_runs.warm_until` **从未实现**，且语义应由 [ADR-048] 的 `RuntimeSession` TTL 承载。三处一并删除，不做兼容。

- **权衡（明说）**：tar 对大项目有拷贝成本。缓解 = **同一 runtime session 内多次 `sh.exec` 只做一次 ingress**（这正是 [ADR-048] 让 `RuntimeSession` 成为显式一等对象的性能理由之一）；超出后按 §决策6 升级到具名卷。

- **验收关键**：`app/sandbox/` 中不再出现 `type="bind"`；`sandbox-runner/Dockerfile` 存在且构建产物含 pytest/ruff + `capabilities.json`；`sh.exec("pytest -q")` 在 **Windows + Docker Desktop 真栈**上返回真实 exit code 与 stdout；Docker 拓扑矩阵（Win+DooD / Linux+DooD / DinD / 无 Docker / rootless）全绿；**凭据 canary**：把假 KEK 放进项目树，断言它不出现在 tar / overlay / change set / artifact / 日志 / prompt / 工具结果；tar 解包拒绝 zip-slip / 硬链接 / 设备节点 / 逃逸 symlink；`config §1.7` 与本 ADR 与 [ADR-025] 修订三者口径一致。

- **取代/延伸关系**：**收窄性修订** [ADR-025]（2026-07-27 修订段）与 [ADR-039] §决策1 的挂载口径；**不改** [ADR-039] 的威胁模型、方案比较表与**禁止上线条件**；复用 [ADR-030] 内容寻址 blob、[ADR-016]/[ADR-017] 持久语义、[ADR-019] 密钥边界、[ADR-009] 不可信内容边界。

- **来源**：backlog B-8；实测失败链（`app/config.py:154` → `app/sandbox/project_sandbox.py:113-145,241-291` → `app/services/project_sandbox.py:141-144` → `infra/docker-compose.yml:163,178,239-243`）；[ADR-025]/[ADR-039]/[ADR-040]；负责人拍板 O-5（tar）、O-6（v1 镜像只 python+pytest+ruff）。

---

### ADR-048 · RuntimeSession + Project 编码模型 = 宿主侧 `fs.*` + 沙箱侧 `sh.*`/`run.*`（删除 `project_run`/`project_tree`/`project_read`/`run_code`；修订 ADR-040 §决策8）

> **状态：已批准（2026-07-30），契约与设计先行；实现待 Phase TR 执行计划获批。**

- **核心判断：file 与 shell 必须分层，不能一刀切**：

  | 候选 | 判断 |
  |---|---|
  | (a) 全部作为宿主一等工具 | ✅ 对 `fs.*` 正确；❌ 对 `sh.*` 不可能（执行必须隔离） |
  | (b) 全部路由进沙箱 | ❌ 让"读一个文件"都依赖容器可用 —— **这正是今天 B-8 的症状** |
  | (c) 全部委派子 agent | ❌ v1 过早；失去逐步审批与逐步可见性 |
  | **(d) 混合** | ✅ **采纳** |

- **决策**：
  1. **分层**：
     - `fs.list` / `fs.read` / `fs.grep` / `fs.write` / `fs.edit` / `fs.delete` → **宿主侧**，直接读写工作副本的 **effective tree**（`base snapshot + overlay`，即 `app/services/project_workcopy.py::effective_tree` 的语义）。**无需容器**、确定性、可完整单测。这**严格强于**被删的 `project_tree`/`project_read` —— 后者只看 head，**看不见 agent 自己刚写的内容**（既有缺陷）。
     - `sh.exec` → **必经 `RuntimeSession`** 进沙箱（tar 进 → exec → tar 出 → fence 持久）。
     - `run.test` / `run.lint` → `sh.exec` 的语义糖 + 能力探测（给模型稳定的高层动作，避免它自己拼命令）。
  2. **产品后果（本设计最重要的判断之一）**：**沙箱不可用时只损失「跑」，不损失「改」。** 今天沙箱一挂，"让 Sherpa 改代码"整体归零。
  3. **`RuntimeSession` 从 v1 起就是显式一等对象**：`runtime.open(scope)` → 物化 + 起容器 → 返回 `runtime_session_id` + `capabilities` + TTL；`sh.exec(runtime_session_id, command)`；`runtime.close(runtime_session_id)` → 持久边界 + 拆容器。`scope ∈ {project, ephemeral}`：`project` 挂工作副本；`ephemeral` 空工作区，**取代被删的 `run_code`**（O-12）—— 从此只有**一套**沙箱代码（`app/sandbox/runner.py` 与 `project_sandbox.py` 合并为 `app/sandbox/runtime.py`）。
  4. **未来沙箱内专用 coding agent 的接缝（零重写）**：以 **sub-agent 适配器**形态出现（[api.md §7.4](contracts/api.md) 早已契约保留）：`delegate.code_task(runtime_session_id, goal, max_steps, budget_tokens)`。它拿到**同一个** `runtime_session_id`、在同一容器里跑、每步仍产出 change set 进**同一个 overlay**、预算/取消/审计走**同一条路**（子 agent 共享父预算，[docs/09](09-roadmap.md) 生产就绪清单）。**唯一前置** = `RuntimeSession` 必须从一开始就是显式可传递对象，而不是 `project_run` 那种"每次调用内部临时起一个容器"的隐式生命周期。**这就是 v1 就要引入 `runtime.open/close` 的根本原因。** 本条**修订 [ADR-040] §决策8**（"不嵌 coding agent" → "v1 不嵌，接缝预留"）。
  5. **策略**（[ADR-046] §决策6 的 args 感知引擎之上）：
     - `fs.*` 读 = `read_only`/allow；`fs.write`/`fs.edit`/`fs.delete` = `idempotent_write`/**allow**（写的是**待评审 overlay**，不是 head；推进 head 永远人工，[ADR-040] §决策6 不变），但 `.env*` / `.github/workflows/**` / `*.pem` / `*.key` / `id_*` **强制 `ask`**（O-4）。
     - `sh.exec` = `non_idempotent_write`/**`ask`**，配**平台安全命令白名单 grants** 自动放行（`pytest`/`ruff`/`python -m`/`ls`/`cat` 等只读或已知安全命令），使常见开发循环不被审批打断，同时保住 `rm -rf`、写 CI 配置一类的闸（O-3）。审批预览**必须**显示确切命令 + 目标路径。
     - `runtime.open`/`runtime.close` = `idempotent_write`/allow。
  6. **删除与重设计**：
     - **删除工具**：`project_run`、`project_tree`、`project_read`（`app/tools/project_tools.py`）、`run_code`（`app/tools/sandbox_tools.py`）。**无 shim、无别名**（[ADR-045] §clean break 表已论证：`app/core/history.py` 不查注册表，删除零成本）。
     - **保留工具**：`project.list` / `project.create` / `project.review_changes`（改名到 `domain.verb`）。
     - **重设计表**：`project_sandbox_runs` → **`project_runtime_sessions`**（session 级：scope / image / capabilities / fence / TTL / state）+ **`project_exec_runs`**（每条命令：command / exit_code / termination_reason / 耗时 / 日志引用）。`scratch_ref` 在 tar 传输下无意义、`container_ref` 归入 session、`warm_until` 从未实现（[ADR-047] §决策7）。
     - **REST 重设计**：`POST /projects/{id}/sandbox-runs`（web 内同步、阻塞 120s）→ `POST /projects/{id}/runtime`（**202**）+ `POST /runtime/{rid}/exec`（**202** + SSE 流式 stdout/stderr）+ `POST /runtime/{rid}/cancel` + `DELETE /runtime/{rid}`，**全部由 worker 执行**（O-9）。
  7. **具名 termination（每个出口都必须具名）** —— 修正 B-8 的错误塌缩：
     `done | cancelled | wall_timeout | mem_limit | pids_limit | output_limit | environment_missing_dependencies | changeset_bounds | path_escape | fence_lost | runtime_start_failed | runtime_image_missing | runtime_daemon_unreachable | runtime_transport_failed | sandbox_disabled | error:<class>`。
     每条失败**写一行 worker 结构化日志 + 一条脱敏的工具观察** —— 模型和用户永远不必猜是哪种失败。
  8. **流式与取消**：`sh.exec` 期间向事件总线发 `runtime.output` 增量帧（`durability: debug`，有界、可丢，**不进 append-only journal 的正确性路径** —— 符合 [ADR-016]「pub/sub 永不是正确性关键」）。取消 = 现有 run 取消信号 → 编排方 `container.kill()` → `termination_reason="cancelled"`。
  9. **/Project UX（O-8）**：Project-bound Chat 使用**三栏工作台**（左文件树 / 中对话 / 右「Changes · Runs · Artifacts」）。人工泳道必须补齐 **Run 控件 + 流式日志面板 + Stop**（今天 `frontend/src/api.ts:1293` 的 `createSandboxRun` 是死代码）；文件树**可编辑**，人的手改与 agent 的手改**落进同一个 overlay**、一起评审。**Plan 对象后置到 v1.5**（目录层预留 `ui.*` 类工具，O-10）。
  10. **不变**：真相源层级（snapshot head → overlay → scratch/容器为可丢缓存）、single-writer lease + 单调 fence、`head_generation` CAS Save（`409 head_moved`）、Save/checkpoint/Discard **人工专属**、change set 有界 + 显式 `truncated`、artifacts 默认 `ephemeral` 不计配额、无包安装、无开网 —— [ADR-040] 这些决策**全部保留**。

- **验收关键**：agent 泳道完成一个真实循环（读代码 → 改 → 跑测试 → 看到失败 → 再改 → 通过）；**沙箱强制关闭时 `fs.*` 全部仍可用**（降级不瘫痪）；`sh.exec("rm -rf /work")` 触发审批且预览含确切命令；每个失败注入映射到唯一具名 `termination_reason` 并在 UI 与模型观察里可区分；人工泳道能点 Run、看到流式日志、能 Stop；能力矩阵（docs/11 §9）相关行 UI 单元格**经真实点击验证**后才置 ✅。

- **取代/延伸关系**：**修订** [ADR-040] §决策8（执行器口径）与其 REST/工具面（§能力面）；**受** [ADR-045] 统领、依赖 [ADR-047] 传输与 [ADR-046] 目录；**不改** [ADR-040] 的真相源层级 / lease+fence / head-gen CAS / 人工评审闸 / 无包安装无开网；**不改** [ADR-039] 隔离前提与禁止上线条件；**不改** [ADR-020] 审批信封（只是把 scope 变结构化）。

- **来源**：backlog B-8；实测 `app/tools/project_tools.py`、`app/services/project_workcopy.py`、`app/api/projects.py::create_sandbox_run`、`frontend/src/{api.ts,components/ChangeReview.tsx,views/ProjectsView.tsx}`；[ADR-040] §决策8/§能力面；负责人拍板 O-3（`sh.exec` ask + 平台安全命令 grants）、O-4（fs 写 allow，敏感路径 ask）、O-8（三栏 UI）、O-9（异步 202）、O-10（Plan 后置）、O-12（`run_code` 一并删）。

---
