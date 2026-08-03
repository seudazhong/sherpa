import { useCallback, useEffect, useRef, useState } from "react";

import { api, type RuntimeExecRun } from "../api";

function duration(run: RuntimeExecRun): string {
  if (run.duration_ms === null) return "—";
  return run.duration_ms < 1000
    ? `${run.duration_ms} ms`
    : `${(run.duration_ms / 1000).toFixed(1)} s`;
}

export function RunsPanel({
  sessionId,
  refreshKey,
}: {
  sessionId: string;
  refreshKey?: string | null;
}) {
  const [runs, setRuns] = useState<RuntimeExecRun[]>([]);
  const [error, setError] = useState<string | null>(null);
  const requestGeneration = useRef(0);

  const load = useCallback(async () => {
    const generation = ++requestGeneration.current;
    try {
      const next = await api.listRuntimeExecs(sessionId);
      if (generation !== requestGeneration.current) return;
      setRuns(next);
      setError(null);
    } catch {
      if (generation !== requestGeneration.current) return;
      setError("Could not load run history.");
    }
  }, [sessionId]);

  useEffect(() => {
    requestGeneration.current += 1;
    setRuns([]);
    setError(null);
    void load();
  }, [load, refreshKey]);

  return (
    <section className="runs-panel">
      <header>
        <strong>Run history</strong>
        <button className="btn btn-quiet btn-small" onClick={() => void load()}>
          Refresh
        </button>
      </header>
      {runs.length === 0 && !error && (
        <div className="project-pane-empty compact">
          <span aria-hidden="true">▶</span>
          <strong>No runs yet</strong>
          <p>Commands run by you or the assistant will appear here.</p>
        </div>
      )}
      {runs.map((run) => (
        <details className="run-history-item" key={run.id}>
          <summary>
            <span className={`pill pill-${run.state}`}>{run.state}</span>
            <code>{run.command_preview}</code>
            <span className="run-history-exit">
              exit {run.exit_code ?? "—"} · {duration(run)}
            </span>
          </summary>
          {run.termination_reason && run.termination_reason !== "done" && (
            <div className="cr-banner cr-warn">
              Termination: <code>{run.termination_reason}</code>
            </div>
          )}
          {run.stdout_head && (
            <pre className="run-history-output">{run.stdout_head}</pre>
          )}
          {run.stderr_tail && (
            <pre className="run-history-output stderr">{run.stderr_tail}</pre>
          )}
          {run.output_truncated && (
            <div className="small muted">Output was truncated.</div>
          )}
        </details>
      ))}
      {error && <div className="auth-error small">{error}</div>}
    </section>
  );
}
