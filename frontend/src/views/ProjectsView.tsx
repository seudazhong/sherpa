import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import {
  api,
  type Project,
  type ProjectEntry,
  type ProjectImportStatus,
  type ProjectSnapshot,
  type ProjectTemplate,
} from "../api";
import { useAuth } from "../auth";
import Sidebar from "../components/Sidebar";

function fmtSize(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  return `${(n / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

function fmtTime(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

const STATUS_PILL: Record<ProjectImportStatus, string> = {
  ready: "pill-success",
  importing: "pill-running",
  failed: "pill-error",
  none: "pill-idle",
};

const STATUS_LABEL: Record<ProjectImportStatus, string> = {
  ready: "ready",
  importing: "importing",
  failed: "import failed",
  none: "no snapshot",
};

type CreatePath = "archive" | "blank" | "template" | "github";
type Surface = "list" | "new" | "detail";

export default function ProjectsView() {
  const { csrf } = useAuth();
  const navigate = useNavigate();
  const [surface, setSurface] = useState<Surface>("list");
  const [projects, setProjects] = useState<Project[]>([]);
  const [templates, setTemplates] = useState<ProjectTemplate[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  // New-project form.
  const [path, setPath] = useState<CreatePath>("archive");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [templateId, setTemplateId] = useState("notes");
  const fileRef = useRef<HTMLInputElement | null>(null);

  // Detail surface.
  const [detail, setDetail] = useState<Project | null>(null);
  const [entries, setEntries] = useState<ProjectEntry[]>([]);
  const [snapshots, setSnapshots] = useState<ProjectSnapshot[]>([]);

  const load = useCallback(async () => {
    try {
      const page = await api.listProjects();
      setProjects(page.items);
    } catch {
      setError("Could not load your projects. Is the backend running?");
    }
  }, []);

  useEffect(() => {
    void load();
    void api
      .projectTemplates()
      .then(setTemplates)
      .catch(() => {});
  }, [load]);

  // Poll while any project is still importing.
  const importing = useMemo(
    () => projects.some((p) => p.import_status === "importing"),
    [projects],
  );
  useEffect(() => {
    if (surface !== "list" || !importing) return;
    const t = setInterval(() => void load(), 2500);
    return () => clearInterval(t);
  }, [surface, importing, load]);

  const openDetail = useCallback(async (id: string) => {
    setError(null);
    try {
      const [proj, tree, snaps] = await Promise.all([
        api.getProject(id),
        api.projectTree(id),
        api.projectSnapshots(id),
      ]);
      setDetail(proj);
      setEntries(tree.entries);
      setSnapshots(snaps);
      setSurface("detail");
    } catch {
      setError("Could not open that project.");
    }
  }, []);

  const resetForm = () => {
    setName("");
    setDescription("");
    setTemplateId("notes");
    setPath("archive");
    if (fileRef.current) fileRef.current.value = "";
  };

  const submitCreate = async () => {
    if (!csrf) return;
    const trimmed = name.trim();
    if (!trimmed) {
      setError("Give the project a name.");
      return;
    }
    setError(null);
    setBusy("create");
    try {
      if (path === "archive") {
        const file = fileRef.current?.files?.[0];
        if (!file) {
          setError("Choose an archive (.zip / .tar / .tar.gz) to import.");
          setBusy(null);
          return;
        }
        await api.importProjectArchive(csrf, trimmed, file);
      } else if (path === "template") {
        await api.createProject(csrf, {
          name: trimmed,
          description: description.trim() || null,
          template_id: templateId,
        });
      } else {
        await api.createProject(csrf, {
          name: trimmed,
          description: description.trim() || null,
        });
      }
      resetForm();
      await load();
      setSurface("list");
    } catch (e) {
      const status = (e as { status?: number }).status;
      if (status === 409) setError("A project with that name already exists.");
      else if (status === 413) setError("That archive is too large.");
      else if (status === 507) setError("Not enough storage quota for this import.");
      else setError("Could not create the project.");
    } finally {
      setBusy(null);
    }
  };

  const openChat = async (id: string) => {
    if (!csrf) return;
    setBusy("chat");
    try {
      const session = await api.openProjectChat(csrf, id);
      navigate(`/?session=${session.id}`);
    } catch {
      setError("Could not open a project chat.");
      setBusy(null);
    }
  };

  const importingCount = projects.filter(
    (p) => p.import_status === "importing",
  ).length;

  return (
    <div className="app">
      <Sidebar />
      <main className="main">
        <header className="topbar">
          <div className="page-heading">
            <span className="page-eyebrow">Workspace · /work/projects</span>
            <h2>Projects</h2>
            <p className="page-sub small">
              A project is durable development state — a read-only file tree,
              snapshots, and activity. It sits beside Drive in your personal
              workspace. Snapshots reuse the same deduped storage as Drive.
            </p>
          </div>
          {surface === "list" && (
            <button
              className="btn btn-primary"
              onClick={() => {
                resetForm();
                setError(null);
                setSurface("new");
              }}
            >
              <span aria-hidden="true">＋</span> New project
            </button>
          )}
          {surface !== "list" && (
            <button
              className="btn btn-quiet"
              onClick={() => {
                setError(null);
                setSurface("list");
              }}
            >
              ← Back to projects
            </button>
          )}
        </header>

        {error && <div className="auth-error">{error}</div>}

        {surface === "list" && (
          <section className="panel">
            <div className="proj-metrics">
              <article className="proj-metric">
                <strong>{projects.length}</strong>
                <span>projects</span>
              </article>
              <article className="proj-metric">
                <strong>{importingCount}</strong>
                <span>importing</span>
              </article>
              <span className="proj-note small muted">
                W2a = blank / template / archive import (<b>no GitHub</b> — that
                is W2b). All projects are <code>unbound</code> (no source).
              </span>
            </div>

            {projects.length === 0 ? (
              <div className="empty-state">
                <span className="empty-icon" aria-hidden="true">
                  ▦
                </span>
                <strong>No projects yet</strong>
                <p className="small muted">
                  Start blank, from a template, or import an archive.
                </p>
                <button
                  className="btn btn-primary"
                  onClick={() => {
                    resetForm();
                    setSurface("new");
                  }}
                >
                  <span aria-hidden="true">＋</span> New project
                </button>
              </div>
            ) : (
              <ul className="proj-list">
                {projects.map((p) => (
                  <li key={p.id} className="proj-row">
                    <button
                      className="proj-open"
                      onClick={() => void openDetail(p.id)}
                    >
                      <span className="proj-name">{p.name}</span>
                      {p.description && (
                        <span className="proj-desc small muted">
                          {p.description}
                        </span>
                      )}
                      <span className="proj-meta small muted">
                        <span
                          className={`pill ${STATUS_PILL[p.import_status]}`}
                        >
                          {STATUS_LABEL[p.import_status]}
                        </span>
                        <span>{fmtSize(p.used_bytes)}</span>
                        <span>unbound</span>
                        <span>{fmtTime(p.last_activity_at ?? p.updated_at)}</span>
                        {p.import_status === "failed" &&
                          p.import_failure_reason && (
                            <span className="proj-fail">
                              {p.import_failure_reason}
                            </span>
                          )}
                      </span>
                    </button>
                    <div className="proj-row-actions">
                      <button
                        className="btn btn-quiet"
                        onClick={() => void openChat(p.id)}
                        disabled={busy === "chat"}
                      >
                        Open in Chat
                      </button>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </section>
        )}

        {surface === "new" && (
          <section className="panel proj-new">
            <h3>Choose a starting point</h3>
            <p className="small muted">
              A project is durable Sherpa state. W2a offers three creation paths.
            </p>
            <div className="proj-paths">
              <button
                className={`proj-path${path === "archive" ? " active" : ""}`}
                onClick={() => setPath("archive")}
              >
                <strong>Upload archive</strong>
                <span className="small muted">
                  Safely expand a ZIP / TAR into a new unbound project snapshot.
                </span>
              </button>
              <button
                className={`proj-path${path === "blank" ? " active" : ""}`}
                onClick={() => setPath("blank")}
              >
                <strong>Blank project</strong>
                <span className="small muted">
                  Start empty and add files as you work.
                </span>
              </button>
              <button
                className={`proj-path${path === "template" ? " active" : ""}`}
                onClick={() => setPath("template")}
              >
                <strong>Use a template</strong>
                <span className="small muted">
                  Copy starter files; the template's history is severed.
                </span>
              </button>
              <button
                className="proj-path disabled"
                disabled
                title="GitHub import lands in W2b"
              >
                <strong>Import from GitHub</strong>
                <span className="small muted">
                  One-time branch import. <b>W2a excludes it</b> — planned for
                  W2b.
                </span>
              </button>
            </div>

            <div className="proj-form">
              <label className="proj-field">
                <span>Project name</span>
                <input
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="Pricing calculator"
                  maxLength={200}
                />
              </label>

              {path !== "archive" && (
                <label className="proj-field">
                  <span>Description (optional)</span>
                  <input
                    value={description}
                    onChange={(e) => setDescription(e.target.value)}
                    placeholder="What is this project for?"
                  />
                </label>
              )}

              {path === "template" && (
                <label className="proj-field">
                  <span>Template</span>
                  <select
                    value={templateId}
                    onChange={(e) => setTemplateId(e.target.value)}
                  >
                    {templates.map((t) => (
                      <option key={t.id} value={t.id}>
                        {t.name} — {t.description}
                      </option>
                    ))}
                  </select>
                </label>
              )}

              {path === "archive" && (
                <>
                  <label className="proj-field">
                    <span>Archive file (.zip / .tar / .tar.gz)</span>
                    <input
                      ref={fileRef}
                      type="file"
                      accept=".zip,.tar,.tgz,.gz,application/zip,application/x-tar,application/gzip"
                    />
                  </label>
                  <p className="proj-safety small muted">
                    <b>Isolated staging + bounded expansion.</b> Size / file
                    count / expansion ratio / depth caps are enforced; absolute
                    and traversal (<code>..</code>) paths, devices, FIFOs, hard
                    links, and escaping symlinks are rejected before anything is
                    written to an immutable snapshot. Credentials never enter a
                    project tree.
                  </p>
                </>
              )}

              <div className="proj-form-actions">
                <button
                  className="btn btn-primary"
                  onClick={() => void submitCreate()}
                  disabled={busy === "create"}
                >
                  {path === "archive" ? "Import project" : "Create project"}
                </button>
                <button
                  className="btn btn-quiet"
                  onClick={() => setSurface("list")}
                >
                  Cancel
                </button>
              </div>
              <p className="small muted">
                Blank / template create in one transaction (immediate snapshot).
                Archive import returns while the durable job builds the initial
                snapshot; the list updates when it's ready.
              </p>
            </div>
          </section>
        )}

        {surface === "detail" && detail && (
          <section className="panel proj-detail">
            <div className="proj-detail-head">
              <div>
                <h3>{detail.name}</h3>
                <span className="small muted">
                  {detail.current_snapshot_id
                    ? `head snapshot · unbound (no source)`
                    : detail.import_status === "failed"
                      ? `import failed${detail.import_failure_reason ? ` · ${detail.import_failure_reason}` : ""}`
                      : "importing…"}
                </span>
              </div>
              <button
                className="btn btn-primary"
                onClick={() => void openChat(detail.id)}
                disabled={busy === "chat" || !detail.current_snapshot_id}
              >
                Open in Chat
              </button>
            </div>

            <div className="proj-facts">
              <span className="fact">
                <strong>Storage</strong>
                {fmtSize(detail.used_bytes)}
              </span>
              <span className="fact">
                <strong>Source</strong>unbound
              </span>
              <span className="fact">
                <strong>Snapshots</strong>
                {snapshots.length}
              </span>
              <span className="fact">
                <strong>Updated</strong>
                {fmtTime(detail.last_activity_at ?? detail.updated_at)}
              </span>
            </div>

            <div className="proj-detail-grid">
              <div className="proj-tree-card">
                <h4>File tree (read-only)</h4>
                {entries.length === 0 ? (
                  <p className="small muted">
                    Empty project — no files in this snapshot yet.
                  </p>
                ) : (
                  <ul className="proj-tree">
                    {entries.map((e) => {
                      const depth = e.path.split("/").length - 1;
                      const base = e.path.split("/").pop() ?? e.path;
                      return (
                        <li
                          key={e.path}
                          className={`proj-tree-row kind-${e.entry_kind}`}
                          style={{ paddingLeft: `${depth * 16 + 4}px` }}
                        >
                          <span className="proj-tree-name">
                            {e.entry_kind === "dir" ? `${base}/` : base}
                          </span>
                          {e.entry_kind === "file" && (
                            <span className="small muted">
                              {fmtSize(e.size_bytes)}
                            </span>
                          )}
                          {e.entry_kind === "symlink" && (
                            <span className="small muted">symlink</span>
                          )}
                        </li>
                      );
                    })}
                  </ul>
                )}
              </div>

              <div className="proj-side">
                <div className="proj-side-card">
                  <h4>Snapshots</h4>
                  <ul className="proj-snaps">
                    {snapshots.map((s) => (
                      <li key={s.id}>
                        <span className="pill pill-idle">{s.reason}</span>
                        <span className="small">
                          {s.entry_count} entries · {fmtSize(s.size_bytes)}
                        </span>
                        <span className="small muted">
                          {fmtTime(s.created_at)}
                        </span>
                      </li>
                    ))}
                    {snapshots.length === 0 && (
                      <li className="small muted">No snapshots yet.</li>
                    )}
                  </ul>
                </div>

                <div className="proj-side-card">
                  <h4>Recent activity</h4>
                  <ul className="proj-activity">
                    {snapshots.map((s) => (
                      <li key={s.id}>
                        <span className="proj-act-title">
                          {s.reason === "import"
                            ? "Initial snapshot created"
                            : `Snapshot (${s.reason})`}
                        </span>
                        <span className="small muted">
                          {s.entry_count} entries · {fmtTime(s.created_at)}
                        </span>
                      </li>
                    ))}
                  </ul>
                  <p className="small muted proj-w3-note">
                    Open in Chat is <b>read / discuss only</b> in W2a — no working
                    copy, sandbox, or edits. Editing &amp; running is W3.
                  </p>
                </div>
              </div>
            </div>
          </section>
        )}
      </main>
    </div>
  );
}
