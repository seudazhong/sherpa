import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ClipboardEvent as ReactClipboardEvent,
  type FormEvent,
} from "react";
import ReactMarkdown from "react-markdown";
import { useSearchParams } from "react-router-dom";
import remarkGfm from "remark-gfm";

import {
  api,
  driveDownloadUrl,
  eventsUrl,
  type AppMeta,
  type DriveNode,
  type PendingApproval,
  type ProjectContext,
  type SessionSummary,
  type WorkingCopySummary,
} from "../api";
import { useAuth } from "../auth";
import { ModelSwitcher } from "../components/ModelSwitcher";
import { ProjectTree } from "../components/ProjectTree";
import type { RuntimeStreamFrame } from "../components/RunPanel";
import { WorkspaceTabs } from "../components/WorkspaceTabs";
import Sidebar from "../components/Sidebar";
import {
  MAX_ATTACHMENTS,
  attachmentErrorText,
  fmtBytes,
  isImage,
  toAttachment,
  uploadToChatFolder,
  type Attachment,
} from "../lib/chatAttachments";

interface Bubble {
  key: string;
  role: "user" | "assistant";
  text: string;
  noEvidence?: boolean;
  attachments?: Attachment[];
}

interface Citation {
  ref: string;
  num: number;
  title: string;
  page: number | null;
  heading: string | null;
  excerpt: string;
}

interface Activity {
  key: string;
  label: string;
  state: "running" | "success" | "error";
  detail?: string;
}

interface Envelope {
  event_id: string;
  id?: string;
  type: string;
  session_seq?: number;
  payload: Record<string, unknown>;
}

interface ApprovalItem {
  pending: PendingApproval;
  nonce: string;
  resolved?: "approved" | "rejected";
}

const starterPrompts = [
  "Plan my open tasks for today",
  "What needs my attention?",
  "Remember my preferred timezone",
];

function stripMarkdown(text: string): string {
  return text
    .replace(/[|#>*_`~]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

// A knowledge_search citation reference is emitted by the tool as [K:<tool_call_id>:<n>].
// Real models often reformat it in their answer (truncate the uuid, drop the :n, and
// append a human description), so we match any [K:...] token and resolve it best-effort
// against the evidence parsed from the knowledge_search tool output (R1: no backend change).
const CITE_TOKEN = /\[(K:[^\]]+?)\]/g;
const NO_EVIDENCE = "No relevant knowledge found";

function hasCitations(text: string): boolean {
  return /\[K:[^\]]+?\]/.test(text);
}

function parseKnowledgeCitations(output: string): Citation[] {
  const headerRe = /^\[(K:[^\]]+?:(\d+))\]\s*(.*)$/;
  const cites: Citation[] = [];
  let current: Citation | null = null;
  for (const line of output.split(/\r?\n/)) {
    const m = line.match(headerRe);
    if (m) {
      if (current) cites.push(current);
      let rest = m[3] ?? "";
      let heading: string | null = null;
      let page: number | null = null;
      const hMatch = rest.match(/§(.+)$/);
      if (hMatch) {
        heading = hMatch[1].trim();
        rest = rest.slice(0, hMatch.index).trim();
      }
      const pMatch = rest.match(/p\.(\d+)\s*$/);
      if (pMatch) {
        page = Number(pMatch[1]);
        rest = rest.slice(0, pMatch.index).trim();
      }
      current = {
        ref: m[1],
        num: Number(m[2]),
        title: rest.trim(),
        page,
        heading,
        excerpt: "",
      };
    } else if (current) {
      current.excerpt += (current.excerpt ? "\n" : "") + line;
    }
  }
  if (current) cites.push(current);
  return cites.map((c) => ({ ...c, excerpt: c.excerpt.trim() }));
}

function citeLocator(c: Citation): string {
  const parts: string[] = [];
  if (c.page) parts.push(`p.${c.page}`);
  if (c.heading) parts.push(`§${c.heading}`);
  return parts.join(" ");
}

interface ResolvedCite {
  key: string; // dedupe key
  label: string; // human label for the chip / sources line
  excerpt: string; // tooltip evidence
}

