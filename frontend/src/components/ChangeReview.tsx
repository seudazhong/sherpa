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
    void load();
  }, [load]);

  if (!csId || !cs) {
    return (
      <section className="change-review">
        <header className="change-review-head">
          <strong>Change Review</strong>
          <span className="muted small">No pending changes yet.</span>
        </header>
        <p className="small muted">
          Ask the assistant to edit files or run tests in this project; staged
          changes will appear here for you to Save or Discard.
        </p>
      </section>
    );
  }

  const toggle = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const showDiff = async (entry: ChangeSetEntry) => {
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
    if (!csrf || busy) return;
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

      {workingCopy.head_moved && (
        <div className="cr-banner cr-conflict" role="alert">
          The project head moved since this working copy opened. Saving will be
          rejected until you review the rebased changes.
        </div>
      )}
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
