# R-MODEL-PROVIDER · 多来源模型 provider（用户在设置里配置）— 调研与设计

> 状态：**调研完成，待负责人拍板方向**（roadmap #8 的「多 provider」那一半；failover/子 agent 后置）。
> 目标：让用户在 **Settings** 里增删配置**多个模型来源**（OpenAI / Anthropic / Gemini / DeepSeek / Qwen / Moonshot / xAI / OpenRouter / Ollama / …），密钥 **AEAD 加密存 DB**，可选默认 + 每会话切换。
> 方法：深读三个参考项目的 provider 层（**AstrBot** `AstrBotDevs/AstrBot`、**hermes-agent** `NousResearch/hermes-agent`、**PI-agent** `earendil-works/pi`）+ provider landscape 调研。所有结论带一手引用。

---

## 0. 现状（Sherpa）

- provider 抽象是一个 Python `Protocol`（`app/providers/base.py`）：`Provider.stream(*, messages, tools, model) -> AsyncIterator[ProviderEvent]`，事件 = `TextDelta | ReasoningDelta | ToolCall(id,name,args) | Finish(stop_reason, input_tokens, output_tokens)`。**这个事件模型已经足够抽象**，与三个参考项目的归一化事件流一致。
- 唯一实现 `OpenAICompatibleProvider`（`app/providers/openai_compatible.py`，原始 httpx SSE）；工具 schema 映射 `{name,description,input_schema}` → OpenAI `{type:function,function:{...}}`。
- `build_provider()`（`app/providers/factory.py`）从**全局 env `settings`** 选 mock / openai_compatible，**单一 provider**；被 `worker.run_job`（×2）+ `connector_tools` 调用。
- 密钥基建齐全：`security/vault.py`（DEK-per-record AEAD，OAuth 用）+ `security/github_token.py`（单密钥直接 KEK 封装 + connector-vault capability 门控，PAT 用）+ `security/keyring.py`（KEK 轮换）。**model API key = 单串密钥 → 复用 github_token.py 形态最贴切。**

---

## 1. 三个参考项目的架构（一手引用）

### 1.1 共识：**声明式 profile + 行为式 transport 分离**，少数 wire 适配器覆盖全部

| 项目 | 语言 | 抽象拆分 | wire 适配器数量 |
|---|---|---|---|
| **hermes-agent** (`NousResearch/hermes-agent`) | **Python** | `ProviderProfile`(dataclass 元数据/quirks) + `ProviderTransport`(ABC：`convert_messages`/`convert_tools`/`build_kwargs`/`normalize_response`) | **4** api_mode：`chat_completions` / `anthropic_messages` / `codex_responses` / `bedrock_converse`（`providers/base.py`, `agent/transports/base.py`） |
| **PI-agent** (`earendil-works/pi`) | TypeScript | `Provider`(config+auth+getModels) + `Api`(每种 wire 一个 `.lazy.ts`) | ~10 `KnownApi`，主力 `openai-completions`/`anthropic-messages`/`google-generative-ai`（`packages/ai/src/providers/all.ts`, `types.ts`） |
| **AstrBot** (`AstrBotDevs/AstrBot`) | Python | `Provider` 基类 + `@register_provider_adapter` 注册表；`ToolSet` 上 3 个 schema 序列化器 | 主力 `openai_chat_completion`/`anthropic_chat_completion`/`googlegenai_chat_completion`（`astrbot/core/provider/`） |

**三者一致的关键洞察**：**「加一个 provider」= 加一条 config 行（api_mode + base_url + key + model 列表），不是加代码**——只有需要新 wire 协议时才加适配器。hermes 用 4 个 api_mode 覆盖 ≥30 个 provider；AstrBot/PI 同理。

- hermes transport ABC 边界（docstring 明示，`agent/transports/base.py:73-100`）：transport **只**管一种 api_mode 的数据路径转换与归一，**不**管 client 构造、流式、凭据刷新、prompt caching、中断、retry——那些留在 `AIAgent`。→ **这正是我们该采纳的关注点分离**：适配器只做「OpenAI 形状 messages/tools ↔ 各家 wire」+ 把各家流归一回我们的 `ProviderEvent`；retry/选择/预算留在 loop/service。

### 1.2 哪些走原生、哪些走 OpenAI 兼容（三方一致）

