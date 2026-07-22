import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  api,
  driveDownloadUrl,
  type DriveNode,
  type DriveVersion,
  type StorageAccount,
} from "../api";
import { useAuth } from "../auth";
import Sidebar from "../components/Sidebar";

function fmtSize(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  return `${(n / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

function fileLabel(node: DriveNode): string {
  if (node.node_type === "folder") return "DIR";
  if (!node.name.includes(".")) return "FILE";
  return node.name.split(".").pop()?.slice(0, 3).toUpperCase() ?? "FILE";
}

interface Crumb {
  id: string | null;
  name: string;
}

export default function WorkspaceView() {
  const { csrf } = useAuth();
  const [nodes, setNodes] = useState<DriveNode[]>([]);
  const [trail, setTrail] = useState<Crumb[]>([{ id: null, name: "Drive" }]);
  const [storage, setStorage] = useState<StorageAccount | null>(null);
  const [query, setQuery] = useState("");
  const [showTrash, setShowTrash] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [versionsFor, setVersionsFor] = useState<string | null>(null);
  const [versions, setVersions] = useState<DriveVersion[]>([]);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const parent = trail[trail.length - 1];
  const searching = query.trim().length > 0;

  const loadStorage = useCallback(async () => {
    try {
      setStorage(await api.driveStorage());
    } catch {
      /* non-fatal */
    }
  }, []);

  const load = useCallback(async () => {
    try {
      const q = query.trim();
      const page = showTrash
        ? await api.driveList({ trashed: true, query: q || undefined, limit: 200 })
        : q
          ? await api.driveList({ query: q, limit: 100 })
          : await api.driveList({ parent: parent.id, limit: 200 });
      setNodes(page.items);
      setError(null);
    } catch {
      setError("Could not load your Drive. Is the backend running?");
    }
  }, [parent.id, query, showTrash]);

  useEffect(() => {
    const t = setTimeout(() => void load(), 180);
    return () => clearTimeout(t);
  }, [load]);

  useEffect(() => {
    void loadStorage();
  }, [loadStorage]);

  const visible = useMemo(() => {
    return [...nodes].sort((a, b) => {
      if (a.node_type !== b.node_type) return a.node_type === "folder" ? -1 : 1;
      return a.name.localeCompare(b.name);
    });
  }, [nodes]);

  const refresh = async () => {
    await load();
    await loadStorage();
  };

  const openFolder = (node: DriveNode) => {
    setQuery("");
    setShowTrash(false);
    setTrail((t) => [...t, { id: node.id, name: node.name }]);
  };

  const goCrumb = (idx: number) => {
    setQuery("");
    setTrail((t) => t.slice(0, idx + 1));
  };

  const newFolder = async () => {
    if (!csrf) return;
    const name = window.prompt("New folder name");
    if (!name?.trim()) return;
    setBusy("folder");
    try {
      await api.driveCreateFolder(csrf, parent.id, name.trim());
      await refresh();
    } catch {
      setError("Could not create folder (name may already exist).");
    } finally {
      setBusy(null);
    }
  };

  const upload = async (file: File) => {
    if (!csrf) return;
    setBusy("upload");
    setError(null);
    try {
      await api.driveUpload(csrf, parent.id, file);
      if (inputRef.current) inputRef.current.value = "";
      await refresh();
    } catch (e) {
      const status = (e as { status?: number }).status;
      setError(
        status === 507
          ? "Not enough storage space for this upload."
          : status === 413
            ? "File is too large."
            : "Upload failed.",
      );
    } finally {
      setBusy(null);
    }
  };

  const trash = async (node: DriveNode) => {
    if (!csrf) return;
    setBusy(node.id);
    try {
      await api.driveTrash(csrf, node.id);
      await refresh();
    } catch {
      setError("Could not move to trash.");
    } finally {
      setBusy(null);
    }
  };

  const restore = async (node: DriveNode) => {
    if (!csrf) return;
    setBusy(node.id);
    try {
      await api.driveRestore(csrf, node.id);
      await refresh();
    } catch {
      setError("Could not restore (a live item may share its name).");
    } finally {
      setBusy(null);
    }
  };

  const purge = async (node: DriveNode) => {
    if (!csrf) return;
    if (
      !window.confirm(
        `Permanently delete “${node.name}”? This cannot be undone.`,
      )
    )
      return;
    setBusy(node.id);
    try {
      await api.drivePurge(csrf, node.id);
      await refresh();
    } catch {
      setError("Could not permanently delete.");
    } finally {
      setBusy(null);
    }
  };

  const startRename = (node: DriveNode) => {
    setRenamingId(node.id);
    setRenameValue(node.name);
  };

  const commitRename = async (node: DriveNode) => {
    if (!csrf || !renameValue.trim()) return;
    setBusy(node.id);
    try {
      await api.driveRename(csrf, node.id, node.version, renameValue.trim());
      setRenamingId(null);
      await refresh();
    } catch {
      setError("Rename failed (name may already exist).");
    } finally {
      setBusy(null);
    }
  };

  const toggleVersions = async (node: DriveNode) => {
    if (versionsFor === node.id) {
      setVersionsFor(null);
      return;
    }
    try {
      setVersions(await api.driveVersions(node.id));
      setVersionsFor(node.id);
    } catch {
      setError("Could not load versions.");
    }
  };

  const restoreVersion = async (node: DriveNode, version: number) => {
    if (!csrf) return;
    setBusy(node.id);
    try {
      await api.driveRestoreVersion(csrf, node.id, version);
      setVersionsFor(null);
      await refresh();
    } catch {
      setError("Could not restore that version.");
    } finally {
      setBusy(null);
    }
  };

  const usedPct = storage
    ? Math.min(100, (storage.used_bytes / storage.quota_bytes) * 100)
    : 0;

  return (
    <div className="app">
      <Sidebar />
      <main className="main">
        <header className="topbar">
          <div className="page-heading">
            <span className="page-eyebrow">Personal workspace</span>
            <h2>Drive</h2>
            <p className="page-sub small">
              Your private files and folders — versioned, and available to Sherpa
            </p>
          </div>
          <label className="btn btn-primary drive-upload-btn">
            <input
              ref={inputRef}
              type="file"
              hidden
              aria-label="Upload file"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) void upload(f);
              }}
            />
            {busy === "upload" ? "Uploading…" : "Upload"}
          </label>
        </header>

        <div className="inbox page-content">
          {error && <div className="auth-error">{error}</div>}

          {storage && (
            <section className="storage-card">
              <div className="storage-head">
                <span className="section-kicker">Storage</span>
                <strong>
                  {fmtSize(storage.used_bytes)} of {fmtSize(storage.quota_bytes)}{" "}
                  used
                </strong>
              </div>
              <div className="storage-bar">
                <span
                  className="storage-bar-fill"
                  style={{ width: `${usedPct}%` }}
                />
              </div>
              <div className="storage-legend">
                <span>
                  <b>{fmtSize(storage.available_bytes)}</b> available
                </span>
                <span>
                  <b>{fmtSize(storage.trashed_bytes)}</b> in trash
                </span>
                {storage.reserved_bytes > 0 && (
                  <span>
                    <b>{fmtSize(storage.reserved_bytes)}</b> reserved
                  </span>
                )}
              </div>
            </section>
          )}

          <div className="drive-toolbar">
            <nav className="drive-breadcrumbs" aria-label="Breadcrumb">
              {searching ? (
                <span className="crumb current">
                  Search results for “{query.trim()}”
                </span>
              ) : (
                trail.map((c, i) => (
                  <span key={`${c.id ?? "root"}-${i}`} className="crumb-wrap">
                    {i > 0 && <span className="crumb-sep">/</span>}
                    <button
                      className={
                        "crumb" + (i === trail.length - 1 ? " current" : "")
                      }
                      onClick={() => goCrumb(i)}
                      disabled={i === trail.length - 1}
                    >
                      {c.name}
                    </button>
                  </span>
                ))
              )}
            </nav>
            <div className="drive-toolbar-actions">
              <label className="session-search-box drive-search">
                <span aria-hidden="true">⌕</span>
                <input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Search Drive…"
                  aria-label="Search Drive"
                  type="search"
                />
              </label>
              <button
                className="btn"
                disabled={busy === "folder" || searching}
                onClick={() => void newFolder()}
              >
                New folder
              </button>
              <button
                className={"btn" + (showTrash ? " btn-primary" : "")}
                onClick={() => {
                  setShowTrash((v) => !v);
                  setQuery("");
                }}
              >
                {showTrash ? "Exit trash" : "Trash"}
              </button>
            </div>
          </div>

          <section className="content-section">
            <div className="section-head">
              <span>
                {showTrash ? "Trash" : searching ? "Matches" : "Contents"}
              </span>
              <span className="count">{visible.length}</span>
            </div>

            {visible.length === 0 && (
              <div className="empty-state">
                <span className="empty-icon" aria-hidden="true">
                  {showTrash ? "🗑" : "▢"}
                </span>
                <strong>
                  {showTrash
                    ? "Trash is empty"
                    : searching
                      ? "No matches"
                      : "This folder is empty"}
                </strong>
                <span>
                  {showTrash
                    ? "Items you delete land here until permanently removed."
                    : searching
                      ? "Try another keyword."
                      : "Upload a file or create a folder to get started."}
                </span>
              </div>
            )}

            {visible.map((node) => (
              <div key={node.id}>
                <article className="todo-row file-row drive-row">
                  <span
                    className={
                      "file-type-icon" +
                      (node.node_type === "folder" ? " folder" : "")
                    }
                    aria-hidden="true"
                  >
                    {fileLabel(node)}
                  </span>
                  {renamingId === node.id ? (
                    <span className="drive-rename">
                      <input
                        value={renameValue}
                        autoFocus
                        onChange={(e) => setRenameValue(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") void commitRename(node);
                          if (e.key === "Escape") setRenamingId(null);
                        }}
                        aria-label="New name"
                      />
                      <button
                        className="btn btn-quiet todo-action"
                        onClick={() => void commitRename(node)}
                      >
                        Save
                      </button>
                      <button
                        className="btn btn-quiet todo-action"
                        onClick={() => setRenamingId(null)}
                      >
                        Cancel
                      </button>
                    </span>
                  ) : (
                    <button
                      className="todo-title drive-name"
                      onClick={() =>
                        node.node_type === "folder" &&
                        !node.trashed &&
                        openFolder(node)
                      }
                      disabled={node.node_type !== "folder" || node.trashed}
                    >
                      {node.name}
                      <span className="item-subtitle">
                        {node.node_type === "folder"
                          ? "Folder"
                          : `${fmtSize(node.size_bytes)} · v${node.version} · ${new Date(
                              node.updated_at,
                            ).toLocaleDateString()}`}
                      </span>
                    </button>
                  )}

                  {renamingId !== node.id && (
                    <span className="drive-actions">
                      {node.trashed ? (
                        <>
                          <button
                            className="btn btn-quiet todo-action"
                            disabled={busy === node.id}
                            onClick={() => void restore(node)}
                          >
                            Restore
                          </button>
                          <button
                            className="btn btn-quiet todo-action danger"
                            disabled={busy === node.id}
                            onClick={() => void purge(node)}
                          >
                            Delete forever
                          </button>
                        </>
                      ) : (
                        <>
                          {node.node_type === "file" && (
                            <>
                              <a
                                className="btn btn-quiet todo-action"
                                href={driveDownloadUrl(node.id)}
                              >
                                Download
                              </a>
                              <button
                                className="btn btn-quiet todo-action"
                                onClick={() => void toggleVersions(node)}
                              >
                                Versions
                              </button>
                            </>
                          )}
                          <button
                            className="btn btn-quiet todo-action"
                            onClick={() => startRename(node)}
                          >
                            Rename
                          </button>
                          <button
                            className="btn btn-quiet todo-action"
                            disabled={busy === node.id}
                            onClick={() => void trash(node)}
                          >
                            Trash
                          </button>
                        </>
                      )}
                    </span>
                  )}
                </article>

                {versionsFor === node.id && (
                  <div className="drive-version-list">
                    {versions.length === 0 && (
                      <span className="drive-version-empty">
                        No previous versions.
                      </span>
                    )}
                    {versions.map((v) => (
                      <div className="drive-version-row" key={v.version}>
                        <span>
                          v{v.version} · {fmtSize(v.size_bytes)} ·{" "}
                          {new Date(v.created_at).toLocaleString()}
                        </span>
                        <button
                          className="btn btn-quiet todo-action"
                          disabled={busy === node.id}
                          onClick={() => void restoreVersion(node, v.version)}
                        >
                          Restore this version
                        </button>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </section>
        </div>
      </main>
    </div>
  );
}
