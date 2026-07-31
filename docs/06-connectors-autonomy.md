# 06 · 连接器 / 自治

> ⚠️ **评审修订（2026-07-19）**：本文的 connector / scheduler / push 长期模式继续有效；v1 **仅含 Gmail 只读连接器**，自治边界与调度交付语义已按 [ADR-010、ADR-011、ADR-017](decisions.md) 修订。下文「评审修订」注释优先于原有范围/已锁定描述。

你需求里最"云助理"的部分。

## 连接器统一抽象（gmail/github/agentic-email 长一样）

> **评审修订（2026-07-19）**：按 [ADR-022](decisions.md)，本文以下 GitHub connector/webhook/issue 均为长期设计示例；**GitHub 连接器推迟到后续里程碑**，不在 v1。

```python
class Connector:
    kind; tenant_id; user_id
    auth: OAuthToken            # 加密落库(DEK 包裹)，绝不进沙箱，日志脱敏
    def sync(cursor) -> items   # 增量拉取(按 cursor)，归一化，入库
    tools: [connector_gmail_search, connector_github_pr, ...]   # 暴露给 agent
```

**两种用法都要**：

- **Pipeline 预注入**：定时 `sync()` 拉数据 → 分析 → 建 todo（旗舰功能，见下）。
- **Retrieval-as-tool**：agent 循环中按需调 `connector_gmail_search`（相关性不确定时自己搜）。

同步先落本地库（供 todo + RAG），工具供按需查询。**先只读，写操作后加且走权限闸。**

## 信任分级：两种"邮箱"是两回事

> **评审修订（2026-07-19）**：按 [ADR-013](decisions.md)，**agentic email 移出 v1 与导航**；v1 只使用普通出站 **digest email**。用户的 Gmail 只读连接器仍保留在 v1。

| | 用户真 Gmail（连接器） | **agentic email**（agent 自有邮箱） |
|---|---|---|
| 本质 | 读**用户的**账户数据 | agent **自己的**通信身份 |
| 授权 | OAuth，最小 scope，只读优先 | 供应商发的专用账号 |
| 用途 | 抽取待办、检索 | 收指令 / 发通知 / 收回复 |
| 隔离性 | 高隐私，谨慎 | 天然隔离，不碰用户私人号 |
| **内容信任** | ⚠️ 正文不可信→SAFE 工具集 | ⚠️ 同样不可信→SAFE 工具集 |

要点：**账户访问可信 ≠ 内容可信**。两者处理入站内容都只给 SAFE 工具集（防注入升级成代码执行）。

## 调度器（at-most-once 是可靠性命门）

> **评审修订（2026-07-19）**：按 [ADR-011/ADR-017](decisions.md)，以 **at-least-once** 取代 at-most-once：持久化唯一 **firing + outbox**，由至少一次 worker 执行，并对投递做幂等/对账。每个 job 单独定义漏发与重复策略（digest 偏向不重复；重要 reminder 偏向 eventual delivery）；必须显式呈现 **missed/failed/unknown**，绝不静默丢弃。

Leader（Redis `SET NX` 选主）每 ~60s tick，**原子领取 + 先推进游标**：

```sql
UPDATE schedules
SET next_run_at = compute_next(spec, now()),      -- ① 先推进游标(崩了也不重触发)
    lock_owner = :me, last_claimed = now()
WHERE id IN (
  SELECT id FROM schedules
  WHERE next_run_at <= now() AND status='active'
  FOR UPDATE SKIP LOCKED                            -- ② 不双领
  LIMIT :batch
) RETURNING id;                                      -- ③ 领到的 → 入队 job
```

支持 `duration / every / cron / ISO`；硬超时中断。**在领取的同一事务里推进 `next_run_at`** = at-most-once，即使 worker 崩溃也不会对同一时隙重发。

## 主动推送（出站，入站的镜像 + 幂等）

```python
def push(user, message, idempotency_key):
    if sent_log.exists(idempotency_key): return          # 幂等，防重发
    ch = pick_channel(user)        # 偏好+可用性: QQ 在线? → QQ; 否则 agentic email
    identity = identities.of(user, ch)
    channels[ch].send(identity.external_id, message)
    sent_log.record(idempotency_key)
```

调度器 at-most-once + 幂等键 = **绝不给用户重复发 10 封邮件**。

## 旗舰功能：分析邮件 → 生成待办 → 主动通知

一条**自治 pipeline**（调度器触发的 mini-agent run）：

```
定时 job(每 N 分钟):
  1. connector_sync() → 拿到自上次 cursor 后的新邮件/issue
  2. 逐条: 跑 core 循环(SAFE 工具集 + "抽取可执行待办" prompt)
  3. 建 todos(source=gmail/github, 按 external_id 去重)
  4. 高优先级 → push(user) 主动通知
```

把**连接器 + core 循环 + todos + 主动推送 + at-most-once** 全串起来。

## 自治边界（已锁定，autonomy ladder）

> **评审修订（2026-07-19）**：按 [ADR-010](decisions.md)，改为 **candidate-first**：连接器内容只自动创建 **candidate**；正式 todo 必须由用户 accept/edit。通知须 opt-in 并受策略门控（quiet hours/caps）。

| 动作 | 默认 | 理由 |
|---|---|---|
| 建 todo（连接器内容） | ✅ 自动 | 低风险，可撤销 |
| 发**通知**（QQ/邮件告知） | ✅ 自动 | 纯告知性 |
| **代表用户发邮件/回复/建 issue** | ⚠️ **ask 审批** | 高风险，对外可见 |
| 沙箱写/跑代码 | ⚠️ 走权限闸 | 见 05 |

**边界：读+建todo+通知 全自动；任何"对外代表用户"的动作走审批**（复用异步 HITL）。

## 同步方式

**先轮询**（简单）；将来再上 Gmail Pub/Sub / GitHub webhook 推送（可后加不改架构）。

## 密钥处理

OAuth 令牌：AES-256 加密落库、**DEK 包裹**（master key 从 env/KMS 取）、每连接器一行；**绝不**进沙箱环境变量；日志脱敏；OAuth refresh 由连接器透明处理。
