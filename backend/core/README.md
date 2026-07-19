# core

Agent 的心脏。跑在 Worker 里，消费 job，向 Redis 总线发事件。

## 组成
- **双循环**：外层 agency（有界迭代）+ 内层 resilience（classify→定向恢复）。
- **turn 状态机**：prologue → 装配 → 调模型 → 消费 → 执行工具 → 下一轮。
- **上下文组装**：分层缓存；记忆召回注入尾部；每轮用 history 副本。
- **压缩**：阈值触发、保 head+recent、verify 变小、不 orphan tool result。
- **流式事件**：`run.started / text-delta / tool-call / tool-result / turn.end / run.settled`。

## 铁律（方案 A）
- 递归/生成器 ReAct 循环 + **turn 粒度持久化**（崩溃从最后完成 turn 重跑）。
- 只在 `stop_reason == tool_use` 才执行工具。
- 每个出口具名终止；一切有界。

见 [../../docs/04-core-loop.md](../../docs/04-core-loop.md)。
