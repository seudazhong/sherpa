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

## 构建与固定（按 image ID digest）

镜像是本地构建、**不推送**的，因此没有 registry `RepoDigests`——用 **image ID digest** 固定：

```powershell
docker build -t sherpa-sandbox-runner:dev sandbox-runner
docker image inspect sherpa-sandbox-runner:dev --format '{{.Id}}'
# 把输出的 sha256:... 写进 .env 的 SANDBOX_IMAGE
```

worker 启动时会检查 `SANDBOX_IMAGE` 是否存在；不存在则该次执行返回
`runtime_image_missing`（断网沙箱不会去拉）。

## 运行约束（由编排方施加，见 `backend/app/sandbox/runtime.py`）

`network_disabled` · `cap_drop=ALL` · `no-new-privileges` · 非 root（10001）·
只读 rootfs + tmpfs `/tmp`（`nosuid,nodev`）· mem/pids/cpu 上限 ·
墙钟上限（`SANDBOX_RUN_TIMEOUT_SECONDS`）· `--rm` · **无任何密钥注入**。
`.env*` / `*.pem` / `*.key` / `id_*` / `.git/config` 在打包前就被拦下，永不进 tar。

## 不在本镜像范围内

生产级隔离（gVisor `runsc` / microVM、不共享 `docker.sock`、每租户出口与配额）
仍是 **ADR-039 的禁止上线条件**，本镜像**不解决**它。Node 是后续可选 profile（O-6）。

见 [../docs/05-tools-permissions-sandbox.md](../docs/05-tools-permissions-sandbox.md)、
[ADR-025/039/047](../docs/decisions.md)。
