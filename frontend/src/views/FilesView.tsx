import { useEffect, useRef, useState } from "react";

import { api, fileDownloadUrl, type FileItem } from "../api";
import { useAuth } from "../auth";
import Sidebar from "../components/Sidebar";

function fmtSize(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
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
          <div>
            <h2>Files</h2>
            <p className="page-sub small">
              Your private file workspace — the agent can read and write here.
            </p>
          </div>
        </header>

        <div className="inbox">
          {error && <div className="auth-error">{error}</div>}

          <section>
            <div className="section-head">Upload</div>
            <article className="cand-card">
              <div className="cand-main">
                <div className="cand-meta small">
                  <input
                    ref={inputRef}
                    type="file"
                    aria-label="File"
                    onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                  />
                  <label>
                    &nbsp;Save as&nbsp;
                    <input
                      value={path}
                      onChange={(e) => setPath(e.target.value)}
                      placeholder={file ? file.name : "notes/todo.md"}
                      aria-label="File path"
                    />
                  </label>
                </div>
              </div>
              <div className="cand-actions">
                <button
                  className="btn btn-primary"
                  disabled={busy === "upload" || !file}
                  onClick={() => void upload()}
                >
                  Upload
                </button>
              </div>
            </article>
          </section>

          <section>
            <div className="section-head">
              Files <span className="count">{files.length}</span>
            </div>
            {files.length === 0 && (
              <div className="empty small muted">
                No files yet. Upload one above, or ask Sherpa to write a file.
              </div>
            )}
            {files.map((f) => (
              <article className="todo-row" key={f.id}>
                <span className="todo-title">{f.path}</span>
                <span className="small muted">
                  {fmtSize(f.size_bytes)} · v{f.version}
                </span>
                <a className="btn todo-action" href={fileDownloadUrl(f.id)}>
                  Download
                </a>
                <button
                  className="btn todo-action"
                  disabled={busy === f.id}
                  onClick={() => void remove(f.id)}
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
