# Sherpa 产品设计 PM Review

> 评审日期：2026-07-19  
> 评审阶段：编码前  
> 评审结论：**有条件通过（Proceed with product reset）**  
> 评审重点：产品价值、用户范围、MVP、信任、采用风险和衡量方式；不是架构正确性审计。

## 0. 执行摘要

Sherpa 的架构设计明显强于当前的产品设计。

文档已经很好地回答了：

- 系统怎样异步运行；
- 怎样隔离用户和代码；
- 怎样统一 Web、邮件和 QQ 入口；
- 怎样持久化消息；
- 怎样限制工具权限；
- 怎样避免重复调度；
- 怎样为未来扩展留边界。

但文档尚未充分回答：

- **第一批用户是谁；**
- **他们为什么会在一周后继续使用；**
- **第一次使用如何在十分钟内获得价值；**
- **自动生成的任务错了怎么办；**
- **用户为什么敢把邮箱、文件和通知权限交给 Sherpa；**
- **“团队协作”具体协作什么；**
- **自托管、模型、邮箱和 QQ 的复杂配置由谁承担；**
- **什么指标证明产品有效，而不只是系统能跑。**

当前方案有“能力清单驱动”的倾向：

它同时想成为云端 agent runtime、个人助理、团队协作工具、代码 agent、连接器平台、任务管理器、邮箱 agent 和 IM bot。

这些能力在架构上可以共存，
但在产品首发中不能拥有相同优先级。

### 0.1 最重要的判断

1. **双定位长期可成立，首发不成立。**  
   “个人助理”与“小团队协作”可共享底层模型，但购买者、激活路径、权限模型、价值单位和留存机制不同。
   v1 应先服务“愿意自托管、愿意连接 Gmail/GitHub 的技术型个人用户”，团队能力后置验证。

2. **真正的产品楔子不是聊天，也不是沙箱，而是可信的 inbox-to-action。**  
   最小可爱产品应围绕：
   `连接 Gmail → 发现可执行事项 → 用户确认/编辑 → 进入待办 → 在合适时间提醒`。
   它比“又一个能聊天和跑代码的 agent”更容易形成差异化。

3. **现有 P0–P6 是技术降险顺序，不是用户价值顺序。**  
   用户到 P3 才看到旗舰价值，到 P4 才形成通知闭环；
   P2 却先投入最危险、最昂贵、且与旗舰链路无关的沙箱。
   因此最早可交付的阶段仍不像一个可留存的产品。

4. **12 项需求几乎都有架构落点，但大多没有产品验收标准。**  
   覆盖并不等于可发布。
   文件“同步”、GitHub 同步、agentic email、QQ、团队协作尤其停留在名词或底层抽象。

5. **信任设计必须从“权限闸”扩展到“用户可理解、可撤回、可纠错”。**  
   自动建 todo 和自动通知虽然技术上低风险，
   但高误报、错误截止日期、跨团队泄漏和通知疲劳都会直接破坏采用。
   用户需要候选箱、置信度/来源、撤销、反馈、静默时段、摘要和审计记录。

### 0.2 建议的产品决策

在大规模编码前，先锁定以下产品决策：

- v1 ICP：自托管的技术型个人用户，而非“所有个人与小团队”；
- v1 核心承诺：把邮件中的行动项变成**可核验、可编辑、可追溯**的待办；
- 自动化默认：先生成“候选待办”，用户建立信任后再逐级自动入列；
- v1 渠道：Web + Gmail；通知先做 Web/摘要邮件，QQ 为实验性扩展；
- v1 不做：团队共享记忆、通用代码沙箱、子 agent、agentic inbound email、多个模型 provider；
- 基础观测、成本和审计不能等到 P6；
- 调度交付语义按任务类型选择，不能全局锁死 at-most-once。

---

## 1. 产品愿景与定位

### 1.1 双定位是否连贯

`README.md` 将 Sherpa 定义为：

> “多租户云端 Agent 运行时——个人助理兼小团队协作。”

`docs/00-overview.md §目标用户` 进一步列出：

- 个人：有记忆、能跑代码、能主动提醒；
- 小团队：共享工作区、共享任务与记忆。

`docs/02-identity-session-memory.md §两层记忆` 和 ADR-004
用“个人 = 单人工作区”统一了 schema。

这解决了**数据模型统一**，
但没有解决**产品定位统一**。

个人助理和团队协作的关键差异如下：

| 维度 | 个人助理 | 小团队协作 |
|---|---|---|
| 购买者 | 使用者本人 | owner/admin/团队负责人 |
| 首要价值 | 少漏事、少整理、节省个人时间 | 共享上下文、分工、透明度、减少协调成本 |
| 数据边界 | “我的数据”较直观 | 私人、项目、团队、频道多级可见性 |
| 信任建立 | 用户与 agent 之间 | 每个成员、团队管理员与 agent 三方之间 |
| 激活事件 | 连上邮箱后发现第一条有用事项 | 邀请成员后完成第一次协作闭环 |
| 留存单位 | 个人每周获得行动价值 | 团队共同工作流持续运行 |
| 核心竞品 | Gmail/Notion AI/个人任务工具/个人 agent | Slack/Teams/Asana/Linear/团队知识工具 |

因此，双定位可作为**平台愿景**，
不应作为**首发叙事**。

如果首页同时强调：

- 能聊天；
- 能跑代码；
- 能接邮箱；
- 能接 GitHub；
- 有自己的邮箱；
- 能登录 QQ；
- 能做团队协作；

用户很难回答：

> “Sherpa 最擅长替我完成哪一件事？”

这就是当前最大的身份危机。

### 1.2 目标用户仍然过宽

“个人”和“小团队”不是足够清晰的 ICP。

至少还需要回答：

- 是否要求用户能维护 Docker；
- 是否要求用户自备模型 API key；
- 用户是否主要使用 Gmail；
- 用户是否需要 GitHub；
- 用户是否在中国大陆，因此 QQ 是主入口；
- 用户是否愿意授予邮件只读权限；
- 用户的工作是否包含大量可从邮件中提取的行动项；
- 团队规模是 2–5 人、5–20 人，还是更大；
- 团队使用 QQ、Slack、Teams 还是邮件；
- 谁是管理员，谁承担部署与模型费用。

### 1.3 建议的 v1 ICP

建议将第一批用户定义为：

> **愿意自托管、使用 Gmail，并希望自动整理工作行动项的技术型个人用户。**

可进一步限定：

- 每周收到至少 50 封工作邮件；
- 已使用某种 todo 工具，但手工整理成本高；
- 能完成 Docker Compose 基础部署；
- 能理解 OAuth 权限；
- 对隐私敏感，愿意用自托管换取数据控制；
- 可能使用 GitHub，但 GitHub 不是激活前置条件。

这个 ICP 与现有技术方向兼容，
也能形成更可验证的产品假设。

### 1.4 建议的一句话定位

不建议首发使用“全能云助理”。

建议：

> **Sherpa 是一个可自托管的行动助理：它从你的邮件中找出需要做的事，让你确认后持续跟进。**

技术用户版本可补充：

> 数据留在自己的部署中，所有自动动作可追溯、可撤回。

### 1.5 建议的产品层级

| 层级 | 定义 | v1 处理 |
|---|---|---|
| 核心结果 | 不漏掉重要行动项 | 必须 |
| 核心工作流 | Gmail → 候选 todo → 确认 → 提醒 | 必须 |
| 信任层 | 来源、解释、编辑、撤销、审计、偏好 | 必须 |
| 使用界面 | Web inbox、todo 列表、设置 | 必须 |
| 扩展来源 | GitHub | 早期扩展 |
| 扩展出口 | QQ/IM | 实验性 |
| 通用 agent | Web 对话、按需查询 | 可保留但不是主叙事 |
| 代码执行 | 沙箱改/跑代码 | 后置 |
| agent 自有身份 | agentic email | 后置 |
| 团队协作 | 共享任务、分派、权限、活动流 | 独立产品阶段 |

### 1.6 应明确的非目标

v1 建议明确“不做”：

- 不替代完整项目管理工具；
- 不代表用户自动对外回复；
- 不承诺零配置支持所有邮箱；
- 不承诺支持所有 QQ 客户端和协议；
- 不运行来自邮件内容的代码；
- 不做通用云 IDE；
- 不做企业级 RBAC/合规；
- 不默认把私人邮件内容分享给团队；
- 不承诺 agent 生成的截止日期一定正确；
- 不以聊天消息数作为主要产品价值。

---

## 2. 12 项需求覆盖评审

### 2.1 总体覆盖结论

