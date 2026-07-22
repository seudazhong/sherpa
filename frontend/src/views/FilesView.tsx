import { useEffect, useRef, useState } from "react";

import { api, fileDownloadUrl, type FileItem } from "../api";
import { useAuth } from "../auth";
import Sidebar from "../components/Sidebar";

function fmtSize(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

function fileLabel(path: string): string {
  if (!path.includes(".")) return "FILE";
  return path.split(".").pop()?.slice(0, 3).toUpperCase() ?? "FILE";
}

export default function FilesView() {
  const { csrf } = useAuth();
  const [files, setFiles] = useState<FileItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [path, setPath] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const load = async () => {
    try {
      setFiles((await api.listFiles()).items);
    } catch {
      setError("Could not load files. Is the backend running?");
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const upload = async () => {
    if (!csrf || !file) return;
    const target = path.trim() || file.name;
    setBusy("upload");
    setError(null);
    try {
      await api.uploadFile(csrf, target, file);
      setPath("");
      setFile(null);
      if (inputRef.current) inputRef.current.value = "";
      await load();
    } catch {
      setError("Upload failed (max 10 MB, no path traversal).");
    } finally {
      setBusy(null);
    }
  };

  const remove = async (id: string) => {
    if (!csrf) return;
    setBusy(id);
    setError(null);
    try {
      await api.deleteFile(csrf, id);
      await load();
    } catch {
      setError("Delete failed.");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="app">
      <Sidebar />
      <main className="main">
        <header className="topbar">
          <div className="page-heading">
            <span className="page-eyebrow">Private workspace</span>
            <h2>Files</h2>
            <p className="page-sub small">
              Durable files Sherpa can read and write on your behalf
            </p>
          </div>
        </header>

        <div className="inbox page-content">
          {error && <div className="auth-error">{error}</div>}

          <section className="upload-card">
            <div className="upload-illustration" aria-hidden="true">
              ↑
            </div>
            <div className="upload-copy">
              <span className="section-kicker">Add a file</span>
              <h3>Upload to your private workspace</h3>
              <p>
                Files stay private and available to Sherpa across conversations.
                Maximum 10 MB.
              </p>
              <div className="control-grid two">
                <label className="control file-control">
                  <span>Choose file</span>
                  <input
                    ref={inputRef}
                    type="file"
                    aria-label="File"
                    onChange={(event) =>
                      setFile(event.target.files?.[0] ?? null)
                    }
                  />
                </label>
                <label className="control">
                  <span>Save as</span>
                  <input
                    value={path}
                    onChange={(event) => setPath(event.target.value)}
                    placeholder={file ? file.name : "notes/todo.md"}
                    aria-label="File path"
                  />
                </label>
              </div>
              {file && (
                <span className="selected-file">
                  {file.name} · {fmtSize(file.size)}
                </span>
              )}
            </div>
            <button
              className="btn btn-primary"
              disabled={busy === "upload" || !file}
              onClick={() => void upload()}
            >
              Upload file
            </button>
          </section>

          <section className="content-section">
            <div className="section-head">
              <span>Workspace files</span>
              <span className="count">{files.length}</span>
            </div>
            {files.length === 0 && (
              <div className="empty-state">
                <span className="empty-icon" aria-hidden="true">
                  □
                </span>
                <strong>No files yet</strong>
                <span>
                  Upload one above, or ask Sherpa to write a file into this
                  workspace.
                </span>
              </div>
            )}
            {files.map((item) => (
              <article className="todo-row file-row" key={item.id}>
                <span className="file-type-icon" aria-hidden="true">
                  {fileLabel(item.path)}
                </span>
                <span className="todo-title">
                  {item.path}
                  <span className="item-subtitle">
                    {fmtSize(item.size_bytes)} · {item.content_type} · updated{" "}
                    {new Date(item.updated_at).toLocaleDateString()}
                  </span>
                </span>
                <a
                  className="btn btn-quiet todo-action"
                  href={fileDownloadUrl(item.id)}
                >
                  Download
                </a>
                <button
                  className="btn btn-quiet todo-action"
                  disabled={busy === item.id}
                  onClick={() => void remove(item.id)}
                >
                  Delete
                </button>
              </article>
            ))}
          </section>
        </div>
      </main>
    </div>
  );
}
