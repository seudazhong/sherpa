# 10 · 技术栈锁定（Tech Stack Lock）

> 具体、可跟随的技术选型，是 [就绪套件](STATUS.md) 的锚点。这里锁定**框架、工具、包管理器**；精确版本以 `uv.lock` / `package-lock.json` 为准。本机已验证可用：`uv 0.11`、Python 3.11、Node 24 / npm 11、Docker 29。

## 后端（core + workers + api）

| 关注点 | 选型 | 说明 |
|---|---|---|
| 语言 | **Python ≥ 3.11** | 本机 3.11.15；`requires-python = ">=3.11"` |
| 包管理 / 运行器 | **uv** (`pyproject.toml` + `uv.lock`) | `uv sync` / `uv run`；本机已装，CI 用官方 action |
| Web 框架 | **FastAPI** + **uvicorn[standard]** | 异步、SSE/WS 原生 |
| ORM / 迁移 | **SQLAlchemy 2.0 (async)** + **asyncpg** + **Alembic** | 迁移单一 owner（ADR-012 修订/reviews §4.3） |
| 关系库 | **PostgreSQL 16** | 单实例单用户（ADR-015）；`tenant_id` 列保留，RLS 脚手架就绪、可后开 |
| 队列 / worker | **arq** + **redis**(redis-py asyncio) | ADR-017 推荐 arq；Redis = 队列 + Streams + 锁 |
| 配置 | **pydantic v2** + **pydantic-settings** | 分层 env（ADR-019 / contracts/config-and-secrets.md） |
| HTTP 客户端 | **httpx** | provider 适配 + 出站 |
| Provider（模型） | 内部 `Provider` 接口；**dev/test = mock/echo provider**；默认真实适配器 = OpenAI 兼容(httpx) | 首个真实 provider 待 §5 决策；不阻塞 M1 |
| 密钥加密 | **cryptography**（AES-GCM AEAD + KEK） | ADR-019 |
| 测试 | **pytest** + **pytest-asyncio** + httpx `ASGITransport` + **respx**(HTTP mock) + pytest-cov | 确定性；provider 用 mock |
| Lint/格式化 | **ruff**（lint + format） | 单一工具 |
| 类型检查 | **mypy**（渐进 strict） | |
| 向量 / RAG | **pgvector**（**推迟出 v1**，ADR-012/022） | v1 依赖不含 |
| 对象存储 | MinIO/S3（**推迟出 v1**） | files 出 v1 |

## 前端（web surface）

| 关注点 | 选型 | 说明 |
|---|---|---|
| 构建 / 框架 | **Vite + React 18 + TypeScript 5** | UI = core 事件流的**客户端**，无需 SSR → Vite SPA 最简（可后换 Next） |
| 包管理 | **npm**（本机已装；pnpm 未装） | |
| 路由 | **react-router-dom** | |
| 数据 | **@tanstack/react-query**（REST）+ 原生 **EventSource**（SSE） | |
| 样式 | 原生 CSS + 从 mockups 移植的 **Daybreak 设计 token**（CSS 变量） | 见 `design-bright/base.css` |
| Lint | **eslint** + **prettier** | |

> **前端方向决策**：选 **Vite SPA** 而非 Next——前端不含 agent 逻辑、无 SSR/SEO 需求，SPA + SSE/WS 订阅最贴合“UI 是流的客户端”铁律。若日后需要 SSR/多页 SEO 再评估 Next。

## Infra / 部署

- **docker compose**（`infra/docker-compose.yml`）：`postgres:16` · `redis:7` · `web`(uvicorn) · `worker`(arq) · `frontend`。**MinIO / pgvector 推迟**（v1 不起）。
- 配置经 env（`.env`，`pydantic-settings` 读取）；`.env.example` 为模板；secrets 分离（ADR-019）。
- 迁移：Alembic，单一 owner + 备份。

## CI

- **GitHub Actions**：
  - backend：`uv sync` → `ruff check` → `ruff format --check` → `mypy` → `pytest`。
  - frontend：`npm ci` → `npm run lint` → `npm run build`。
- 生成的客户端/契约漂移门禁（后置，reviews §4.3）。

## 规范命令（权威，写入 AGENTS.md）

```bash
# backend（在 backend/）
uv sync                      # 安装依赖
uv run uvicorn app.main:app --reload   # 起 web
uv run arq app.worker.WorkerSettings   # 起 worker
uv run pytest -q             # 测试
uv run ruff check . && uv run ruff format --check .   # lint
uv run mypy app             # 类型
uv run alembic upgrade head # 迁移

# frontend（在 frontend/）
npm ci && npm run dev / build / lint
```

> Windows 本机无 `make`；规范命令用原始 `uv` / `npm`。`Makefile` 仅作 Unix/CI 便捷封装。