// Resolve one [K:...] token to a citation. Handles the exact tool ref as well as
// the model's reformatted variants by prefix-matching the tool_call_id and, when a
// description is appended, matching it against the evidence's heading/title.
function resolveCite(
  inner: string,
  cites: Record<string, Citation>,
): ResolvedCite {
  const trimmed = inner.trim();
  const exact = cites[trimmed];
  if (exact) {
    const loc = citeLocator(exact);
    return {
      key: exact.ref,
      label: `${exact.title}${loc ? " · " + loc : ""}`,
      excerpt: exact.excerpt,
    };
  }
  const idMatch = trimmed.match(/^K:([0-9a-fA-F][0-9a-fA-F-]*)/);
  const id = idMatch ? idMatch[1] : "";
  const candidates = Object.values(cites).filter((c) =>
    c.ref.startsWith(`K:${id}`),
  );
  const afterId = trimmed.slice(2 + id.length);
  const nMatch = afterId.match(/^:(\d+)/);
  let chosen: Citation | undefined;
  if (nMatch) chosen = cites[`K:${id}:${nMatch[1]}`];
  let desc = afterId.replace(/^[\s:—–-]+/, "").trim();
  if (/^\d+$/.test(desc)) desc = "";
  if (!chosen && desc && candidates.length) {
    chosen =
      candidates.find((c) => c.heading && desc.includes(c.heading)) ??
      candidates.find(
        (c) => c.heading && desc.includes((c.heading.split("/").pop() ?? "").trim()),
      ) ??
      candidates.find((c) => c.title && desc.includes(c.title));
  }
  if (!chosen && candidates.length) chosen = candidates[0];
  if (chosen) {
    const loc = citeLocator(chosen);
    return {
      key: chosen.ref,
      label: desc || `${chosen.title}${loc ? " · " + loc : ""}`,
      excerpt: chosen.excerpt,
    };
  }
  return {
    key: `K:${id}:${desc}`,
    label: desc || (id ? id.slice(0, 8) : trimmed),
    excerpt: "",
  };
}