| # | 需求 | 设计覆盖 | 产品完整度 | 结论 |
|---:|---|---|---|---|
| 1 | Docker 一键部署 | 强 | 弱 | 黄色 |
| 2 | 多用户登录 | 中 | 弱 | 黄色 |
| 3 | 沙箱改/跑代码 | 强 | 中弱 | 黄色 |
| 4 | 每用户存储/同步 | 中弱 | 弱 | 红色 |
| 5 | Gmail 等邮箱 | 中 | 弱 | 黄色 |
| 6 | GitHub 同步 | 弱 | 弱 | 红色 |
| 7 | 定时任务 | 强技术设计 | 弱产品设计 | 黄色 |
| 8 | 内容分析→待办 | 中强 | 中弱 | 黄色，最值得优先 |
| 9 | agentic email | 中弱 | 很弱 | 红色 |
| 10 | QQ/IM bot | 中弱 | 很弱 | 红色 |
| 11 | 主动通知 | 中 | 弱 | 黄色 |
| 12 | 小团队协作 | 弱 | 很弱 | 红色 |

这里的“红色”不代表没有代码架构，
而是还不能据此判断用户能否成功完成任务。

### 2.2 需求 1：Docker 一键部署

**设计证据**

- `README.md` 把 Docker 一键部署列为首要能力；
- `docs/07-observability-deployment.md §部署编排`
  列出 web、worker、scheduler、channels、sandbox-orch、Postgres、Redis、MinIO、frontend；
- ADR-012 选择 Postgres + pgvector、Redis、MinIO，理由是减少服务数。

**评审**

运行时能被 `docker compose up` 拉起，
不等于用户已经拥有“可用的 Sherpa”。

用户仍可能需要：

- 配置模型 API key；
- 创建 Gmail OAuth 应用和 callback URL；
- 配置域名、TLS 和反向代理；
- 配置 master encryption key；
- 配置 agentic email 供应商；
- 配置 QQ bot 账号/协议；
- 运行数据库迁移；
- 创建第一个管理员；
- 判断各服务是否健康；
- 备份和升级。

因此当前文案更准确地说是“单机 Compose 部署”，
不是“一键完成产品启用”。

**缺失**

- 支持的宿主机与最低资源；
- ARM/x86 支持；
- 首次设置向导；
- 默认安全配置；
- 健康检查和错误诊断；
- 升级、迁移、回滚和备份 UX；
- OAuth callback 的本地/公网模式；
- 无公网域名时是否可用；
- telemetry 是否默认关闭；
- 成功部署的时间目标。

**建议验收标准**

- 在文档声明的全新 Linux 主机上，用户只需复制 env 模板并运行一个命令即可启动；
- 首次访问进入 setup wizard，而不是空白页或原始 API；
- wizard 能检查模型、数据库、Redis、对象存储和加密 key；
- 不启用 Gmail/QQ 时，核心产品仍能运行；
- 20 分钟内完成“部署→注册→首次对话”；
- 升级失败可回滚，用户数据不丢失；
- 管理页明确显示每个服务和连接器的健康状态。

### 2.3 需求 2：多用户登录

**设计证据**

- `docs/02-identity-session-memory.md` 定义 tenant/user/identity/session；
- `docs/08-data-model.md §身份 & 租户` 定义 users、memberships、identities；
- `docs/01-architecture.md §Gateway` 提到认证、会话和租户解析。

**评审**

这是身份**模型**，
还不是登录**产品流程**。

**缺失**

- 注册开放还是仅邀请；
- 邮箱验证；
- 登录 session 生命周期；
- 忘记密码和重置；
- 管理员 bootstrap；
- 禁用/删除用户；
- 多设备和登出其他设备；
- 密码策略与暴力破解保护；
- OAuth login 是否支持；
- 用户加入多个 tenant 时的切换 UX；
- 个人空间能否退出/删除；
- owner 离开团队如何处理。

**建议验收标准**

- 首位用户安全创建 owner 账号；
- owner 可邀请、撤销邀请、停用成员；
- 未验证 identity 不能触发 agent；
- 用户可查看并撤销所有登录设备和外部身份绑定；
- tenant 切换时，文件、todo、记忆和会话不会串租户；
- 忘记密码流程可用且不会泄漏账号是否存在；
- 被移除成员立即失去该 tenant 的访问和推送资格。

### 2.4 需求 3：沙箱改/跑代码

**设计证据**

- `docs/05-tools-permissions-sandbox.md` 对 schema 校验、权限闸、异步审批和容器隔离定义较完整；
- ADR-007 锁定 ephemeral per-run 容器、持久 workspace、默认断网；
- `docs/09-roadmap.md` 将其列为 P2。

**评审**

这是 12 项中技术设计最具体的能力之一，
但用户体验仍未定义。

用户不会只关心“命令是否安全执行”，还会关心：

- agent 改了哪些文件；
- 能否先看 diff；
- 一次审批到底授权什么；
- 依赖安装为什么失败；
- 断网时如何安装包；
- 运行多久会超时；
- 失败后文件是否保留；
- 是否能撤销改动；
- 并发执行是否覆盖文件；
- 哪些语言和镜像可用。

**产品风险**

沙箱会显著扩大部署、支持、安全和成本表面积，
却不是旗舰 inbox-to-action 的必要条件。

**建议验收标准**

- 用户可在执行前看到命令、工作目录、网络权限、预计影响；
- 写文件后展示 diff，并支持接受/撤销；
- 默认镜像、语言、CPU、内存、超时有清晰说明；
- 断网失败给出可操作解释，而不是只返回 exit code；
- 不同用户无法读取彼此文件；
- 运行中可取消，取消后有确定状态；
- 一次、会话、永久授权的范围可查看并撤销；
- 从邮件/webhook 触发的 run 永远不能获得代码执行权限。

### 2.5 需求 4：每用户存储，上传/同步文件与代码

**设计证据**

- `docs/05-tools-permissions-sandbox.md` 定义持久 workspace；
- `docs/08-data-model.md §记忆 & 文件` 只有 files 的基础字段；
- `docs/09-roadmap.md` 在 P1 写“文件上传/同步”。

**评审**

“上传”有落点，
“同步”尚未被定义。

可能的“同步”至少有四种不同产品：

- 浏览器上传/下载；
- 本地文件夹双向同步；
- Git 仓库 clone/pull/push；
- Gmail/GitHub 附件或内容导入。

它们的冲突、版本、权限和安全模型完全不同。

**缺失**

- v1 到底支持哪种同步；
- 文件大小、类型、数量和总配额；
- 目录操作；
- 版本历史与恢复；
- 同名冲突；
- 病毒/恶意文件处理；
- 分享和团队可见性；
- 删除与对象存储回收；
- 搜索/索引状态；
- 上传失败续传；
- 文件导出。

**建议验收标准**

- v1 明确仅承诺浏览器上传/下载和 agent workspace 读写；
- 用户可创建目录、上传、下载、重命名、移动和删除；
- 单文件及总配额可见；
- agent 修改可追溯到 run，至少保留可恢复版本；
- 文件索引状态和失败原因可见；
- 删除账号时 blob 和派生 embedding 都被删除；
- “本地双向同步”和“Git 同步”若不做，界面与文案不得暗示已支持。

### 2.6 需求 5：连接 Gmail 等邮箱

**设计证据**

- `docs/06-connectors-autonomy.md §连接器统一抽象` 定义 sync cursor 和只读优先；
- ADR-013 区分用户 Gmail 与 agentic email；
- `docs/02-identity-session-memory.md` 给出每 5 分钟同步示例。

**评审**

抽象合理，
但 OAuth onboarding 和数据使用承诺是核心产品，不是实现细节。

**缺失**

- 首发是否只支持 Gmail；
- Google OAuth app 由部署者创建还是项目提供；
- self-host callback 配置；
- scope 精确列表；
- 全邮箱、特定 label 还是用户选择范围；
- 历史回溯窗口；
- 附件是否读取；
- 邮件保留策略；
- revoked/expired token 恢复；
- Google API 配额；
- consumer app verification；
- 用户如何暂停同步并删除已同步数据。

**建议验收标准**

- 用户可在五步以内完成 Gmail 连接；
- 授权前明确展示读取范围、存储内容和用途；
- 默认只同步用户选定 label 或时间窗口；
- token 失效时给出重新连接 CTA，不丢失 cursor；
- 用户可暂停、立即断开并删除已同步正文/embedding；
- 每封来源邮件有可回跳链接；
- 重复同步不产生重复候选 todo；
- 不支持的账号类型在连接前说明。

### 2.7 需求 6：同步 GitHub

**设计证据**

- `docs/06-connectors-autonomy.md` 把 GitHub 放入统一 Connector；
- `docs/08-data-model.md` 允许 source=github；
- `docs/09-roadmap.md` 在 P3 同时安排 GitHub 与 Gmail。

**评审**

GitHub 目前主要是名称级覆盖。

“同步 GitHub”可能意味着：

- 同步 issues；
- 同步 PR review requests；
- 同步 notifications；
- 同步仓库代码；
- 同步 commits；
- 创建 issue；
- 在 PR 上评论；
- clone 到 workspace。

