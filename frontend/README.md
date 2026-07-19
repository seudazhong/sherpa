# frontend

TypeScript 前端（Next 或 Vite 待定）。**它是 core 事件流的客户端**，不含任何 agent 逻辑。

## 页面
- 登录 / 注册 / 绑定外部身份（QQ、邮箱）。
- 会话：订阅 SSE/WS 实时渲染 core 事件流（text-delta / tool-call / tool-result）。
- 看板：todos（含连接器自动生成的）。
- 文件：个人存储空间上传/同步。
- 连接器：连接 Gmail/GitHub、管理 agentic email。
- 审批卡片：渲染 `permission.asked` 事件，回 once/session/always/reject。
- 设置：定时任务、通知偏好、persona。

## 铁律
- UI 永不直接调模型；一切经后端 API。
- 审批走 correlation-id 协议（与 QQ/邮箱等其他 surface 一致）。

见 [../docs/03-runtime-async-jobs.md](../docs/03-runtime-async-jobs.md)。
