# 11 · Agent 工具面(能力层 + 双适配器)

> **状态**:设计稿(2026-07-20),等你 review。落地任务见 [`IMPLEMENTATION.md` Phase M-tools](IMPLEMENTATION.md);决策见 [ADR-023](decisions.md);工具协议契约见 [`contracts/api.md §7`](contracts/api.md)。
>
> **目标**:凡用户在 UI 上能看到/能做的功能,agent 都能通过**工具**自主驱动——且 REST 与 Tool **不重复业务逻辑**。

---

## 1. 原则

1. **一个能力,两个客户端**:业务逻辑只写一遍(能力层 `app/services/`);`REST` 是人的适配器,`Tool` 是 agent 的适配器,都只做"解析入参 → 调同一 service → 组织出参/错误"。
2. **共享上下文与权限**:两个适配器共用 `CallerContext`(tenant/user/session/run/actor)与同一**四道闸权限引擎**。
3. **纵切开发**:一次一个能力(service→REST→Tool→权限→测试→浏览器验收),不横切。
4. **一次性安全门不弱化**:不可信内容分析仍无工具(ADR-009);对外写动作走审批(ADR-020);审批"解决"是人的职责;破坏性数据操作 `ask`/仅人工。

---

## 2. 架构

```
                    ┌──────────────────────────── app/services/  (能力层 = 唯一业务逻辑) ───────────┐
   HTTP 请求         │  context.py   CallerContext(tenant/user/session/run/actor)                    │
  (人) ─► app/api/*  │  errors.py    ServiceError 层级(NotFound/VersionConflict/Forbidden/Invalid…)  │
        REST 适配器 ─┼─►candidates.py  accept_candidate() edit_ dismiss_ list_                       │
                    │  todos.py       create_todo() update_ complete_ list_                          │
  模型 tool_call     │  connectors.py  sync_connector() list_ pause_ resume_                          │
  (agent)─► app/tools/  schedules.py   create_schedule() list_ cancel_                               │
        Tool 适配器 ─┴─►…              (领域校验 + 变更 + flush;抛 typed error;**不 commit**)        ┘
                                        │
                    共享:CallerContext · 四道闸(REGISTERED→VISIBLE→ALLOWED→EXECUTABLE) · effect/审批(仅外部动作)
```

**分层职责(硬边界)**

| 层 | 负责 | 不负责 |
|---|---|---|
| `services/` | 领域校验、乐观并发、DB 变更(flush)、抛 typed `ServiceError`、返回领域对象 | HTTP、工具 schema、commit、审批渲染 |
| `api/*`(REST) | 解析 HTTP、建 `CallerContext(actor="user")`、调 service、**commit**、map error→HTTPException | 业务逻辑 |
| `tools/*`(Tool) | 校验 args、建 `CallerContext(actor="agent")`、走权限闸、调 service、组 `ToolResult`、写活动回执 | 业务逻辑、事务边界(由 loop 每轮 commit) |

---

## 3. CallerContext(统一上下文)

统一现有 `RequestContext`(REST)与 `ToolContext`(api.md §7)。service 只认它。

```python
# app/services/context.py
@dataclasses.dataclass(frozen=True)
class CallerContext:
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    actor: Literal["user", "agent", "system"]   # 审计 + 策略输入
    session_id: uuid.UUID | None = None
    run_id: uuid.UUID | None = None
    invocation_id: uuid.UUID | None = None
```

- REST 适配器:`CallerContext(tenant, user, actor="user")`(由 `RequestContext` 构造)。
- Tool 适配器:`CallerContext(tenant, user, actor="agent", session_id, run_id, invocation_id)`(由 loop 的 `ToolContext` 构造)。
- 背景 job:`actor="system"`。

---

## 4. service 契约

