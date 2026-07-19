# 05 · 工具 / 权限 / 沙箱

> ⚠️ **评审修订（2026-07-19）**：本文的接口与长期设计继续有效；v1 范围与安全边界已按 [ADR-007、ADR-009、ADR-020](decisions.md) 收窄。下文「评审修订」注释优先于原有的起步范围/已锁定描述。

你"改/跑代码"的核心，也是**全项目最难、最危险的一环**（多租户代码隔离）。

## 工具接口（内置/MCP/子 agent 长一样）

```python
class Tool:
    name: str
    description: str          # 这是 prompt 工程——模型靠它决定何时调用
    input_schema: JSONSchema  # 执行前校验 + 发给 provider
    flags: {is_read_only, is_concurrency_safe, is_destructive}
    def execute(args) -> Result:
        # 两个输出：
        #   llm_content    → 给模型，精简
        #   return_display → 给用户，富文本
        ...
```

> 铁律：**执行前 schema 校验**；错误当"观察"喂回模型（不崩循环）；**输出限长**（2000 行 / 50KB，超了写文件、只回 head+tail + 路径）。

## 四道闸 + 权限引擎

```
REGISTERED → VISIBLE(本轮可见，check_fn+信任分级) → ALLOWED(策略) → EXECUTABLE(调用时授权)
```

**权限代数**：`allow | ask | deny`，**last-match 胜**，冲突 `deny > ask > allow`，默认 `ask`；**每租户一套策略**；DENY 先于一切从工具池剔除（看不见就没法滥用）。

**cloud 里 `ask` 是异步的**（复用事件总线）：

> **评审修订（2026-07-19）**：按 [ADR-020](decisions.md)，现在只冻结版本化的**语义审批信封**；在首个 `ask` 动作进入范围前，不实现任何 renderer/surface。v1 的 candidate 确认是独立业务工作流，**不是 approval**。

```
工具要跑 → evaluate → ask
   → emit "permission.asked"(带 correlation_id) → 任意 surface 渲染(Web卡片/QQ消息)
   → 等回复: once/session/always/reject
   → always 落 permissions 表; reject 连带 fail 本会话所有 pending
```

→ **一个远程 QQ 就能批准一个 Worker 里的写操作**，跨端一致。

## 起步工具箱（按信任分级发放）

> **评审修订（2026-07-19）**：按 [ADR-009](decisions.md)，连接器内容（email）改由专用 **`CONNECTOR_ANALYSIS` 无工具结构化抽取**能力处理：不提供通用 SAFE 工具、不读 workspace/memory、无副作用，且只产出 **candidate**。SAFE/FULL 工具集只用于已认证用户的交互式会话。v1 工具保持最小：`read/glob/grep`（只读）、candidate/todo、memory（user-private）、Gmail 只读 connector、`ask_user`；不含 `write`/`bash`/`task`，`run_code` 亦后置。

| 工具 | SAFE 集(email/webhook 等不可信入口) | FULL 集(web/QQ 已认证用户) |
|---|---|---|
| `read/glob/grep`（工作区内） | ✅ | ✅ |
| `todo_write` / `memory_*` | ✅ | ✅ |
| `web_fetch`（经 SSRF 代理） | ✅ 只读 | ✅ |
| `ask_user` | ✅ | ✅ |
| `write/edit`（工作区内） | ❌ | ✅（走权限闸） |
| **`run_code` / `bash`（沙箱）** | ❌ | ✅（走权限闸 + 沙箱） |
| `connector_*`（gmail/github） | ❌ | ✅ |
| `task`（子 agent） | ❌ | ✅ |

**工具集在 turn 开始时一次定死、中途不变**（Hermes）→ 保住 prompt 缓存；不可信内容永远拿不到 `bash`。

## 沙箱（多租户代码隔离）

> **评审修订（2026-07-19）**：按 [ADR-007](decisions.md)，**sandbox / `run_code` 移出 v1**。重新进入范围前，必须具备后端中立执行契约、gVisor/Firecracker（或专用节点）、出口策略、聚合配额，并完成威胁评审。

`run_code`/`bash` 工具 → 调 **Sandbox 服务**（独立进程）→ 起隔离容器执行。

**三个隔离维度 + 硬默认（全部 fail-closed）：**

```
① 文件系统: 只挂【该 user 的 workspace 卷】(rw)，别的一律不挂
             root 只读 + tmpfs /tmp，非 root 用户运行
② 网络:     默认 --network none(断网)
             需出网 → 只经 SSRF 代理(封 loopback/link-local/内网段)
③ 资源:     --cap-drop ALL, cpu/mem/pids-limit, 硬超时 kill
④ 密钥:     连接器令牌【绝不】进沙箱环境变量
```

输出经限长再回 Worker。这套 = 手册 Ch12 的"两级沙箱 + SSRF 代理 + 最小权限"。

## compute 模型（已锁定）

- **文件（workspace）** = 每用户持久（对象存储/命名卷）。
- **计算容器** = **ephemeral 每次一容器**：起容器 → 挂 workspace 卷 → 执行 → 销毁。干净、安全、简单（可用池预热降低启动开销）。
- **隔离后端** = **Docker-per-run 起步**（compose 友好）；真要跑不可信第三方代码再上 **gVisor/Firecracker**（可后加，不改上层）。
- 将来"跑 dev server"再加 persistent 会话容器。

## sandbox socket 安全

docker socket 只给 `sandbox-orch` 进程（它只跑我方可信代码）；不可信代码在它**派生的**隔离容器里，**不接触 socket**。
