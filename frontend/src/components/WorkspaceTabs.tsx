import { useState } from "react";

import type { WorkingCopySummary } from "../api";
import { ArtifactsPanel } from "./ArtifactsPanel";
import { ChangeReview } from "./ChangeReview";
import { RunPanel, type RuntimeStreamFrame } from "./RunPanel";
import { RunsPanel } from "./RunsPanel";

type WorkspaceTab = "changes" | "runs" | "artifacts";

export function WorkspaceTabs({
  projectId,
  sessionId,
  csrf,
  workingCopy,
  frames,
  onWorkingCopy,
  onChanged,
}: {
  projectId: string;
  sessionId: string;
  csrf: string | null;
  workingCopy: WorkingCopySummary | null;
  frames: RuntimeStreamFrame[];
  onWorkingCopy: (workingCopy: WorkingCopySummary | null) => void;
  onChanged: () => void;
}) {
  const [tab, setTab] = useState<WorkspaceTab>("changes");
  const refreshKey =
    workingCopy?.last_exec?.id ?? workingCopy?.updated_at ?? null;

  return (
    <>
      <RunPanel
        projectId={projectId}
        sessionId={sessionId}
        csrf={csrf}
        workingCopy={workingCopy}
        frames={frames}
        onWorkingCopy={onWorkingCopy}
      />
      <nav className="workspace-tabs" aria-label="Project workspace sections">
        <button
          className={tab === "changes" ? "active" : ""}
          onClick={() => setTab("changes")}
        >
          Changes
          {workingCopy && workingCopy.overlay_entry_count > 0 && (
            <span>{workingCopy.overlay_entry_count}</span>
          )}
        </button>
        <button
          className={tab === "runs" ? "active" : ""}
          onClick={() => setTab("runs")}
        >
          Runs
        </button>
        <button
          className={tab === "artifacts" ? "active" : ""}
          onClick={() => setTab("artifacts")}
        >
          Artifacts
        </button>
      </nav>
      {tab === "changes" &&
        (workingCopy ? (
          <ChangeReview
            projectId={projectId}
            csrf={csrf}
            workingCopy={workingCopy}
            onChanged={onChanged}
          />
        ) : (
          <div className="project-pane-empty compact">
            <span aria-hidden="true">✓</span>
            <strong>No pending changes</strong>
            <p>Edits from you or the assistant will appear here.</p>
          </div>
        ))}
      {tab === "runs" && (
        <RunsPanel sessionId={sessionId} refreshKey={refreshKey} />
      )}
      {tab === "artifacts" && workingCopy && (
        <ArtifactsPanel
          projectId={projectId}
          workingCopyId={workingCopy.id}
          csrf={csrf}
          refreshKey={refreshKey}
        />
      )}
      {tab === "artifacts" && !workingCopy && (
        <div className="project-pane-empty compact">
          <span aria-hidden="true">◇</span>
          <strong>No current workspace artifacts</strong>
          <p>Open or modify this chat's working copy before viewing its artifacts.</p>
        </div>
      )}
    </>
  );
}
