# channels

SURFACES 适配器：把各平台的入站/出站统一到 core 的事件词汇。

## 入站类型
- **Webhook/HTTP**（github、部分 IM）→ 打到 `api/`。
- **长连接监听**（QQ WebSocket、IMAP 轮询）→ 独立 listener 进程。

## 每个 channel 实现
- `parse_inbound(raw) -> Event`：归一化（含 `channel`/`scope`/`external_id`）。
- `send(external_id, message)`：出站（主动推送用）。

## 起步 channel
`web/rest` · `email`（含 agentic email） · `im/qq`（aiocqhttp/OneBot） · `webhook/github`。

## 铁律
- 消息类 surface = **不可信输入**。
- 出站走 `push()` 幂等 + at-most-once。

见 [../../docs/02-identity-session-memory.md](../../docs/02-identity-session-memory.md) · [../../docs/03-runtime-async-jobs.md](../../docs/03-runtime-async-jobs.md)。
