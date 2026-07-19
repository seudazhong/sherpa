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

> **v1 范围（ADR-022）**：以上为**长期愿景**页面清单。v1 实际交付子集 = 会话(Chat，次要) · 个人 **Candidate 收件箱**（非团队看板） · Gmail-only 连接器 · 设置(通知/自治/数据) · 提醒类调度。**推迟出 v1**：团队工作区/协作、文件存储、GitHub、QQ/IM、agentic email、沙箱、通用 cron、多 provider。另需为 v1 补充：run/活动回执（"agent 代我做了什么"）、数据导出/删除控制、首次接入向导。详见 [design-bright 范围说明](../docs/design-bright/README.md) 与 [评审汇总](../docs/reviews/README.md)。

## 铁律
- UI 永不直接调模型；一切经后端 API。
- 审批走 correlation-id 协议（与 QQ/邮箱等其他 surface 一致）。

见 [../docs/03-runtime-async-jobs.md](../docs/03-runtime-async-jobs.md)。
