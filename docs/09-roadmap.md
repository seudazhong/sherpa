# 09 · 分阶段路线图

> ⚠️ **本路线图已按[设计评审](reviews/README.md)重排（2026-07-19）**：原「P0核心→P1多租户→P2沙箱…」是**组件序**；评审改为**价值/风险序**，v1 范围见 [ADR-022](decisions.md)。下方为修订后的里程碑。

## v1 里程碑（价值/风险序，取代原 P0–P6）

**v1 = 自托管、单实例、单用户的 Gmail → Action 助理**（保留 Web 聊天为次要界面）。核心原则：**为真正上线的每个能力付全额安全/持久化成本；有风险的能力宁可砍掉，不做弱化版。**

| 里程碑 | 目标 | 出口标准（Exit） |
|---|---|---|
| **M1 · 契约与价值门** | 冻结 租户/事件/effect/调度/密钥 契约 + 受支持部署 profile；做 50–100 封邮件的脱敏抽取基准；可点击的 Candidate Inbox 原型 | 抽取精度足以证明值得接入真实 Gmail；目标用户理解「权限↔价值」交换 |
| **M2 · 个人 Inbox-to-Action alpha** | Postgres(+租户键) + durable jobs/events/outbox + 一个 provider + owner 引导 + demo 模式 + Gmail 只读 OAuth + 受限同步 + 候选三联(accept/edit/dismiss) + 来源可溯 + 去重 + 基础成本/反馈 + 暂停/断开/删除 | 真实用户独立得到有用候选；断线重连/重试可用；已接受项全部可溯源；effect 重放测试通过 |
| **M3 · 可信跟进（private beta）** | 已接受 todo + due/snooze + Web 收件箱 + 每日摘要 + 安静时段/配额 + 持久 schedule firing + 投递对账 + 连接器健康 + 导出 + 备份/恢复指引 + a11y 基线 | 达 PM 质量门：候选精度达标、零跨租户/未授权动作、无静默 job 失败、通知投诉可控、周度行动价值证据 |

**v1 明确排除**（各带 tracking issue，属后续里程碑，见 ADR-022）：代码执行/沙箱 · 文件/MinIO · GitHub · QQ/IM 入站 · agentic email · 团队/共享记忆 · memory/RAG/pgvector · 对外写动作 · 通用 cron · 多 provider failover · 跨渠道审批渲染器 · token 级流式打磨。

**M3 之后 · v1 之后里程碑（优先级序，项目负责人 2026-07-20 拍板；仍可按观测需求重排）**

> v1 收尾 = [M-tools（agent 工具面）](IMPLEMENTATION.md) + 审批闭环 + M3。之后按下表推进；该序体现「先把**单用户助理做强**（记忆/文件/代码），再扩**渠道**，最后上**多人**」。

| 序 | 里程碑 | 能实现什么 | 原需求 |
|---|---|---|---|
| 1 | **两层记忆 + RAG(pgvector)** | agent 跨会话长期记忆（user 私有；tenant 共享待多用户）+ 混合检索 | 铁律#6 |
| 2 | **个人文件存储 / MinIO** | 每用户持久 workspace，上传/同步文件与代码，agent 读写 | #4 |
| 3 | **代码执行 / 沙箱**（gVisor/Firecracker） | agent 改/跑代码/跑测试（最危险，前置：后端中立执行契约 + 隔离 + 出口策略 + 聚合配额 + 威胁评审） | #3 · ADR-007 |
| 4 | **QQ / IM bot 入站**（AstrBot/aiocqhttp） | 在 QQ 等 IM 里跟 agent 对话、收通知、批审批 | #10 |
| 5 | **agentic email**（agent 自有邮箱，ADR-013） | agent 拥有独立邮箱身份，自主收发 | #9 |
| 6 | **通用定时任务 cron** | 任意周期性自主任务/工作流（不止提醒） | #7 |
| 7 | **GitHub 连接器** | 同步 issue/PR/repo → 候选/待办 →（后）代码动作 | #6 |
| 其他（按需定序） | **多用户/团队协作** · **对外写动作 + 跨渠道审批渲染器** · **多 provider failover + 子 agent** · **评估飞轮增强 + 插件/skills/MCP + 流式打磨** | 多人共享；代表用户对外行动；模型容灾；可持续演进 | #2 #12 #11 |

**依赖提醒（不改顺序，仅标注给未来的执行者）**：① 记忆的「tenant 共享」层要等「多用户」（在"其他"）才完整——单用户阶段先做 user 私有记忆；② agentic email(5) 与 QQ 批审批(4) 会用到「跨渠道审批渲染器」（在"其他"），届时可能需把渲染器的一部分提前。

## 构建顺序细则（手册 Ch18 build sequence，映射到本项目）

> 注：以下为**长期完整**构建序；v1（M1–M3）只覆盖其子集——沙箱(7)、两层记忆的 tenant 共享部分(9)、多 provider failover(10)、IM/扩展(12) 均推迟出 v1（见 ADR-022）。

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
- [ ] 自治/定时动作：唯一 firing + outbox + 幂等投递（**at-least-once**），missed/failed/unknown 可见（ADR-017）。
- [ ] 依赖/发布 pinned、脚本门禁、打包制品冒烟测试。
