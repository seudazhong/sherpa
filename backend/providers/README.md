# providers

模型 / provider 层。定义**一个窄接口**，让每个 provider 都遵从。

## 接口（参考 Gemini `ContentGenerator` / Letta `LLMClientBase`）
- `build_request(ctx)` · `stream(ctx)` · `count_tokens` · `to_internal_events(chunks)`。

## 分离三层（Pi）
- **model** = 元数据 + 能力。
- **provider** = 目录 / 认证 / 可用性 / 路由。
- **API adapter** = 线序列化 + 流解析。

## 职责
- 流式归一化成一套内部 event（绝不泄漏原始 chunk）。
- **分类失败 → 定向恢复**（rate-limit/5xx→fallback；overflow→压缩；401→换凭据）。
- failover 要**协调身份**（跨家族切换会改 system prompt）。
- **早加第 2 个 provider**（build sequence 第 1 步）。

见 [../../docs/04-core-loop.md](../../docs/04-core-loop.md)。
