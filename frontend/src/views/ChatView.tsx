import { useEffect, useRef, useState, type FormEvent } from "react";

import { api, eventsUrl } from "../api";
import { useAuth } from "../auth";

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

export default function ChatView() {
  const { email, csrf, logout } = useAuth();
  const [bubbles, setBubbles] = useState<Bubble[]>([]);
  const [activities, setActivities] = useState<Activity[]>([]);
  const [draft, setDraft] = useState("");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const esRef = useRef<EventSource | null>(null);
  const endRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let cancelled = false;

    const openStream = (sid: string, cursor: string) => {
      const es = new EventSource(eventsUrl(sid, cursor));
      esRef.current = es;
      const parse = (e: Event) => JSON.parse((e as MessageEvent).data) as Envelope;

      es.onopen = () => setConnected(true);
      es.onerror = () => setConnected(false);
      es.addEventListener("run.started", () => setRunning(true));
      es.addEventListener("text-delta", (e) => {
        const env = parse(e);
        setBubbles((b) => [
          ...b,
          { key: env.event_id, role: "assistant", text: String(env.payload.text ?? "") },
        ]);
      });
      es.addEventListener("tool-call", (e) => {
        const env = parse(e);
        setActivities((a) => [
          ...a,
          { key: env.event_id, label: `Tool · ${String(env.payload.name ?? "")}`, state: "running" },
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
    };

    const boot = async () => {
      try {
        const page = await api.listSessions();
        let sid = page.items[0]?.id ?? null;
        if (!sid) {
          if (!csrf) return;
          sid = (await api.createSession(csrf)).id;
        }
        if (cancelled) return;
        setSessionId(sid);
        const mp = await api.listMessages(sid);
        if (cancelled) return;
        setBubbles(
          mp.items.map((m) => ({
            key: m.id,
            role: m.role,
            text: m.parts.map((p) => p.text).join(" "),
          })),
        );
        openStream(sid, mp.event_cursor);
      } catch {
        if (!cancelled) setError("Could not load your workspace. Is the backend running?");
      }
    };

    void boot();
    return () => {
      cancelled = true;
      esRef.current?.close();
      esRef.current = null;
    };
  }, [csrf]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [bubbles, activities]);

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
      <aside className="sidebar">
        <div className="brand-row">
          <span className="logo">S</span>
          <span className="brand-name">Sherpa</span>
        </div>
        <div className="nav-label">Workspace</div>
        <span className="nav-item active">◌ Chat</span>
        <span className="nav-item muted">✓ Todos</span>
        <span className="nav-item muted">◷ Schedules</span>
        <span className="nav-item muted">⌁ Connectors</span>
        <div className="nav-spacer" />
        <div className="nav-user">
          <span className="avatar">{(email ?? "?").slice(0, 1).toUpperCase()}</span>
          <div>
            <strong>{email ?? "Owner"}</strong>
            <br />
            <button className="linklike" onClick={() => void logout()}>
              Sign out
            </button>
          </div>
        </div>
      </aside>

      <main className="main">
        <header className="topbar">
          <div>
            <h2>Chat</h2>
            <p className="page-sub small">
              Personal workspace · <span className="chip">Web chat</span> · Mock model
            </p>
          </div>
          <span className={connected ? "pill pill-live" : "pill pill-idle"}>
            {connected ? "Live" : "Connecting…"}
          </span>
        </header>

        <div className="thread">
          {running && (
            <section className="run-banner" role="status" aria-live="polite">
              <span className="spin" aria-hidden="true" />
              <div>
                <strong>Sherpa is working…</strong>
                <div className="sub small muted">You can leave; the run continues on the server.</div>
              </div>
            </section>
          )}

          {error && <div className="auth-error">{error}</div>}

          {bubbles.length === 0 && !error && (
            <div className="empty small muted">Say hello to start a conversation.</div>
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
                <div className="bubble-agent">{m.text}</div>
              </article>
            ),
          )}

          {activities.length > 0 && (
            <div className="run-log" aria-label="Run activity">
              <div className="rl-head">Run activity</div>
              {activities.map((a) => (
                <div className="tool-card" key={a.key}>
                  <div className="row">
                    <span className={`pill pill-${a.state}`}>
                      {a.state === "running" ? "Running" : a.state === "error" ? "Error" : "✓"}
                    </span>
                    <strong>{a.label}</strong>
                  </div>
                  {a.detail && <pre className="code mt-8">{a.detail}</pre>}
                </div>
              ))}
            </div>
          )}
          <div ref={endRef} />
        </div>

        <form className="composer" onSubmit={send}>
          <textarea
            value={draft}
            placeholder="Message Sherpa…"
            rows={2}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void send(e as unknown as FormEvent);
              }
            }}
          />
          <button className="btn btn-primary" type="submit" disabled={!draft.trim()}>
            Send
          </button>
        </form>
      </main>
    </div>
  );
}
