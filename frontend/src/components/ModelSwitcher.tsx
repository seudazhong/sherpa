// Per-conversation model switcher (ADR-041): lets a chat run on a source/model other than
// the global default. Persists both the source id and the model to the session, and shows
// the *effective* model a run would use (session override → global default → server env),
// which is the only honest label once sources are configured (backlog B-1).
import { useCallback, useEffect, useState } from "react";

import { api, type ModelProvider, type SessionModelState } from "../api";
import { useAuth } from "../auth";

const DEFAULT_VALUE = "__default__";

function effectiveLabel(state: SessionModelState): string {
  if (state.effective_source === "env") {
    return state.effective_kind === "mock"
      ? "Mock model"
      : `Server default · ${state.effective_model}`;
  }
  return state.effective_provider_name
    ? `${state.effective_provider_name} · ${state.effective_model}`
    : state.effective_model;
}

const SOURCE_HINT: Record<SessionModelState["effective_source"], string> = {
  session: "Model for this conversation (set here)",
  default: "Model for this conversation (your default source)",
  env: "Model for this conversation (server default — no source configured)",
};

export function ModelSwitcher({ sessionId }: { sessionId: string }) {
  const { csrf } = useAuth();
  const [providers, setProviders] = useState<ModelProvider[]>([]);
  const [state, setState] = useState<SessionModelState | null>(null);
  const [value, setValue] = useState<string>(DEFAULT_VALUE);

  const apply = useCallback((sel: SessionModelState) => {
    setState(sel);
    setValue(
      sel.model_provider_id && sel.model
        ? `${sel.model_provider_id}::${sel.model}`
        : DEFAULT_VALUE,
    );
  }, []);

  const load = useCallback(async () => {
    try {
      const [ps, sel] = await Promise.all([
        api.listModelProviders(),
        api.getSessionModel(sessionId),
      ]);
      setProviders(ps.filter((p) => p.enabled && p.models.length > 0));
      apply(sel);
    } catch {
      /* provider config is optional; hide on failure */
    }
  }, [sessionId, apply]);

  useEffect(() => {
    void load();
  }, [load]);

  if (!state) return null;

  // When an override is picked, the select itself already spells out the pair —
  // showing it twice is noise. The label earns its place for "Default model"
  // (which hides the resolved pair) and when there is no select at all.
  const selectShowsEffective =
    providers.length > 0 && value !== DEFAULT_VALUE && state.effective_source === "session";

  const onChange = async (next: string) => {
    setValue(next);
    if (!csrf) return;
    try {
      if (next === DEFAULT_VALUE) {
        apply(await api.setSessionModel(csrf, sessionId, { model_provider_id: null, model: null }));
      } else {
        const [pid, model] = next.split("::");
        apply(await api.setSessionModel(csrf, sessionId, { model_provider_id: pid, model }));
      }
    } catch {
      /* revert on failure */
      void load();
    }
  };

  return (
    <label className="model-switcher" title={SOURCE_HINT[state.effective_source]}>
      <span aria-hidden="true">◈</span>
      {!selectShowsEffective && (
        <span className="model-effective">{effectiveLabel(state)}</span>
      )}
      {/* Nothing configured → label only; the run uses the server's env provider. */}
      {providers.length > 0 && (
        <select
          value={value}
          onChange={(e) => void onChange(e.target.value)}
          aria-label="Model for this conversation"
        >
          <option value={DEFAULT_VALUE}>Default model</option>
          {providers.map((p) =>
            p.models.map((m) => (
              <option key={`${p.id}::${m}`} value={`${p.id}::${m}`}>
                {p.display_name} · {m}
              </option>
            )),
          )}
        </select>
      )}
    </label>
  );
}
