# api

FastAPI 入口。**无状态**，可水平扩。web 永不跑 core 循环。

## 职责
- 认证 / 登录。
- REST：会话、todos、文件、连接器、设置。
- **SSE/WS**：订阅 Redis 事件总线 → 实时回推 core 事件给前端。
- 入站 webhook（github 等）→ `gateway.resolve_inbound` → 入队。
- 入队 run job → 立即返回 202/ack（不阻塞）。

## 铁律
- 一个 core，多 surface：前端 + channels 都是本 API 的客户端。
- 即使本地也要加 auth（loopback 也不裸奔）。

见 [../../docs/03-runtime-async-jobs.md](../../docs/03-runtime-async-jobs.md)。
