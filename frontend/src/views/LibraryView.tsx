import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  api,
  type DriveNode,
  type KnowledgeHit,
  type KnowledgeSearchResult,
  type KnowledgeSource,
  type KnowledgeStatus,
} from "../api";
import { useAuth } from "../auth";
import Sidebar from "../components/Sidebar";

const IN_PROGRESS: KnowledgeStatus[] = [
  "queued",
  "parsing",
  "chunking",
  "embedding",
  "deleting",
];

const STATUS_PILL: Record<KnowledgeStatus, string> = {
  ready: "kb-ready",
  queued: "kb-progress",
  parsing: "kb-progress",
  chunking: "kb-progress",
  embedding: "kb-progress",
  stale: "kb-stale",
  failed: "kb-failed",
  deleting: "kb-deleting",
};

const STATUS_LABEL: Record<KnowledgeStatus, string> = {
  ready: "ready",
  queued: "queued",
  parsing: "parsing",
  chunking: "chunking",
  embedding: "embedding",
  stale: "stale",
  failed: "failed",
  deleting: "removing",
};

// Durable job stages (ADR-036 KB2b) rendered as the honest in-flight label, so a
// long ingest no longer sits on "queued" for its whole run.
const STAGE_LABEL: Record<string, string> = {
  queued: "queued",
  snapshot: "snapshotting",
  parse: "parsing",
  chunk: "chunking",
  embed: "embedding",
  activate: "activating",
};

function progressLabel(s: KnowledgeSource): string {
  if (!IN_PROGRESS.includes(s.status)) return STATUS_LABEL[s.status];
  if (s.status === "deleting") return STATUS_LABEL.deleting;
  if (s.stage === "embed" && s.progress_total)
    return `embedding ${s.progress_done ?? 0}/${s.progress_total}`;
  if (s.stage) return STAGE_LABEL[s.stage] ?? s.stage;
  return STATUS_LABEL[s.status];
}

function extLabel(name: string): { label: string; cls: string } {
  const ext = name.includes(".")
    ? (name.split(".").pop() ?? "").toLowerCase()
    : "";
  if (ext === "pdf") return { label: "PDF", cls: "pdf" };
  if (ext === "md" || ext === "markdown") return { label: "MD", cls: "md" };
  if (ext === "docx" || ext === "doc") return { label: "DOC", cls: "doc" };
  if (ext === "txt") return { label: "TXT", cls: "txt" };
  return { label: (ext || "?").slice(0, 3).toUpperCase(), cls: "txt" };
}

function langLabel(lang: string | null): string {
  if (!lang) return "—";
  if (lang.startsWith("zh")) return "中文";
  if (lang.startsWith("en")) return "EN";
  return lang.toUpperCase();
}

function matchClass(matched: KnowledgeHit["matched_by"]): string {
  if (matched.includes("lexical") && matched.includes("vector")) return "both";
  if (matched.includes("lexical")) return "lex";
  return "vec";
}

function matchBadge(matched: KnowledgeHit["matched_by"]): {
  cls: string;
  text: string;
} {
  const c = matchClass(matched);
  if (c === "both") return { cls: "both", text: "both · 词法+向量" };
  if (c === "lex") return { cls: "lex", text: "lexical · zhparser" };
  return { cls: "vec", text: "vector · 语义" };
}

function locator(hit: KnowledgeHit): string {
  const parts: string[] = [];
  if (hit.locator.page) parts.push(`p.${hit.locator.page}`);
  if (hit.locator.heading) parts.push(`§${hit.locator.heading}`);
  return parts.join(" · ");
}

interface Crumb {
  id: string | null;
  name: string;
}

