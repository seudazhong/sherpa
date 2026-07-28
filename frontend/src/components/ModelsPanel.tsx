// Model providers (ADR-041): the Settings "Models" panel. Configure multiple model sources
// (OpenAI / Anthropic / Gemini / DeepSeek / Qwen / …), test a connection, pick a global
// default, and set each source's default model. The API key is write-only — sent on
// add/update, never displayed.
import { useCallback, useEffect, useState } from "react";

import { api, type ModelProvider, type ModelProviderKind } from "../api";
import { useAuth } from "../auth";

const KIND_LABEL: Record<ModelProviderKind, string> = {
  openai_compatible: "OpenAI compatible",
  anthropic: "Anthropic (native)",
  gemini: "Gemini (native)",
};

export function ModelsPanel() {
  const { csrf } = useAuth();
  const [providers, setProviders] = useState<ModelProvider[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [form, setForm] = useState({
    kind: "openai_compatible" as ModelProviderKind,
    display_name: "",
    base_url: "",
    api_key: "",
    default_model: "",
  });

  const load = useCallback(async () => {
    try {
      setProviders(await api.listModelProviders());
    } catch {
      setError("Could not load model providers.");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const add = async () => {
    if (!csrf || !form.display_name.trim() || !form.api_key.trim()) return;
    setBusy("add");
    setError(null);
    try {
      const created = await api.createModelProvider(csrf, {
        kind: form.kind,
        display_name: form.display_name.trim(),
        api_key: form.api_key,
        base_url: form.base_url.trim() || null,
        default_model: form.default_model.trim() || null,
      });
      // Immediately test the new source to populate its model catalog.
      try {
        await api.testModelProvider(csrf, created.id);
      } catch {
        /* keep the source; the card shows its status */
      }
      setForm({ kind: "openai_compatible", display_name: "", base_url: "", api_key: "", default_model: "" });
      setAdding(false);
      await load();
    } catch (e) {
      setError(e instanceof Error && e.message.includes("409") ? "A source with that name already exists." : "Could not add the source.");
    } finally {
      setBusy(null);
    }
  };

  const test = async (p: ModelProvider) => {
    if (!csrf) return;
    setBusy(p.id);
    try {
      await api.testModelProvider(csrf, p.id);
      await load();
    } finally {
      setBusy(null);
    }
  };

  const makeDefault = async (p: ModelProvider) => {
    if (!csrf) return;
    setBusy(p.id);
    try {
      await api.setDefaultModelProvider(csrf, p.id);
      await load();
    } finally {
      setBusy(null);
    }
  };

  const setModel = async (p: ModelProvider, model: string) => {
    if (!csrf) return;
    try {
      await api.updateModelProvider(csrf, p.id, { default_model: model });
      await load();
    } catch {
      setError("Could not update the default model.");
    }
  };

  const remove = async (p: ModelProvider) => {
    if (!csrf) return;
    setBusy(p.id);
    try {
      await api.deleteModelProvider(csrf, p.id);
      await load();
    } finally {
      setBusy(null);
    }
  };

  return (
    <section className="settings-panel models-panel">
      <div className="settings-panel-head">
        <span className="form-card-icon" aria-hidden="true">◈</span>
        <div>
          <h3>Models</h3>
          <p>
            Configure your model sources. Keys are encrypted on the server and never shown;
            the assistant uses the default source unless a chat overrides it.
          </p>
        </div>
        {!adding && (
          <button className="btn btn-small mp-add" onClick={() => setAdding(true)}>
            ＋ Add source
          </button>
        )}
      </div>

      {error && <div className="auth-error small">{error}</div>}

      <div className="mp-list">
        {providers.length === 0 && !adding && (
          <div className="small muted">
            No model sources yet. Add one, or Sherpa falls back to the built-in provider.
          </div>
        )}
        {providers.map((p) => (
          <article key={p.id} className={`mp-card${p.is_default ? " mp-default" : ""}`}>
            <div className="mp-head">
              <span className="pill mp-kind">{KIND_LABEL[p.kind]}</span>
              <strong>{p.display_name}</strong>
              {p.is_default && <span className="pill pill-success">default</span>}
              <span
                className={`pill ${p.status === "active" ? "pill-success" : p.status === "error" ? "pill-error" : "pill-idle"}`}
              >
                {p.status}
              </span>
              <span className="mp-spacer" />
              {!p.is_default && (
                <button className="btn btn-quiet btn-small" disabled={busy === p.id} onClick={() => void makeDefault(p)}>
                  Set default
                </button>
              )}
              <button className="btn btn-quiet btn-small" disabled={busy === p.id} onClick={() => void test(p)}>
                Test
              </button>
              <button className="btn btn-quiet btn-small btn-danger" disabled={busy === p.id} onClick={() => void remove(p)}>
                Delete
              </button>
            </div>
            <div className="mp-facts small muted">
              {p.base_url && <span className="mono">{p.base_url}</span>}
              <span>key {p.has_key ? "✓ set" : "— none"}</span>
              <span>{p.models.length} models</span>
              {p.status === "error" && p.last_error && <span className="mp-err">{p.last_error}</span>}
            </div>
            {p.models.length > 0 && (
              <label className="mp-model">
                <span className="small muted">Default model</span>
                <select value={p.default_model ?? ""} onChange={(e) => void setModel(p, e.target.value)}>
                  {p.default_model && !p.models.includes(p.default_model) && (
                    <option value={p.default_model}>{p.default_model}</option>
                  )}
                  {p.models.map((m) => (
                    <option key={m} value={m}>{m}</option>
                  ))}
                </select>
              </label>
            )}
          </article>
        ))}
      </div>

      {adding && (
        <div className="mp-form">
          <div className="mp-form-grid">
            <label className="mp-field">
              <span>Type</span>
              <select
                value={form.kind}
                onChange={(e) => setForm({ ...form, kind: e.target.value as ModelProviderKind })}
              >
                <option value="openai_compatible">OpenAI compatible (OpenAI / DeepSeek / Qwen / Moonshot / xAI / OpenRouter / Ollama …)</option>
                <option value="anthropic">Anthropic (native)</option>
                <option value="gemini">Gemini (native)</option>
              </select>
            </label>
            <label className="mp-field">
              <span>Name</span>
              <input
                value={form.display_name}
                placeholder="e.g. DeepSeek"
                onChange={(e) => setForm({ ...form, display_name: e.target.value })}
              />
            </label>
            <label className="mp-field full">
              <span>Base URL (optional — the kind's default endpoint if blank)</span>
              <input
                value={form.base_url}
                placeholder="https://api.deepseek.com/v1"
                onChange={(e) => setForm({ ...form, base_url: e.target.value })}
              />
            </label>
            <label className="mp-field full">
              <span>API key</span>
              <input
                type="password"
                value={form.api_key}
                placeholder="paste key (stored encrypted, never shown)"
                onChange={(e) => setForm({ ...form, api_key: e.target.value })}
              />
            </label>
            <label className="mp-field">
              <span>Default model (optional)</span>
              <input
                value={form.default_model}
                placeholder="deepseek-chat"
                onChange={(e) => setForm({ ...form, default_model: e.target.value })}
              />
            </label>
          </div>
          <div className="mp-form-actions">
            <button className="btn btn-quiet" onClick={() => setAdding(false)}>Cancel</button>
            <span className="mp-spacer" />
            <button
              className="btn btn-primary"
              disabled={busy === "add" || !form.display_name.trim() || !form.api_key.trim()}
              onClick={() => void add()}
            >
              Add &amp; test
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
