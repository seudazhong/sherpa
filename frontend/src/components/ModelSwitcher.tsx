// Per-conversation model switcher (ADR-041): lets a chat run on a source/model other than
// the global default. Persists both the source id and the model to the session.
import { useCallback, useEffect, useState } from "react";

import { api, type ModelProvider } from "../api";
import { useAuth } from "../auth";

const DEFAULT_VALUE = "__default__";

export function ModelSwitcher({ sessionId }: { sessionId: string }) {
  const { csrf } = useAuth();
  const [providers, setProviders] = useState<ModelProvider[]>([]);
  const [value, setValue] = useState<string>(DEFAULT_VALUE);

  const load = useCallback(async () => {
    try {
      const [ps, sel] = await Promise.all([
        api.listModelProviders(),
        api.getSessionModel(sessionId),
      ]);
      setProviders(ps.filter((p) => p.enabled && p.models.length > 0));
      setValue(
        sel.model_provider_id && sel.model
          ? `${sel.model_provider_id}::${sel.model}`
          : DEFAULT_VALUE,
      );
    } catch {
      /* provider config is optional; hide on failure */
    }
  }, [sessionId]);

  useEffect(() => {
    void load();
  }, [load]);

  // Nothing configured → no switcher (the assistant uses the env/default provider).
  if (providers.length === 0) return null;

  const onChange = async (next: string) => {
    setValue(next);
    if (!csrf) return;
    try {
      if (next === DEFAULT_VALUE) {
        await api.setSessionModel(csrf, sessionId, { model_provider_id: null, model: null });
      } else {
        const [pid, model] = next.split("::");
        await api.setSessionModel(csrf, sessionId, { model_provider_id: pid, model });
      }
    } catch {
      /* revert on failure */
      void load();
    }
  };

  return (
    <label className="model-switcher" title="Model for this conversation">
      <span aria-hidden="true">◈</span>
      <select value={value} onChange={(e) => void onChange(e.target.value)}>
        <option value={DEFAULT_VALUE}>Default model</option>
        {providers.map((p) =>
          p.models.map((m) => (
            <option key={`${p.id}::${m}`} value={`${p.id}::${m}`}>
              {p.display_name} · {m}
            </option>
          )),
        )}
      </select>
    </label>
  );
}