当前文档没有选择。

**建议 v1 范围**

先只做：

- 用户选择具体仓库；
- 读取 assigned issues；
- 读取 requested reviews；
- 将其转换为带来源的候选 todo；
- 不写 issue、不评论、不 push 代码。

**建议验收标准**

- 支持 GitHub App 或细粒度 token，且只访问用户选择的仓库；
- 用户可看到授权仓库和 scope；
- issue/PR 更新能更新同一 todo，而不是重复创建；
- 仓库权限丢失后连接器明确降级；
- 断开后可选择保留或删除同步数据；
- 任何写回 GitHub 的能力必须单独授权并逐次审批。

### 2.8 需求 7：设置并执行定时任务

**设计证据**

- `docs/06-connectors-autonomy.md §调度器` 定义 cron/every/ISO、leader、原子领取；
- ADR-011 锁定 at-most-once；
- `docs/03-runtime-async-jobs.md` 统一交互和定时 job。

**评审**

调度引擎设计较强，
调度产品语义较弱。

**缺失**

- 用户如何创建：表单、自然语言还是两者；
- 时区和夏令时；
- 一次性提醒；
- 暂停、编辑、跳过、立即运行；
- 运行历史；
- 错过执行；
- 失败重试；
- “每月最后一天”等边界；
- 删除 schedule 是否取消已入队 job；
- 团队 schedule 的 owner 和权限；
- 任务失败是否通知。

**对 ADR-011 的产品异议**

“绝不重复”不是所有任务的最高优先级。

对于新闻摘要，漏一次通常可接受；
对于“明早提醒我提交投标”，漏一次可能比重复一次严重得多。

统一 at-most-once 会把基础设施故障转化为**无声漏提醒**。

**建议验收标准**

- UI 永远显示下一次运行的绝对时间和时区；
- 用户可测试运行并查看结果；
- 每次运行有 `scheduled/running/succeeded/failed/skipped` 历史；
- 一次性提醒默认采用“最终送达优先”；
- 周期摘要可采用“避免重复优先”；
- 系统停机恢复后明确展示 missed run，按策略补跑或跳过；
- 重复通知率和漏执行率都有 SLO。

### 2.9 需求 8：分析连接器内容，智能生成/规划待办

**设计证据**

- `docs/06-connectors-autonomy.md §旗舰功能` 给出完整 pipeline；
- `docs/02-identity-session-memory.md` 给出 Gmail 邮件生成 todo 的具体例子；
- `docs/08-data-model.md` 定义 todos 和 todo_deps。

**评审**

这是当前最清楚、最有差异化、最值得做成 v1 的需求。

但“模型成功调用 todo_write”不等于产品成功。

需要回答：

- 什么内容算行动项；
- 谁是执行人；
- 截止日期不明确时怎么办；
- 模型应不应该猜；
- 同一线程更新如何合并；
- newsletter 和 FYI 如何过滤；
- 低置信度结果放哪里；
- 用户怎样纠错；
- 错误反馈如何改善后续行为；
- 一个大任务如何拆分；
- todo 与来源邮件如何双向追溯。

**关键建议：引入候选待办箱**

v1 不应默认把所有推断直接写入正式 todo 列表。

建议状态：

`candidate → accepted | edited | dismissed → planned → done`

每个 candidate 应展示：

- 标题；
- 建议截止日期；
- 建议优先级；
- 责任人；
- 来源邮件和摘录；
- “为什么认为这是行动项”的简短解释；
- 置信度或不确定性标签；
- accept/edit/dismiss。

**建议验收标准**

- 在标注测试集上，行动项 precision 达到约定阈值；
- 明确日期的提取准确率单独衡量；
- 不明确日期不擅自生成精确截止时间；
- 同一邮件/线程重复同步不重复建项；
- 用户可一键接受、编辑或 dismiss；
- accepted todo 100% 可追溯到来源；
- dismiss 原因可采集但不强迫填写；
- 用户可选择“候选模式”或“高置信度自动入列”；
- 删除来源数据时有清晰的 todo 保留策略。

### 2.10 需求 9：agentic email

**设计证据**

- `docs/06-connectors-autonomy.md §信任分级` 定义 agent 自有邮箱；
- ADR-013 把它定位为收指令、发通知和收回复的通信身份；
- 数据模型把它列为 connector kind。

**评审**

这是高想象力、高信任门槛、高运营复杂度能力。

文档中的“供应商发的专用账号”不是可执行产品方案。

**缺失**

- 邮箱供应商；
- 地址格式和自定义域；
- 创建、回收和转移；
- 谁支付；
- 发信配额；
- SPF/DKIM/DMARC；
- 退信和投诉；
- spam reputation；
- 允许哪些 sender 下指令；
- sender spoofing 防护；
- 邮件 thread 与 session 映射；
- 多租户地址隔离；
- 用户离开团队后的邮箱所有权；
- abuse prevention；
- 数据保留和 eDiscovery。

**产品判断**

v1 不应同时承担“用户 Gmail 读取”和“自建 agent 邮件身份”两套邮箱 onboarding。

先用 Web + 普通通知邮件完成价值验证。
agentic inbound email 应在用户已经信任自动化后再引入。

**建议验收标准**

- 只有已验证 sender 能发控制指令；
- 未验证 sender 的内容最多进入隔离 inbox，不触发动作；
- 用户能查看 agent 代表哪个 tenant、允许联系谁；
- 所有出站邮件有审计、退信状态和停发开关；
- 地址回收不会把旧租户邮件交给新租户；
- 单用户/tenant 有发送配额和滥用告警；
- 供应商不可用时有明确降级方案。

### 2.11 需求 10：登录 QQ 等 IM bot

**设计证据**

- `docs/00-overview.md` 引用 AstrBot/aiocqhttp；
- `docs/02-identity-session-memory.md` 定义 QQ identity、私聊和群聊 UMO；
- `docs/03-runtime-async-jobs.md` 设置 Channels listener；
- P5 计划完成 IM 入站。

**评审**

会话归一化清晰，
但协议、账号和平台政策风险没有转化为产品决策。

“QQ 等”也过于开放。
QQ、企业微信、Slack、Teams、Telegram 的审核和运行模型不同，
不能只靠同一个 adapter 抽象视为等价。

**缺失**

- v1 只支持 QQ 还是还包括其他 IM；
- 使用官方还是非官方协议；
- bot 账号如何登录、掉线和重新验证；
- 封号风险；
- 二维码/设备验证；
- 群管理员同意；
- 群成员身份绑定；
- 群消息隐私告知；
- mention 规则；
- rate limit；
- rich approval 卡片不可用时的文本降级；
- bot 离线时的备用渠道。

**建议验收标准**

- 明确标注 experimental 和协议风险；
- QQ 绑定必须通过 Web 发起并双向验证；
- 群聊启用需要群管理员和 tenant owner 同意；
- bot 只响应 mention 或明确命令，不能默认读取并分析整个群；
- 用户可随时解绑并清除 identity；
- 掉线、封禁、重新登录状态对管理员可见；
- QQ 不可用时不影响 Web 核心工作流。

### 2.12 需求 11：主动发送邮件/IM 通知

**设计证据**

- `docs/06-connectors-autonomy.md §主动推送` 定义 pick_channel 和 sent_log；
- ADR-010 默认允许通知；
- ADR-011 提供幂等思路。

**评审**

当前 `QQ 在线 → QQ，否则 agentic email` 太简单。

通知策略是产品体验，
不能只按渠道可用性选择。

**缺失**

- opt-in；
- 通知类别；
- 紧急/普通阈值；
- quiet hours；
- 工作日；
- 时区；
- 即时与 digest；
- 每日上限；
- snooze；
- 渠道优先级；
- 用户对某来源静音；
- 送达/退信；
- 从通知直接 accept/dismiss；
- 团队通知收件人；
- 敏感内容是否显示在锁屏。

**建议验收标准**

- 首次自动通知前明确征得同意；
- 默认提供每日摘要，而不是每个 candidate 即时推送；
- 用户可按来源、优先级、渠道和时间配置；
- 支持 quiet hours 和每日频率上限；
- 通知包含最少必要信息，敏感正文默认不外显；
- 每条通知可追溯触发原因；
- 用户可一键静音相似通知；
- 送达失败在 Web 中可见；
- 通知退订不影响核心 todo 功能。

### 2.13 需求 12：小团队协作

**设计证据**

- `docs/02-identity-session-memory.md` 定义 team tenant 和共享 memory block；
- `docs/08-data-model.md` 定义 memberships(owner/member) 和 tenant/user todo；
- ADR-004 统一个人和团队 schema。

**评审**

当前设计实现了**多租户基础**，
没有定义完整的**协作产品**。

共享记忆不是协作本身。

最基本的团队协作通常还需要：