function citeUrlTransform(url: string): string {
  if (url.startsWith("kbcite:")) return url;
  if (/^(https?:|mailto:|\/|#|\.)/i.test(url)) return url;
  return "";
}

function sessionLabel(s: SessionSummary): string {
  const clean =
    stripMarkdown(s.title || s.last_message_preview || "") || "New chat";
  return clean.length > 40 ? clean.slice(0, 40) + "…" : clean;
}

interface NumberedCite {
  num: number;
  label: string;
  excerpt: string;
}

function AgentMessage({
  text,
  noEvidence,
  cites,
}: {
  text: string;
  noEvidence?: boolean;
  cites: Record<string, Citation>;
}) {
  // Build a deduped, numbered list of the citations this answer references.
  const byKey = new Map<string, NumberedCite>();
  const order: NumberedCite[] = [];
  for (const m of text.matchAll(CITE_TOKEN)) {
    const r = resolveCite(m[1], cites);
    if (!byKey.has(r.key)) {
      const entry: NumberedCite = {
        num: byKey.size + 1,
        label: r.label,
        excerpt: r.excerpt,
      };
      byKey.set(r.key, entry);
      order.push(entry);
    }
  }
  const byNum = new Map(order.map((e) => [String(e.num), e]));
  const linkified = text.replace(CITE_TOKEN, (whole, inner) => {
    const entry = byKey.get(resolveCite(inner, cites).key);
    return entry ? `[${entry.num}](kbcite:${entry.num})` : whole;
  });

  return (
    <div className="bubble-agent markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        urlTransform={citeUrlTransform}
        components={{
          a({ href, children }) {
            if (href && href.startsWith("kbcite:")) {
              const entry = byNum.get(href.slice("kbcite:".length));
              if (entry) {
                return (
                  <sup
                    className="kb-chip"
                    title={entry.excerpt || entry.label}
                  >
                    {entry.num}
                  </sup>
                );
              }
              return <>{children}</>;
            }
            return (
              <a href={href} target="_blank" rel="noreferrer">
                {children}
              </a>
            );
          },
        }}
      >
        {linkified}
      </ReactMarkdown>

      {noEvidence && (
        <div className="kb-noevidence">
          ⚠️ Sherpa found no sufficient evidence in your Knowledge base for this,
          so it won’t guess. Add a relevant document, or ask it to use another
          source.
        </div>
      )}

      {order.length > 0 && (
        <div className="kb-sources-line">
          <span>Sources:</span>
          {order.map((e) => (
            <span className="kb-chip static" key={e.num} title={e.excerpt}>
              <sup>{e.num}</sup> {e.label}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

export default function ChatView() {
  const { email, csrf } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [bubbles, setBubbles] = useState<Bubble[]>([]);
  const [activities, setActivities] = useState<Activity[]>([]);
  const [approvals, setApprovals] = useState<ApprovalItem[]>([]);
  const [draft, setDraft] = useState("");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [projectCtx, setProjectCtx] = useState<ProjectContext | null>(null);
  const [workingCopy, setWorkingCopy] = useState<WorkingCopySummary | null>(
    null,
  );
  const [projectPane, setProjectPane] = useState<
    "files" | "conversation" | "workspace"
  >("conversation");
  const [running, setRunning] = useState(false);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [meta, setMeta] = useState<AppMeta | null>(null);
  const [cites, setCites] = useState<Record<string, Citation>>({});
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [attaching, setAttaching] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [pickerNodes, setPickerNodes] = useState<DriveNode[]>([]);
  const [pickerQuery, setPickerQuery] = useState("");
  const [visionOk, setVisionOk] = useState(true);
  const [runtimeFrames, setRuntimeFrames] = useState<RuntimeStreamFrame[]>([]);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const insufficientRef = useRef(false);
  const esRef = useRef<EventSource | null>(null);
  const endRef = useRef<HTMLDivElement | null>(null);

  const ingestCitations = useCallback((output: string) => {
    if (output.startsWith(NO_EVIDENCE)) {
      insufficientRef.current = true;
      return;
    }
    const parsed = parseKnowledgeCitations(output);
    if (parsed.length === 0) return;
    insufficientRef.current = false;
    setCites((prev) => {
      const next = { ...prev };
      for (const c of parsed) next[c.ref] = c;
      return next;
    });
  }, []);

  const openStream = useCallback((sid: string, cursor: string) => {
    const es = new EventSource(eventsUrl(sid, cursor));
    esRef.current = es;
    const parse = (e: Event) => {
      const raw = JSON.parse((e as MessageEvent).data) as Partial<Envelope>;
      return {
        ...raw,
        event_id: raw.event_id ?? raw.id ?? crypto.randomUUID(),
        type: raw.type ?? "",
        payload: raw.payload ?? {},
      } as Envelope;
    };

    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);
    es.addEventListener("run.started", () => {
      insufficientRef.current = false;
      setRunning(true);
    });
    es.addEventListener("text-delta", (e) => {
      const env = parse(e);
      const text = String(env.payload.text ?? "");
      const noEvidence = insufficientRef.current && !hasCitations(text);
      setBubbles((b) => [
        ...b,
        {
          key: env.event_id,
          role: "assistant",
          text,
          noEvidence,
        },
      ]);
    });
    es.addEventListener("tool-call", (e) => {
      const env = parse(e);
      setActivities((a) => [
        ...a,
        {
          key: env.event_id,
          label: `Tool · ${String(env.payload.name ?? "")}`,
          state: "running",
        },
      ]);
    });
    es.addEventListener("tool-result", (e) => {
      const env = parse(e);
      const output = String(env.payload.output ?? "");
      if (String(env.payload.name ?? "") === "knowledge_search") {
        ingestCitations(output);
      }
      setActivities((a) => [
        ...a,
        {
          key: env.event_id,
          label: `Tool result · ${String(env.payload.name ?? "")}`,
          state: "success",
          detail: output,
        },
      ]);
    });
    es.addEventListener("tool-error", (e) => {
      const env = parse(e);
      setActivities((a) => [
        ...a,
        {
          key: env.event_id,
          label: `Tool error · ${String(env.payload.name ?? "")}`,
          state: "error",
          detail: String(env.payload.output ?? ""),
        },
      ]);
    });
    es.addEventListener("permission.asked", (e) => {
      const env = parse(e);
      const correlationId = String(env.payload.correlation_id ?? "");
      const nonce = String(env.payload.nonce ?? "");
      if (!correlationId || !nonce) return;
      // The single-use nonce arrives only on this event; the immutable envelope
      // fields come from the pending-approvals projection. Combine to resolve.
      void api.listPermissions().then((page) => {
        const pending = page.items.find(
          (p) => p.correlation_id === correlationId,
        );
        if (!pending) return;
        setApprovals((a) =>
          a.some((x) => x.pending.correlation_id === correlationId)
            ? a
            : [...a, { pending, nonce }],
        );
      });
    });
    es.addEventListener("run.settled", (e) => {
      const env = parse(e);
      setRunning(false);
      setActivities((a) => [
        ...a,
        {
          key: env.event_id,
          label: `Run ${String(env.payload.status ?? "settled")}`,
          state: "success",
          detail: String(env.payload.reason ?? ""),
        },
      ]);
    });
    const pushRuntimeFrame = (type: RuntimeStreamFrame["type"], event: Event) => {
      const env = parse(event);
      const key = env.event_id ?? env.id ?? crypto.randomUUID();
      setRuntimeFrames((frames) =>
        [...frames.slice(-499), { key, type, payload: env.payload }],
      );
    };
    es.addEventListener("runtime.state", (event) =>
      pushRuntimeFrame("runtime.state", event),
    );
    es.addEventListener("runtime.output", (event) =>
      pushRuntimeFrame("runtime.output", event),
    );
  }, [ingestCitations]);

  // R1: rebuild the citation map for an already-persisted transcript by replaying
  // the journal backlog (from cursor 0) and parsing prior knowledge_search tool
  // outputs. Reads only until the first keep-alive (backlog exhausted), then aborts;
  // it never touches bubbles, so there is no duplication with the live tail stream.
  const backfillCitations = useCallback(
    async (sid: string) => {
      try {
        const ac = new AbortController();
        const timer = setTimeout(() => ac.abort(), 8000);
        const res = await fetch(eventsUrl(sid, 0), {
          credentials: "include",
          headers: { Accept: "text/event-stream" },
          signal: ac.signal,
        });
        if (!res.body) {
          clearTimeout(timer);
          return;
        }
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buf = "";
        let done = false;
        while (!done) {
          const { value, done: readerDone } = await reader.read();
          if (readerDone) break;
          buf += decoder.decode(value, { stream: true });
          const frames = buf.split("\n\n");
          buf = frames.pop() ?? "";
          for (const frame of frames) {
            if (frame.startsWith(":")) {
              // keep-alive comment => journal backlog is drained.
              done = true;
              break;
            }
            let evType = "";
            let data = "";
            for (const line of frame.split("\n")) {
              if (line.startsWith("event:")) evType = line.slice(6).trim();
              else if (line.startsWith("data:")) data += line.slice(5).trim();
            }
            if (evType !== "tool-result" && evType !== "tool-error") continue;
            try {
              const env = JSON.parse(data) as Envelope;
              if (String(env.payload.name ?? "") === "knowledge_search") {
                const output = String(env.payload.output ?? "");
                if (!output.startsWith(NO_EVIDENCE)) {
                  const parsed = parseKnowledgeCitations(output);
                  if (parsed.length > 0) {
                    setCites((prev) => {
                      const next = { ...prev };
                      for (const c of parsed) next[c.ref] = c;
                      return next;
                    });
                  }
                }
              }
            } catch {
              /* skip unparseable frame */
            }
          }
        }
        clearTimeout(timer);
        await reader.cancel().catch(() => {});
        ac.abort();
      } catch {
        /* backfill is best-effort; live citations still work */
      }
    },
    [],
  );

  const loadSession = useCallback(
    async (sid: string) => {
      esRef.current?.close();
      esRef.current = null;
      setSessionId(sid);
      setBubbles([]);
      setActivities([]);
      setApprovals([]);
      setCites({});
      setProjectCtx(null);
      setWorkingCopy(null);
      setRuntimeFrames([]);
      setProjectPane("conversation");
      insufficientRef.current = false;
      setRunning(false);
      const mp = await api.listMessages(sid);
      setBubbles(
        mp.items.map((m) => ({
          key: m.id,
          role: m.role,
          text: m.parts
            .filter((p) => !p.attachment)
            .map((p) => p.text)
            .join(" "),
          attachments: m.parts
            .map((p) => p.attachment)
            .filter((a): a is Attachment => !!a),
        })),
      );
      openStream(sid, mp.event_cursor);
      void backfillCitations(sid);
      void api
        .getSessionModel(sid)
        .then((s) => setVisionOk(s.supports_vision))
        .catch(() => setVisionOk(true));
      void api
        .projectContext(sid)
        .then((pc) => {
          setProjectCtx(pc.project_id ? pc : null);
          setWorkingCopy(pc.working_copy ?? null);
        })
        .catch(() => setProjectCtx(null));
    },
    [openStream, backfillCitations],
  );

  // W3 (ADR-040): after each run settles, refresh the Project-bound chat's task
  // working copy so newly-staged changes surface in Change Review.
  const refreshWorkingCopy = useCallback(async () => {
    if (!sessionId || !projectCtx?.project_id) return;
    try {
      setWorkingCopy(await api.getWorkingCopy(sessionId));
    } catch {
      /* transient; leave the prior state */
    }
  }, [sessionId, projectCtx?.project_id]);

  useEffect(() => {
    if (!running) void refreshWorkingCopy();
  }, [running, refreshWorkingCopy]);

  useEffect(() => {
    let cancelled = false;
    const boot = async () => {
      try {
        const page = await api.listSessions({ limit: 100 });
        if (cancelled) return;
        setSessions(page.items);
        const requested = searchParams.get("session");
        const preferred =
          requested && page.items.some((s) => s.id === requested)
            ? requested
            : null;
        let sid = preferred ?? page.items[0]?.id ?? null;
        if (!sid) {
          if (!csrf) return;
          const created = await api.createSession(csrf);
          setSessions([created]);
          sid = created.id;
        }
        if (requested) {
          searchParams.delete("session");
          setSearchParams(searchParams, { replace: true });
        }
        if (cancelled) return;
        await loadSession(sid);
      } catch {
        if (!cancelled)
          setError("Could not load your workspace. Is the backend running?");
      }
    };
    void boot();
    return () => {
      cancelled = true;
      esRef.current?.close();
      esRef.current = null;
    };
  }, [csrf, loadSession]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [bubbles, activities]);

  useEffect(() => {
    void api
      .getMeta()
      .then(setMeta)
      .catch(() => {});
  }, []);

  const newChat = async () => {
    if (!csrf) return;
    try {
      const created = await api.createSession(csrf);
      setSessions((s) => [created, ...s]);
      await loadSession(created.id);
    } catch {
      setError("Could not start a new chat.");
    }
  };

  const switchSession = async (sid: string) => {
    if (sid === sessionId) return;
    try {
      await loadSession(sid);
    } catch {
      setError("Could not open that conversation.");
    }
  };

  const resolveApproval = async (item: ApprovalItem, choice: string) => {
    if (!csrf) return;
    try {
      await api.resolvePermission(csrf, item.pending, item.nonce, choice);
      const outcome = choice === "reject" ? "rejected" : "approved";
      setApprovals((a) =>
        a.map((x) =>
          x.pending.correlation_id === item.pending.correlation_id
            ? { ...x, resolved: outcome }
            : x,
        ),
      );
    } catch {
      setError("Could not submit your decision.");
    }
  };

  const addAttachments = async (files: File[]) => {
    if (!csrf || files.length === 0) return;
    const room = MAX_ATTACHMENTS - attachments.length;
    if (room <= 0) {
      setError(`You can attach at most ${MAX_ATTACHMENTS} files per message.`);
      return;
    }
    setAttaching(true);
    setError(null);
    try {
      for (const file of files.slice(0, room)) {
        const att = await uploadToChatFolder(csrf, file);
        setAttachments((a) => [...a, att]);
      }
    } catch (e) {
      setError(attachmentErrorText(e));
    } finally {
      setAttaching(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  const onPaste = (e: ReactClipboardEvent<HTMLTextAreaElement>) => {
    const files = Array.from(e.clipboardData.files ?? []);
    if (files.length === 0) return;
    e.preventDefault();
    void addAttachments(files);
  };

  const openPicker = async () => {
    setPickerOpen(true);
    try {
      const page = await api.driveList({ limit: 200 });
      setPickerNodes(page.items.filter((n) => n.node_type === "file"));
    } catch {
      setError("Could not load your Drive.");
    }
  };

  const pickFromDrive = async (query: string) => {
    try {
      const page = query.trim()
        ? await api.driveList({ query: query.trim(), limit: 100 })
        : await api.driveList({ limit: 200 });
      setPickerNodes(page.items.filter((n) => n.node_type === "file"));
    } catch {
      setError("Could not search your Drive.");
    }
  };

  const send = async (e: FormEvent) => {
    e.preventDefault();
    const text = draft.trim();
    if (!text || !sessionId || !csrf) return;
    const sent = attachments;
    setDraft("");
    setAttachments([]);
    setBubbles((b) => [
      ...b,
      {
        key: crypto.randomUUID(),
        role: "user",
        text,
        attachments: sent,
      },
    ]);
    try {
      await api.prompt(
        csrf,
        sessionId,
        text,
        sent.map((a) => ({ drive_node_id: a.drive_node_id, version: a.version })),
      );
    } catch {
      setError("Failed to send message.");
    }
  };

  return (
    <div className="app">
      <Sidebar />

      <main className="main">
        <header className="topbar chat-topbar">
          <div className="page-heading">
            <span className="page-eyebrow">Workspace</span>
            <h2>Chat</h2>
            <p className="page-sub small">
              Ask, review, and act from one quiet workspace
            </p>
          </div>
          <div className="topbar-actions">
            {sessions.length > 0 && (
              <select
                className="session-select"
                value={sessionId ?? ""}
                onChange={(e) => void switchSession(e.target.value)}
                aria-label="Switch conversation"
              >
                {sessions.map((s) => (
                  <option key={s.id} value={s.id}>
                    {sessionLabel(s)}
                  </option>
                ))}
              </select>
            )}
            <button
              className="btn"
              onClick={() => void newChat()}
              aria-label="Start a new chat"
            >
              <span aria-hidden="true">＋</span> New chat
            </button>
            <span
              className={
                connected ? "status-indicator online" : "status-indicator"
              }
            >
              <span className="status-dot" />
              {connected ? "Live" : "Connecting"}
            </span>
          </div>
        </header>

        {projectCtx?.project_id && (
          <nav className="project-pane-tabs" aria-label="Project workspace panes">
            <button
              className={projectPane === "files" ? "active" : ""}
              onClick={() => setProjectPane("files")}
            >
              Files
            </button>
            <button
              className={projectPane === "conversation" ? "active" : ""}
              onClick={() => setProjectPane("conversation")}
            >
              Chat
            </button>
            <button
              className={projectPane === "workspace" ? "active" : ""}
              onClick={() => setProjectPane("workspace")}
            >
              Workspace
              {workingCopy && workingCopy.overlay_entry_count > 0 && (
                <span>{workingCopy.overlay_entry_count}</span>
              )}
            </button>
          </nav>
        )}

        <div
          className={`chat-workspace ${
            projectCtx?.project_id
              ? `project-workspace pane-${projectPane}`
              : "general-workspace"
          }`}
        >
          {projectCtx?.project_id && (
            <aside className="project-files-pane" aria-label="Project files">
              {sessionId && (
                <ProjectTree
                  key={sessionId}
                  sessionId={sessionId}
                  csrf={csrf}
                  projectName={projectCtx.project_name ?? "Project"}
                  refreshKey={
                    workingCopy?.updated_at ??
                    workingCopy?.open_change_set_id ??
                    null
                  }
                  onWorkingCopy={setWorkingCopy}
                />
              )}
            </aside>
          )}

          <section className="project-conversation-pane">
            <div className="thread">
          <div className="thread-meta">
            <span className="chip">Web chat</span>
            {/* With a session the switcher owns the model label (it knows the
                effective source); before one exists we can only show the server
                default from /meta. */}
            {sessionId ? (
              <ModelSwitcher sessionId={sessionId} />
            ) : (
              <span>
                {meta
                  ? meta.real_model
                    ? `Server default · ${meta.model}`
                    : "Mock model"
                  : "Loading model…"}
              </span>
            )}
            {projectCtx?.project_id && (
              <span
                className="chip project-chip"
                title="This chat is bound to a project; edits stage into a task working copy you review before saving."
              >
                ▦ {projectCtx.project_name ?? "Project"}
                <span className="project-chip-note">
                  {workingCopy
                    ? `working copy · ${workingCopy.state}`
                    : projectCtx.bound
                      ? "bound"
                      : "project"}
                </span>
              </span>
            )}
            {projectCtx?.project_id &&
              workingCopy &&
              (workingCopy.open_change_set_id ||
                workingCopy.overlay_entry_count > 0) && (
                <button
                  className="btn btn-small review-toggle"
                  onClick={() => setProjectPane("workspace")}
                >
                  Review changes
                  <span className="review-count">
                    {workingCopy.overlay_entry_count}
                  </span>
                </button>
              )}
          </div>

          {running && (
            <section className="run-banner" role="status" aria-live="polite">
              <span className="spin" aria-hidden="true" />
              <div>
                <strong>Sherpa is working</strong>
                <div className="sub small muted">
                  This run is saved and continues if you leave.
                </div>
              </div>
            </section>
          )}

          {error && <div className="auth-error">{error}</div>}

          {bubbles.length === 0 && !error && (
            <div className="chat-empty">
              <span className="chat-empty-mark" aria-hidden="true">
                S
              </span>
              <h3>What can I help you move forward?</h3>
              <p>
                Sherpa can organize tasks, remember context, work with your
                connected channels, and ask before external actions.
              </p>
              <div className="prompt-grid">
                {starterPrompts.map((prompt) => (
                  <button
                    type="button"
                    key={prompt}
                    onClick={() => setDraft(prompt)}
                  >
                    <span>{prompt}</span>
                    <span aria-hidden="true">↗</span>
                  </button>
                ))}
              </div>
            </div>
          )}

          {bubbles.map((m) =>
            m.role === "user" ? (
              <article className="msg me" key={m.key}>
                <div className="bubble-user">
                  {m.attachments && m.attachments.length > 0 && (
                    <div className="msg-attachments">
                      {m.attachments.map((a) =>
                        isImage(a.content_type) ? (
                          <a
                            key={a.drive_node_id + a.version}
                            href={driveDownloadUrl(a.drive_node_id)}
                            target="_blank"
                            rel="noreferrer"
                          >
                            <img
                              className="msg-attachment-thumb"
                              src={driveDownloadUrl(a.drive_node_id)}
                              alt={a.name}
                            />
                          </a>
                        ) : (
                          <a
                            className="attachment-chip static"
                            key={a.drive_node_id + a.version}
                            href={driveDownloadUrl(a.drive_node_id)}
                            target="_blank"
                            rel="noreferrer"
                          >
                            <span aria-hidden="true">▤</span>
                            {a.name}
                            <span className="muted">
                              {fmtBytes(a.size_bytes)}
                            </span>
                          </a>
                        ),
                      )}
                    </div>
                  )}
                  {m.text}
                </div>
                <div className="who" aria-hidden="true">
                  {(email ?? "?").slice(0, 1).toUpperCase()}
                </div>
              </article>
            ) : (
              <article className="msg" key={m.key}>
                <div className="who" aria-hidden="true">
                  S
                </div>
                <AgentMessage
                  text={m.text}
                  noEvidence={m.noEvidence}
                  cites={cites}
                />
              </article>
            ),
          )}

          {approvals.length > 0 && (
            <div className="run-log" aria-label="Pending approvals">
              <div className="rl-head">Approvals needed</div>
              {approvals.map((item) => (
                <div className="tool-card" key={item.pending.correlation_id}>
                  <div className="row">
                    <span className="pill pill-error">approval</span>
                    <strong>{item.pending.tool_name}</strong>
                  </div>
                  <div className="small muted mt-8">
                    {item.pending.human_readable_preview.summary}
                  </div>
                  <div className="cand-meta small">
                    {item.pending.human_readable_preview.details.map((d) => (
                      <span className="muted" key={d.label}>
                        · {d.label}: {d.value}
                      </span>
                    ))}
                  </div>
                  {item.resolved ? (
                    <div
                      className={`pill mt-8 ${
                        item.resolved === "approved"
                          ? "pill-success"
                          : "pill-idle"
                      }`}
                    >
                      {item.resolved === "approved"
                        ? "✓ Approved — running…"
                        : "✕ Rejected"}
                    </div>
                  ) : (
                    <div className="cand-actions mt-8">
                      <button
                        className="btn btn-primary"
                        onClick={() => void resolveApproval(item, "allow_once")}
                      >
                        Approve
                      </button>
                      <button
                        className="btn"
                        onClick={() => void resolveApproval(item, "reject")}
                      >
                        Reject
                      </button>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}

          {activities.length > 0 && (
            <details className="run-log" aria-label="Run activity">
              <summary>
                <span>
                  Run activity{" "}
                  <span className="count">{activities.length}</span>
                </span>
                <span className="small muted">Tools and receipts</span>
              </summary>
              <div className="run-log-body">
                {activities.map((a) => (
                  <div className="tool-card" key={a.key}>
                    <div className="row">
                      <span className={`pill pill-${a.state}`}>
                        {a.state === "running"
                          ? "Running"
                          : a.state === "error"
                            ? "Error"
                            : "Done"}
                      </span>
                      <strong>{a.label}</strong>
                    </div>
                    {a.detail && <pre className="code mt-8">{a.detail}</pre>}
                  </div>
                ))}
              </div>
            </details>
          )}
          <div ref={endRef} />
            </div>

            <form className="composer" onSubmit={send}>
          <div className="composer-shell">
            {attachments.length > 0 && (
              <div className="composer-attachments">
                {attachments.map((a) => (
                  <span className="attachment-chip" key={a.drive_node_id}>
                    {isImage(a.content_type) ? (
                      <img
                        className="attachment-chip-thumb"
                        src={driveDownloadUrl(a.drive_node_id)}
                        alt=""
                      />
                    ) : (
                      <span aria-hidden="true">▤</span>
                    )}
                    <span className="attachment-chip-name" title={a.name}>
                      {a.name}
                    </span>
                    <span className="muted">{fmtBytes(a.size_bytes)}</span>
                    <button
                      type="button"
                      aria-label={`Remove ${a.name}`}
                      onClick={() =>
                        setAttachments((list) =>
                          list.filter(
                            (x) => x.drive_node_id !== a.drive_node_id,
                          ),
                        )
                      }
                    >
                      ✕
                    </button>
                  </span>
                ))}
              </div>
            )}
            {!visionOk && attachments.some((a) => isImage(a.content_type)) && (
              <div className="composer-warning small">
                This chat’s model source is marked as not supporting images —
                attached images will be described as unavailable. Switch the
                model above, or enable vision for that source in Settings.
              </div>
            )}
            <textarea
              value={draft}
              placeholder="Ask Sherpa anything…"
              rows={2}
              onChange={(e) => setDraft(e.target.value)}
              onPaste={onPaste}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void send(e as unknown as FormEvent);
                }
              }}
            />
            <div className="composer-footer">
              <div className="composer-tools">
                <label className="btn btn-quiet composer-attach">
                  <input
                    ref={fileInputRef}
                    type="file"
                    hidden
                    multiple
                    aria-label="Attach files"
                    onChange={(e) =>
                      void addAttachments(Array.from(e.target.files ?? []))
                    }
                  />
                  {attaching ? "Attaching…" : "＋ Attach"}
                </label>
                <button
                  type="button"
                  className="btn btn-quiet"
                  onClick={() => void openPicker()}
                >
                  From Drive
                </button>
                <span className="composer-hint">
                  Enter to send · paste an image to attach
                </span>
              </div>
              <button
                className="send-button"
                type="submit"
                disabled={!draft.trim()}
              >
                <span>Send</span>
                <span aria-hidden="true">↑</span>
              </button>
            </div>
          </div>
            </form>
          </section>

          {projectCtx?.project_id && (
            <aside className="project-review-pane" aria-label="Project workspace">
              <header className="project-pane-head">
                <div>
                  <strong>Workspace</strong>
                  <span>Changes · Runs · Artifacts</span>
                </div>
              </header>
              {sessionId && (
                <WorkspaceTabs
                  key={sessionId}
                  projectId={projectCtx.project_id}
                  sessionId={sessionId}
                  csrf={csrf}
                  workingCopy={workingCopy}
                  frames={runtimeFrames}
                  onWorkingCopy={setWorkingCopy}
                  onChanged={() => void refreshWorkingCopy()}
                />
              )}
            </aside>
          )}
        </div>

        {pickerOpen && (
          <div
            className="drive-picker-backdrop"
            role="dialog"
            aria-label="Attach from Drive"
            onClick={() => setPickerOpen(false)}
          >
            <div
              className="drive-picker"
              onClick={(e) => e.stopPropagation()}
            >
              <div className="section-head">
                <span>Attach from Drive</span>
                <button
                  className="btn btn-quiet todo-action"
                  onClick={() => setPickerOpen(false)}
                >
                  Close
                </button>
              </div>
              <label className="session-search-box">
                <span aria-hidden="true">⌕</span>
                <input
                  type="search"
                  value={pickerQuery}
                  placeholder="Search your Drive…"
                  aria-label="Search Drive"
                  onChange={(e) => {
                    setPickerQuery(e.target.value);
                    void pickFromDrive(e.target.value);
                  }}
                />
              </label>
              <div className="drive-picker-list">
                {pickerNodes.length === 0 && (
                  <span className="small muted">No files found.</span>
                )}
                {pickerNodes.map((n) => (
                  <button
                    className="drive-picker-row"
                    key={n.id}
                    onClick={() => {
                      setAttachments((a) =>
                        a.length >= MAX_ATTACHMENTS ||
                        a.some((x) => x.drive_node_id === n.id)
                          ? a
                          : [...a, toAttachment(n)],
                      );
                      setPickerOpen(false);
                    }}
                  >
                    <span>{n.name}</span>
                    <span className="muted small">
                      {fmtBytes(n.size_bytes)} · v{n.version}
                    </span>
                  </button>
                ))}
              </div>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