export default function LibraryView() {
  const { csrf } = useAuth();
  const [tab, setTab] = useState<"sources" | "search">("sources");
  const [sources, setSources] = useState<KnowledgeSource[]>([]);
  const [selected, setSelected] = useState<KnowledgeSource | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  // Search test
  const [query, setQuery] = useState("");
  const [searching, setSearching] = useState(false);
  const [result, setResult] = useState<KnowledgeSearchResult | null>(null);

  // Drive picker
  const [pickerOpen, setPickerOpen] = useState(false);
  const [picked, setPicked] = useState<Record<string, DriveNode>>({});
  const [driveTrail, setDriveTrail] = useState<Crumb[]>([
    { id: null, name: "Drive" },
  ]);
  const [driveQuery, setDriveQuery] = useState("");
  const [driveNodes, setDriveNodes] = useState<DriveNode[]>([]);
  const pickerParent = driveTrail[driveTrail.length - 1];

  // Direct upload (drop files on Knowledge)
  const [dragging, setDragging] = useState(false);
  const [uploadNote, setUploadNote] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const pickedNodes = useMemo(() => Object.values(picked), [picked]);

  const anyInProgress = useMemo(
    () => sources.some((s) => IN_PROGRESS.includes(s.status)),
    [sources],
  );

  const load = useCallback(async () => {
    try {
      const rows = await api.listKnowledgeSources();
      setSources(rows);
      setSelected((cur) =>
        cur ? (rows.find((r) => r.id === cur.id) ?? null) : null,
      );
      setError(null);
    } catch {
      setError("Could not load your Knowledge base. Is the backend running?");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // Poll while an ingestion is running so status pills and progress stay truthful.
  useEffect(() => {
    if (!anyInProgress) return;
    const t = setInterval(() => void load(), 1500);
    return () => clearInterval(t);
  }, [anyInProgress, load]);

  const counts = useMemo(() => {
    let processing = 0;
    let stale = 0;
    let failed = 0;
    for (const s of sources) {
      if (IN_PROGRESS.includes(s.status)) processing += 1;
      else if (s.status === "stale") stale += 1;
      else if (s.status === "failed") failed += 1;
    }
    return { processing, stale, failed };
  }, [sources]);

  const addFromDrive = async (nodes: DriveNode[]) => {
    if (!csrf || nodes.length === 0) return;
    setBusy("add");
    try {
      const result = await api.addKnowledgeSources(
        csrf,
        nodes.map((n) => n.id),
      );
      setPickerOpen(false);
      setPicked({});
      setError(
        result.failed.length
          ? `Added ${result.added.length}; skipped ${result.failed.length} (${[
              ...new Set(result.failed.map((f) => f.code)),
            ].join(", ")}).`
          : null,
      );
      await load();
    } catch {
      setError("Could not add those files to Knowledge.");
    } finally {
      setBusy(null);
    }
  };

  /** Drop files straight onto Knowledge: they are saved to your Drive first (a
   *  source is always backed by a real Drive file, ADR-036) and then indexed. */
  const uploadAndIndex = async (files: File[]) => {
    if (!csrf || files.length === 0) return;
    setBusy("upload");
    setError(null);
    const uploaded: string[] = [];
    const rejected: string[] = [];
    try {
      for (const [i, file] of files.entries()) {
        setUploadNote(`Uploading ${i + 1}/${files.length} — ${file.name}`);
        try {
          const node = await api.driveUpload(csrf, null, file);
          uploaded.push(node.id);
        } catch {
          rejected.push(file.name);
        }
      }
      let skipped = 0;
      if (uploaded.length) {
        setUploadNote(`Indexing ${uploaded.length} file(s)…`);
        const result = await api.addKnowledgeSources(csrf, uploaded);
        skipped = result.failed.length;
      }
      if (rejected.length || skipped)
        setError(
          `Uploaded ${uploaded.length}/${files.length}.` +
            (rejected.length ? ` Failed: ${rejected.join(", ")}.` : "") +
            (skipped ? ` ${skipped} could not be indexed.` : ""),
        );
      await load();
    } finally {
      setBusy(null);
      setUploadNote(null);
    }
  };

  const rebuild = async (source: KnowledgeSource) => {
    if (!csrf) return;
    setBusy(source.id);
    try {
      await api.reindexKnowledgeSource(csrf, source.id);
      await load();
    } catch {
      setError("Could not start a rebuild.");
    } finally {
      setBusy(null);
    }
  };

  const rebuildAll = async () => {
    if (!csrf || sources.length === 0) return;
    setBusy("all");
    try {
      for (const s of sources) {
        if (s.status === "deleting") continue;
        await api.reindexKnowledgeSource(csrf, s.id);
      }
      await load();
    } catch {
      setError("Could not rebuild every source.");
    } finally {
      setBusy(null);
    }
  };

  const remove = async (source: KnowledgeSource) => {
    if (!csrf) return;
    if (
      !window.confirm(
        `Remove “${source.display_name}” from Knowledge? It stops being searchable. The Drive file is not deleted.`,
      )
    )
      return;
    setBusy(source.id);
    try {
      await api.removeKnowledgeSource(csrf, source.id);
      setSelected(null);
      await load();
    } catch {
      setError("Could not remove the source.");
    } finally {
      setBusy(null);
    }
  };

  const runSearch = async () => {
    const q = query.trim();
    if (!q) return;
    setSearching(true);
    setError(null);
    try {
      setResult(await api.knowledgeSearch(q, 6));
    } catch {
      setError("Search failed. Is the backend running?");
    } finally {
      setSearching(false);
    }
  };

  // Drive picker loading
  const loadPicker = useCallback(async () => {
    try {
      const q = driveQuery.trim();
      const page = q
        ? await api.driveList({ query: q, limit: 100 })
        : await api.driveList({ parent: pickerParent.id, limit: 200 });
      setDriveNodes(page.items);
    } catch {
      setDriveNodes([]);
    }
  }, [driveQuery, pickerParent.id]);

  useEffect(() => {
    if (!pickerOpen) return;
    const t = setTimeout(() => void loadPicker(), 160);
    return () => clearTimeout(t);
  }, [pickerOpen, loadPicker]);

  const openPicker = () => {
    setDriveTrail([{ id: null, name: "Drive" }]);
    setDriveQuery("");
    setDriveNodes([]);
    setPicked({});
    setPickerOpen(true);
  };

  const pickerVisible = useMemo(
    () =>
      [...driveNodes].sort((a, b) => {
        if (a.node_type !== b.node_type)
          return a.node_type === "folder" ? -1 : 1;
        return a.name.localeCompare(b.name);
      }),
    [driveNodes],
  );

  const groupedHits = useMemo(() => {
    if (!result) return [];
    const groups: Array<{ title: string; hits: KnowledgeHit[] }> = [];
    for (const h of result.hits) {
      const last = groups[groups.length - 1];
      if (last && last.title === h.title) last.hits.push(h);
      else groups.push({ title: h.title, hits: [h] });
    }
    return groups;
  }, [result]);

  return (
    <div className="app">
      <Sidebar />
      <main className="main">
        <header className="topbar">
          <div className="page-heading">
            <span className="page-eyebrow">Organize · /library</span>
            <h2>Knowledge</h2>
            <p className="page-sub small">
              Let Sherpa consult documents you add and cite exact sources — your
              files, not scraped mail or web pages.
            </p>
          </div>
          <button
            className="btn btn-primary"
            onClick={openPicker}
            disabled={busy === "add"}
          >
            <span aria-hidden="true">＋</span> Add from Drive
          </button>
        </header>

        <div className="inbox page-content">
          {error && <div className="auth-error">{error}</div>}

          <section className="kb-banner">
            <span className="kb-banner-lock" aria-hidden="true">
              🔒
            </span>
            <span>
              Embedding <b className="kb-k">ollama · bge-m3 · 1024-d</b> — local,
              document text stays on-box
            </span>
            <span className="kb-banner-retr">
              Retrieval <b className="kb-k">zhparser lexical</b> +{" "}
              <b className="kb-k">pgvector</b> · RRF fusion
            </span>
          </section>

          <div className="kb-tabs" role="tablist">
            <button
              role="tab"
              aria-selected={tab === "sources"}
              className={"kb-tab" + (tab === "sources" ? " active" : "")}
              onClick={() => {
                setTab("sources");
                setSelected(null);
              }}
            >
              Sources
            </button>
            <button
              role="tab"
              aria-selected={tab === "search"}
              className={"kb-tab" + (tab === "search" ? " active" : "")}
              onClick={() => setTab("search")}
            >
              Search test
            </button>
          </div>

          {tab === "sources" && !selected && (
            <>
              <div className="kb-summary">
                <div className="kb-summary-count">
                  <strong>{sources.length}</strong> sources
                  <span className="muted">
                    {" "}
                    · {counts.processing} processing · {counts.stale} need
                    rebuild · {counts.failed} failed
                  </span>
                </div>
                <button
                  className="btn"
                  onClick={() => void rebuildAll()}
                  disabled={busy === "all" || sources.length === 0}
                >
                  {busy === "all" ? "Rebuilding…" : "↻ Rebuild all"}
                </button>
              </div>

              <section
                className={"kb-drop" + (dragging ? " over" : "")}
                onDragOver={(e) => {
                  e.preventDefault();
                  setDragging(true);
                }}
                onDragLeave={() => setDragging(false)}
                onDrop={(e) => {
                  e.preventDefault();
                  setDragging(false);
                  void uploadAndIndex(Array.from(e.dataTransfer.files));
                }}
              >
                <input
                  id="kb-file-input"
                  ref={fileInputRef}
                  type="file"
                  multiple
                  accept=".pdf,.md,.markdown,.txt,.docx"
                  hidden
                  onChange={(e) => {
                    const files = Array.from(e.target.files ?? []);
                    e.target.value = "";
                    void uploadAndIndex(files);
                  }}
                />
                <span className="kb-drop-icon" aria-hidden="true">
                  ⇪
                </span>
                <div className="kb-drop-main">
                  <strong>
                    {uploadNote ?? "Drop documents here to index them"}
                  </strong>
                  <span className="muted small">
                    PDF · Markdown · DOCX · TXT — saved to your Drive first, then
                    indexed. Or{" "}
                    <button
                      className="kb-linkbtn"
                      disabled={busy === "upload"}
                      onClick={() => fileInputRef.current?.click()}
                    >
                      choose files
                    </button>{" "}
                    ·{" "}
                    <button className="kb-linkbtn" onClick={openPicker}>
                      add from Drive
                    </button>
                  </span>
                </div>
              </section>

              <section className="content-section kb-list">
                {sources.length === 0 && (
                  <div className="empty-state">
                    <span className="empty-icon" aria-hidden="true">
                      ▧
                    </span>
                    <strong>No documents yet</strong>
                    <span>
                      Drop a PDF, Markdown, DOCX, or TXT file above — or add one
                      you already keep in Drive — to make it searchable with
                      citations.
                    </span>
                    <button className="btn btn-primary" onClick={openPicker}>
                      Add from Drive
                    </button>
                  </div>
                )}

                {sources.map((s) => {
                  const ext = extLabel(s.display_name);
                  return (
                    <article className="kb-row" key={s.id}>
                      <span
                        className={`kb-ficon ${ext.cls}`}
                        aria-hidden="true"
                      >
                        {ext.label}
                      </span>
                      <div className="kb-row-main">
                        <div className="kb-row-name">
                          <button
                            className="kb-linkbtn"
                            onClick={() => setSelected(s)}
                          >
                            {s.display_name}
                          </button>
                          <span className="kb-lang">
                            {langLabel(s.language)}
                          </span>
                        </div>
                        <div className="kb-row-meta">
                          <span>
                            {s.active_version
                              ? `v${s.active_version} · ${s.chunk_count} chunks`
                              : `${s.chunk_count} chunks`}
                          </span>
                          <span className="kb-dot">
                            updated{" "}
                            {new Date(s.updated_at).toLocaleDateString()}
                          </span>
                          {s.status === "failed" && s.failure_code && (
                            <span className="kb-fail">
                              failed: {s.failure_code}
                            </span>
                          )}
                        </div>
                      </div>
                      <div className="kb-row-actions">
                        <span className={`pill ${STATUS_PILL[s.status]}`}>
                          {progressLabel(s)}
                        </span>
                        <button
                          className="btn btn-quiet todo-action"
                          onClick={() => setSelected(s)}
                        >
                          Open
                        </button>
                        <button
                          className="btn btn-quiet todo-action"
                          disabled={busy === s.id || s.status === "deleting"}
                          onClick={() => void rebuild(s)}
                        >
                          Rebuild
                        </button>
                        <button
                          className="btn btn-quiet todo-action danger"
                          disabled={busy === s.id}
                          onClick={() => void remove(s)}
                        >
                          Remove
                        </button>
                      </div>
                    </article>
                  );
                })}
              </section>

              <p className="kb-hint">
                Only files you explicitly add (PDF / Markdown / DOCX / TXT). v1:
                one private Knowledge base — no crawlers, connector sync, multiple
                libraries, team sharing, or OCR.
              </p>
            </>
          )}

          {tab === "sources" && selected && (
            <SourceDetail
              source={selected}
              busy={busy === selected.id}
              onBack={() => setSelected(null)}
              onRebuild={() => void rebuild(selected)}
              onRemove={() => void remove(selected)}
            />
          )}

          {tab === "search" && (
            <>
              <div className="kb-searchbar">
                <input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") void runSearch();
                  }}
                  placeholder="Try: 预算 审批阈值 approval threshold"
                  aria-label="Search knowledge"
                />
                <button
                  className="btn btn-primary"
                  onClick={() => void runSearch()}
                  disabled={searching || !query.trim()}
                >
                  {searching ? "Searching…" : "Search"}
                </button>
              </div>

              {result && (
                <>
                  <p className="kb-search-meta">
                    retrieval_invocation_id{" "}
                    <span className="mono">
                      {result.retrieval_invocation_id.slice(0, 8)}…
                    </span>{" "}
                    · {result.hits.length} hits · ready + active versions only ·
                    tenant/user filtered
                  </p>

                  {result.hits.length === 0 ? (
                    <div className="kb-noevidence">
                      ⚠️ No sufficient evidence in Knowledge for this query. Sherpa
                      would tell you rather than guess. Add a relevant document or
                      rephrase.
                    </div>
                  ) : (
                    groupedHits.map((g, gi) => (
                      <div className="kb-group" key={`${g.title}-${gi}`}>
                        <p className="kb-group-head">{g.title}</p>
                        {g.hits.map((h) => {
                          const badge = matchBadge(h.matched_by);
                          return (
                            <div
                              className={`kb-hit ${matchClass(h.matched_by)}`}
                              key={h.chunk_id}
                            >
                              <div className="kb-hit-ex">{h.excerpt}</div>
                              <div className="kb-hit-foot">
                                <span className={`kb-badge ${badge.cls}`}>
                                  {badge.text}
                                </span>
                                <span className="kb-cite">
                                  {h.citation_ref}
                                </span>
                                {locator(h) && (
                                  <span className="kb-loc">{locator(h)}</span>
                                )}
                                <span className="kb-score">
                                  RRF {h.score.toFixed(4)}
                                </span>
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    ))
                  )}
                </>
              )}

              {!result && (
                <p className="kb-hint">
                  Badges: <b>both</b> = lexical &amp; vector agreed · <b>lexical</b>{" "}
                  = zhparser exact term (codes / names / terms vectors miss) ·{" "}
                  <b>vector</b> = bge-m3 cross-lingual meaning. Below the floor →
                  explicit “insufficient evidence”.
                </p>
              )}
            </>
          )}
        </div>
      </main>

      {pickerOpen && (
        <div
          className="kb-modal-backdrop"
          role="presentation"
          onClick={() => setPickerOpen(false)}
        >
          <div
            className="kb-modal"
            role="dialog"
            aria-label="Add a Drive file to Knowledge"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="kb-modal-head">
              <strong>Add from Drive</strong>
              <button
                className="icon-button"
                aria-label="Close"
                onClick={() => setPickerOpen(false)}
              >
                ×
              </button>
            </div>

            <label className="session-search-box">
              <span aria-hidden="true">⌕</span>
              <input
                value={driveQuery}
                onChange={(e) => setDriveQuery(e.target.value)}
                placeholder="Search Drive files…"
                aria-label="Search Drive"
                type="search"
              />
            </label>

            {!driveQuery.trim() && (
              <nav className="drive-breadcrumbs kb-picker-crumbs">
                {driveTrail.map((c, i) => (
                  <span key={`${c.id ?? "root"}-${i}`} className="crumb-wrap">
                    {i > 0 && <span className="crumb-sep">/</span>}
                    <button
                      className={
                        "crumb" + (i === driveTrail.length - 1 ? " current" : "")
                      }
                      onClick={() => setDriveTrail((t) => t.slice(0, i + 1))}
                      disabled={i === driveTrail.length - 1}
                    >
                      {c.name}
                    </button>
                  </span>
                ))}
              </nav>
            )}

            <div className="kb-picker-list">
              {pickerVisible.length === 0 && (
                <div className="empty-state compact embedded">
                  <strong>No files here</strong>
                  <span>Upload documents in Drive first.</span>
                </div>
              )}
              {pickerVisible.map((node) =>
                node.node_type === "folder" ? (
                  <button
                    className="kb-picker-row folder"
                    key={node.id}
                    onClick={() =>
                      setDriveTrail((t) => [
                        ...t,
                        { id: node.id, name: node.name },
                      ])
                    }
                  >
                    <span className="kb-ficon dir" aria-hidden="true">
                      DIR
                    </span>
                    <span className="kb-picker-name">{node.name}</span>
                    <span className="kb-picker-go" aria-hidden="true">
                      ›
                    </span>
                  </button>
                ) : (
                  <label className="kb-picker-row selectable" key={node.id}>
                    <input
                      type="checkbox"
                      checked={Boolean(picked[node.id])}
                      onChange={() =>
                        setPicked((p) => {
                          const next = { ...p };
                          if (next[node.id]) delete next[node.id];
                          else next[node.id] = node;
                          return next;
                        })
                      }
                    />
                    <span
                      className={`kb-ficon ${extLabel(node.name).cls}`}
                      aria-hidden="true"
                    >
                      {extLabel(node.name).label}
                    </span>
                    <span className="kb-picker-name">{node.name}</span>
                  </label>
                ),
              )}
            </div>

            <div className="kb-picker-foot">
              <span className="muted small">
                {pickedNodes.length
                  ? `${pickedNodes.length} selected`
                  : "Select one or more files"}
              </span>
              <button
                className="btn btn-primary"
                disabled={busy === "add" || pickedNodes.length === 0}
                onClick={() => void addFromDrive(pickedNodes)}
              >
                {busy === "add"
                  ? "Adding…"
                  : `Add ${pickedNodes.length || ""} source${
                      pickedNodes.length === 1 ? "" : "s"
                    }`}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function SourceDetail({
  source,
  busy,
  onBack,
  onRebuild,
  onRemove,
}: {
  source: KnowledgeSource;
  busy: boolean;
  onBack: () => void;
  onRebuild: () => void;
  onRemove: () => void;
}) {
  return (
    <>
      <div className="kb-detail-head">
        <button className="btn btn-quiet" onClick={onBack}>
          ‹ Back to sources
        </button>
        <div className="kb-row-actions">
          <button
            className="btn"
            disabled={busy || source.status === "deleting"}
            onClick={onRebuild}
          >
            ↻ Rebuild
          </button>
          <button
            className="btn btn-danger"
            disabled={busy}
            onClick={onRemove}
          >
            Remove source
          </button>
        </div>
      </div>

      <div className="kb-detail-title">
        <span className={`kb-ficon ${extLabel(source.display_name).cls}`}>
          {extLabel(source.display_name).label}
        </span>
        <div>
          <div className="kb-detail-name">
            {source.display_name}{" "}
            <span className="kb-lang">{langLabel(source.language)}</span>
          </div>
          <div className="muted small">
            source id <span className="mono">{source.id.slice(0, 8)}…</span>
            {source.file_id && (
              <>
                {" "}
                · Drive file{" "}
                <span className="mono">{source.file_id.slice(0, 8)}…</span>
              </>
            )}
          </div>
        </div>
      </div>

      <div className="kb-detail-grid">
        <section className="content-section">
          <div className="section-head">
            <span>Index status</span>
            <span className={`pill ${STATUS_PILL[source.status]}`}>
              {progressLabel(source)}
            </span>
          </div>
          <dl className="kb-kv">
            <dt>Status</dt>
            <dd>{progressLabel(source)}</dd>
            <dt>Active version</dt>
            <dd>
              {source.active_version
                ? `v${source.active_version}`
                : "— (not yet activated)"}
            </dd>
            <dt>Chunks</dt>
            <dd>{source.chunk_count}</dd>
            <dt>Language</dt>
            <dd>
              {source.language ?? "—"}{" "}
              <span className="muted">
                (<span className="mono">sherpa_text</span> = zhparser)
              </span>
            </dd>
            <dt>Updated</dt>
            <dd>{new Date(source.updated_at).toLocaleString()}</dd>
            {source.status === "failed" && source.failure_code && (
              <>
                <dt>Failure</dt>
                <dd className="kb-fail">{source.failure_code}</dd>
              </>
            )}
          </dl>
        </section>

        <section className="content-section">
          <div className="section-head">
            <span>Embedding profile</span>
          </div>
          <div className="kb-disclosure">
            <b>Local · document text stays on-box.</b>
            <br />
            provider <span className="mono">ollama</span> · model{" "}
            <span className="mono">bge-m3</span> · dim{" "}
            <span className="mono">1024</span> · <span className="mono">cosine</span>.
            Changing model/dim rebuilds the whole library under a new profile —
            vector spaces are never mixed.
          </div>
          <div className="section-head" style={{ marginTop: 16 }}>
            <span>Fail-safe</span>
          </div>
          <p className="muted small" style={{ margin: 0 }}>
            If embedding fails, the previous ready version stays searchable and
            every exit is named. Claim leases expire and recover; two workers
            never activate different generations.
          </p>
        </section>
      </div>
    </>
  );
}
