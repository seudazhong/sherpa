# 08 · 数据模型

> **评审修订说明（2026-07-19）**：本页保留长期 schema 设计；已确认的 v1 单用户简化与一次性门契约见下方修订（依据 [ADR-015~022](decisions.md)）。

多租户 schema。**铁律：每张表都带 `tenant_id`**（行级隔离，Dify 多租户范式）；OAuth 令牌**加密落库**。

> **评审修订（2026-07-19）**：按 [ADR-015](decisions.md)，v1 为单实例、单 owner；仍保留 `tenant_id` 列与复合键，但强制 RLS、最小权限数据库角色与 KMS 推迟到团队/托管里程碑，启用团队/托管前必须补齐。

## 身份 & 租户

```
tenants(id, name, kind[personal|team], created_at)
users(id, email, pw_hash, default_workspace, ...)
memberships(user_id, tenant_id, role[owner|member])
identities(id, tenant_id, user_id, channel, external_id, verified)   ← 身份链接
```

## 会话 & 转录（append-only）

```
sessions(id, tenant_id, umo_key, user_id?, channel, persona, model_override, status,
         token_rollup, cost_rollup)
messages(id, session_id, role, seq, created_at)
parts(id, message_id, kind[text|reasoning|tool|file|step], content)   ← 拆分便于列表
```

> `admitted_seq` / `promoted_seq`：durable prompt admission（入库即受理，coordinator 提升为可见历史）。

## 能力状态

```
permissions(id, tenant_id, scope, pattern, effect[allow|ask|deny])    ← 保存的授权规则
connectors(id, tenant_id, user_id, kind[gmail|github|agentic_email],
           token_enc, scopes, status, cursor)                         ← 加密 + 增量游标
schedules(id, tenant_id, spec[cron|every|iso], next_run_at, lock_owner, status)  ← at-most-once
sent_log(id, tenant_id, idempotency_key, channel, sent_at)            ← 主动推送幂等
sandbox_runs(id, tenant_id, session_id, image, exit_code, started_at)
```

> **评审修订（2026-07-19）**：按 [ADR-019](decisions.md)，`connectors.token_enc` 在 v1 必须落实为逐记录 AEAD + 可轮换 KEK：OAuth callback 当场加密，仅连接器可解密，日志全程脱敏；字段契约见下节。

> **评审修订（2026-07-19）**：按 [ADR-017](decisions.md)，上表 `schedules` 的全局 at-most-once 语义已废止；改为持久化唯一 firing + outbox + at-least-once worker + 幂等/对账投递，显式暴露 missed/failed/unknown。

## 评审新增契约表（v1 一次性门，ADR-016~021）

以下是评审要求在 P0 前冻结、从首批真实数据起即保留的契约；业务当前态仍可放普通表，不要求把整套系统改成纯事件溯源。

### 事件日志 + outbox（ADR-016）

```
event_journal(id, tenant_id, session_id?, run_id?, session_seq?, run_seq?, event_type,
              envelope_version, payload_redacted, payload_size, created_at)       ← append-only
outbox(id, tenant_id, event_id, topic, delivery_key, status[pending|delivered|failed],
       attempts, available_at, delivered_at?)
```

- `(tenant_id, session_id, session_seq)` / `(tenant_id, run_id, run_seq)` 唯一；信封版本化，payload 必须有界、脱敏。
- event journal + 事务性 outbox 是恢复、重放、投影、流式的真相源；Redis Streams 只加速投递。

### 副作用 invocation + 调度 firing（ADR-017）

```
effect_invocations(invocation_id, tenant_id, run_id, idempotency_key, effect_class,
                   args_hash, outcome[succeeded|failed|effect_unknown], attempts,
                   reconciliation_state?, created_at, settled_at?)
schedule_firings(id, tenant_id, schedule_id, firing_key, scheduled_for,
                 status[pending|running|succeeded|missed|failed|unknown],
                 attempts, delivery_outcome[succeeded|missed|failed|unknown], invocation_id?)
```

