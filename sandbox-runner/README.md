# sandbox-runner

Sherpa 编码沙箱的 **自建执行镜像**（ADR-047 §决策4，负责人拍板 O-6）。
编排方是 `backend/app/sandbox/`（`runtime.py` + `transport.py`）。

> ⚠️ 本 README 曾描述"只挂该 user 的 workspace 卷"。**那已被 ADR-047 收窄推翻**：
> 沙箱**不挂任何宿主路径**，工作区通过 tar 注入。下面写的是今天真实成立的口径。

## 内容（v1 = python + pytest + ruff，别的一概没有）

| 有 | 版本 |
|---|---|
| python | 3.11.9 |
| pytest | 8.3.3 |
| ruff | 0.6.9 |

**故意没有**：`git`、`curl`/`wget`/`nc`、node/npm、网络、任何运行时安装能力。
容器断网（ADR-025），**永不装包**——缺东西就是一条明确的
`environment_missing_dependencies` 观察，不是一次静默下载。
镜像自带 `/opt/sherpa/capabilities.json`（并同步为 `sherpa.capabilities` label），
让"缺什么"能连"有什么"一起报给模型，而不是丢一个裸 exit 127。

## `/work` 是匿名卷（这一条是承重的）

容器以**只读 rootfs** 运行，所以 `/work` 必须是挂载点才可写。镜像里的
`VOLUME /work` 让 Docker 在创建容器时自动生成一个**匿名卷**：

* 编排方因此**不向守护进程传任何 mount、任何宿主路径**（ADR-047 §决策1）——
  这正是 backlog **B-8** 的结构性修复：旧代码把 *worker 容器内*的路径当
  `Mount(source=...)` 交给*宿主*守护进程解析，在 Windows + Docker Desktop 上必然失败；
* 匿名卷继承镜像里该目录的属主（uid/gid **10001**），所以非 root 用户写得进去；
* 随容器一起删除（`remove(force=True, v=True)`），节点上不留残留。

**已知偏差（如实记录）**：`docs/contracts/config-and-secrets.md §1.7` 写 `/work` 带
`nosuid,nodev`。Docker 只对 **tmpfs** 和 **bind** 暴露这两个 flag，**匿名卷没有**，
所以今天**没有设**。等效防护由 `cap_drop=ALL` + `no-new-privileges` + 非 root 承担
（setuid 提不了权，无 `CAP_MKNOD` 建不了设备节点）。不写成"已设"。

## 构建与固定（按 image ID digest）—— **强制，不是建议**

镜像是本地构建、**不推送**的，因此没有 registry `RepoDigests`——用 **image ID digest** 固定：

```powershell
docker build -t sherpa-sandbox-runner:dev sandbox-runner
docker image inspect sherpa-sandbox-runner:dev --format '{{.Id}}'
# 把输出的 sha256:... 写进 .env 的 SANDBOX_IMAGE
```

**运行时对此是 fail-closed 强制的**（`app/sandbox/runtime.py::verify_runner_image`，
在创建容器**之前**执行）。两道检查，但**它们的分量完全不同**：

1. **引用必须不可变（这是唯一的安全根）** —— `sherpa-sandbox-runner:dev` 这样的 **tag 会被直接拒绝**，
   返回具名出口 `runtime_image_untrusted`。tag 随时可以被重新指向别的字节，
   而"评审过的镜像"和"实际跑的镜像"必须是同一个。
   **`SANDBOX_IMAGE` 里那个由运维选定的 digest 就是本部署的信任根 / 白名单（且只有一项）。**
2. **标签必须像我们的 runner（这是**防手滑**，不是安全控制）** —— 必须带
   `org.opencontainers.image.title=sherpa-sandbox-runner`。
   ⚠️ **OCI 标签是普通镜像元数据，任何能构建镜像的人都能随便写，因此可伪造。**
   能让运维把恶意 digest 配进去的攻击者，同样能给那个 digest 写上这个标签。
   它的价值只在于：把"digest 粘错了"变成**一条清晰的拒绝**，而不是启动一个没有 `/work` 卷、
   没有 pytest/ruff、用户还是 root 的容器，在很后面以莫名其妙的方式失败。

> **我们不做、也不声称做**签名或 attestation 验证。**没有任何来源证明（provenance）被校验。**
> 真正的供应链验证（cosign / in-toto 式签名或 attestation，以及"谁有权签名"的策略）
> **明确不在 v1 范围内**，作为已知缺口记录在 `docs/contracts/config-and-secrets.md §1.7`，
> 归入 ADR-039 的生产 runner 工作（那条线本来就卡住多用户/不可信代码上线）。

因此 `SANDBOX_IMAGE` **没有可用的默认值**：全新 checkout 会明确失败，而不是"看起来能跑"
却在跑一个可变 tag 指向的未知镜像。

区分三种失败：**已固定但本地没有** → `runtime_image_missing`（断网沙箱不会去拉）；
**未固定 / 标签对不上** → `runtime_image_untrusted`；
**守护进程应答了但报错** → `runtime_transport_failed`（连不上才是 `runtime_daemon_unreachable`）。

引用形态：裸 image ID digest（`sha256:<64位小写hex>`）或仓库 digest
（`[主机[:端口]/]名字@sha256:<64位小写hex>`，**支持带端口的私有 registry**）。digest 部分校验从严。

> ⚠️ **勘误（2026-07-31）**：本节此前写 "worker 启动时会检查 `SANDBOX_IMAGE` 是否存在"。
> **没有这样的启动期预检**——检查发生在**每次执行前**，不是进程启动时。已按实际行为改写。

基础镜像也按 **registry digest** 固定（`python:3.11.9-slim-bookworm@sha256:8fb09919…`），
理由同上：上游每次安全重建都会把 tag 指向新字节。刷新时显式执行 `docker pull` +
`docker image inspect ... --format '{{index .RepoDigests 0}}'` 后改 Dockerfile。

## 运行约束（由编排方施加，见 `backend/app/sandbox/runtime.py`）

`network_disabled` · `cap_drop=ALL` · `no-new-privileges` · 非 root（10001）·
只读 rootfs + tmpfs `/tmp`（`nosuid,nodev`）· mem/pids/cpu 上限 ·
墙钟上限（`SANDBOX_RUN_TIMEOUT_SECONDS`）· `--rm` · **无任何密钥注入**。
`.env*` / `*.pem` / `*.key` / `id_*` / `.git/config` 在打包前就被拦下，永不进 tar。

**镜像不由 compose 构建。** `infra/docker-compose.yml` 里没有 `sandbox-runner` 服务，
`docker compose up --build` **不会**构建它——它是被 worker 通过 docker socket 启动的
兄弟容器，不是编排的一部分。改了本目录任何文件之后，必须手动重跑上面的
`docker build` 并**重新固定 digest**，否则跑的还是旧镜像（或者 digest 对不上而失败）。

## 不在本镜像范围内

生产级隔离（gVisor `runsc` / microVM、不共享 `docker.sock`、每租户出口与配额）
仍是 **ADR-039 的禁止上线条件**，本镜像**不解决**它。Node 是后续可选 profile（O-6）。

见 [../docs/05-tools-permissions-sandbox.md](../docs/05-tools-permissions-sandbox.md)、
[ADR-025/039/047](../docs/decisions.md)。
