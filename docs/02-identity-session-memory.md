# 02 · 身份 / 会话 / 记忆模型

这是整个系统的地基，解决"一人多入口"难题：同一个人从 Web、QQ、agentic email、Gmail 触发进来，必须归到**同一身份**、共享同一份记忆和待办。解法 = **UMO 会话键 + 身份链接**（AstrBot UMO + OpenClaw identity links）。

## 四个概念（别混为一谈）

| 概念 | 是什么 | 例子 |
|---|---|---|
| `tenant`（工作区） | 顶层容器 = 团队 / 个人空间 | `team-alpha`、`personal-u1` |
| `user`（人） | 登录身份，可属于多个 tenant | 张三 `u1` |
| `identity`（外部身份链接） | 某渠道的外部 ID → user | `qq:12345→u1`、`email:z@x.com→u1` |
| `session`（会话） | 一段对话，用 **UMO 键**标识 | `qq:group:789`、`email:thread:<id>` |

## UMO 会话键

格式：`channel:type:external_id`

```
web:chat:<uuid>   ·   qq:private:12345   ·   qq:group:789
email:thread:<msgid>   ·   webhook:github:<repo>
```

一把键驱动：**用哪份记忆、哪个 persona、哪条限流、哪批 todos、哪个 provider**。

## 关键区分：session ≠ user

- 一个 QQ 群是**一个 session、多个 user**。
- 一个人有**跨渠道的多个 session**。

## 两层记忆（个人助理 + 团队协作都要）

- **user 级私有记忆**：Letta core memory block，个人画像/偏好。
- **tenant 级共享记忆**：团队共享 block；编辑会 rebuild 所有成员 prompt → **保持小**。

```python
recall = user_memory(session.user)                  # user 私有 block
if session.tenant.kind == "team":
    recall += tenant_memory(session.tenant)         # + 团队共享 block
# 包成 <memory-context> 注入"当前用户消息"，绝不进 system prompt（否则毁缓存）
```

## 心脏：`resolve_inbound()`

所有入口最后都汇进这**一个函数**（gateway 层）——这就是"一人多入口"的本质：

```python
def resolve_inbound(event):
    identity = identities.find(event.channel, event.external_id, verified=True)
    if not identity:                          # 未绑定 → 引导绑定 / 拒绝
        return handle_unlinked(event)
    user   = identity.user
    tenant = resolve_workspace(user, event)   # DM→default_workspace; 群→群绑定工作区
    umo    = f"{event.channel}:{event.scope}:{event.key}"
    session= sessions.get_or_create(tenant, umo, user)
    ctx    = assemble(session,
                      user_memory(user),
                      tenant_memory(tenant) if tenant.kind == "team" else None)
    tools  = SAFE_TOOLS if event.channel in ("email","webhook") else FULL_TOOLS  # 信任分级
    return enqueue_run(session, ctx, tools)   # 异步 job（见 03）
```

出站（主动推送）是它的镜像：`push(user) → pick_channel(user) → 该渠道 identity → 发送`。

## 一人多入口：完整例子

**场景**：张三用 `z@x.com` 注册，绑了 QQ（12345）、连了 Gmail/GitHub，在个人空间和 `team-alpha`。

**入口① 注册+绑定后落库**
```
users:        u1 (email=z@x.com)
tenants:      t_personal(kind=personal)   t_alpha(kind=team)
memberships:  (u1,t_personal,owner)  (u1,t_alpha,member)
identities:   (web,oauth-sub,u1,✓)  (qq,12345,u1,✓)  (email,z@x.com,u1,✓)  ← 绑定发验证码防冒领
connectors:   (gmail,u1,token_enc,readonly)  (github,u1,token_enc)
users.default_workspace = t_personal          ← DM 默认落哪个工作区
```

**入口② Gmail 邮件 → 建待办 → 推 QQ**
```
1. 调度器每 5min 拉 Gmail(只读)
2. 新邮件"周五前交季度报告"
3. ⚠️ 正文不可信 → 只给 SAFE 工具集（能读能建todo；不能 shell/写文件）
4. core 分析 → todos(t_personal,u1,"季度报告",due=周五,source=gmail)
5. push(u1): QQ 在线? → 发 QQ；⚠️ at-most-once 防重发
6. 张三 QQ 收到通知
```

**入口③ 张三 QQ 回"帮我拆解" → 落到同一记忆/同一 todos**
```
qq:12345 → identities → u1 → t_personal → umo="qq:private:12345"
→ 装配 u1 私有记忆 + 该用户 todos(含"季度报告")
→ core 拆成子任务写回 todos/todo_deps → QQ 出站
```
→ **同一个 u1、同一批 todos**。换 Web 打开看板看到的也是同一批。

## 这个模型暴露/隐含的 3 个选择

| 设计点 | 选择 |
|---|---|
| DM 落哪个工作区 | `users.default_workspace`；群聊绑定到群所属工作区 |
| 记忆归属 | 个人会话=user 私有；团队会话=+tenant 共享（两层） |
| 不可信内容 | email/webhook → SAFE 工具集（防注入升级成代码执行） |
