# 00 · 总览

## 定位

Sherpa 不是 local agent，而是一个**多租户云端 Agent 运行时（cloud agent runtime）**。

按《How to Develop an AI Agent》手册第 17 章，它是四个参考蓝图的**混血**：

| 借用蓝图 | 参考项目 | 拿什么 |
|---|---|---|
| **运行时 / 网关**（主干） | Hermes、OpenClaw | 网关托管多适配器、故障转移、共享预算、多会话存储、at-most-once 调度、主动推送、插件 |
| **应用平台多租户** | Dify | 多用户/工作区隔离、进程外沙箱执行不可信代码、连接器、SSRF 出口代理 |
| **记忆服务器** | Letta | 每用户记忆分层、agent = 持久实体、待办规划 |
| **多平台聊天机器人** | AstrBot | QQ（`aiocqhttp`）等 IM 适配器、洋葱管道、UMO 会话键 |

## 目标用户

- **个人**：一个随时待命、有记忆、能跑代码、能主动提醒你的私人助理。
- **小团队**：共享工作区、共享任务与记忆的协作助手。

个人与团队用**同一套 schema**（个人 = 单人工作区）。

## 需求清单（全部纳入设计）

| # | 需求 | 落到哪层 |
|---|---|---|
| 1 | Docker 一键部署 | [07 部署](07-observability-deployment.md) |
| 2 | 多用户登录 | [02 身份](02-identity-session-memory.md) |
| 3 | 沙箱：改/跑代码 | [05 沙箱](05-tools-permissions-sandbox.md) |
| 4 | 个人存储：上传/同步文件与代码 | [05](05-tools-permissions-sandbox.md) · [08](08-data-model.md) |
| 5 | 连接 Gmail 等邮箱 | [06 连接器](06-connectors-autonomy.md) |
| 6 | 同步 GitHub | [06](06-connectors-autonomy.md) |
| 7 | 设置并执行定时任务 | [06 调度](06-connectors-autonomy.md) |
| 8 | 分析连接器内容 → 智能生成/规划待办 | [06 旗舰 pipeline](06-connectors-autonomy.md) |
| 9 | agentic email（agent 自有邮箱） | [06](06-connectors-autonomy.md) |
| 10 | 登录 QQ 等 IM bot | [02 channels](02-identity-session-memory.md) · [03](03-runtime-async-jobs.md) |
| 11 | 主动发邮件/IM 通知 | [06 主动推送](06-connectors-autonomy.md) |
| 12 | 小团队协作 | [02 两层记忆](02-identity-session-memory.md) |

## 核心论点（贯穿全设计）

> **Agent = 包裹着"非确定性模型"的"确定性运行时"。** 模型负责决策，**我们的代码**负责循环、工具、上下文、状态和护栏。健壮性在代码里，不在 prompt 里。

## 六大铁律（后续所有文档反复引用）

1. 循环**永远有界**，每个退出路径都有**具名原因**。
2. 只在结构化 `stop_reason == tool_use` 时才执行工具（绝不正则解析文本）。
3. 调模型**前**先持久化用户输入；每轮用 history 的**副本**。
4. 上下文按易变性分层，稳定前缀做缓存；动态数据放尾部。
5. 工具输出**限长**、溢出到磁盘；错误当"观察"喂回模型。
6. 记忆 = RAM（上下文）+ Disk（外部存储），agent 用工具自己分页。
