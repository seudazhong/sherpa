import { useCallback, useEffect, useMemo, useState } from "react";

import {
  api,
  ApiError,
  type ProjectFileContent,
  type ProjectFileEntry,
  type WorkingCopySummary,
} from "../api";

function depth(path: string): number {
  return Math.max(0, path.split("/").length - 1);
}

function fileLabel(path: string): string {
  return path.split("/").pop() ?? path;
}

export function ProjectTree({
  sessionId,
  csrf,
  projectName,
  onWorkingCopy,
}: {
  sessionId: string;
  csrf: string | null;
  projectName: string;
  onWorkingCopy: (workingCopy: WorkingCopySummary) => void;
}) {
  const [entries, setEntries] = useState<ProjectFileEntry[]>([]);
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [file, setFile] = useState<ProjectFileContent | null>(null);
  const [isNew, setIsNew] = useState(false);
  const [draft, setDraft] = useState("");
  const [executable, setExecutable] = useState(false);
  const [newPath, setNewPath] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadTree = useCallback(async () => {
    try {
      const page = await api.listProjectFiles(sessionId);
      setEntries(page.entries);
      if (page.truncated) {
        setError("This tree is partial. Narrower folder browsing is required.");
      }
    } catch {
      setError("Could not load the effective project tree.");
    }
  }, [sessionId]);

  useEffect(() => {
    setSelectedPath(null);
    setFile(null);
    setIsNew(false);
    setDraft("");
    setError(null);
    void loadTree();
  }, [loadTree]);

  const isDirty =
    selectedPath !== null &&
    (file
      ? draft !== file.content || executable !== file.executable
      : isNew && (draft !== "" || executable));

  const canLeaveDraft = () =>
    !isDirty || window.confirm("Discard your unsaved file edits?");

  const openFile = async (entry: ProjectFileEntry) => {
    if (!canLeaveDraft()) return;
    setSelectedPath(entry.path);
    setIsNew(false);
    setError(null);
    if (entry.entry_kind !== "file") {
      setFile(null);
      setDraft("");
      return;
    }
    try {
      const next = await api.getProjectFile(sessionId, entry.path);
      setFile(next);
      setDraft(next.content);
      setExecutable(next.executable);
    } catch (e) {
      setFile(null);
      setDraft("");
      setError(
        e instanceof ApiError && e.status === 413
          ? "This file is too large for the built-in editor."
          : "This file is binary or could not be loaded.",
      );
    }
  };

  const beginNew = () => {
    const path = newPath.trim().replaceAll("\\", "/");
    if (!path) return;
    const existing = entries.find((entry) => entry.path === path);
    if (existing) {
      setError("That path already exists; it has been selected instead.");
      void openFile(existing);
      return;
    }
    if (!canLeaveDraft()) return;
    setSelectedPath(path);
    setFile(null);
    setIsNew(true);
    setDraft("");
    setExecutable(false);
    setError(null);
  };

  const save = async () => {
    if (!csrf || !selectedPath || busy) return;
    setBusy(true);
    setError(null);
    try {
      const workingCopy = await api.writeProjectFile(csrf, sessionId, {
        path: selectedPath,
        content: draft,
        executable,
        if_hash: file?.content_hash ?? null,
        create_only: isNew,
      });
      onWorkingCopy(workingCopy);
      await loadTree();
      const next = await api.getProjectFile(sessionId, selectedPath);
      setFile(next);
      setIsNew(false);
      setDraft(next.content);
      setExecutable(next.executable);
      setNewPath("");
    } catch (e) {
      setError(
        e instanceof ApiError && e.status === 409
          ? "This file changed after you opened it. Reload before saving."
          : "Could not save this file.",
      );
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!csrf || !selectedPath || busy) return;
    const selected = entries.find((entry) => entry.path === selectedPath);
    if (!window.confirm(`Delete ${selectedPath} from the pending working copy?`)) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const workingCopy = await api.deleteProjectFile(
        csrf,
        sessionId,
        selectedPath,
        selected?.entry_kind === "dir",
        file?.content_hash ?? selected?.content_hash,
      );
      onWorkingCopy(workingCopy);
      setSelectedPath(null);
      setFile(null);
      setIsNew(false);
      setDraft("");
      await loadTree();
    } catch (e) {
      setError(
        e instanceof ApiError && e.status === 409
          ? "The runtime is busy or this file changed. Refresh and try again."
          : "Could not delete this path.",
      );
    } finally {
      setBusy(false);
    }
  };

  const selectedEntry = useMemo(
    () => entries.find((entry) => entry.path === selectedPath) ?? null,
    [entries, selectedPath],
  );

  return (
    <div className="project-tree">
      <header className="project-pane-head">
        <div>
          <strong>Files</strong>
          <span title={projectName}>{projectName}</span>
        </div>
        <button
          className="btn btn-quiet btn-small"
          onClick={() => void loadTree()}
        >
          Refresh
        </button>
      </header>

      <div className="project-new-file">
        <input
          value={newPath}
          placeholder="new/path.txt"
          aria-label="New project file path"
          onChange={(event) => setNewPath(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") beginNew();
          }}
        />
        <button className="btn btn-small" onClick={beginNew} disabled={!newPath.trim()}>
          New
        </button>
      </div>

      <div className="project-tree-list" role="tree" aria-label="Effective project tree">
        {entries.length === 0 && (
          <span className="small muted project-tree-empty">Empty project</span>
        )}
        {entries.map((entry) => (
          <button
            key={entry.path}
            role="treeitem"
            aria-selected={selectedPath === entry.path}
            className={`project-tree-row ${
              selectedPath === entry.path ? "active" : ""
            }`}
            style={{ paddingLeft: `${10 + depth(entry.path) * 14}px` }}
            onClick={() => void openFile(entry)}
            title={entry.path}
          >
            <span aria-hidden="true">
              {entry.entry_kind === "dir" ? "▸" : entry.executable ? "◆" : "·"}
            </span>
            <span>{fileLabel(entry.path)}</span>
            {entry.entry_kind === "file" && (
              <small>{entry.size_bytes} B</small>
            )}
          </button>
        ))}
      </div>

      {selectedPath && (
        <section className="project-editor">
          <header>
            <strong title={selectedPath}>{selectedPath}</strong>
            {selectedEntry && (
              <button
                className="btn btn-danger btn-small"
                onClick={() => void remove()}
                disabled={busy}
              >
                Delete
              </button>
            )}
          </header>
          {selectedEntry && selectedEntry.entry_kind !== "file" ? (
            <p className="small muted">
              {selectedEntry.entry_kind === "dir"
                ? "Select a file to edit. Deleting this folder is recursive and remains reviewable until Save."
                : "Symlinks are shown for context but are not editable in the built-in editor."}
            </p>
          ) : (
            <>
              <textarea
                value={draft}
                aria-label={`Edit ${selectedPath}`}
                spellCheck={false}
                onChange={(event) => setDraft(event.target.value)}
              />
              <footer>
                <label>
                  <input
                    type="checkbox"
                    checked={executable}
                    onChange={(event) => setExecutable(event.target.checked)}
                  />
                  Executable
                </label>
                <button
                  className="btn btn-primary btn-small"
                  onClick={() => void save()}
                  disabled={busy}
                >
                  {busy ? "Saving…" : "Save to changes"}
                </button>
              </footer>
            </>
          )}
        </section>
      )}

      {error && <div className="auth-error small project-tree-error">{error}</div>}
    </div>
  );
}