- 邀请与成员生命周期；
- todo assignee；
- 多 assignee 或 watcher；
- 评论；
- mention；
- 活动记录；
- 状态和优先级；
- 团队 inbox；
- 私人 vs 团队来源；
- 角色和权限；
- 管理员策略；
- 通知路由；
- owner 转移；
- 离职数据处理；
- 审计和导出。

现有 role 只有 owner/member，
也不足以表达管理员、普通成员、访客或只读成员。

更重要的是：

私人 Gmail 生成的 todo 在什么条件下可进入 team tenant，
当前没有明确的用户控制。

**路线图缺陷**

P0–P6 没有一个阶段明确交付团队协作闭环。
P1 的多租户骨架不是可用的团队协作。

**建议**

团队能力应作为单独产品阶段，
先验证以下最小协作闭环：

`邀请成员 → 分享一个已确认 todo → 指派 → 评论/更新 → 完成 → 活动可追溯`

**建议验收标准**

- 私人 connector 数据默认不能被团队检索或注入共享 prompt；
- 用户必须显式分享 candidate/todo 才进入团队；
- assignee、watcher、评论、活动流可用；
- owner/admin/member 权限有清楚矩阵；
- 成员移除后权限立即生效；
- 团队数据可导出；
- 管理员能看到自动化规则，但不能默认读取成员私人连接器内容；
- 团队通知只发送给相关成员。

---

## 3. MVP 与范围

### 3.1 当前 P0–P6 不是合适的产品 MVP 切法

`docs/09-roadmap.md` 的顺序是：

`核心 → 多租户 → 沙箱 → 连接器 → 调度/推送 → IM → 观测`

这是一条合理的技术组件构建路线，
但不是最短用户价值路线。

当前顺序的问题：

- P0 只有单用户 REST 对话和工具，属于技术 demo；
- P1 投入多租户、MinIO 和文件同步，但尚无旗舰价值；
- P2 投入高风险沙箱，仍没有 inbox-to-action；
- P3 才能分析邮件生成 todo，但没有主动提醒闭环；
- P4 才第一次接近可留存的完整产品；
- P5 增加入口，但不一定增加核心价值；
- P6 才补 trace、成本与评估，太晚发现质量和单位经济问题；
- 没有任何阶段明确交付团队协作。

换言之：

**用户要等到 P4 才能体验 Sherpa 最有辨识度的承诺。**

### 3.2 真正的最小可爱产品（MLP）

建议将 MLP 命名为：

> **Inbox-to-Action**

核心用户旅程：

1. 用户完成自托管部署；
2. 创建个人账号；
3. 连接只读 Gmail；
4. 选择同步范围；
5. Sherpa 扫描一小段近期邮件；
6. 显示 3–10 条候选行动项；
7. 用户 accept/edit/dismiss；
8. accepted 项进入 todo；
9. Sherpa 在到期前通过 Web 或摘要邮件提醒；
10. 用户完成任务并能回到来源邮件。

这条旅程同时验证：

- 部署；
- 登录；
- OAuth；
- connector；
- 异步 job；
- 模型质量；
- todo；
- 调度；
- 通知；
- 信任；
- 留存潜力。

不需要先验证：

- 代码沙箱；
- QQ 入站；
- agentic inbound email；
- 团队共享记忆；
- 子 agent；
- 多 provider failover；
- 通用插件市场。

### 3.3 MLP 必须包含的功能

#### A. 首次体验

- setup wizard；
- 管理员 bootstrap；
- 模型 key 验证；
- Gmail OAuth；
- 示例/演示数据模式；
- 同步进度；
- 清楚的空状态。

#### B. 候选待办箱

- 来源摘录；
- 建议标题；
- 建议 due date；
- 解释/不确定性；
- accept/edit/dismiss；
- 批量操作；
- 重复合并；
- 回到原邮件。

#### C. 正式 todo

- open/done；
- due date；
- priority；
- 来源；
- 手工创建；
- 编辑；
- 删除；
- snooze。

#### D. 提醒

- Web 通知中心；
- 每日摘要邮件；
- quiet hours；
- 频率设置；
- 失败可见；
- 退订。

#### E. 信任与控制

- 连接器 scope；
- 暂停同步；
- 删除连接器数据；
- 自动化模式；
- 运行历史；
- 每条生成结果的来源；
- 成本/用量基本可见。

#### F. 质量与运维

- 最小 trace；
- prompt/model version；
- 用户反馈；
- 连接器健康；
- job 状态；
- 基本备份与恢复说明。

### 3.4 应从 MLP 切除或推迟

| 能力 | 建议 | 原因 |
|---|---|---|
| 通用代码沙箱 | 推迟 | 高风险、高支持成本，不是核心闭环必要条件 |
| 每用户完整文件同步 | 缩为上传/下载 | “同步”范围过大 |
| GitHub | Gmail 验证后加入 | 两个 connector 同时做会混淆质量和 onboarding 问题 |
| agentic inbound email | 推迟 | 供应商、身份、滥用、deliverability 未定 |
| QQ 入站 | 实验性后置 | 外部协议和封号风险高，不应阻塞核心价值 |
| 团队共享记忆 | 推迟 | 尚无共享权限和协作语义 |
| 子 agent/ensemble | 推迟 | 用户价值未验证前属于复杂度 |
| 第二 provider | 接口保留、实现推迟 | 先验证一个模型的任务质量 |
| hybrid search/RAG | 按需后置 | 邮件候选提取首版不依赖完整记忆系统 |
| 复杂自然语言 cron | 推迟 | 先做明确时间和每日摘要 |
| 外部代表用户写操作 | 推迟 | 信任尚未建立 |

### 3.5 MLP 的发布门槛

至少满足：

- 10 位目标用户能独立完成部署或有明确的安装支持；
- 至少 80% 测试用户能完成 Gmail 连接；
- 中位 time-to-first-useful-candidate 小于 15 分钟；
- 候选行动项 precision 达到可接受阈值；
- 所有 accepted todo 可追溯到来源；
- 用户可以完整暂停、断开、删除；
- 不会因单封恶意邮件获得 workspace、memory 或外部写权限；
- 通知可控且默认不会轰炸；
- 重要任务不会因调度语义无声消失；
- 基础成本和失败率可见。

### 3.6 “技术 demo”“alpha”“v1”应分开定义

| 里程碑 | 目的 | 不应宣称 |
|---|---|---|
| Core demo | 证明异步 loop、工具和持久化可工作 | 不宣称产品可用 |
| Internal alpha | 用真实 Gmail 验证候选质量和信任 UX | 不宣称一键部署或团队协作 |
| Private beta | 目标用户独立部署并持续使用 | 不宣称 QQ/agentic email 稳定 |
| v1 | Inbox-to-Action 达到质量、可靠性和删除/控制门槛 | 不宣称“全能助手” |
| Team beta | 验证最小协作闭环 | 不与个人 v1 混为一个激活漏斗 |

---

## 4. 优先级与路线图评审

### 4.1 对现有阶段的逐项意见

#### P0 核心

优点：

- 快速证明 loop 和 provider 接口；
- 有利于早期技术学习。

问题：

- SQLite 与 ADR-012 的 Postgres 目标不一致，可能产生一次性迁移工作；
- REST 对话 + 工具不能验证旗舰价值；
- “早加第 2 个 provider”先于真实用户质量验证，价值较低。

建议：

- P0 保持极短；
- 只做一个 provider；
- 从第一天记录 prompt/model/token/cost；
- 使用最小 Postgres，避免 SQLite 行为差异；
- 用固定邮件样本而非纯聊天作为第一条端到端测试。

#### P1 多租户骨架

优点：

- tenant 隔离必须尽早正确；
- Compose、登录和持久化是外部试用前提。

问题：

- 一次纳入 PG、Redis、MinIO、文件同步和完整多用户，范围太大；
- 个人旗舰验证不需要完整团队功能；
- “文件同步”定义不清。

建议：

- 保留 tenant_id 和个人 workspace；
- 只实现 owner + personal tenant；
- 团队邀请和复杂 membership 后置；
- 文件能力仅做候选/附件所需最小集。

#### P2 代码沙箱

优点：

- 属于原始需求；
- 架构风险高，做技术 spike 有意义。

问题：

- 作为正式产品阶段早于连接器价值，排序错误；
- 会引入镜像、资源、Docker socket、网络和审批 UX；
- 会拖慢第一批用户验证。

建议：

- 做隔离性技术 spike，但不阻塞 MLP；
- 正式能力移至 Inbox-to-Action v1 之后；
- 不在产品首页早期承诺完整 cloud coding agent。

#### P3 连接器

优点：

- 第一次进入产品差异化；
- Gmail pipeline 是正确候选。

问题：

- Gmail + GitHub 同时做，增加两套 auth 和内容语义；
- 缺少候选箱、反馈、置信度和来源 UX；
- 没有 P4 就没有完整提醒闭环。

建议：

