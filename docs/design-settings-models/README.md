# design-settings-models — user-configurable model providers static UI (ADR-041)

Static, self-contained mockup for the **Models** Settings panel (multi-source model
providers), on the production **Quiet Work** design system
([`../design-refined/README.md`](../design-refined/README.md)). Open `index.html`
directly in a browser (no build).

> **Design / contract-first only (ADR-041).** This is a static design draft — the
> capability is **not implemented**: no `model_providers` table, no adapters, no
> production Settings **Models** page. Do not read the mock as shipped behaviour.
> Implementation starts only after owner review of ADR-041 + the frozen contract
> deltas (data-model §Model providers · api §10.8 · config `PROVIDER_*` fallback ·
> capability matrix docs/11 §9).

**Owner-approved direction (2026-07-28):**

- Replace the env single provider with a **DB-backed, user-configured multi-provider
  registry**; API keys **AEAD-encrypted at rest** (reuse `security/github_token.py`
  KEK sealing), decrypted only at the `Provider.stream()` boundary.
- First-version wire adapters: enhance **`openai_compatible`** (covers OpenAI /
  DeepSeek / Qwen / Moonshot / Mistral / xAI / Groq / OpenRouter / Ollama / Gemini-OAI
  via `base_url`) + native **`anthropic`** + native **`gemini`**.
- **Global default + per-conversation model override** (a switch carries the source
  reference so a later message never reuses a stale endpoint/wire).
- Provider config is a **human Settings action** — **no agent tool** (crosses the
  credential boundary, like GitHub connections).
- **Deferred** (each a later ADR): cross-provider failover, MoA/ensemble, cost ledger,
  Bedrock/Vertex/OpenAI-Responses, sub-agents, multi-key rotation.

**Surfaces (`index.html`):**

1. **Configured sources** — cards per source: `kind` pill
   (openai_compatible/anthropic/gemini), display name, `base_url`, key-set indicator
   (never the key), fetched `models` count, status (active/error with a redacted last
   error), default badge/toggle, per-source default-model picker, Test/Edit/Delete.
2. **Add source** — kind dropdown, display name, base_url (optional → kind default),
   a **password-input API key that is never echoed** ("AEAD-sealed server-side, write
   only"), optional default model, **Test connection** + Save.
3. **Per-conversation model switch** — a chat top-bar switcher mock (this chat on
   Anthropic while the global default stays OpenAI).

**Notes:** the production Settings route is `/preferences` (avoids an API proxy prefix).
Responsive: single-column layout, verified at 390 px with no horizontal scroll. Research:
[`../research/model-provider.md`](../research/model-provider.md); ADR:
[`../decisions.md` ADR-041](../decisions.md).
