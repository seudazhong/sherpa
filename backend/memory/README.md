# memory

三层记忆（MemGPT / Letta）。context = RAM，memory = disk，agent 用工具自己分页。

## 层次
```
IN-CONTEXT (RAM)              EXTERNAL (DISK)
CORE memory (可编辑 block   ↔  RECALL memory (全消息历史，可搜)
 编译进 system prompt)      ↔  ARCHIVAL memory (向量库 passages, pgvector)
```

## 两层归属（本项目）
- **user 私有 block**：个人画像/偏好。
- **tenant 共享 block**：团队共享（编辑 rebuild 所有成员 prompt → 保持小）。

## 注入
记忆召回包成 `<memory-context>` 注入**当前用户消息（尾部）**，绝不进 system prompt（否则毁缓存）。

## 检索
hybrid：BM25(FTS) + 向量(pgvector KNN) + 时间衰减（可选 MMR 去重）。

## 自编辑工具
`core_memory_append/replace` 等；检查 `read_only`；持久化后 rebuild prompt；版本乐观锁。

见 [../../docs/02-identity-session-memory.md](../../docs/02-identity-session-memory.md)。
