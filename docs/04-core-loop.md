# 04 · core 循环内部

> **评审修订（2026-07-19）**：双循环、stop-reason 闸与 turn 粒度持久化的核心设计不变；[ADR-017](decisions.md) 补强了副作用与崩溃恢复契约。

手册第 4 章的"心脏"。跑在 Worker 里，消费一个 job，向 Redis 总线发事件。

## 双循环骨架（外层管 agency，内层管 resilience）

```python
def run_core_loop(job):
    session = load(job.session_id)          # 输入已在 web 层持久化(durable admission)
    budget  = Budget(max_turns=50, max_tokens=…, max_tool_calls=…)   # ① 一切有界
    emit("run.started", session)

    while budget.turns_left and not interrupted(session):     # ═══ OUTER: 决策 ═══
        ctx = assemble_context(session)                       # ④ 每轮重建"副本"，不改存储

        try:
            reply = call_model_resilient(ctx)                 # ↓ INNER
        except Unrecoverable as e:
            return settle(session, "failed", e)

        persist(session, reply)                               # ② 先落库再动作
        if reply.stop_reason != "tool_use":                   # ③ stop-reason 闸
            return settle(session, "completed", reply.text)   #    只在 tool_use 才执行工具

        results = execute_tools(reply.tool_calls, session)    # 权限闸 + 并行/串行 + 限长
        persist(session, results)
        drain_steering(session)                               # HITL: 安全边界 drain steer
        budget.consume()

    return settle(session, "stopped:budget", grace_call(session))   # ⑤ 具名终止 + grace
```

```python
def call_model_resilient(ctx):                    # ═══ INNER: 可靠性 ═══
    for attempt in bounded(3):                     # classify → 定向恢复，不盲重试
        try:
            return stream_model(ctx)               # 流式：边收边 emit text-delta/tool-call
        except (RateLimit, ServerError): activate_fallback(); continue
        except ContextOverflow:        ctx = compact(ctx); continue   # bounded ≤2-3
        except AuthError:              rotate_creds(); continue
    raise Unrecoverable
```

外层对 provider 故障**无感知**——这就是分双循环的意义。

## turn 顺序（Hermes `build_turn_context` / OpenCode `runTurnAttempt`）

1. **Prologue**（每轮一次）：净化输入；恢复/构建 system prompt；**持久化用户消息**（在 web 层已做）；超阈值预压缩；预取记忆一次。
2. **装配请求**：构建 history 的**每轮副本**（绝不改存储的 transcript）；注入临时 system + 记忆召回到当前消息；打缓存断点；清理孤儿 tool result。
3. **调模型**（内层）：流式；分类失败；恢复。
4. **消费回复**：stop-reason 闸。
5. **执行工具**：并行/串行；追加结果。
6. **准备下一轮**：drain steer；查中断；扣预算。

## 上下文装配（分层缓存 + 两层记忆注入尾部）

```
┌ STABLE PREFIX   角色/mandates · 工具描述 · 风格 · skills 索引   → 缓存(字节稳定)
│ CONTEXT FILES   tenant persona / 指令
│ SESSION-STABLE  日期 · 模型 · env · 记忆索引
├──────── CACHE BOUNDARY ────────
└ MUTABLE TAIL    transcript · <memory-context> · 本轮输入          → 每轮变
```

> 铁律：动态数据走尾部；prefix 字节稳定（sorted JSON + 确定性 tool-call ID）→ 命中缓存省 ~75% 输入成本。记忆召回注入尾部当前消息，**绝不进 system prompt**。

> 实现：SESSION-STABLE 层由 [`backend/app/core/session_context.py`](../backend/app/core/session_context.py) 渲染（日期到「天」粒度、surface 标签、绑定的 project），在 `core/loop.py` 里按「全局 prefix → 每用户 core memory → 每会话 ambient」拼进 system message——共享度越高的层越靠前，跨会话前缀才可复用（backlog B-3）。

## 流式事件词汇（emit → Redis → SSE，一套通吃 UI + 可观测）

```
run.started → [text-delta · reasoning-delta · tool-call · tool-result · tool-error]*
            → turn.end → … → run.settled
```

- **UI 是这条流的客户端**（前端订阅渲染）。
- **同一条流也是可观测性数据源**（事件溯源天然产出 trace，见 07）。
- `turn.end ≠ run.settled`：settled 只在 retry/compaction/排队续跑**全部结束**后才发。

## 护栏（都要，且都有界）

| 机制 | 规则 |
|---|---|
| **具名终止** | 每出口有名字：completed / stopped:budget / failed / interrupted；优先级 `timeout>aborted>failed>completed` |
| **grace call** | budget=0 时允许**最后一次**调用让模型收尾 |
| **压缩** | 阈值(如 window 65%)触发 → 保 head+recent → **verify 真变小**(膨胀就拒绝) → 不 orphan tool result → 写新 epoch，session 身份不变 |
| **中断/steer** | interrupt: 别的入口在 Redis 设 flag → 循环顶部检查 → 干净退出+持久化；steer: 排队，当前工具批后、下次调模型前 drain；**两队列分开** |
| **无进展停机** | 重复相同 tool call / doom-loop → 警告再停 |

## 崩溃恢复：方案 A（已锁定）

**turn 边界持久化**：每个 turn 的 messages/results 落库；Worker 崩溃 → 从**最后一个完成的 turn** 重跑。用简单的递归/生成器 ReAct 循环，够健壮又不过度工程。将来要 intra-turn 可恢复（方案 B，显式状态机）再升级。

> **评审修订（2026-07-19）**：按 [ADR-017](decisions.md)，turn 粒度恢复可能**重新执行工具**，因此每个副作用都必须具备**幂等键 + effect 分类**；遇到 `effect_unknown` 时停下对账，**绝不盲目重试**。
