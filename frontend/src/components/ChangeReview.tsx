// Workspace Projects W3 (ADR-040): the human Change Review surface for a Project-bound
// chat. Shows the pending working-copy changes (added/modified/deleted + bounded diffs +
// artifacts) and the review actions — Save selected / Save + checkpoint / Discard. Saving
// advances the project head; a moved head is rejected (409) and surfaced as a conflict.
import { useCallback, useEffect, useState } from "react";
import {
  api,
  ApiError,
  type ChangeSet,
  type ChangeSetEntry,
  type WorkingCopySummary,
} from "../api";

const MARK: Record<string, string> = {
  added: "+",
  modified: "~",
  deleted: "-",
};

interface SaveConflictDetail {
  error: "head_moved";
  base_snapshot_id: string;
  current_snapshot_id: string;
  message: string;
}

function conflictDetail(error: ApiError): SaveConflictDetail | null {
  const body = error.body as { detail?: unknown } | null;
  const detail = body?.detail as Partial<SaveConflictDetail> | undefined;
  return detail?.error === "head_moved"
    ? (detail as SaveConflictDetail)
    : null;
}

export function ChangeReview({
  projectId,
  csrf,
  workingCopy,
  onChanged,
}: {
  projectId: string;
  csrf: string | null;
  workingCopy: WorkingCopySummary;
  onChanged: () => void;
}) {
  const csId = workingCopy.open_change_set_id;
  const [cs, setCs] = useState<ChangeSet | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [openDiff, setOpenDiff] = useState<{ id: string; text: string } | null>(
    null,
  );
  const [checkpoint, setCheckpoint] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [conflict, setConflict] = useState<SaveConflictDetail | null>(null);

  const load = useCallback(async () => {
    if (!csId) {
      setCs(null);
      return;
    }
    try {
      const next = await api.getChangeSet(projectId, csId);
      setCs(next);
      setSelected(
        new Set(next.entries.filter((e) => e.selected).map((e) => e.id)),
      );
    } catch {
      setError("Could not load the change review.");
    }
  }, [csId, projectId]);

  useEffect(() => {
    setOpenDiff(null);
    if (!workingCopy.head_moved && workingCopy.state !== "conflicted") {
      setConflict(null);
    }
    void load();
  }, [load, workingCopy.head_moved, workingCopy.state]);

  const toggle = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const showDiff = async (entry: ChangeSetEntry) => {
    if (!csId) return;
    if (openDiff?.id === entry.id) {
      setOpenDiff(null);
      return;
    }
    if (!entry.has_diff) {
      setOpenDiff({
        id: entry.id,
        text: entry.is_binary
          ? "(binary file — no inline diff)"
          : "(diff exceeds the review cap — download only)",
      });
      return;
    }
    try {
      const text = await api.getChangeSetDiff(projectId, csId, entry.id);
      setOpenDiff({ id: entry.id, text });
    } catch {
      setOpenDiff({ id: entry.id, text: "(could not load diff)" });
    }
  };

  const doSave = async (withCheckpoint: boolean) => {
    if (!csrf || !csId || busy) return;
    setBusy(true);
    setError(null);
    try {
      await api.applyChangeSet(csrf, projectId, csId, {
        selected_entry_ids: [...selected],
        checkpoint:
          withCheckpoint && checkpoint.trim()
            ? { name: checkpoint.trim() }
            : null,
      });
      setCheckpoint("");
      onChanged();
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        setConflict(conflictDetail(e));
        setError(
          "The project head moved since these changes opened — review the rebased changes and Save again.",
        );
        onChanged();
      } else {
        setError("Save failed.");
      }
    } finally {
      setBusy(false);
    }
  };

  const closeRuntimeForRebase = async () => {
    const initial = workingCopy.runtime;
    if (!csrf || !initial) return;
    let runtime = initial;
    for (let attempt = 0; attempt < 180; attempt += 1) {
      if (runtime.state === "failed" || runtime.state === "closed") return;
      if (runtime.state === "executing") {
        throw new Error("Stop the active run before rebasing.");
      }
      if (runtime.state === "ready") {
        runtime = await api.closeRuntime(csrf, runtime.id);
      } else {
        await new Promise((resolve) => setTimeout(resolve, 500));
        runtime = await api.getRuntime(runtime.id);
      }
    }
    throw new Error("Runtime did not close in time.");
  };

  const doRebase = async () => {
    if (!csrf || busy) return;
    setBusy(true);
    setError(null);
    try {
      await closeRuntimeForRebase();
      await api.rebaseWorkingCopy(csrf, projectId, workingCopy.id);
      setConflict(null);
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not rebase the review.");
    } finally {
      setBusy(false);
    }
  };

  const rebasePrompt = (
    <div className="cr-banner cr-conflict" role="alert">
      <div>
        <strong>The project head moved.</strong>
        <span>
          {conflict
            ? ` Base ${conflict.base_snapshot_id.slice(0, 8)} is behind ${conflict.current_snapshot_id.slice(0, 8)}.`
            : " Rebase these pending changes onto the current head, then review and Save again."}
        </span>
      </div>
      <button
        className="btn btn-small"
        onClick={() => void doRebase()}
        disabled={busy}
      >
        {busy ? "Rebasing…" : "Rebase review"}
      </button>
    </div>
  );
  const headMovedNotice = (
    <div className="cr-banner cr-warn">
      The project head moved since this review opened. Save will be rejected
      safely; after that, use Rebase review to generate a fresh comparison.
    </div>
  );

  if (!csId || !cs) {
    return (
      <section className="change-review">
        <header className="change-review-head">
          <strong>Change Review</strong>
          <span className="muted small">No open review.</span>
        </header>
        {workingCopy.state === "conflicted" && rebasePrompt}
        {workingCopy.head_moved &&
          workingCopy.state !== "conflicted" &&
          headMovedNotice}
        {!workingCopy.head_moved && workingCopy.state !== "conflicted" && (
          <p className="small muted">
            Edits from you or the assistant will appear here for Save or Discard.
          </p>
        )}
        {error && <div className="auth-error small">{error}</div>}
      </section>
    );
  }

  const doDiscard = async () => {
    if (!csrf || busy) return;
    setBusy(true);
    setError(null);
    try {
      await api.discardChangeSet(csrf, projectId, csId);
      onChanged();
    } catch {
      setError("Discard failed.");
    } finally {
      setBusy(false);
    }
  };

  const selectedCount = selected.size;

  return (
    <section className="change-review">
      <header className="change-review-head">
        <strong>Change Review</strong>
        <span className="change-counts">
          <span className="cc-add">+{cs.added_count}</span>
          <span className="cc-mod">~{cs.modified_count}</span>
          <span className="cc-del">-{cs.deleted_count}</span>
        </span>
        <button className="btn btn-quiet btn-small" onClick={() => void load()}>
          Refresh
        </button>
      </header>

      {workingCopy.state === "conflicted" && rebasePrompt}
      {workingCopy.head_moved &&
        workingCopy.state !== "conflicted" &&
        headMovedNotice}
      {cs.truncated && (
        <div className="cr-banner cr-warn">
          Partial review — the change set exceeded the review bounds. Absence of
          a path here does not mean it is unchanged.
        </div>
      )}
      {workingCopy.last_exec &&
        workingCopy.last_exec.termination_reason &&
        workingCopy.last_exec.termination_reason !== "done" && (
          <div className="cr-banner cr-warn">
            Last run ended with{" "}
            <code>{workingCopy.last_exec.termination_reason}</code>.
          </div>
        )}

      <ul className="cr-entries">
        {cs.entries.map((e) => (
          <li key={e.id} className="cr-entry">
            <label className="cr-entry-main">
              <input
                type="checkbox"
                checked={selected.has(e.id)}
                onChange={() => toggle(e.id)}
              />
              <span className={`cr-mark cr-${e.change_kind}`}>
                {MARK[e.change_kind]}
              </span>
              <span className="cr-path">{e.path}</span>
              {e.is_binary && <span className="pill">binary</span>}
              {e.executable && <span className="pill">exec</span>}
            </label>
            <button
              className="btn btn-quiet btn-small"
              onClick={() => void showDiff(e)}
            >
              {openDiff?.id === e.id ? "Hide diff" : "Diff"}
            </button>
            {openDiff?.id === e.id && (
              <pre className="cr-diff">{openDiff.text}</pre>
            )}
          </li>
        ))}
      </ul>

      {error && <div className="auth-error small">{error}</div>}

      <div className="cr-actions">
        <input
          className="cr-checkpoint"
          placeholder="Checkpoint name (optional)"
          value={checkpoint}
          onChange={(e) => setCheckpoint(e.target.value)}
        />
        <button
          className="btn btn-danger"
          onClick={() => void doDiscard()}
          disabled={busy}
        >
          Discard
        </button>
        <button
          className="btn"
          onClick={() => void doSave(true)}
          disabled={busy || selectedCount === 0 || !checkpoint.trim()}
          title="Save the selected changes and pin a named checkpoint"
        >
          Save + checkpoint
        </button>
        <button
          className="btn btn-primary"
          onClick={() => void doSave(false)}
          disabled={busy || selectedCount === 0}
        >
          Save selected ({selectedCount})
        </button>
      </div>
      <p className="small muted cr-foot">
        Saving advances the project head — a human review action. The assistant
        cannot Save on your behalf.
      </p>
    </section>
  );
}
