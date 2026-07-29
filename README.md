# Sherpa

> 自托管的个人 AI Agent —— 为你背负，为你向导。

Sherpa 是一个 `docker compose` 一键启动的**个人 AI 助理**：一个会用工具的 agent 内核 + 一个 Web 界面。
它把你的邮件、文件、知识、项目和日程留在**你自己的机器**上，模型来源由你配置，所有对外动作（发邮件、改文件）都要经过你点头。

- **不是** SaaS，也不是聊天套壳：整个栈跑在你的 Docker 里，数据在你的 Postgres/MinIO 里。
- 当前版本（v1，[ADR-022](docs/decisions.md)）面向**单用户自托管**，主线是"Gmail → 行动"：只读邮件 → 生成带来源的候选待办 → 你确认 → 提醒/执行。
- 内核是一个**有界、可恢复**的 agent 循环：每一步先落库再执行，进程被杀也能接着跑，不会把副作用重放两次。

```
你 ──► Web UI ──► API ──► 事件日志(Postgres) ──► worker: agent 循环 ──► 工具/连接器
                    ▲                                    │
                    └────────── SSE 流式 ◄── Redis ◄──────┘
```

## 功能

| 能力 | 说明 | 页面 |
|---|---|---|
| 聊天 | 流式回复、工具调用可见、审批卡片、知识引用、**每个会话单独选模型** | `/` |
| Today | 从邮件抽取的待办候选、跟进事项、通知汇总 | `/today` |
| 审批 | 待审批的对外动作；可给可信对象（如某个收件人）**预授权** | `/approvals` |
| 会话库 | 历史会话浏览与搜索（支持中文分词），可恢复/重连/救回 | `/history` |
| 知识库 | 把网盘文件做成来源：解析→切块→向量+词法混合检索，回答带引用 | `/library` |
| 网盘 Drive | 个人文件：文件夹、上传、版本、回收站、配额 | `/workspace` |
| 项目 | 空白/模板/压缩包/GitHub 只读导入；agent 在一次性沙箱里改代码，改动交你评审 | `/work/projects` |
| 记忆 | 常驻的核心记忆块 + 语义笔记（本地嵌入模型，数据不出机器） | `/remember` |
| 定时任务 | cron/间隔/每周/每月触发一段提示词，看运行历史 | `/reminders` |
| 活动 | 事件回执、数据导出与删除 | `/data` |
| 消息 | QQ（官方机器人平台）、agent 自己的邮箱收发 | `/messaging` |
| 连接器 | Gmail 授权与同步、QQ 绑定 | `/integrations` |
| 设置 | 模型来源（OpenAI 兼容 / Anthropic / Gemini）、通知偏好 | `/preferences` |

> SPA 路由刻意避开了 API 代理前缀（`/sessions`、`/knowledge` 等被后端占用），所以页面名和路由不同名。

## 环境要求

