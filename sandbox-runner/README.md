# sandbox-runner

沙箱执行镜像（窄腰隔离）。`backend/sandbox/` 编排它来跑用户/agent 代码。

## 内容
- 预置 runtime（python / node / …）的基础镜像。
- 非 root 用户、最小工具集。
- 入口脚本：读工作区、执行、限长输出、退出码。

## 运行约束（由编排方施加，见 backend/sandbox）
- 只挂该 user 的 workspace 卷。
- `--network none`、root 只读、`--cap-drop ALL`、资源上限、硬超时。
- 无任何密钥注入。

见 [../docs/05-tools-permissions-sandbox.md](../docs/05-tools-permissions-sandbox.md)。
