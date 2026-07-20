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

## 9. 能力矩阵(UI 功能 ↔ service ↔ REST ↔ Tool ↔ 权限)

> ✅=已有 ⬜=待补 ❌=不给 agent(故意)

| UI 功能 | service 函数 | REST | Tool | effect | 策略 | 缺口 |
|---|---|---|---|---|---|---|
| 列候选 | `list_candidates` | GET /candidates ✅ | `list_candidates` ⬜ | read_only | allow | Tool |
| 接受候选→todo | `accept_candidate` | POST …/accept ✅ | `accept_candidate` ⬜ | idempotent_write | allow | service抽取+Tool |
| 编辑候选 | `edit_candidate` | POST …/edit ✅ | `edit_candidate` ⬜ | idempotent_write | allow | service+Tool |
| 忽略候选 | `dismiss_candidate` | POST …/dismiss ✅ | `dismiss_candidate` ⬜ | idempotent_write | allow | service+Tool |
| 列待办 | `list_todos` | GET /todos ✅ | `list_todos` ⬜ | read_only | allow | Tool |
| 改待办(完成/改期/snooze) | `update_todo` | PATCH /todos/{id} ✅ | `update_todo` ⬜ | idempotent_write | allow | service+Tool |
| **新建待办** | `create_todo` | ❌ 无 | `todo_write` ⬜ | idempotent_write | allow | **REST+Tool 都缺** |
| 列连接器 | `list_connectors` | GET /connectors ✅ | `list_connectors` ⬜ | read_only | allow | Tool |
| **触发同步分析** | `sync_connector` | POST …/sync ✅ | `sync_connector` ⬜ | idempotent_write | allow | Tool(补后 agent 可自主拉邮件生成候选) |
| 连接器暂停/恢复 | `pause/resume_connector` | POST …/pause·/resume ✅ | ⬜ | idempotent_write | allow | Tool |
| **建提醒/日程** | `create_schedule` | ❌ 无 | `create_schedule` ⬜ | idempotent_write | allow | **REST+Tool 都缺** |
| 列/取消日程 | `list/cancel_schedule` | ❌ 无 | ⬜ | — | allow | **REST+Tool 都缺** |
| 列通知 | `list_notifications` | GET /notifications ✅ | `list_notifications` ⬜ | read_only | allow | Tool |
| 改通知设置 | `update_settings` | PATCH /settings ✅ | `update_settings` ⬜ | idempotent_write | allow | service+Tool |
| 活动台账 | `list_activity` | GET /activity ✅ | `list_activity` ⬜ | read_only | allow | Tool |
| **发邮件(外部)** | `send_email` | ❌(仅 Tool) | `send_email` ✅(#20) | non_idempotent_write | **ask** | — |
| 列/解决审批 | `list/resolve_approval` | GET /permissions·/resolve ✅ | ❌ **不给 agent** | — | user-only | agent 不批自己的动作 |
| 导出/删除导入数据 | `export/delete_imported` | ✅ | ⬜(建议 `ask` 或不给) | non_idempotent_write | ask | 谨慎 |

**结论**:agent 现有工具仅 `echo`/`get_time`/`send_email`;上表 ⬜ 即"要让 agent 驱动全部 UI"需补的清单。其中 `create_todo` / `create_schedule` / `list/cancel_schedule` 连 REST 都缺,需一并补齐。

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