- Gmail 先行；
- 将 todo candidate 和基本提醒合入同一阶段；
- GitHub 作为第二个来源，在 Gmail 指标达标后复制 connector 模式。

#### P4 调度与主动推送

优点：

- 形成“主动助理”差异化；
- 与 flagship 强相关。

问题：

- 不应与 P3 分开到两个用户价值里程碑；
- agentic email/IM 出站不是首个通知闭环的必要条件；
- at-most-once 尚未按任务风险分级。

建议：

- 先做 Web 通知中心 + digest email；
- 与 Gmail candidate pipeline 同批交付；
- agentic email 和 QQ 出站独立试验。

#### P5 IM 入站

优点：

- 验证“一人多入口”；
- 对中国用户可能有明显便利。

问题：

- 多入口是便利，不是已验证的核心价值；
- QQ 协议依赖可能把产品可靠性绑定在外部不稳定因素上；
- approval over QQ 在权限解释不足时容易误操作。

建议：

- 作为 opt-in beta；
- 先验证用户是否真的希望通过 QQ 处理 candidate；
- 不把它作为 v1 GA 阻塞项。

#### P6 可观测与评估

这是当前排序中最需要调整的部分。

`docs/07-observability-deployment.md` 已认识到事件可自然产生 trace，
却把观测和评估整体放到最后。

产品团队在 P3 之前就必须知道：

- 哪些邮件生成了 candidate；
- 哪个 prompt/model 生成；
- 成本是多少；
- 用户是否接受；
- 哪种错误最多；
- connector 是否漏同步；
- job 是否失败；
- 通知是否送达。

建议：

- P0：基础 run/generation/cost trace；
- flagship alpha：candidate feedback 和质量看板；
- beta：可靠性、连接器健康和用户可见使用量；
- 高级评估平台可后置。

### 4.2 建议的新路线图

#### R0：价值与风险原型

目标：

- 用离线邮件样本验证行动项提取；
- 用低保真界面验证 candidate accept/edit/dismiss；
- 明确 Gmail OAuth 和 QQ/agentic email 可行性。

交付：

- 50–100 封脱敏标注邮件数据集；
- prompt/model baseline；
- candidate UX 原型；
- connector provider 决策；
- 信任和数据删除原则。

退出条件：

- 目标用户认为结果值得连接真实邮箱；
- precision 达到进入 alpha 的最低线；
- 产品范围和 ICP 锁定。

#### R1：个人运行底座

目标：

- 可部署、可登录、可运行异步 job、可观测。

交付：

- Compose；
- setup wizard；
- personal tenant；
- Web UI；
- Postgres/Redis；
- 一个 provider；
- durable admission；
- 基础 trace/cost；
- job 状态和错误恢复。

退出条件：

- 新环境可重复安装；
- 失败可诊断；
- 数据隔离测试通过。

#### R2：Inbox-to-Action alpha

目标：

- 完成 Gmail → candidate → todo 的首个闭环。

交付：

- Gmail readonly OAuth；
- 同步范围；
- candidate inbox；
- accept/edit/dismiss；
- source traceability；
- 去重；
- 基本 todo；
- 反馈埋点。

退出条件：

- 真实用户产生第一条有用 candidate；
- 质量和成本可衡量；
- 用户能暂停、断开和删除。

#### R3：主动跟进 private beta

目标：

- 从“一次有用”变成“每周持续有用”。

交付：

- due/snooze；
- schedule history；
- Web 通知；
- digest email；
- quiet hours；
- 频率上限；
- connector health；
- missed-run 策略。

退出条件：

- 用户每周接受并完成来源型 todo；
- 通知关闭率和投诉率可接受；
- 无声漏任务低于 SLO。

#### R4：来源与入口扩展

目标：

- 验证可扩展性，不改变核心结果。

候选交付：

- GitHub assigned issue/review；
- QQ beta；
- 自然语言 schedule；
- 更细连接器过滤；
- API/webhook。

每项需单独达到：

- 有明确用户需求；
- 不恶化核心精度；
- 有独立健康和撤销控制。

#### R5：代码能力

目标：

- 为技术用户增加从 todo 到执行的能力。

交付：

- workspace upload/download；
- sandbox spike 转正式能力；
- diff/revert；
- approval UX；
- 限额和成本。

进入条件：

- 用户明确要求“从行动项直接执行代码”；
- 隔离和支持成本可接受。

#### R6：团队 beta

目标：

- 验证真实协作，而不只是共享 schema。

交付：

- invite；
- owner/admin/member；
- 显式分享；
- assignee/watcher；
- comment/activity；
- team inbox；
- 管理员策略；
- team export/deletion。

进入条件：

- 个人用户出现可验证的分享需求；
- 私人/团队数据边界完成用户研究和威胁建模。

#### R7：agent identity

目标：

- 验证 agentic email 是否比普通通知渠道带来新增价值。

交付前置：

- provider；
- sender authentication；
- reputation；
- abuse controls；
- ownership lifecycle；
- pricing。

### 4.3 优先级原则

后续每项功能应按以下顺序评分：

1. 是否缩短首次价值时间；
2. 是否提高行动项质量；
3. 是否提高用户控制和信任；
4. 是否提高每周重复价值；
5. 是否降低 onboarding 或运维负担；
6. 是否降低不可逆风险；
7. 最后才是架构完整性和能力广度。

不要以“已经有统一抽象，所以顺便支持”作为产品优先级依据。

---

## 5. 关键产品缺口

### 5.1 Onboarding 不是一个流程，而是多个高摩擦流程叠加

当前潜在 onboarding 包含：

- 部署 8 个服务；
- 配模型 key；
- 注册管理员；
- 配域名/TLS；
- 建 Google OAuth app；
- 授权 Gmail；
- 选择同步范围；
- 配 agentic email；
- 登录 QQ；
- 创建或加入 workspace；
- 设通知偏好。

如果同时出现，转化率会非常低，
且失败原因难以诊断。

建议采用渐进式 onboarding：

1. 先进入可用 Web；
2. 用示例数据体验 candidate；
3. 再连接 Gmail；
4. 看到第一条价值后再开提醒；
5. 使用一段时间后再推荐 GitHub/QQ；
6. 团队和 agentic email 永远不是首次激活前置项。

### 5.2 自治需要“信任阶梯”，不只是 allow/ask/deny

ADR-010 锁定：

> 读 + 建 todo + 通知全自动。

从技术风险看，这些动作可撤销；
从产品风险看，它们会改变用户的信息环境。

建议用可配置的信任阶梯：

| 级别 | 行为 |
|---|---|
| 0 观察 | 只分析，不保存候选 |
| 1 建议 | 生成 candidate，用户确认 |
| 2 受限自动化 | 高置信度自动建 todo，其他进 candidate |
| 3 自动跟进 | 自动建 todo + 按用户规则提醒 |
| 4 代表行动 | 对外动作逐次审批 |

升级应由用户主动选择，
并能一键降级。

### 5.3 SAFE 工具集仍然过宽

ADR-009 和 `docs/05-tools-permissions-sandbox.md §起步工具箱`
允许不可信 email/webhook 使用：

- workspace read/glob/grep；
- todo_write；
- memory_*；
- web_fetch；
- ask_user。

“只读”不等于“低产品风险”。

恶意邮件可能：

- 诱导读取私密 workspace；
- 污染持久 memory；
- 大量创建垃圾 todo；
- 通过通知向用户回显敏感内容；
- 触发外部 web fetch。

建议旗舰 pipeline 使用比 SAFE 更窄的
`CONNECTOR_ANALYSIS_TOOLS`：

- 只读取当前 connector item；
- 只创建 candidate；
- 不读 workspace；
- 不写 memory；
- 不任意 web_fetch；
- 不直接通知，通知由确定性策略层决定。

这是应当在编码前重开 ADR-009 的关键理由。

### 5.4 没有质量纠错闭环

`docs/07-observability-deployment.md` 计划记录 trace 和 scores，
但产品侧尚未定义反馈事件。

至少需要：

- accepted；
- accepted_with_edit；
- dismissed；
- duplicate；
- wrong_assignee；
- wrong_due_date；
- not_actionable；
- sensitive；
- notification_helpful；
- notification_annoying。

这些事件既是产品指标，
也是 prompt/regression 数据来源。

### 5.5 没有通知疲劳模型

只做幂等不能避免疲劳。

用户可能仍每天收到 30 条不同但低价值的通知。

需要：

- urgency threshold；
- digest；
- source mute；
- daily cap；
- quiet hours；
- semantic bundling；
- snooze；
- “为什么收到”；
- 从通知直接反馈。

### 5.6 空状态未设计

关键空状态包括：

- 尚未连接任何 connector；
- Gmail 已连接但暂无新邮件；
- 邮件很多但没有行动项；
- 所有 candidate 被 dismiss；
- 没有 due todo；
- QQ 未绑定；
- 团队只有 owner；
- 模型 key 不可用；
- 同步仍在运行；
- 数据已删除。

