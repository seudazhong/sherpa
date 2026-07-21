import { useEffect, useRef, useState } from "react";

import { api, type ChannelsStatus, type ThreadTranscript } from "../api";
import { useAuth } from "../auth";
import Sidebar from "../components/Sidebar";

export default function MessagingView() {
  const { csrf } = useAuth();
  const [status, setStatus] = useState<ChannelsStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [text, setText] = useState("");
  const [active, setActive] = useState<string | null>(null);
  const [thread, setThread] = useState<ThreadTranscript | null>(null);
  const pollRef = useRef<number | null>(null);

  const loadStatus = async () => {
    try {
      setStatus(await api.channelsStatus());
    } catch {
      setError("Could not load channel status. Is the backend running?");
    }
  };

  useEffect(() => {
    void loadStatus();
    return () => {
      if (pollRef.current) window.clearInterval(pollRef.current);
    };
  }, []);

  const openThread = async (sid: string) => {
    setActive(sid);
    try {
      setThread(await api.threadTranscript(sid));
    } catch {
      setError("Could not load thread.");
    }
  };

  // After a simulated inbound message the worker replies asynchronously; poll the
  // thread for a short while so the round-trip becomes visible in the human lane.
  const pollThread = (sid: string) => {
    if (pollRef.current) window.clearInterval(pollRef.current);
    let ticks = 0;
    pollRef.current = window.setInterval(async () => {
      ticks += 1;
      try {
        const tx = await api.threadTranscript(sid);
        setThread(tx);
        const hasReply = tx.messages.some((m) => m.role === "assistant");
        if (hasReply || ticks >= 10) {
          if (pollRef.current) window.clearInterval(pollRef.current);
          pollRef.current = null;
        }
      } catch {
        /* keep polling */
      }
    }, 1500);
  };

  const simulate = async () => {
    if (!csrf || !text.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const res = await api.simulateQQ(csrf, text.trim());
      setText("");
      await loadStatus();
      if (res.session_id) {
        setActive(res.session_id);
        await openThread(res.session_id);
        pollThread(res.session_id);
      }
    } catch {
      setError("Simulate failed.");
    } finally {
      setBusy(false);
    }
  };

  const qq = status?.qq;

  return (
    <div className="app">
      <Sidebar />
      <main className="main">
        <header className="topbar">
          <div>
            <h2>Messaging</h2>
            <p className="page-sub small">
              Chat with Sherpa over QQ / IM — inbound messages run the same agent loop, and you
              can approve actions right from the chat.
            </p>
          </div>
        </header>

        <div className="inbox">
          {error && <div className="auth-error">{error}</div>}

          <section>
            <div className="section-head">QQ connection</div>
            <article className="cand-card">
              <div className="cand-main">
                <div className="cand-title">
                  {qq?.configured ? (
                    <span className="pill pill-success">Connected</span>
                  ) : qq?.enabled ? (
                    <span className="pill pill-running">Enabled · needs owner id</span>
                  ) : (
                    <span className="pill pill-idle">Not configured</span>
                  )}
                </div>
                <div className="cand-meta small muted">
                  <div>
                    Backend: <code>{qq?.kind ?? "…"}</code> (OneBot v11 / aiocqhttp)
                  </div>
                  <div>
                    Webhook: <code>{qq?.webhook_path ?? "/channels/qq/webhook"}</code> ·
                    signature {qq?.webhook_secret_set ? "set" : "not set"}
                  </div>
                  <div>
                    Owner QQ id: {qq?.owner_id_set ? "set" : "not set"} · API base{" "}
                    <code>{qq?.api_base ?? "…"}</code>
                  </div>
                </div>
                {!qq?.configured && (
                  <p className="small muted">
                    To connect a real bot: run a self-hosted OneBot bridge (go-cqhttp / Lagrange /
                    AstrBot), set <code>QQ_KIND=onebot</code>, <code>QQ_OWNER_ID</code>,{" "}
                    <code>QQ_WEBHOOK_SECRET</code> and <code>QQ_API_BASE</code>, and point its
                    event webhook at <code>/channels/qq/webhook</code>. You can try the flow below
                    without a bot.
                  </p>
                )}
              </div>
            </article>
          </section>

          <section>
            <div className="section-head">Try it (simulate an inbound message)</div>
            <div className="composer">
              <textarea
                rows={2}
                value={text}
                onChange={(e) => setText(e.target.value)}
                placeholder="e.g. Remind me to file taxes on April 10, or: what can you do?"
                aria-label="Simulated inbound message"
              />
              <button
                className="btn btn-primary"
                disabled={busy || !text.trim()}
                onClick={() => void simulate()}
              >
                {busy ? "Sending…" : "Send as QQ"}
              </button>
            </div>
            <p className="small muted">
              This injects a message as if it arrived from your QQ, runs the agent, and shows the
              reply below. Approvals appear as an <code>approve &lt;id&gt;</code> prompt — reply
              with that to authorize an action.
            </p>
          </section>

          <section>
            <div className="section-head">
              Threads <span className="count">{status?.threads.length ?? 0}</span>
            </div>
            {(status?.threads.length ?? 0) === 0 && (
              <div className="empty small muted">No IM threads yet. Send a message above.</div>
            )}
            {status?.threads.map((t) => (
              <article
                className={"todo-row" + (active === t.session_id ? " active" : "")}
                key={t.session_id}
              >
                <span className="todo-title">QQ · {t.external_id}</span>
                <span className="small muted">{new Date(t.created_at).toLocaleString()}</span>
                <button className="btn todo-action" onClick={() => void openThread(t.session_id)}>
                  Open
                </button>
              </article>
            ))}
          </section>

          {thread && (
            <section>
              <div className="section-head">
                Transcript · QQ {thread.external_id}
                <button
                  className="btn todo-action"
                  style={{ marginLeft: "auto" }}
                  onClick={() => void openThread(thread.session_id)}
                >
                  Refresh
                </button>
              </div>
              {thread.messages.length === 0 && (
                <div className="empty small muted">No messages yet.</div>
              )}
              {thread.messages.map((m, i) => (
                <article className={"msg" + (m.role === "user" ? " me" : "")} key={i}>
                  <div className={m.role === "user" ? "bubble-user" : "bubble-agent"}>{m.text}</div>
                </article>
              ))}
            </section>
          )}
        </div>
      </main>
    </div>
  );
}
