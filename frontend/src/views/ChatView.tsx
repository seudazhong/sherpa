import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";
import ReactMarkdown from "react-markdown";
import { useSearchParams } from "react-router-dom";
import remarkGfm from "remark-gfm";

import {
  api,
  eventsUrl,
  type AppMeta,
  type PendingApproval,
  type SessionSummary,
} from "../api";
import { useAuth } from "../auth";
import Sidebar from "../components/Sidebar";

interface Bubble {
  key: string;
  role: "user" | "assistant";
  text: string;
}

interface Activity {
  key: string;
  label: string;
  state: "running" | "success" | "error";
  detail?: string;
}

interface Envelope {
  event_id: string;
  type: string;
  session_seq: number;
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

function sessionLabel(s: SessionSummary): string {
  const clean =
    stripMarkdown(s.title || s.last_message_preview || "") || "New chat";
  return clean.length > 40 ? clean.slice(0, 40) + "…" : clean;
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
  const [running, setRunning] = useState(false);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [meta, setMeta] = useState<AppMeta | null>(null);
  const esRef = useRef<EventSource | null>(null);
  const endRef = useRef<HTMLDivElement | null>(null);

  const openStream = useCallback((sid: string, cursor: string) => {
    const es = new EventSource(eventsUrl(sid, cursor));
    esRef.current = es;
    const parse = (e: Event) =>
      JSON.parse((e as MessageEvent).data) as Envelope;

    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);
    es.addEventListener("run.started", () => setRunning(true));
    es.addEventListener("text-delta", (e) => {
      const env = parse(e);
      setBubbles((b) => [
        ...b,
        {
          key: env.event_id,
          role: "assistant",
          text: String(env.payload.text ?? ""),
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
      setActivities((a) => [
        ...a,
        {
          key: env.event_id,
          label: `Tool result · ${String(env.payload.name ?? "")}`,
          state: "success",
          detail: String(env.payload.output ?? ""),
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
  }, []);

  const loadSession = useCallback(
    async (sid: string) => {
      esRef.current?.close();
      esRef.current = null;
      setSessionId(sid);
      setBubbles([]);
      setActivities([]);
      setApprovals([]);
      setRunning(false);
      const mp = await api.listMessages(sid);
      setBubbles(
        mp.items.map((m) => ({
          key: m.id,
          role: m.role,
          text: m.parts.map((p) => p.text).join(" "),
        })),
      );
      openStream(sid, mp.event_cursor);
    },
    [openStream],
  );

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

  const send = async (e: FormEvent) => {
    e.preventDefault();
    const text = draft.trim();
    if (!text || !sessionId || !csrf) return;
    setDraft("");
    setBubbles((b) => [...b, { key: crypto.randomUUID(), role: "user", text }]);
    try {
      await api.prompt(csrf, sessionId, text);
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

        <div className="thread">
          <div className="thread-meta">
            <span className="chip">Web chat</span>
            <span>
              {meta
                ? meta.real_model
                  ? meta.model
                  : "Mock model"
                : "Loading model…"}
            </span>
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
                <div className="bubble-user">{m.text}</div>
                <div className="who" aria-hidden="true">
                  {(email ?? "?").slice(0, 1).toUpperCase()}
                </div>
              </article>
            ) : (
              <article className="msg" key={m.key}>
                <div className="who" aria-hidden="true">
                  S
                </div>
                <div className="bubble-agent markdown">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {m.text}
                  </ReactMarkdown>
                </div>
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
            <textarea
              value={draft}
              placeholder="Ask Sherpa anything…"
              rows={2}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void send(e as unknown as FormEvent);
                }
              }}
            />
            <div className="composer-footer">
              <span className="composer-hint">
                Enter to send · Shift + Enter for a new line
              </span>
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
      </main>
    </div>
  );
}
