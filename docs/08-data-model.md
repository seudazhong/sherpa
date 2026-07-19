# 08 · 数据模型

多租户 schema。**铁律：每张表都带 `tenant_id`**（行级隔离，Dify 多租户范式）；OAuth 令牌**加密落库**。

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

## 记忆 & 文件

```
memory_blocks(id, tenant_id, owner[user|tenant], label, value, limit, version)   ← Letta core
memory_passages(id, tenant_id, owner, text, embedding, tags, created_at)         ← 向量/archival(pgvector)
files(id, tenant_id, user_id, path, object_key, size, synced_from?)              ← blob 在 MinIO
```

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