- **OpenAI 兼容（同一个 `chat_completions` 适配器 + 不同 `base_url`）覆盖**：DeepSeek(`api.deepseek.com/v1`)、Qwen/DashScope(compatible-mode)、Moonshot/Kimi(`api.moonshot.cn/v1`)、Mistral、xAI Grok(`api.x.ai/v1`)、Groq、OpenRouter、NVIDIA NIM、Fireworks、DeepInfra、SiliconFlow、Ollama/LM Studio/vLLM(本地)、**Gemini 的 OpenAI 兼容层**(`generativelanguage.googleapis.com/v1beta/openai/`)。
  - AstrBot：DeepSeek/Qwen/Moonshot 全部 `ProviderOpenAIOfficial` + 换 `api_base`，**无独立 DashScope 适配器**（报告原话："a single OpenAI-compat adapter with configurable base_url covers most providers"）。
- **值得上原生适配器**（拿到更完整 tool-use/thinking + 少踩兼容层的坑）：
  - **Anthropic Messages API**（native）：system 顶层字段、`tools` 用 `input_schema`（≈ 我们内部形状）、`tool_use`/`tool_result` content block、block 化 SSE（`content_block_delta`: `text_delta`/`thinking_delta`/`input_json_delta`）。Kimi-coding、MiniMax 是 **Anthropic-compat 端点**，复用同一适配器。
  - **Google Gemini `generateContent`**（native）：`functionDeclarations`、`functionCall`/`functionResponse` parts、更严 JSON schema、`thought_signature`。