每个空状态都应告诉用户：

- 当前发生什么；
- 是否正常；
- 下一步能做什么；
- 何时会更新。

否则异步系统很容易被感知为“没有反应”。

### 5.7 错误恢复 UX 未定义

`docs/04-core-loop.md` 详细定义 failed、interrupted、budget stop，
但这些状态如何向用户解释尚未定义。

用户需要看到的是：

- 哪一步失败；
- 已完成什么；
- 是否会自动重试；
- 是否产生费用；
- 是否可能重复；
- 用户能否手动重试；
- 是否需要重新授权；
- 失败是否影响其他任务。

建议为 connector、run、schedule、notification 分别定义用户可理解状态，
不要把内部 stop reason 原样暴露。

### 5.8 管理员与团队管理缺失

至少需要：

- 成员邀请/移除；
- owner 转移；
- connector 所有权；
- 自动化规则 owner；
- 通知策略；
- 用量/配额；
- 审批记录；
- 账号停用；
- 数据导出；
- tenant 删除；
- 安全事件日志。

对 self-hosted 部署还需要：

- 系统管理员和 tenant owner 是否同一角色；
- 系统管理员能否读取用户内容；
- 是否支持多个独立 tenant；
- 谁能配置全局模型 key。

### 5.9 数据导出、删除和 GDPR 类权利缺失

当前设计强调 tenant_id 和加密 token，
但没有完整数据生命周期。

至少要定义：

- 用户可导出哪些数据；
- 导出格式；
- 删除账号；
- 删除 tenant；
- 删除 connector 原始内容；
- 删除 embedding；
- 删除 memory；
- 删除日志和 trace；
- 备份中的删除延迟；
- 法定/运维保留期；
- 团队 owner 与成员删除请求冲突；
- token 撤销；
- agentic mailbox 回收。

即使个人项目不正式宣称 GDPR 合规，
也应实现“查看、导出、删除、断开”四项基本控制。

### 5.10 成本、计费和价格模型缺失

`docs/07-observability-deployment.md` 记录 session cost_rollup，
但没有用户侧用量设计。

需要先决定：

- 完全 self-hosted + BYOK；
- 项目托管模型 key；
- 混合模式；
- 是否提供 managed hosting；
- agentic mailbox 成本由谁承担；
- 每用户/tenant 配额；
- 超额时停机、降级还是提示；
- 团队成本怎样归属；
- sandbox compute 怎样计量。

建议首版：

- 明确为 self-hosted/BYOK；
- 不做复杂账单；
- 提供每周、每 connector、每 run 的 token/估算费用；
- 支持预算上限和告警；
- 文案不使用“免费”，因为模型与基础设施有真实成本。

未来可能的定价假设：

| 模式 | 优点 | 风险 |
|---|---|---|
| 开源 self-hosted 免费 | 易获得技术用户信任 | 支持成本高、收入弱 |
| Managed 按席位 | 易理解 | agent 成本随使用波动 |
| 按用量 | 匹配成本 | 用户不敢开启自动化 |
| 基础席位 + 用量上限 | 相对平衡 | 需要清晰成本控制 |
| 团队版按 workspace | 符合协作价值 | 团队价值尚未验证 |

在团队价值验证前，不应提前锁定复杂价格。

### 5.11 隐私与团队共享语义缺失

“一人多入口”解决了身份汇聚，
也增加了意外汇聚风险。

需要明确：

- 个人 QQ DM 默认落个人 tenant 是否永远成立；
- 用户切换 default_workspace 后旧入口会怎样；
- Gmail connector 是 user-owned 还是 tenant-owned；
- 私人邮件 candidate 如何显式分享；
- team session 是否注入私人 memory；
- 群聊中不同用户的私人 memory 是否可能被模型引用；
- 团队 shared memory 谁能编辑和纠错；
- 离开团队后哪些 memory 保留。

默认原则应是：

> 私人来源永不因为用户加入团队而自动变成团队上下文。

### 5.12 支持与可解释性缺失

对非确定性系统，
用户需要的不只是“查看 trace”，而是产品化解释：

- 为什么创建这个 todo；
- 为什么选择这个 due date；
- 为什么通知我；
- 为什么没有处理这封邮件；
- 为什么需要这个权限；
- 哪个模型执行；
- 消耗了多少；
- 怎样避免下次再犯。

解释应短、可操作，
不能要求普通用户阅读内部事件流。

---

## 6. 对锁定 ADR 的产品意见

架构 ADR 可以锁定实现方向，
但不应锁死尚未验证的产品行为。

### 6.1 ADR-001：云端 agent，不做 local agent

**结论：基本同意，但需澄清“云端”。**

Sherpa 是长驻服务，
云端运行时方向合理。

但“Docker 自托管”也可能运行在用户本地 NAS 或私有服务器。
对用户应强调：

- always-on service；
- browser-accessible；
- self-hostable；
- 不必等同于项目方托管 SaaS。

否则“cloud”与“数据由自己控制”的卖点可能冲突。

### 6.2 ADR-004：两层记忆统一个人和团队

**结论：架构上保留，产品上不足。**

共享 memory block 不能代替：

- 显式分享；
- 任务分派；
- 评论；
- 活动流；
- 可见性；
- 团队管理。

且“编辑会 rebuild 所有成员 prompt”是成本/隐私风险，
应要求成员知道哪些内容被共享注入。

### 6.3 ADR-005：所有运行异步 job

**结论：同意，但必须补异步 UX。**

产品必须定义：

- 排队反馈；
- 进度；
- 预计等待；
- 离开页面后如何回来；
- 失败和重试；
- 取消；
- 重连后的事件补发；
- 完成后通知。

否则正确架构会表现为“系统卡住”。

### 6.4 ADR-007：ephemeral Docker-per-run

**结论：同意作为安全基线，不同意作为早期产品优先级。**

建议保留技术决策，
但把完整沙箱从旗舰 MLP 路径移除。

### 6.5 ADR-009：SAFE vs FULL

**结论：必须重开。**

二级信任比没有信任分级好，
但 SAFE 仍可读取 workspace、写 memory 和 todo。

产品需要至少三级：

- `CONNECTOR_ANALYSIS`：当前 item → candidate only；
- `AUTHENTICATED_READ`：用户主动请求的只读检索；
- `FULL`：经过权限闸的写入/执行。

不可信输入应遵循数据最小化，
而不只是禁用 shell。

### 6.6 ADR-010：读、建 todo、通知全自动

**结论：不同意作为统一默认值。**

建议改为：

- 读：用户授权范围内自动；
- 生成 candidate：自动；
- 进入正式 todo：默认需确认，可配置高置信度自动；
- 通知：首次 opt-in，受 quiet hours/digest/cap 约束；
- 对外代表行动：逐次审批。

产品理由：

- 错误 todo 会污染用户系统；
- 错误通知会快速导致关闭渠道；
- “可撤销”不代表“无信任成本”；
- 新用户和已建立信任的用户应有不同默认。

### 6.7 ADR-011：全局 at-most-once

**结论：不同意一刀切。**

建议按 job 类型定义交付策略：

| Job | 建议语义 |
|---|---|
| connector sync | 可重试，cursor 幂等 |
| candidate 生成 | at-least-once + external_id 去重 |
| 普通 digest | at-most-once 可接受 |
| 关键提醒 | 最终送达优先 + 幂等 |
| 对外写操作 | 明确 idempotency + 审批 token |
| sandbox run | 默认不自动重跑副作用操作 |

产品目标不是技术上统一，
而是让用户面对每类失败都得到合理结果。

### 6.8 ADR-012：Postgres + Redis + MinIO

**结论：架构可接受，但“一键”承诺要降噪。**

三个状态服务加多个应用服务对个人用户仍然重。

需用安装、升级、备份和健康 UX 抵消复杂度，
否则产品只适合工程师维护。

### 6.9 ADR-013：agentic email 与 Gmail 分离

**结论：信任模型同意，产品优先级不同意。**

分离是正确的；
但 agentic email 的供应、身份、声誉、滥用和生命周期尚未解决。

应作为独立实验，
不能与 Gmail connector 一起成为 v1 前置依赖。

---

## 7. 产品与采用风险

### 7.1 风险登记表

