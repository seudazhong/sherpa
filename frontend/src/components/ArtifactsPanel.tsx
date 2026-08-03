import { useCallback, useEffect, useRef, useState } from "react";

import { api, type ProjectArtifact } from "../api";

export function ArtifactsPanel({
  projectId,
  workingCopyId,
  csrf,
  refreshKey,
}: {
  projectId: string;
  workingCopyId: string;
  csrf: string | null;
  refreshKey?: string | null;
}) {
  const [artifacts, setArtifacts] = useState<ProjectArtifact[]>([]);
  const [error, setError] = useState<string | null>(null);
  const requestGeneration = useRef(0);

  const load = useCallback(async () => {
    const generation = ++requestGeneration.current;
    try {
      const next = await api.listProjectArtifacts(projectId, workingCopyId);
      if (generation !== requestGeneration.current) return;
      setArtifacts(next);
      setError(null);
    } catch {
      if (generation !== requestGeneration.current) return;
      setError("Could not load artifacts.");
    }
  }, [projectId, workingCopyId]);

  useEffect(() => {
    requestGeneration.current += 1;
    setArtifacts([]);
    setError(null);
    void load();
  }, [load, refreshKey]);

  const keep = async (artifact: ProjectArtifact) => {
    if (!csrf) return;
    try {
      await api.keepProjectArtifact(csrf, projectId, artifact.id);
      await load();
    } catch {
      setError("Could not keep the artifact.");
    }
  };

  const exportToDrive = async (artifact: ProjectArtifact) => {
    if (!csrf) return;
    try {
      await api.exportProjectArtifact(csrf, projectId, artifact.id);
      await load();
    } catch {
      setError("Could not export the artifact.");
    }
  };

  return (
    <section className="artifacts-panel">
      <header>
        <strong>Artifacts</strong>
        <button className="btn btn-quiet btn-small" onClick={() => void load()}>
          Refresh
        </button>
      </header>
      {artifacts.length === 0 && !error && (
        <div className="project-pane-empty compact">
          <span aria-hidden="true">◇</span>
          <strong>No artifacts yet</strong>
          <p>Run logs and generated reports will appear here.</p>
        </div>
      )}
      <ul>
        {artifacts.map((artifact) => (
          <li key={artifact.id}>
            <div>
              <strong>{artifact.name}</strong>
              <span>
                {artifact.size_bytes} B · {artifact.retention}
              </span>
            </div>
            {artifact.retention === "ephemeral" && (
              <button
                className="btn btn-quiet btn-small"
                onClick={() => void keep(artifact)}
              >
                Keep
              </button>
            )}
            <button
              className="btn btn-quiet btn-small"
              onClick={() => void exportToDrive(artifact)}
            >
              Export
            </button>
          </li>
        ))}
      </ul>
      {error && <div className="auth-error small">{error}</div>}
    </section>
  );
}