- 每个副作用执行前先持久化 invocation；`(tenant_id, idempotency_key)` 与 `(tenant_id, schedule_id, firing_key)` 唯一。
- firing 走 outbox + at-least-once worker + 幂等投递；`effect_unknown` / `unknown` 停下对账，不盲重试、不静默丢失。

### 候选 + 来源 provenance（ADR-018）

```
connector_items(id, tenant_id, connector_id, provider_item_id, revision,
                source_deleted_at?, deletion_state, fetched_at)
extractions(id, tenant_id, connector_item_id, extraction_version, generation_id, created_at)
candidates(id, tenant_id, extraction_id, generation_id, dedupe_key,
           status[pending|accepted|edited|dismissed], accepted_todo_id?, created_at)
candidate_provenance(id, tenant_id, candidate_id, connector_item_id, connector_revision,
                     extraction_version, generation_id, accepted_todo_id?)
```

- 稳定保存 connector item/revision → extraction version → generation → candidate → accepted todo 全链；`dedupe_key` 有租户内唯一约束。
- 来源删除只记删除语义/时间并触发对账，不破坏既有 provenance、反馈与审计链。

### 审批信封（ADR-020）

```
approval_envelopes(id, tenant_id, envelope_version, correlation_id, run_id, invocation_id,
                   args_hash, policy_version, expires_at, nonce, preview_redacted,
                   authorized_decider, decision?, decided_at?)
```

- 这是现在冻结、首个 `ask` 动作入围后才实现渲染器的契约；精确绑定 tenant/run/invocation/参数/policy/有效期/nonce，`correlation_id` 与 nonce 唯一。

### 审计回执（ADR-021）

```
audit_receipts(id, tenant_id, receipt_version, receipt_type, run_id?, invocation_id?,
               subject_ref?, summary_redacted, outcome, occurred_at, created_at)  ← append-only
```

- 只存稳定、脱敏的语义回执；与 schema 易变、可能含敏感数据的 debug/telemetry 事件分离。

### 连接器密钥信封（ADR-019）

```
connector_credentials(id, tenant_id, connector_id, ciphertext, nonce, aad_version,
                      algorithm, encrypted_dek, kek_id, key_version, rotated_at?)
```

- 每条凭据独立 AEAD；`kek_id` / `key_version` 支持 KEK 轮换，明文令牌不得进入日志、事件或 outbox。

## 记忆 & 文件

```
memory_blocks(id, tenant_id, owner[user|tenant], label, value, limit, version)   ← Letta core
memory_passages(id, tenant_id, owner, text, embedding, tags, created_at)         ← 向量/archival(pgvector)
files(id, tenant_id, user_id, path, object_key, size, synced_from?)              ← blob 在 MinIO
```

> **评审修订（2026-07-19）**：按 [ADR-012/022](decisions.md)，`memory_passages`（pgvector/RAG）与 `files`（MinIO）均推迟到 post-v1；此处只保留长期 schema，不属于 v1 迁移或受支持部署。

## 待办（复用现有 todos，加多租户）

```
todos(id, tenant_id, user_id?, title, description, status, source[manual|gmail|github], due, ...)
todo_deps(todo_id, depends_on)
```

## 遥测

```
traces(id, tenant_id, session_id, user_id, tags)
generations(id, trace_id, model, in_tok, out_tok, cost, prompt_version, latency)
scores(id, trace_id?, observation_id?, kind[eval|annotation|api|feedback], value)
```

## 隔离与索引要点

- 每张表 `tenant_id` 建索引；查询默认带 `tenant_id` 过滤（可用 RLS 兜底）。
- `sessions.umo_key` 唯一（每租户），是"万能 per-conversation join 键"。
- `identities(channel, external_id)` 唯一，供 `resolve_inbound` 反查。
- `schedules(next_run_at, status)` 索引，供调度器高效领取。
