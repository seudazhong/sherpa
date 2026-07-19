# connectors

外部服务连接器。统一抽象：**auth + sync + tools**。

## 接口
- `auth`：OAuthToken（加密落库，DEK 包裹，绝不进沙箱，日志脱敏）。
- `sync(cursor) -> items`：增量拉取、归一化、入库。
- `tools`：暴露给 agent（`connector_gmail_search` 等）。

## 起步连接器
- `gmail`：读用户邮箱（OAuth 最小 scope、只读优先）。
- `github`：同步仓库/issue/PR。
- `agentic_email`：agent 自有邮箱（收指令/发通知/收回复）。

## 两种用法
- **Pipeline 预注入**：定时 sync → 分析 → 建 todo（旗舰功能）。
- **Retrieval-as-tool**：agent 按需搜索。

## 铁律
- 账户访问可信 ≠ 内容可信 → 入站内容走 SAFE 工具集。
- 先只读，写操作走权限闸。

见 [../../docs/06-connectors-autonomy.md](../../docs/06-connectors-autonomy.md)。