| 风险 | 概率 | 影响 | 早期信号 | 缓解 |
|---|---:|---:|---|---|
| 定位过宽，用户不知道为何使用 | 高 | 高 | 首页理解度低、激活路径分散 | 聚焦 Inbox-to-Action 和单一 ICP |
| Docker/OAuth onboarding 过重 | 高 | 高 | 部署成功但 Gmail 连接率低 | setup wizard、示例模式、渐进 onboarding |
| 用户不愿授权邮箱 | 中高 | 高 | OAuth 页面退出率高 | readonly、范围选择、自托管、删除控制 |
| 行动项误报导致不信任 | 高 | 高 | dismiss 高、首次周后停用 | candidate 模式、来源、解释、反馈 |
| 截止日期推断错误 | 中高 | 高 | 大量 edit due date | 不确定时不猜、单独评估日期准确率 |
| 通知疲劳 | 高 | 高 | mute/unsubscribe 高 | digest、quiet hours、cap、优先级阈值 |
| at-most-once 造成漏提醒 | 中 | 高 | 用户报告“没有提醒”且无运行记录 | 按任务类型选语义、missed-run 可见 |
| QQ 非官方协议不稳定/封号 | 高 | 中高 | 频繁掉线、登录失败 | experimental、非阻塞、替代渠道 |
| agentic email 被滥用或进垃圾箱 | 中高 | 高 | bounce/complaint 上升 | 后置、配额、认证、信誉监控 |
| SAFE 工具遭 prompt injection | 中 | 极高 | 异常 workspace 读取/memory 写入 | pipeline 专用最小工具集 |
| 团队内私人数据意外泄漏 | 中 | 极高 | 权限投诉、跨来源引用 | 私人默认、显式分享、审计、隔离测试 |
| 模型成本不可控 | 中高 | 高 | 单用户成本方差大 | BYOK、预算、缓存、用户可见用量 |
| 自托管升级/备份失败 | 中 | 高 | issue 集中在运维 | 支持矩阵、备份验证、升级回滚 |
| 沙箱扩大安全与支持面 | 中高 | 高 | 资源泄漏、安装包失败多 | 后置、镜像限制、配额、技术 spike |
| “团队协作”不比现有工具好 | 高 | 中高 | 无邀请、无共享闭环 | 等个人分享需求出现后再设计 |
| 生成 todo 但用户仍不完成 | 中高 | 高 | accept 高、completion 低 | 跟进、snooze、优先级、减少噪声 |
| 连接器同步延迟降低信任 | 中 | 中高 | 手动刷新多、状态查询多 | last sync、进度、健康、手动重试 |
| 邮件数据保留引发隐私顾虑 | 中 | 高 | 连接后快速断开 | 最小保留、删除、范围选择、透明文案 |
| 多入口 identity 误绑定 | 低中 | 极高 | 错人收到通知 | 双向验证、解绑、敏感操作二次确认 |
| 项目成为架构练习而非产品 | 高 | 高 | 组件完成多、真实用户少 | 每阶段绑定用户结果和指标 |

### 7.2 最大的信任障碍

“一个有自己邮箱、会主动行动的 agent”
可能吸引早期技术爱好者，
也会让大多数用户立刻想到：

- 它会不会替我乱发；
- 谁能给它发指令；
- 邮件里的恶意文字会不会控制它；
- 它会不会把私人内容发到群里；
- 我离开后它是否还在运行；
- 我怎样关掉所有自动化；
- 出错由谁负责。

因此信任不是一个安全说明页，
而应体现在每个产品动作里：

- 明确来源；
- 明确权限；
- 明确收件人；
- 明确自动化规则；
- 明确撤销；
- 明确审计；
- 明确停止按钮。

### 7.3 依赖风险

以下能力依赖项目无法控制的外部方：

- Google OAuth verification 和 API quota；
- GitHub App 权限模型；
- QQ 协议与账号政策；
- agentic email provider；
- 模型 API 价格、限流和内容政策；
- Docker 宿主机安全能力。

每个外部依赖都应有：

- owner；
- 支持矩阵；
- 失败状态；
- 降级路径；
- 替代方案；
- 退出标准。

尤其不应让 QQ 或 agentic email 阻塞 Web + Gmail 核心产品发布。

---

## 8. v1 成功指标

### 8.1 北极星指标

建议北极星指标：

> **每周行动价值用户（Weekly Action Value Users, WAVU）**

定义：

在一个自然周内，用户至少：

1. 接受或显著编辑一条 connector 生成的 candidate；
2. 并完成、snooze 或基于该 todo 执行一次有意图的后续动作。

这个指标优于：

- 消息数；
- 模型调用数；
- 生成 todo 数；
- 连接器同步量；
- token 消耗。

因为它同时要求“发现有用事项”和“用户真的采取行动”。

### 8.2 激活漏斗

建议逐步记录：

1. `deployment_started`
2. `deployment_healthy`
3. `admin_created`
4. `demo_candidate_viewed`
5. `gmail_oauth_started`
6. `gmail_connected`
7. `first_sync_completed`
8. `first_candidate_generated`
9. `first_candidate_accepted_or_edited`
10. `first_notification_configured`
11. `first_source_todo_completed`

建议 beta 目标：

| 指标 | 初始目标 |
|---|---:|
| 健康部署 → admin 创建 | ≥ 90% |
| admin 创建 → 查看 demo candidate | ≥ 85% |
| 查看 demo → 发起 Gmail OAuth | ≥ 60% |
| OAuth 发起 → 连接成功 | ≥ 80% |
| Gmail 连接 → 首次同步成功 | ≥ 95% |
| 首次同步 → 至少一个 candidate | 需按邮箱样本分层，不设虚假统一目标 |
| 有 candidate → 7 天内 accept/edit | ≥ 50% |
| 中位 time-to-first-value | < 15 分钟，不含用户部署下载时间 |

这些目标应作为 beta 假设，
根据真实样本调整，而不是当作既定事实。

### 8.3 质量与信任指标

| 指标 | 定义 | 建议门槛 |
|---|---|---:|
| Candidate precision | 生成项中被 accept/edit 的比例，需校正未处理项 | alpha ≥ 70%，v1 目标 ≥ 80% |
| Straight accept rate | 无编辑直接接受 | 趋势上升，不单独追高 |
| Material edit rate | 标题/责任人/due 明显修改 | < 25% |
| Dismiss rate | 被明确 dismiss | < 20%，按来源分类 |
| Duplicate rate | 用户或规则确认的重复项 | < 1% |
| Due-date accuracy | 明确日期邮件中日期提取正确 | ≥ 95% |
| Unsupported-date guess rate | 无明确日期却生成精确日期 | 接近 0 |
| Source traceability | accepted todo 可回溯来源 | 100% |
| Unauthorized action | 未经规则/审批的动作 | 0 |
| Sensitive leakage | 跨用户/tenant 泄漏 | 0 |
| Automation rollback rate | 用户撤销自动动作 | 按模式监控 |

不要只看接受率。

过少生成 candidate 也可能人为提高 precision，
所以还应在标注数据集上衡量 recall。

### 8.4 参与度指标

- 每周查看 candidate inbox 的激活用户比例；
- 每周 accepted/edited source todo 数；
- 每周完成 source todo 的用户比例；
- 每周回到来源邮件的次数；
- snooze 后最终完成率；
- digest 打开率；
- 通知到 accept/done 的转化率；
- 手工创建 todo 与自动 candidate 的混合使用；
- 第 2 个 connector 的采用率；
- 用户主动调整自动化规则的比例。

### 8.5 留存指标

建议按“完成激活”用户计算，
不要把未连接 Gmail 的注册用户混入产品留存。

| 指标 | 定义 | beta 参考目标 |
|---|---|---:|
| W1 retained | 激活后一周仍产生行动价值 | ≥ 50% |
| W4 retained | 第四周仍为 WAVU | ≥ 30% |
| Connector retained | 30 天后 Gmail 仍连接且健康 | ≥ 70% |
| Notification retained | 30 天后未全局关闭提醒 | ≥ 60% |
| Automation upgrade | 从 candidate 模式升级受限自动化 | 观察性，不设强目标 |

团队版应另设 cohort，
不能与个人留存混算。

### 8.6 可靠性与性能指标

- connector sync 成功率；
- connector freshness P50/P95；
- job queue latency；
- run completion rate；
- notification delivery rate；
- missed schedule rate；
- duplicate notification rate；
- OAuth refresh failure rate；
- first candidate latency；
- Web 首屏和 candidate 列表延迟；
- 数据删除完成时间；
- 备份恢复演练成功率。

建议初始 SLO：

- Gmail 增量同步 99% 在承诺窗口内完成；
- 重复通知 < 0.1%；
- 关键提醒无声丢失 < 0.1%；
- accepted todo 来源可用率 99.9%；
- 用户请求删除后，在线数据在 24 小时内删除；
- run 失败必须 100% 有用户可见状态。

### 8.7 成本指标

- 每激活用户每周模型成本；
- 每 accepted candidate 成本；
- 每 WAVU 成本；
- 无用户价值 run 的成本；
- connector 存储/用户；
- sandbox 分钟/用户；
- cache hit rate；
- 按 prompt version 的质量/成本比；
- P50/P95 单 run token；
- 用户预算触发率。

最关键的单位经济指标是：

> **每条被接受且最终产生行动的 candidate 成本。**

### 8.8 定性研究

