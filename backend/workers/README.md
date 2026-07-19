# workers

异步 job 消费者（Celery/arq）。**core 循环跑在这里，不在 web。**

## 职责
- 出队 run job → claim（session 锁：同会话串行）→ 跑 `core.run_core_loop`。
- 调沙箱 / 连接器 / 记忆。
- publish 归一化事件到 Redis 总线。
- 完成：持久化最终结果、滚动 token/成本、（如需）触发主动 push。

## 并发
- session 内串行（Redis 锁）；跨 session 并行（全局上限，如 main 4 / subagent 8）。
- **serialize in-session, parallelize across sessions.**

## 无状态
可水平扩，状态全在共享层。

见 [../../docs/03-runtime-async-jobs.md](../../docs/03-runtime-async-jobs.md)。
