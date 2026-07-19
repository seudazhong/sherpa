# gateway

多租户前门。**所有入站事件汇入这里**。

## 职责
- 认证 / 会话校验。
- **身份链接**：外部 ID（qq:12345 / email:z@x.com）→ user（`identities` 表）。
- 租户解析：DM→`users.default_workspace`；群→群绑定工作区。
- 事件归一化：各 channel 原始事件 → 一套统一 event 词汇。
- 构造 UMO 会话键 `channel:type:external_id`。
- 每租户限流；路由到 core（入队异步 job）。

## 核心函数
`resolve_inbound(event)` —— "一人多入口"的心脏。见 [../../docs/02-identity-session-memory.md](../../docs/02-identity-session-memory.md)。

## 铁律
- 上下文装配是**信任边界**：不可信内容 → SAFE 工具集。
- 调模型前先持久化输入（durable admission）。