数字不能独立解释信任。

alpha 至少进行：

- 5 次部署可用性测试；
- 8–10 次 Gmail 授权与权限文案访谈；
- 每周查看 5 个高价值与 5 个低价值 candidate；
- 访谈用户为何 dismiss；
- 观察用户如何理解“agentic email”；
- 测试用户是否知道如何停止所有自动化；
- 测试团队用户如何判断私人和共享边界。

---

## 9. 需要补充的核心产品规格

在编码相关模块前，建议至少有以下轻量 PRD：

### 9.1 Flagship pipeline PRD

应定义：

- 支持的邮件类型；
- candidate 判定；
- 状态机；
- due/assignee 规则；
- 去重；
- thread 更新；
- 来源展示；
- 反馈；
- 自动化升级；
- 质量门槛。

### 9.2 Onboarding PRD

应定义：

- setup wizard；
- demo mode；
- Gmail OAuth；
- progress；
- health check；
- failure recovery；
- first value；
- 渐进式 connector/notification 启用。

### 9.3 Notification policy

应定义：

- 类别；
- urgency；
- opt-in；
- channel；
- quiet hours；
- digest；
- frequency cap；
- sensitive preview；
- delivery failure；
- unsubscribe。

### 9.4 Data lifecycle and privacy spec

应定义：

- 收集；
- 存储；
- 派生数据；
- embedding；
- retention；
- export；
- delete；
- backup；
- connector disconnect；
- team sharing。

### 9.5 Schedule semantics spec

应定义：

- timezone；
- DST；
- missed run；
- retry；
- idempotency；
- edit/cancel；
- history；
- critical vs noncritical delivery。

### 9.6 Team collaboration PRD

在团队阶段前定义：

- 角色；
- invite；
- visibility；
- share；
- assign；
- comments；
- activity；
- admin；
- offboarding；
- private connector policy。

---

## 10. 给团队的开放问题

### 10.1 愿景与市场

1. 第一批 10 位用户具体是谁？
2. 他们当前用什么方法把邮件变成行动项？
3. 最大痛点是漏事、整理时间、提醒，还是跨工具协作？
4. 为什么他们会选择 Sherpa，而不是 Gmail + 现有 todo 工具？
5. 自托管是核心价值，还是当前实现约束？
6. Sherpa 未来要成为产品，还是可嵌入的 runtime？
7. “个人助理”和“小团队”哪一个对 v1 负责？
8. 首页只允许写一个承诺时，会写什么？

### 10.2 用户与 onboarding

9. 用户是否必须懂 Docker？
10. 谁提供模型 API key？
11. 没有公网域名能否连接 Gmail？
12. Google OAuth app 由每个部署者创建吗？
13. 用户拒绝 Gmail 权限后，仍能体验什么价值？
14. 能否用演示数据在授权前展示结果？
15. 首次价值的目标时间是多少？
16. 哪一步失败时由谁提供支持？

### 10.3 Flagship pipeline

17. todo 是直接创建，还是先进入 candidate inbox？
18. 什么置信度允许自动入列？
19. 模型是否可以推测没有明确写出的截止日期？
20. 邮件 thread 更新如何更新既有 todo？
21. 谁定义“高优先级”？
22. newsletter、群发和 FYI 如何过滤？
23. 附件是否参与分析？
24. 用户 dismiss 后，类似邮件是否继续生成？
25. 用户是否可以只选择特定 label/sender？
26. accepted todo 是否会反向标记邮件？

### 10.4 自治与信任

27. 用户第一次连接 Gmail 时默认处于哪个自治等级？
28. 自动通知是否明确 opt-in？
29. 是否有全局 emergency stop？
30. 一次审批、会话审批和永久审批如何解释？
31. 审批多久过期？
32. 从 QQ 批准 Web 发起操作时，怎样展示完整上下文？
33. 谁能更改团队自动化规则？
34. 用户如何查看过去 30 天 agent 做过的所有动作？
35. 恶意邮件能否写 memory 或读取 workspace？答案应为否。

### 10.5 调度与通知

36. 重要提醒和普通摘要是否使用相同交付语义？
37. 系统离线两小时后是否补跑？
38. 夏令时重复/缺失小时如何处理？
39. 默认即时通知还是每日摘要？
40. 每日最大通知数是多少？
41. QQ 离线时是否 fallback 到邮件？
42. fallback 是否可能泄漏敏感内容？
43. 通知失败是否在 Web 中显示？

### 10.6 Agentic email 与 IM

44. agentic mailbox 由哪个供应商提供？
45. 地址由用户、tenant 还是 agent 拥有？
46. 谁可以给 agent 发指令？
47. 如何验证 sender，不只依赖 From header？
48. 如何处理退信、投诉和 spam reputation？
49. QQ 采用官方还是非官方协议？
50. 平台封禁后产品是否仍完整可用？
51. 群聊是否默认只响应 mention？
52. 群成员是否都必须绑定 identity？

### 10.7 团队协作

53. 最小团队规模和典型团队是什么？
54. 团队协作的首要对象是 todo、文件、记忆还是会话？
55. 私人 Gmail 生成的内容如何显式分享？
56. 团队 owner 能否查看成员私人 connector 状态或内容？
57. 除 owner/member 外需要哪些角色？
58. 谁能创建团队 schedule？
59. 成员离开后，他创建的 todo、memory 和 schedule 归谁？
60. 团队版与个人版使用同一 onboarding 是否合理？

### 10.8 数据与商业

61. 用户能否一键导出全部数据？
62. 删除 connector 时删除哪些派生内容？
63. 备份中的删除最长延迟是多少？
64. self-hosted 管理员是否技术上能读所有租户数据？
65. 项目是否计划 managed hosting？
66. v1 是 BYOK 还是平台付模型费？
67. 用户在哪里看到成本和预算？
68. agentic email 和 sandbox 成本怎样计量？
69. 免费/付费边界是什么？
70. 哪个指标达到后才值得做团队版？

---

## 11. 编码前建议完成的决策

### 必须完成

1. 锁定 v1 ICP；
2. 锁定单一首发承诺；
3. 决定 candidate-first，而非默认正式 todo；
4. 重开 ADR-009 的 SAFE 工具范围；
5. 重开 ADR-010 的自动化默认；
6. 重开 ADR-011 的统一 at-most-once；
7. 将基础 trace/cost/feedback 前移；
8. 定义 Gmail OAuth 的真实部署方案；
9. 定义数据断开、导出和删除；
10. 重排路线图，使 Gmail→candidate→todo→提醒成为第一个产品里程碑。

### 应尽快完成

11. 设计 setup wizard 和示例模式；
12. 建立邮件标注集与质量基线；
13. 定义通知偏好和 quiet hours；
14. 定义 connector health/error UX；
15. 将 GitHub 的“同步”缩成具体对象；
16. 将“文件同步”缩成明确 v1 范围；
17. 给 QQ 和 agentic email 设置独立 go/no-go gate；
18. 为团队协作创建独立 roadmap phase；
19. 定义 self-hosted/BYOK 成本承诺；
20. 招募首批 10 位目标用户，而不是等所有 P0–P6 完成。

### 可以保留的优秀基础

以下设计值得继续：

- 四层架构和窄腰原则（`docs/01-architecture.md`）；
- tenant/user/identity/session 分离（`docs/02-identity-session-memory.md`）；
- durable prompt admission（`docs/03-runtime-async-jobs.md`）；
- 有界双循环和 stop-reason gate（`docs/04-core-loop.md`）；
- 异步权限审批思路（`docs/05-tools-permissions-sandbox.md`）；
- Gmail 与 agentic email 的信任区分（`docs/06-connectors-autonomy.md`）；
- 事件作为观测原语（`docs/07-observability-deployment.md`）；
- tenant_id 隔离方向（`docs/08-data-model.md`）。

但这些是可靠产品的**必要条件**，
不是用户采用的**充分条件**。

---

## 12. 最终建议

Sherpa 值得继续，
但不应按当前 P0–P6 原样推进到“全能力完成”后再找用户。

建议采取：

> **愿景保留，首发收窄；架构保留，产品顺序重排。**

具体来说：

- 长期仍可做个人 + 团队的多租户云 agent runtime；
- 首发只证明一个结果：可靠地把邮件行动项变成用户信任的待办；
- 把候选确认、来源追溯、通知控制、删除和成本放在核心路径；
- 把沙箱、QQ、agentic email 和团队协作变成后续独立赌注；
- 用真实用户的激活、接受、完成和留存决定扩张，而不是用组件完成度决定扩张。

如果不收窄，
最大的风险不是工程做不出来，
而是做出一个架构完整、能力很多、却没有一个使用理由足够强的系统。

如果按建议收窄，
Sherpa 有机会以“可信的自托管行动助理”建立明确楔子，
再逐步长成原设计中的多入口、可执行、可协作平台。
