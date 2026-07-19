# tools

工具接口 + 内置工具箱 + 权限闸。**内置/MCP/子 agent 共享一个执行路径**。

## 工具形状
`name` · `description`(prompt 工程) · `input_schema`(执行前校验) · `flags`(read_only/concurrency_safe/destructive) · `execute()→{llm_content, return_display}`。

## 四道闸
`REGISTERED → VISIBLE(check_fn+信任分级) → ALLOWED(策略) → EXECUTABLE(调用时授权)`

## 权限引擎
`allow|ask|deny`，last-match 胜，`deny>ask>allow`，默认 `ask`，每租户一套。`ask` = 异步 HITL（发 `permission.asked` 事件）。

## 起步工具箱（信任分级）
- SAFE：`read/glob/grep` · `todo_write` · `memory_*` · `web_fetch` · `ask_user`。
- FULL(+)：`write/edit` · `run_code/bash`(沙箱) · `connector_*` · `task`。

## 铁律
- schema 校验后执行；错误当观察。
- 输出限长（2000 行/50KB）溢出到文件。
- 并行只并行 proven-independent；输出顺序确定。

见 [../../docs/05-tools-permissions-sandbox.md](../../docs/05-tools-permissions-sandbox.md)。