| 用途 | 需要 |
|---|---|
| 一键运行（推荐） | Docker Engine / Docker Desktop + Compose v2；约 4 GB 内存、10 GB 磁盘 |
| 本地开发后端 | Python **3.11+** 与 [uv](https://docs.astral.sh/uv/)，外加一套 Postgres/Redis/MinIO（可用 compose 起） |
| 本地开发前端 | Node **18+** 与 npm |
| 真实模型（可选） | 一个 OpenAI 兼容端点（litellm/vLLM/OpenRouter/Ollama…）或 Anthropic / Gemini 的 key；不填则用离线 `mock` |
| 语义检索（可选） | `embeddings` profile 自带 ollama + bge-m3，首次拉取约 1.2 GB |

首次 `up --build` 会构建自带的 Postgres 镜像（pgvector + zhparser 中文分词），需要几分钟。

## 快速开始

```bash
git clone https://github.com/seudazhong/sherpa.git
cd sherpa
cp .env.example .env
```

编辑 `.env`，**至少**改这四项（`.env` 已在 `.gitignore` 中，不要提交）：

```ini
APP_SECRET=<随机字符串>
KEK=<base64 的 32 字节>      # 加密保险箱的主密钥，丢了等于所有已存凭据作废
OWNER_EMAIL=you@example.com
OWNER_PASSWORD=<你的登录密码>
```

生成 `KEK`：

```bash
openssl rand -base64 32                                              # Linux / macOS
python -c "import base64,os;print(base64.b64encode(os.urandom(32)).decode())"   # Windows PowerShell
```

起栈（Postgres + Redis + MinIO + 迁移 + web + worker + 前端）：

```bash
docker compose -f infra/docker-compose.yml --env-file .env up --build -d
```

打开 **http://localhost:5173**，用 `OWNER_EMAIL` / `OWNER_PASSWORD` 登录（不改就是默认的
`owner@localhost` / `sherpa-dev-password`）。API 在 http://localhost:8000。

**接上真实模型**：登录后进 **设置 → Models** 添加一个来源（选 `openai_compatible` / `anthropic` / `gemini`，
填 base URL + API key，拉取模型列表并设默认），然后在聊天右上角切换即可 —— 不用改配置文件、不用重启。
也可以在 `.env` 里设 `PROVIDER_KIND=openai_compatible` + `PROVIDER_BASE_URL` + `PROVIDER_API_KEY` 作为进程级默认。

可选组件：

```bash
# 本地嵌入模型（知识库 / 语义记忆需要）—— 两种摆法，二选一：
#
# (a) 宿主机 ollama（有显卡就用这个）：容器里的 ollama 默认吃不到宿主 GPU，
#     宿主直装能直接用显卡。宿主上执行 `ollama pull bge-m3`，并让它监听所有网卡
#     （`OLLAMA_HOST=0.0.0.0` 后重启 ollama —— 默认只绑 127.0.0.1，容器连不上），
#     然后 .env 里设 EMBEDDING_KIND=ollama、EMBEDDING_BASE_URL=http://host.docker.internal:11434，
#     不需要起 embeddings profile。
#
# (b) 自带 ollama 容器（零配置，纯 CPU）：.env 设 EMBEDDING_KIND=ollama、
#     EMBEDDING_BASE_URL=http://ollama:11434，首次启动拉 bge-m3 约 1.2 GB。
docker compose -f infra/docker-compose.yml --env-file .env --profile embeddings up -d

# 可观测：自带 Phoenix，看每次 LLM 调用的完整 prompt（UI http://localhost:6006）
# 并在 .env 设 OTEL_ENABLED=true、OTEL_EXPORTER_OTLP_ENDPOINT=http://phoenix:4317
docker compose -f infra/docker-compose.yml --env-file .env --profile observability up -d
```

常用运维：

```bash
docker compose -f infra/docker-compose.yml --env-file .env logs -f worker   # agent 循环日志都在 worker
docker compose -f infra/docker-compose.yml --env-file .env restart web worker
docker compose -f infra/docker-compose.yml --env-file .env down             # 停止（数据保留在卷里）
docker compose -f infra/docker-compose.yml --env-file .env down -v          # 连数据一起删
```

数据库迁移由 `migrate` 服务在每次 `up` 时自动执行（重复执行是空操作）。

## 本地开发

只用 Docker 起依赖，应用跑在宿主机上，方便热重载和断点：

```bash
docker compose -f infra/docker-compose.yml --env-file .env up -d postgres redis minio
```

后端（在 `backend/`，用 uv）：

```bash
uv sync                                   # 安装依赖
uv run alembic upgrade head               # 迁移（当前 head: 0031）
uv run uvicorn app.main:app --reload      # API，监听 :8000
uv run arq app.worker.WorkerSettings      # worker：agent 循环 + 定时任务 + outbox 中继，另开一个终端
```

本地跑时把 `.env` 里的 `DATABASE_URL` / `REDIS_URL` 指向 `localhost`（compose 内部用的是服务名）：

```ini
DATABASE_URL=postgresql+asyncpg://sherpa:sherpa@localhost:5432/sherpa
REDIS_URL=redis://localhost:6379/0
```

前端（在 `frontend/`）：

```bash
npm ci
npm run dev        # http://localhost:5173，API 请求默认代理到 http://localhost:8000
```

**web 和 worker 必须共用同一个 `KEK`** —— web 加密存下的凭据由 worker 解密使用。

## 构建与检查

提交前请跑通对应的门禁（与 CI 一致，见 `.github/workflows/ci.yml`）：

```bash
# backend/
uv run ruff check . && uv run ruff format --check .
uv run mypy app
uv run pytest                 # 需要 Postgres + Redis（MinIO 用于网盘相关用例）

# frontend/
npm run lint
npm run build                 # tsc -b && vite build

# 镜像
docker compose -f infra/docker-compose.yml build
```

> ✅ **测试套件与开发数据互相隔离**（[ADR-044](docs/decisions.md)，落地 backlog B-9）：`uv run pytest` 会自动使用
> 专用的 `<应用库>_test` 库、Redis 逻辑库 15 和一个合成 owner，因此**开着整套栈和 worker 直接跑即可**，
> 不会碰到你真实的模型来源、项目和会话。可用 `TEST_DATABASE_URL` / `TEST_REDIS_URL` 覆盖；
> 若因为已有的测试库缺少 `_sherpa_test_marker` 标记而拒绝启动，用 `SHERPA_TEST_DB_ADOPT=1` 收编一次，
> 或用 `SHERPA_TEST_DB_RESET=1` 重建。

## 配置速查

完整契约见 [`docs/contracts/config-and-secrets.md`](docs/contracts/config-and-secrets.md)，默认值见 [`backend/app/config.py`](backend/app/config.py)。

| 变量 | 默认 | 作用 |
|---|---|---|
| `APP_SECRET` / `KEK` / `KEK_ID` | dev 占位值 | 会话签名；凭据保险箱的 AEAD 主密钥（**生产必须改**） |
| `OWNER_EMAIL` / `OWNER_PASSWORD` | `owner@localhost` / `sherpa-dev-password` | 唯一用户的登录口令 |
| `DATABASE_URL` / `REDIS_URL` | compose 注入 | Postgres（真相源）/ Redis（队列 + 流） |
| `PROVIDER_KIND` | `mock` | `mock` / `openai_compatible`；进程级默认模型来源（UI 里配置的来源优先） |
| `PROVIDER_BASE_URL` / `PROVIDER_API_KEY` / `PROVIDER_MODEL` | — | 上者对应的端点、密钥、模型名 |
| `EMBEDDING_KIND` / `EMBEDDING_BASE_URL` | `mock` / `http://ollama:11434` | 嵌入模型（`ollama` 配 `embeddings` profile）；换模型=全量重嵌 |
| `STORAGE_KIND` / `MINIO_*` | `minio` | 网盘与项目快照的对象存储 |
| `SANDBOX_KIND` | `docker` | 代码执行沙箱；`disabled` 可完全关掉 |
| `EMAIL_KIND` / `AGENTMAIL_*` | `recording` | agent 自己的邮箱；默认只记录不真发 |
| `GMAIL_CLIENT_ID` / `GMAIL_CLIENT_SECRET` / `GMAIL_REDIRECT` | dev 占位值 | Gmail 只读 OAuth，需要你自己的 Google 凭据 |
| `OTEL_ENABLED` / `OTEL_EXPORTER_OTLP_ENDPOINT` | `false` / 空 | 追踪；关闭时零开销。`OTEL_CAPTURE_MESSAGE_CONTENT=true` 会把 prompt 写进 span（含隐私） |
| `DRIVE_QUOTA_BYTES` / `PROJECT_MAX_*` / `SCHEDULED_TASK_*` | 见 config.py | 配额与安全上限 |

密钥只从环境读取、加密落库、仅在调用边界解密，**绝不进日志、事件、prompt 或沙箱**（ADR-019）。

## 目录结构

```
backend/     Python 后端（uv）
  app/       实际代码：core 循环 · providers · tools · connectors · scheduler ·
             memory · knowledge · services · persistence · api · sandbox · observability
  tests/     pytest（默认 mock provider，不打真实模型）
  migrations/  alembic
  core/ providers/ tools/ … 各子系统的 README（设计说明，非代码）
frontend/    Vite + React + TS 单页应用（npm）
infra/       docker-compose、Postgres(pgvector+zhparser) 镜像、部署说明
docs/        架构设计、ADR 决策记录、冻结契约、UI 设计稿、状态与任务分解
```

## 文档

| 文档 | 内容 |
|---|---|
| [AGENTS.md](AGENTS.md) | 开发约定：命令、完成标准、提交规范、护栏（**动手前必读**） |
| [docs/00-overview.md](docs/00-overview.md) | 定位、目标与需求清单 |
| [docs/01-architecture.md](docs/01-architecture.md) | 四层架构、进程拓扑、窄腰原则 |
| [docs/04-core-loop.md](docs/04-core-loop.md) | agent 双循环、停止原因闸、上下文装配、恢复 |
| [docs/05-tools-permissions-sandbox.md](docs/05-tools-permissions-sandbox.md) | 工具接口、四道权限闸、沙箱隔离 |
| [docs/contracts/](docs/contracts/) | **冻结契约**：数据模型 · 事件与副作用 · API · 配置与密钥 |
| [docs/decisions.md](docs/decisions.md) | 全部架构决策（ADR）及理由 |
| [docs/11-agent-tool-surface.md](docs/11-agent-tool-surface.md) | agent 工具清单 + 能力矩阵（REST / 工具 / UI） |
| [docs/STATUS.md](docs/STATUS.md) | 实时进度：当前阶段、已完成、下一步 |
| [docs/backlog.md](docs/backlog.md) | 手工测试发现、尚未排期的问题 |
| [docs/09-roadmap.md](docs/09-roadmap.md) | 路线图与里程碑 |
| [docs/design-refined/](docs/design-refined/README.md) | 生产 UI 设计系统 `Quiet Work`（另有若干静态设计稿） |

## 项目状态

主线与多数扩展能力已经跑通并逐条端到端验证：core 双循环、Gmail→候选→待办、审批与预授权、记忆、网盘、
知识库、项目与变更评审、定时任务、QQ 与邮件渠道、可观测、多来源模型配置。

尚未做：GitHub 写回（推送 / PR）、多用户与团队协作、插件与 MCP、多 provider 故障切换与子 agent、评测飞轮。
最新进度以 [`docs/STATUS.md`](docs/STATUS.md) 为准。
