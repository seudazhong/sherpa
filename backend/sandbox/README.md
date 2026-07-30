# sandbox

代码执行编排。项目命令（`app/services/project_sandbox.py`）调这里起隔离容器；本目录同时是**具名终止原因**（events §2.11 ④）的唯一词表。通用 `run_code` 片段执行器已**删除**（ADR-045 clean break / ADR-048 O-12），临时执行改由 `runtime.open(scope="ephemeral")` + `sh.exec` 承载（Phase TR P4）。

## 模型（已锁定）
- **ephemeral 每次一容器**：起容器 → 挂该 user 的 workspace 卷 → 执行 → 销毁。
- **持久 workspace 卷**（文件在 MinIO/命名卷）。
- **Docker-per-run 后端**（compose 友好）；后续可上 gVisor/Firecracker。

## 硬默认（fail-closed）
```
文件: 只挂该 user workspace(rw)，root 只读 + tmpfs /tmp，非 root 运行
网络: --network none；出网只经 SSRF 代理(封 loopback/内网段)
资源: --cap-drop ALL, cpu/mem/pids-limit, 硬超时 kill
密钥: 连接器令牌绝不进沙箱环境
```

## socket 安全
docker socket 只给本编排进程（只跑我方可信代码）；不可信代码在**派生的**容器里，不接触 socket。

见 [../../docs/05-tools-permissions-sandbox.md](../../docs/05-tools-permissions-sandbox.md)。
