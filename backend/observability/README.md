# observability

trace / 成本 / 事件投影。**大部分白嫖自 core 的事件流**（事件溯源即观测原语）。

## 数据模型
```
TRACE(tenant_id·user_id·session_id·tags)
  ├─ generation → model·tokens·cost·latency·prompt_version
  ├─ tool / retriever
  └─ score ×N (eval|annotation|api|feedback)
```

## 职责
- 从事件流投影到 `traces`/`generations`/`scores` 表。
- 成本滚进 `sessions.cost_rollup`；子 agent 成本→父。
- 每 generation 记 prompt 版本。

## 演进
先用自己的表；专业化再接 Langfuse（TS+docker，同栈）。评估飞轮后置。

见 [../../docs/07-observability-deployment.md](../../docs/07-observability-deployment.md)。
