# infra

部署与运维。**docker-compose 一键部署**。

## 内容（待落地）
- `docker-compose.yml`：web · worker · scheduler · channels · sandbox-orch · postgres(+pgvector) · redis · minio · frontend（langfuse 可选）。
- `.env.example`：配置模板（secrets 分离，master key）。
- 迁移脚本（Alembic）+ 备份策略。

## 铁律
- 无状态 web/worker + 共享状态（PG/Redis/MinIO）→ scale-out-ready。
- scheduler 单 leader（Redis `SET NX`）。
- docker socket 只给 sandbox-orch。
- 配置分层：env + 文件；secrets 分离、`0600`、脱敏。

见 [../docs/07-observability-deployment.md](../docs/07-observability-deployment.md)。
