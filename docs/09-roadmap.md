# 09 · 分阶段路线图

按**租户→沙箱→连接器→自治→通道→运维**逐级降险。每阶段都能独立跑通、可演示。

| 阶段 | 目标 | 产出 | 主要文档 |
|---|---|---|---|
| **P0 核心** | 手册那 40 行有界循环 + provider 层 + 工具接口 + SQLite | 单用户、一个 REST 端点跑通对话+工具 | [04](04-core-loop.md) [05](05-tools-permissions-sandbox.md) |
| **P1 多租户骨架** | docker-compose + 登录 + 每用户存储 | web+worker+PG+Redis+MinIO；工作区隔离；文件上传/同步 | [02](02-identity-session-memory.md) [03](03-runtime-async-jobs.md) [07](07-observability-deployment.md) |
| **P2 代码沙箱** | ephemeral 隔离容器、断网默认、挂 workspace 卷 | 能安全改/跑代码 | [05](05-tools-permissions-sandbox.md) |
| **P3 连接器** | GitHub + Gmail（只读优先）→ 生成 todos | "分析邮件→规划待办" 旗舰 pipeline | [06](06-connectors-autonomy.md) |
| **P4 调度+主动推送** | at-most-once cron + agentic email/IM 出站 | 定时任务 + 主动通知 | [06](06-connectors-autonomy.md) |
| **P5 IM 入站** | QQ 等，归一化事件 + 身份链接 | 多入口同一身份（一人多入口闭环） | [02](02-identity-session-memory.md) |
| **P6 可观测+评估** | trace/成本/回归数据集 | 生产就绪 | [07](07-observability-deployment.md) |

## 构建顺序细则（手册 Ch18 build sequence，映射到本项目）

1. 选模型 + provider 接口（**早加第 2 个 provider**）。
2. 有界双循环。
3. 工具接口 + 起步工具箱（内置/MCP/子agent 同接口）。
4. 上下文工程（分层 + 缓存 + context file）。
5. 持久化状态（Postgres/session 树；**调模型前先持久化**）。
6. 流式 typed 事件（UI = 流的客户端）。
7. 权限 + 沙箱（**上真机 shell/write 前必须**）。
8. 压缩（阈值、保护 head+recent、verify）。
9. 记忆（两层：user 私有 + tenant 共享 + hybrid search）。
10. 子 agent/ensemble + failover（共享预算；classify-and-recover）。
11. 埋点 + 评估飞轮（trace/spans/scores；goldens；回归数据集；pinned 实验）。
12. 扩展性（插件/hooks/skills/MCP；两遍信任；footprint ladder）。
13. 部署（一个 core 藏在本地协议后；web+workers；at-most-once 调度）。

## 生产就绪清单（发布前逐项打勾）

- [ ] 每个循环/恢复有界；每个出口具名。
- [ ] 用户输入在首次调模型前持久化；崩溃可恢复。
- [ ] 工具执行 gated on stop reason；输出限长 & 溢出。
- [ ] prompt 前缀字节稳定 & 缓存；动态数据在尾部。
- [ ] 压缩按阈值、保留 recent、verify。
- [ ] 权限引擎 gate 每个变更类工具；全自动需沙箱。
- [ ] secrets 分离、加密/keystore、`0600`、脱敏。
- [ ] provider 带分类的 failover；共享配额有守卫。
- [ ] 子 agent 共享一个预算，隔离运行。
- [ ] 每次 run 产出带 cost/tokens/prompt-version 的 trace。
- [ ] 自治/定时动作 at-most-once。
- [ ] 依赖/发布 pinned、脚本门禁、打包制品冒烟测试。
