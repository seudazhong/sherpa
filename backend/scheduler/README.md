# scheduler

定时任务 + 主动推送。**单 leader**（Redis `SET NX` 选主）。

## at-most-once（可靠性命门）
每 ~60s tick，原子领取到期任务并**在同一事务推进 `next_run_at`**：
```sql
UPDATE schedules SET next_run_at=compute_next(spec,now()), lock_owner=:me
WHERE id IN (SELECT id FROM schedules
             WHERE next_run_at<=now() AND status='active'
             FOR UPDATE SKIP LOCKED LIMIT :batch)
RETURNING id;   -- 领到的 → 入队 job
```
支持 `duration/every/cron/ISO`；硬超时中断。

## 主动推送（幂等）
`push(user, message, idempotency_key)`：查 `sent_log` 去重 → `pick_channel`（QQ 在线优先，否则 agentic email）→ 发送 → 记录。

## 自治边界
读+建todo+通知 全自动；对外代表用户的动作走 `ask` 审批。

见 [../../docs/06-connectors-autonomy.md](../../docs/06-connectors-autonomy.md)。