**签名约定**
```python
async def accept_candidate(
    session: AsyncSession, ctx: CallerContext, *, candidate_id: uuid.UUID, if_version: int
) -> CandidateAcceptance: ...
```
- 第一参 `session`,第二参 `ctx`,其余关键字参。
- **事务**:service 只做变更 + `await session.flush()`,**不 commit**;由适配器 commit(REST handler / loop 每轮)。理由:可组合(一轮内多次 service 调用同一事务)、与现有 handler 一致。
- **乐观并发**:凡带 `version` 的资源,写入需 `if_version`,不符抛 `VersionConflict`。
- **返回**:领域对象(复用 `api/schemas.py` 的 Pydantic 模型或 dataclass),两个适配器各自序列化。
- **租户隔离**:所有查询/变更用 `ctx.tenant_id`;跨租户资源一律当"不存在"(NotFound),不泄漏。

**内部写 vs 外部动作(关键区分)**
- **内部同租户写**(候选/待办/日程)= 纯事务 DB 操作,**不走 effect/幂等层**,只靠 `if_version` 乐观并发。
- **外部/非幂等动作**(`send_email`)= 走 `begin_invocation` + 审批(#20 已建)。
- **触发 job**(`sync_connector`)= 入队 arq job(与现有 `POST /connectors/{id}/sync` 同路径)。

---

## 5. 错误分类学(一处定义,双向映射)

```python
# app/services/errors.py
class ServiceError(Exception):
    code: str = "service_error"
    http_status: int = 400            # REST → HTTPException(http_status, code)
                                      # Tool → ToolError(f"{code}: {message}")

class NotFound(ServiceError):        code="not_found";        http_status=404
class VersionConflict(ServiceError): code="version_conflict"; http_status=409
class Forbidden(ServiceError):       code="forbidden";        http_status=403
class Invalid(ServiceError):         code="invalid";          http_status=422
class Conflict(ServiceError):        code="conflict";         http_status=409
```

| ServiceError | REST | Tool(喂回模型的 `llm_content`) |
|---|---|---|
| `NotFound` | 404 | `error: not_found: <msg>` |
| `VersionConflict` | 409 | `error: version_conflict: 请先 list_* 拿最新 version 再改` |
| `Forbidden` | 403 | `error: forbidden: <msg>` |
| `Invalid` | 422 | `error: invalid: <msg>` |

> 工具的错误永远是"观察",不崩循环(api.md §7)。

---

## 6. Tool 落地规格

**6.1 接口对齐契约(当前代码偏离,需回归)**
契约(api.md §7 L1049)是 `execute(self, ctx: ToolContext, args)`;当前 `base.py` 是 `execute(self, args)`——**缺 ToolContext**。M-tools T1 修正:
```python
class Tool(Protocol):
    name: str; description: str
    input_schema: dict[str, object]; flags: ToolFlags
    async def execute(self, ctx: ToolContext, args: dict[str, object]) -> ToolResult: ...
```

**6.2 flags → effect_class(已有 `permissions/policy.classify_effect`)**

| flags | effect_class | 例 |
|---|---|---|
| `is_read_only=True` | `read_only` | `list_*` |
| 写 + `is_concurrency_safe=True` + 非破坏 | `idempotent_write` | `accept_candidate`、`update_todo` |
| `is_destructive=True` 或 非并发安全 | `non_idempotent_write` | `send_email`、`delete_imported` |

**6.3 SAFE/FULL**:所有数据工具 = **FULL**(仅已认证用户会话)。`echo`/`get_time` 保持 SAFE。不可信内容会话(未来邮件等)永不获得数据工具(ADR-009)。v1 只有 FULL 会话。

**6.4 活动回执**:Tool 适配器在 service 成功后自动 `record_receipt(actor="agent", …)`——**agent 每次工具动作 = 一条 activity**。REST(人自己点)不记回执。背景 job 记 `system/connector`。这样"活动台账 = agent/系统替我做的事"语义自洽。

---

## 7. 四道闸 + ALLOWED 策略引擎

现状:`registry.py` 有 **VISIBLE** 闸;#20 `permissions/policy.py` 只有"非只读就 ask"极简版。**缺真正的 ALLOWED 策略引擎**(api.md §7.1 步骤 3)。

**7.1 v1 策略(硬编码默认 + 未来每租户策略表扩展点)**
```python
def evaluate(ctx, tool, scope) -> Literal["allow", "ask", "deny"]:
    # last-match 胜;冲突 deny > ask > allow;默认 deny(白名单式,收紧于 api.md 的默认 ask)
    if tool.flags.is_read_only:                 return "allow"   # 同租户只读
    if effect_class(tool) == "non_idempotent_write": return "ask" # 外部/破坏性 → 审批
    if scope.is_own_tenant_write:               return "allow"   # 同租户幂等写(候选/待办/日程)
    return "deny"
```
- **同租户读/幂等写 → allow**:你让 agent"接受候选/建待办/触发同步",agent 代你执行,无需再审批。
- **外部/破坏性 → ask**:`send_email`(#20 已走此路)、`delete_imported`。
- 未来:`permission_rules` 表(每租户 `allow|ask|deny` + scope 通配 + last-match),`evaluate` 读表。

**7.2 loop 集成**:`_run_tool`(#20 已建 ask 分支)前置一步 `evaluate`:
- `deny` → 写"error: not_permitted"观察,不执行;
- `ask` → 生成审批信封 + 挂起(现有路径);
- `allow` → 执行(调 service)。

---

## 8. 输出限长 + spill(当前缺)

`bounding.py` 只做 head/tail 截断;**未落地** api.md §7.2 的 `ToolOutputSpillReference`(超 2000 行/50KB → 落盘 `TOOL_OUTPUT_ROOT/{invocation_id}.txt` + 回 head/tail 摘要 + spill 引用)。M-tools T8 补齐。

---

## 9. 能力矩阵(UI ↔ service ↔ REST ↔ Tool ↔ 权限)— 活的追踪表

> ✅=已交付 ⬜=待补 ❌=故意不做(附原因) · **一行未完成 = 任一非 ❌ 单元格还是 ⬜**。
> **UI 列是 DoD 闸**:用户可见能力,UI 还是 ⬜ 就不算 Done(见 AGENTS.md §2)。每个能力两条 Playwright 验证:agent 路径(chat→tool)+ **人工路径(真实点 UI 控件)**。

| 能力 | service | REST | Tool | **UI** | effect | 策略 |
|---|---|---|---|---|---|---|
| 列候选 | ✅ | GET /candidates ✅ | `list_candidates` ✅ | Inbox ✅ | read_only | allow |
| 接受候选→todo | ✅ | POST …/accept ✅ | `accept_candidate` ✅ | Inbox(Accept)✅ | idempotent_write | allow |
| 编辑候选 | ✅ | POST …/edit ✅ | `edit_candidate` ✅ | ⬜(仅 chat/REST) | idempotent_write | allow |
| 忽略候选 | ✅ | POST …/dismiss ✅ | `dismiss_candidate` ✅ | Inbox(Dismiss)✅ | idempotent_write | allow |
| 列待办 | ✅ | GET /todos ✅ | `list_todos` ✅ | Inbox ✅ | read_only | allow |
| 改待办(完成/改期/snooze) | ✅ | PATCH /todos/{id} ✅ | `update_todo`/`complete_todo` ✅ | ⬜(Inbox 只读,无完成按钮) | idempotent_write | allow |
| 新建待办 | ✅ | POST /todos ✅ | `todo_write` ✅ | ⬜ | idempotent_write | allow |
| 列连接器 | ✅ | GET /connectors ✅ | `list_connectors` ✅ | ⬜(侧栏占位) | read_only | allow |
| 触发同步分析 | ✅ | POST …/sync ✅ | `sync_connector` ✅ | ⬜ | idempotent_write | allow |
| 建提醒/日程 | ✅ | POST /schedules ✅ | `create_reminder`/`create_daily_digest` ✅ | Schedules(/reminders)✅ | idempotent_write | allow |
| 通用定时任务 cron（agent_task，ADR-031） | ✅ schedules（cadence 引擎+护栏） | POST /schedules · /run-now · /status · GET …/firings ✅ | `create_scheduled_task` ✅ | Schedules 调度台（建/试跑/暂停/历史）✅ | idempotent_write（run 内外部动作仍 ask） | allow |
| 列/取消日程 | ✅ | GET /schedules · /cancel ✅ | `list_schedules`/`cancel_schedule` ✅ | Schedules ✅ | idempotent_write | allow |
| 列通知 | ✅ | GET /notifications ✅ | `list_notifications` ✅ | Inbox ✅ | read_only | allow |
| 读/改通知设置 | ✅ | GET·PATCH /settings ✅ | `get_settings`/`update_settings` ✅ | Settings(/preferences)✅ | idempotent_write | allow |
| 活动台账 | ✅ | GET /activity ✅ | `list_activity` ✅ | Activity(/data)✅ | read_only | allow |
| 会话:新建/切换 | (会话 API) | POST·GET /sessions ✅ | ❌ 不给 agent | Chat(new chat + 切换)✅ | — | — |
| 会话库:浏览/续跑/重命名/恢复(P0) | ✅ sessions | GET/PATCH /sessions · resume-state · recover · timeline ✅ | ❌ 不给 agent | Sessions(/history)✅ | read_only/idempotent_write | allow |
| 会话内容搜索(P1) | ✅ search | GET /sessions?query= ✅ | ❌ 不给 agent | Sessions 搜索框 ✅ | read_only | allow |
| 个人网盘:列/建夹/上传/下载/改名·移动/版本·恢复版本/回收站·恢复(P2) | ✅ drive | /drive/* ✅ | `drive_list`/`search`/`make_folder`/`write`/`read`/`move`/`trash`/`restore` ✅ | Drive(/workspace)✅ | idempotent_write | allow |
| 个人网盘:永久删除(purge) | ✅ drive | DELETE /drive/nodes/{id} ✅ | ❌ **不给 agent**(人工/审批专属) | Drive(Delete forever + 确认)✅ | non_idempotent_write | user-only |
| 项目:列/新建(空·模板)(ADR-037, W2a) | ✅ services/projects.py | GET·POST /projects | `project_list`/`project_create` | ✅ /work/projects(列表 + 新建空/模板) | idempotent_write | allow |
| 项目:归档导入(ZIP/TAR)(ADR-037, W2a) | ✅ services/projects_import.py(durable job) | POST /projects/imports(github→501) | ❌ 不给 agent(人工上传) | ✅ 新建项目·上传归档(安全解压 + 失败态) | idempotent_write(durable job) | allow |
| 项目:详情·文件树·快照(ADR-037, W2a) | ✅ services/projects.py | GET /projects/{id}·/tree·/snapshots | `project_tree`/`project_read` | ✅ 项目详情(只读树 + 快照 + 活动) | read_only | allow |
| 项目:Open in Chat(project 绑定会话)(ADR-037, W2a) | ✅ services/projects.py | POST /projects/{id}/chats·GET /sessions/{id}/project-context | ❌ 不给 agent(会话创建) | ✅ project 绑定 Chat(首消息后不可变) | idempotent_write | user-only |
| 项目:GitHub 连接(凭据)(ADR-038, W2b) | ✅ services/github_source.py(AEAD vault·连接边界·软撤销) | GET·POST·DELETE /connections/github | ❌ 不给 agent(凭据边界) | ✅ /work/projects(GitHub 连接面板·PAT·永不回显 token) | idempotent_write | user-only |
| 项目:GitHub 一次性导入(选 repo/ref → 有界归档获取 → 不可变初始快照 + source OID)(ADR-038, W2b) | ✅ services/projects_import.py(github 分支·durable job·resolve→OID→tarball→安全解压→快照) | POST /projects/imports kind=github(**202**)·POST /projects/{id}/imports/retry·GET /projects/github/repos·/refs | ❌ 不给 agent(人工·跨凭据+不可信外部内容) | ✅ /work/projects(repo/ref 选择·导入进度·成功来源元数据·失败/重试·390px) | idempotent_write(durable job) | user-only |
| 项目:任务工作副本（W3，跨 turn 持久 working copy + overlay + lease/fence + head_generation CAS）(ADR-040/ADR-039) | ✅ **W3**（ADR-040/039 生产实现；migration 0030；仅挂一次性 scratch 副本、绝不挂真相源；ADR-025 已正式修订；docker.sock/多用户隔离 = ADR-039 前置门控） | GET /sessions/{id}/working-copy · GET/POST …/working-copies·sandbox-runs（W3） | `project_run`（W3，allow） | ✅ **W3**（`/work/projects` Chat 内 Change Review 面板 + working-copy 状态） | idempotent_write | allow |
| 项目:变更评审（W3，added/modified/deleted + artifacts + 有界 diff/truncated）(ADR-040) | ✅ **W3**（生产实现；change-set 投影 + 溢出 diff + 二进制检测） | GET …/change-sets/{cs}·/entries/{e}/diff · GET …/artifacts（W3） | `project_review_changes`（W3，read_only·allow） | ✅ **W3**（Change Review 条目列表 + diff + artifacts Keep/Export） | read_only | allow |
| 项目:Save selected / Save+checkpoint / Discard / Keep·Export artifact（W3，人工评审闸；head 移动→CAS 拒绝）(ADR-040) | ✅ **W3**（生产实现；apply=head_generation CAS→409 SaveConflict） | POST …/change-sets/{cs}/apply（409 SaveConflict）·/discard · …/artifacts/{a}/keep·export（W3） | ❌ **不给 agent**（推进 head=人工评审决定） | ✅ **W3**（Save selected/Save+checkpoint/Discard + Keep/Export 按钮） | idempotent_write（apply=CAS） | user-only |
| 项目:GitHub 同步 / push / PR(对外写) | ❌ **W4**(后续 ADR;走 ADR-020 审批) | POST /projects/{id}/push 等(W4) | `project_push`(W4,ask) | ⬜ **W4** | non_idempotent_write | **ask** |
| 模型 provider:配置多来源(OpenAI/Anthropic/Gemini/DeepSeek/Qwen…；AEAD 密钥) (ADR-041) | ✅ **生产实现**(migration 0031 model_providers + AEAD 密钥；services/model_providers；3 wire 适配器) | GET/POST /providers · GET/PATCH/DELETE …/{id} · POST …/{id}/test·default · GET …/{id}/models | ❌ **不给 agent**(跨凭据边界=人工设置,同 GitHub 连接) | ✅ **Settings「Models」面**(增删/测试连接/选默认/每源默认 model；密钥 password 永不回显) | — | user-only |
| 模型 provider:全局默认 + 每会话切 model (ADR-041) | ✅ **生产实现**(sessions.model_provider_id/model；build_provider 按 DB 解析；env 兜底) | POST …/{id}/default · GET/POST /sessions/{id}/model | ❌ **不给 agent**(model 选择=设置) | ✅ **chat 顶栏 model 切换器** | — | user-only |
| 发邮件(外部) | — | ❌(仅 Tool) | `send_email` ✅ | ⬜ 审批渲染器(v1 收尾) | non_idempotent_write | **ask** |
| 连接 Gmail(OAuth) | — | connect/callback ✅ | ❌ | ⬜(需真实 Google 凭据) | — | — |
| 列/解决审批 | — | GET /permissions·/resolve ✅ | ❌ **不给 agent**(不自批) | Approvals(/approvals，可解析后台/定时审批)✅ | — | user-only |
| 预授权 grants(白名单自动放行，ADR-034) | ✅ grants | GET/POST/DELETE /grants ✅ | ❌ **不给 agent**(不能自发权限) | Approvals 页「Pre-authorized」增删 ✅ | idempotent_write(命中仍记 effect+审计) | user-only |
| 导出/删除导入数据 | ✅ | ✅ | ❌(破坏性,不给 agent) | Activity(Export/Delete)✅ | non_idempotent_write | ask/human |
| QQ/IM 入站对话 + IM 审批(post-v1 里程碑4) | channels ✅ | POST /channels/qq/webhook · GET /channels · /simulate · /threads ✅ | 复用有界循环(非独立 tool);审批复用 v1 基座 | Messaging(/messaging)✅ | idempotent_write | HMAC+owner allowlist |
| agentic email 入站 + 邮件审批 + 统一发信(post-v1 里程碑5) | notifications(build_email_sender)✅ · channels ✅ | POST /channels/email/webhook · /simulate · GET /channels/threads ✅ | `send_email`(走统一发信接缝,真实 AgentMail)✅;入站复用有界循环;审批复用基座 | Messaging(email 段)✅ | non_idempotent_write | Svix+owner allowlist · **ask** |
| 知识库:检索(带引用)(ADR-036, KB3/KB4/KB5) | ✅ knowledge_search | POST /knowledge/search ✅ | `search_knowledge` ✅ | Knowledge(/library)检索测试 ✅ + Chat 引用 chips/无依据态 ✅ | read_only | allow |
| 知识库:列来源(ADR-036, KB4/KB5) | ✅ knowledge | GET /knowledge/sources ✅ | `list_knowledge_sources` ✅ | Knowledge 主页(/library)✅ | read_only | allow |
| 知识库:加来源(从 Drive 文件)(ADR-036, KB1/KB4/KB5) | ✅ knowledge | POST /knowledge/sources ✅ | `add_knowledge_source`(按 path 解析) ✅ | Knowledge「从 Drive 添加」拾取器 ✅ | idempotent_write | allow |
| 知识库:重建来源(ADR-036, KB1/KB4/KB5) | ✅ knowledge | POST /knowledge/sources/{id}/reindex ✅ | `reindex_knowledge_source` ✅ | 来源详情「重建」+ 全部重建 ✅ | idempotent_write | allow |
| 知识库:删除来源(ADR-036, KB1/KB4/KB5) | ✅ knowledge | DELETE /knowledge/sources/{id} ✅ | `remove_knowledge_source`(破坏性→审批) ✅ | 来源详情/列表「移除」+ 确认 ✅ | non_idempotent_write | **ask** |

**剩余 UI ⬜(下一步补完的清单):** 候选 Edit 抽屉 · 待办完成/编辑控件 · Connectors 连接页(需 OAuth 凭据)· 审批渲染器(approve/reject + run 恢复,属 v1 收尾)。~~Knowledge(/library)页~~ **✅ KB5 已交付**(主页/来源详情/检索测试 + Chat 引用 chips + 无依据态;Sidebar/路由/API/Vite proxy 就位)。**Projects(/work/projects)= ADR-037 W2a 已实现并两栈验证**:上面 4 行的 service/REST/Tool/UI 单元格全部 ✅(空/模板/归档导入 + 详情只读树/快照/活动 + Open in Chat 不可变绑定 + `list/create/tree/read` agent 工具);生产导航已暴露(Sidebar「Projects」)。GitHub 导入 = **ADR-038 W2b 已实现并两栈验证**(migration `0029`):契约把 `POST /projects/imports kind=github` 从 501 升为 202、新增 repo/ref 选择端点 + GitHub connection 端点 + `project_sources`/`github_connections` 数据模型 + events `create_kind=github`;生产实现落地 `services/github_source.py`(连接生命周期 + 只读 REST 代理 + resolve→OID/有界 tarball 获取)、`services/projects_import.py` 的 github 分支(durable job·复用 W2a 内存安全解压器·剥离顶层目录·source OID·幂等重试·无 effect_unknown)、`api/connections.py` + `api/projects.py`(kind=github 202·retry·repos/refs·source provenance)、生产 `/work/projects` UI(GitHub 连接面板/repo·ref 选择/导入进度/成功来源元数据/失败·重试/390px);**service/REST/UI 单元格全部 ✅**,GitHub 导入不给 agent(人工·跨凭据边界),凭据只在 AEAD vault/连接边界、绝不进树/快照/prompt/日志/事件/工具结果。W3(sandbox)/W4(对外写)仍为后续 ADR。**这张表就是防"后端做了、前端忘了"的看板——每加一个能力,先在这里补行,UI 列不 ✅ 不收工。**

---

## 10. 新增一个能力的模板(照抄即可)

**① service**(`app/services/todos.py`)
```python
async def create_todo(session, ctx, *, title, description=None, due_at=None, priority="medium") -> Todo:
    if not title.strip():
        raise Invalid("title required")
    todo = TodoModel(tenant_id=ctx.tenant_id, id=uuid.uuid4(), user_id=ctx.user_id,
                     title=title, status="open", due_at=due_at, priority=priority, source="agent")
    session.add(todo); await session.flush()
    return _to_schema(todo)   # 不 commit
```

**② REST 适配器**(`app/api/todos.py`)
```python
@router.post("/todos", status_code=201)
async def create(body: TodoCreate, ctx: RequestContext = Depends(require_csrf),
                 db: AsyncSession = Depends(get_session)) -> Todo:
    ctx2 = CallerContext(ctx.tenant_id, ctx.user_id, actor="user")
    try:
        todo = await services.todos.create_todo(db, ctx2, title=body.title, due_at=body.due_at, ...)
    except ServiceError as e:
        raise HTTPException(e.http_status, e.code)
    await db.commit(); return todo
```

**③ Tool 适配器**(`app/tools/data_tools.py`)
```python
class CreateTodoTool:
    name = "todo_write"; flags = ToolFlags(is_read_only=False)
    input_schema = {"type":"object","properties":{"title":{"type":"string"}, ...},"required":["title"]}
    description = "Create a to-do for the user. Own-data write; no approval needed."
    async def execute(self, ctx: ToolContext, args) -> ToolResult:
        validate_args(self.input_schema, args)
        cc = CallerContext(ctx.tenant_id, ctx.user_id, actor="agent",
                           session_id=ctx.session_id, run_id=ctx.run_id, invocation_id=ctx.invocation_id)
        todo = await services.todos.create_todo(self.session, cc, title=args["title"], ...)  # 抛 ServiceError→ToolError
        return ToolResult(llm_content=f"created todo {todo.id}: {todo.title}",
                          return_display=DisplayPayload(format="json", content=todo.model_dump(mode="json")))
```
(注:Tool 如何拿到 `session`/`ctx` 由 loop 注入,见 T1。)

---

## 11. 验收模板(每个能力四层 + 浏览器)

1. **service 单测**:直接调 `create_todo` 等,断言变更 + 各 `ServiceError`(NotFound/VersionConflict/…)。
2. **Tool 单测**:注册表取工具,args 校验 + 成功/错误 → `ToolResult`/`ToolError`。
3. **loop 集成**:mock provider 脚本 emit 该 `tool_call` → 断言 effect 落地 + 活动回执 + 策略(allow 直接执行 / ask 生成信封)。
4. **REST 回归**:适配器薄化后原 REST 行为不变(复用现有 REST 测试)。
5. **浏览器 E2E(遵守既定规则)**:重启服务 + Playwright 让 agent 在对话里真·自主操作(例:"接受 Q3 那条候选" → Inbox 里候选变 todo;"同步我的 Gmail" → Activity 冒出 read/inference 回执),截图留档。

---

## 12. 故意不给 agent 的能力(边界)

| 能力 | 为何不给 |
|---|---|
| 解决审批(`resolve_approval`) | agent 不能批准自己的外部动作;审批是人的职责(ADR-020) |
| 不可信内容分析拿工具 | ADR-009:邮件内容走无工具隔离 pipeline,永不 wield 工具 |
| 直接 commit / 绕过权限/事件/审计 | api.md §7:工具不得自开租户/审批/审计旁路 |
| 删除导入数据(`delete_imported`) | 破坏性 → `ask` 或仅人工;默认不放进 agent 工具集 |

---

## 13. 依赖与顺序(详见 IMPLEMENTATION M-tools)

```
T1 ToolContext+CallerContext+service 脚手架(阻塞项)
   └► T2 ALLOWED 策略引擎
        ├► T3 候选工具(纵切样板)
        ├► T4 待办工具(+补 POST /todos)
        ├► T5 连接器工具(agent 自主同步→回执)
        ├► T6 日程工具(+补 /schedules REST)
        └► T7 通知/设置/活动 只读+设置工具
   └► T8 输出 spill + DisplayPayload(独立,可并行)
（後）memory_*:需先建两层记忆表,单列,不在本阶段）
```