- landscape 佐证（web，2025-2026）：DeepSeek/Qwen/Mistral/xAI/Moonshot 均提供 OpenAI 兼容端点；Gemini 有 OpenAI 兼容层但 native 功能更全（[LiteLLM providers](https://docs.litellm.ai/docs/providers)、[all-llm-provider-list](https://github.com/foisalislambd/all-llm-provider-list)、[futuresearch: LLM API differences](https://futuresearch.ai/blog/llm-provider-quirks/)）。

### 1.3 密钥与配置存储（三者都明文——我们的差异化点）

| 项目 | 存储 | 加密 |
|---|---|---|
| AstrBot | `data/cmd_config.json`（明文）；key 支持 `$ENV_VAR` 间接 | **无** |
| PI-agent | `~/.pi/agent/{models,auth}.json`（明文，`0o600`）；apiKey 支持 `$ENV`/`!shell-cmd` | **无** |
| hermes | `.env`/环境变量 + `~/.hermes/auth.json`（OAuth，明文） | **无**（`.env.example` 明写 plaintext warning） |

→ **Sherpa 用 AEAD 加密存 DB 是明确的安全优势**（三方都没有）。密文入 `model_providers` 行，仅在 `stream()` 调用时于连接边界解密，绝不进日志/事件/prompt。

### 1.4 model 注册与选择（三者一致）

- **每 provider 一个 model 列表**：live 拉 `{base_url}/models` + 一份 **curated fallback 列表**（拉取失败/慢时兜底，hermes `ProviderProfile.fallback_models`、PI 静态 catalog、AstrBot `get_models()`）。
- **默认 + 每会话覆盖**：三者都支持——全局 `default_provider`/`default_model` + 每会话/每次调用切 model（hermes `/model`、AstrBot per-umo `provider_perf_*`、PI session `model_change` event）。
- hermes 踩坑 #25106：切默认 model 时**必须同时持久化 `base_url` + `api_mode`**，否则下次用旧端点/协议 → 我们的 schema 要让 model 选择携带其 provider 引用。

### 1.5 工具 + 流式 + 推理归一（三者一致）

- **工具**：canonical 内部 schema（name/description/params）→ 每格式序列化器。AstrBot `ToolSet` 有 `openai_schema()`/`anthropic_schema()`/`google_schema()` 三个。**我们内部 `{name,description,input_schema}` 已是 Anthropic 形状**，OpenAI 映射已有。Gemini 需 schema 收敛（type-as-list→单一、去 `additionalProperties`、`array.items` 必填）。
- **流式**：归一到单一事件 union。**我们的 `TextDelta/ReasoningDelta/ToolCall/Finish` 已经是对的形状**。Anthropic=block 化、Gemini=parts，各适配器内重组。
- **推理/thinking**：归一到 `ReasoningDelta`；处理 native 字段 vs `<think>` 标签剥离；**opaque 签名必须原样回传**（Anthropic thinking signature、Gemini `thought_signature`），否则多轮 HTTP 400。

### 1.6 failover / retry / 成本（三者一致：failover 不在 provider 层）

- **retry**（429/5xx 指数退避）三者都做（AstrBot tenacity `{408,409,429,500-504,529}`；PI 分类 retryable vs fail-fast `insufficient_quota/quota exceeded`；hermes 在 `AIAgent`）。
- **跨-provider failover 三者都不在 provider 层做**（PI 明说 no built-in failover；hermes/AstrBot 放编排/pipeline 层的 fallback 列表）→ **印证本期不做 failover**。
- **成本**：归一 usage（input/output/cache-read/cache-write/**reasoning tokens**）+ 每-model 费率表（hermes `usage_pricing.py`、PI `calculateCost` 分层计价）。→ 我们 `Finish` 可扩 cache/reasoning tokens，成本 ledger 后置。

---

## 2. 真实 provider 坑（写进 ADR/测试，来自三方源码注释）

**Anthropic**：① 连续同角色消息必须合并（AstrBot `_merge_consecutive`；hermes）；② `tool_result` 放进 `user` 消息（非 `tool` role）；③ system 顶层字段；④ `max_tokens` 必填正数（`bool` 是 `int` 子类要先排除，hermes `_resolve_positive_anthropic_max_tokens`）；⑤ Claude 4.7+ 拒绝任何 temperature/top_p/top_k（含默认值，hermes `_forbids_sampling_params`）；⑥ thinking block 签名 + 与 tool_use 的**交错顺序**必须原样重放，否则 400（hermes `anthropic_content_blocks`）；⑦ `refusal`/`end_turn` 空 content 是**合法**，不能当失败重试（否则死循环，hermes `validate_response`）。

**Gemini**：① JSON schema 更严（`type:["string","null"]`→取首个非 null；去 `additionalProperties`；`array.items` 必填，AstrBot `google_schema`）；② **不流式传 tool 参数**（单块，PI README 明示）；③ `thought_signature` 每个 tool call 必须回传，否则 400（hermes/PI）。

**OpenAI 兼容碎片化**：① 推理字段变体 `reasoning_content` vs `reasoning`（Groq/OpenRouter）；② `<think>...</think>` 标签泄漏进 content 需正则剥离（AstrBot `_parse_openai_completion`）；③ 部分 provider 的 tool-call delta 缺 `index`/`type`（SiliconFlow/Gemini-OAI，AstrBot 手动补）；④ DeepSeek-Reasoner/Moonshot 拒绝空 assistant 消息（需过滤/补空 content）；⑤ usage 有时在 `choices[].usage`（MoonshotAI）；⑥ Ollama `think:false` 不稳，用 `reasoning_effort:none`；⑦ thinking 格式极度碎片化（PI 列了 10 种 `thinkingFormat`）。

**配置**：切 model 必须连带持久化 `base_url`+`api_mode`（hermes #25106）；per-model api_mode 用**目标 model**解析而非全局默认（否则 MoA/fallback 槽继承错协议，hermes runtime_provider 注释）。

---

## 3. 推荐方案（GO）— 契合 Sherpa 架构

### 3.1 数据模型（新 `model_providers` 表）
一行 = 一个用户配置的来源：
- `kind`/`api_mode`（`openai_compatible` | `anthropic` | `gemini`；forward：`bedrock`/`vertex`/`openai_responses`）
- `name`（显示名，用户起）· `base_url`（可空→用 kind 默认）· `enabled` · `is_default`
- **AEAD 密钥列**（复用 `github_connections`/connectors 列形态：`token_enc`/`nonce`/`kek_id`/`key_version`/`token_algorithm`/`aad_version`）——**唯一活跃默认**唯一索引
- `models`（该来源可用 model 列表：live 拉 `/models` + curated fallback）· `default_model`
- `tenant_id` 复合键（ADR-015）；每 owner 多行；`uq` 保证唯一默认

**model 选择**：全局默认（`is_default` 行 + `default_model`）；**每会话覆盖**（`sessions` 上加可空 `model_provider_id`+`model` 绑定，或 session_state），切换即带上其 provider 引用（避免 hermes #25106）。

### 3.2 适配器（本期 3 个 wire，全在现有 `Provider.stream` 之下）
1. **`openai_compatible`**（已有，增强）：加推理字段/`<think>` 剥离、缺 `index/type` 修补、空 assistant 过滤等兼容层坑；覆盖 DeepSeek/Qwen/Moonshot/Mistral/xAI/Groq/OpenRouter/Ollama/Gemini-OAI…
2. **`anthropic`**（新，native Messages API）：system 顶层、`input_schema` 工具（≈ 我们内部）、`tool_result` 入 user、连续同角色合并、`max_tokens` 必填、block SSE→`Text/Reasoning/ToolCall/Finish`、thinking 签名回传。
3. **`gemini`**（新，native `generateContent`）：`functionDeclarations` + schema 收敛、parts 流→归一、`thought_signature` 回传。

工具序列化沿用「canonical → 每格式」：抽出 `to_openai_tools`/`to_anthropic_tools`/`to_gemini_tools`（对齐 AstrBot 的三序列化器）。

### 3.3 解析入口
`build_provider(db, ctx)` 按 owner 的 DB 配置（选定/默认 provider + model）构造适配器，密钥经 connector-vault capability 于连接边界解密；**env 仅作离线/mock 兜底**。3 个调用点（worker ×2、connector_tools）传入 tenant/user + 可选 session 的 model 覆盖。

### 3.4 REST + Settings UI
- REST：`GET/POST/PATCH/DELETE /providers`（增删改；密钥只入不出）+ `POST /providers/{id}/test`（测试连接：拉 `/models` 或一次极小 chat）+ `GET /providers/{id}/models`（列 model）+ 选默认。CSRF on writes。
- **Settings 新增「Models」面**：来源卡片（kind/base_url/状态/默认徽章）、加来源表单（kind 下拉 + base_url + password 输入的 key，**永不回显**）、测试连接、每来源 model 下拉选默认、每会话 model 切换器（chat 顶栏）。

### 3.5 本期范围 & 后置
- **本期**：多来源配置 + AEAD 密钥 + 测试连接 + 列 model + 全局默认 + **每会话切 model** + 3 个 wire 适配器（OpenAI 兼容 / Anthropic / Gemini）。
- **后置（各自后续 ADR）**：跨-provider **failover**、MoA/ensemble、成本 ledger、Bedrock/Vertex/OpenAI-Responses、子 agent、prompt-cache 计量、多 key 轮换环。

---

## 4. 待负责人拍板的 scope 决策

1. **每会话切 model**：推荐**做**（三方都做、成本低、体验好）——除全局默认外，chat 顶栏可切来源/model，绑定持久化到会话。□ 同意 / □ 先只做全局默认
2. **本期 native 适配器范围**：推荐 **OpenAI 兼容 + Anthropic + Gemini 三个**（覆盖你点名的 OpenAI/Anthropic/Gemini/DeepSeek/Qwen + Moonshot/xAI/OpenRouter/Ollama 等）；Bedrock/Vertex/OpenAI-Responses/Codex 后置。□ 同意 / □ 先只做 OpenAI 兼容、原生后置 / □ 还要别的
3. **默认仍保留 env `PROVIDER_*` 作为无 DB 配置时的兜底 + 测试 mock**（不破坏现有离线/CI）。□ 同意

拍板后即进 **ADR（约 ADR-041）+ 契约先行**（data-model `model_providers` + `sessions` model 绑定 · api §providers · config · 能力矩阵行 + 静态 Settings「Models」稿），再生产实现 + 两栈验证。

---

## 5. 引用

- **AstrBot** `AstrBotDevs/AstrBot@3f9aa74`：`astrbot/core/provider/{provider.py,register.py}`、`sources/{openai_source,anthropic_source,gemini_source,groq_source,openrouter_source,kimi_code_source,request_retry}.py`、`astrbot/core/agent/tool.py`、`core/provider/manager.py`、`entities.py`。
- **hermes-agent** `NousResearch/hermes-agent@7100e8d`：`providers/base.py`、`agent/transports/{base,anthropic,bedrock,codex,chat_completions,types}.py`、`agent/anthropic_adapter.py`、`agent/usage_pricing.py`、`hermes_cli/runtime_provider.py`、`website/docs/developer-guide/model-provider-plugin.md`、回归测试 `test_25106_*`。
- **PI-agent** `earendil-works/pi@c820aa2`：`packages/ai/src/{types.ts,models.ts,providers/all.ts,providers/{anthropic,google,deepseek,xai}.ts,utils/retry.ts,auth/*}`、`packages/coding-agent/src/core/{model-config,auth-storage,resolve-config-value,model-runtime}.ts`。
- **Landscape**（web，2025-2026）：[LiteLLM Providers](https://docs.litellm.ai/docs/providers)、[all-llm-provider-list](https://github.com/foisalislambd/all-llm-provider-list)、[futuresearch: LLM API differences](https://futuresearch.ai/blog/llm-provider-quirks/)、[Function Calling OpenAI vs Anthropic vs Google](https://qveris.ai/guides/function-calling/)。
- **Sherpa 现状**：`app/providers/{base,factory,openai_compatible,mock}.py`、`app/security/{vault,github_token,keyring}.py`、`app/models/settings.py`、`docs/09-roadmap.md` #8。
