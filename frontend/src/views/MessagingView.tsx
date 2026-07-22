import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { api, type ChannelsStatus, type ThreadTranscript } from "../api";
import { useAuth } from "../auth";
import Sidebar from "../components/Sidebar";

export default function MessagingView() {
  const { csrf } = useAuth();
  const [status, setStatus] = useState<ChannelsStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [qqText, setQqText] = useState("");
  const [emailText, setEmailText] = useState("");
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

  const sendInbound = async (channel: "qq" | "email", message: string) => {
    if (!csrf || !message.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const res =
        channel === "qq"
          ? await api.simulateQQ(csrf, message.trim())
          : await api.simulateEmail(csrf, message.trim());
      if (channel === "qq") setQqText("");
      else setEmailText("");
      await loadStatus();
      const sid = res.session_id ?? active;
      if (sid) {
        setActive(sid);
        await openThread(sid);
        pollThread(sid);
      }
    } catch {
      setError("Send failed.");
    } finally {
      setBusy(false);
    }
  };

  const qq = status?.qq;
  const email = status?.email;
  const threadChannel = (thread?.channel === "email" ? "email" : "qq") as
    "qq" | "email";

  return (
    <div className="app">
      <Sidebar />
      <main className="main">
        <header className="topbar">
          <div className="page-heading">
            <span className="page-eyebrow">Channels</span>
            <h2>Messaging</h2>
            <p className="page-sub small">
              Reach the same Sherpa from QQ or email, with approvals kept in
              context
            </p>
          </div>
        </header>

        <div className="inbox page-content">
          {error && <div className="auth-error">{error}</div>}

          <section className="channel-grid">
            <article className="channel-card">
              <header>
                <span className="channel-icon qq" aria-hidden="true">
                  QQ
                </span>
                <div>
                  <span className="section-kicker">Instant messaging</span>
                  <h3>QQ</h3>
                  <p>Message Sherpa through your official QQ bot.</p>
                </div>
                <span
                  className={
                    qq?.configured ? "pill pill-success" : "pill pill-idle"
                  }
                >
                  {qq?.configured ? "Connected" : "Not connected"}
                </span>
              </header>
              <div className="channel-card-body">
                <div className="channel-fact">
                  <span>Status</span>
                  <strong>
                    {qq?.configured
                      ? "Ready for inbound messages"
                      : "Connect a bot to begin"}
                  </strong>
                </div>
                <details className="disclosure">
                  <summary>Technical details</summary>
                  <div className="technical-grid">
                    <span>
                      AppID <code>{qq?.app_id || "Not set"}</code>
                    </span>
                    <span>Transport · official WebSocket gateway</span>
                  </div>
                </details>
                <Link className="btn" to="/integrations">
                  Manage connection
                </Link>
              </div>
            </article>

            <article className="channel-card">
              <header>
                <span className="channel-icon email" aria-hidden="true">
                  @
                </span>
                <div>
                  <span className="section-kicker">Agentic inbox</span>
                  <h3>Email</h3>
                  <p>
                    Send and receive agent requests through a dedicated inbox.
                  </p>
                </div>
                <span
                  className={
                    email?.configured
                      ? "pill pill-success"
                      : email?.enabled
                        ? "pill pill-running"
                        : "pill pill-idle"
                  }
                >
                  {email?.configured
                    ? "Connected"
                    : email?.enabled
                      ? "Needs inbox"
                      : "Not configured"}
                </span>
              </header>
              <div className="channel-card-body">
                <div className="channel-fact">
                  <span>Inbox</span>
                  <strong>{email?.inbox_id || "Not configured"}</strong>
                </div>
                <details className="disclosure">
                  <summary>Technical details</summary>
                  <div className="technical-grid">
                    <span>
                      Provider <code>{email?.kind ?? "…"}</code>
                    </span>
                    <span>
                      Webhook{" "}
                      <code>
                        {email?.webhook_path ?? "/channels/email/webhook"}
                      </code>
                    </span>
                    <span>
                      Signature{" "}
                      {email?.webhook_secret_set
                        ? "configured"
                        : "not configured"}
                    </span>
                    <span>
                      Owner allowlist {email?.owner_email || "any sender"}
                    </span>
                  </div>
                </details>
              </div>
            </article>
          </section>

          <section className="content-section">
            <div className="section-head">
              <span>Channel test</span>
              <span className="section-hint">Development-only simulation</span>
            </div>
            <div className="test-grid">
              <details className="test-panel">
                <summary>
                  <span>Simulate QQ inbound</span>
                  <span aria-hidden="true">＋</span>
                </summary>
                <div className="channel-composer">
                  <textarea
                    rows={3}
                    value={qqText}
                    onChange={(e) => setQqText(e.target.value)}
                    placeholder="e.g. What can you help me with?"
                    aria-label="Simulated inbound QQ message"
                  />
                  <button
                    className="btn btn-primary"
                    disabled={busy || !qqText.trim()}
                    onClick={() => void sendInbound("qq", qqText)}
                  >
                    Send test
                  </button>
                </div>
              </details>
              <details className="test-panel">
                <summary>
                  <span>Simulate email inbound</span>
                  <span aria-hidden="true">＋</span>
                </summary>
                <div className="channel-composer">
                  <textarea
                    rows={3}
                    value={emailText}
                    onChange={(e) => setEmailText(e.target.value)}
                    placeholder="e.g. Summarize my open tasks"
                    aria-label="Simulated inbound email"
                  />
                  <button
                    className="btn btn-primary"
                    disabled={busy || !emailText.trim()}
                    onClick={() => void sendInbound("email", emailText)}
                  >
                    Send test
                  </button>
                </div>
              </details>
            </div>
          </section>

          <section className="content-section">
            <div className="section-head">
              <span>Threads</span>
              <span className="count">{status?.threads.length ?? 0}</span>
            </div>
            {(status?.threads.length ?? 0) === 0 && (
              <div className="empty-state compact">
                <strong>No channel conversations yet</strong>
                <span>New QQ and email conversations will appear here.</span>
              </div>
            )}
            {status?.threads.map((t) => (
              <article
                className={
                  "todo-row thread-row" +
                  (active === t.session_id ? " active" : "")
                }
                key={t.session_id}
              >
                <span
                  className={`channel-mini-icon ${t.channel === "email" ? "email" : "qq"}`}
                >
                  {t.channel === "email" ? "@" : "QQ"}
                </span>
                <span className="todo-title">
                  {t.channel === "email"
                    ? "Email conversation"
                    : "QQ conversation"}
                  <span className="item-subtitle">
                    {t.external_id} · {new Date(t.created_at).toLocaleString()}
                  </span>
                </span>
                <button
                  className="btn btn-quiet todo-action"
                  onClick={() => void openThread(t.session_id)}
                >
                  Open
                </button>
              </article>
            ))}
          </section>

          {thread && (
            <section className="transcript-card">
              <div className="section-head">
                <span>
                  Conversation · {thread.channel === "email" ? "Email" : "QQ"}
                  <span className="thread-identity">{thread.external_id}</span>
                </span>
                <button
                  className="btn btn-quiet todo-action"
                  onClick={() => void openThread(thread.session_id)}
                >
                  Refresh
                </button>
              </div>
              {thread.messages.length === 0 && (
                <div className="empty-state compact">No messages yet.</div>
              )}
              <div className="transcript-messages">
                {thread.messages.map((m, i) => (
                  <article
                    className={"msg" + (m.role === "user" ? " me" : "")}
                    key={i}
                  >
                    <div
                      className={
                        m.role === "user" ? "bubble-user" : "bubble-agent"
                      }
                    >
                      {m.text}
                    </div>
                  </article>
                ))}
              </div>

              {thread.pending_approvals.length > 0 && (
                <div className="section-head approval-head">
                  <span>Pending approvals</span>
                  <span className="count">
                    {thread.pending_approvals.length}
                  </span>
                </div>
              )}
              {thread.pending_approvals.map((a) => (
                <article className="cand-card" key={a.correlation_id}>
                  <div className="cand-main">
                    <div className="cand-title">
                      <span className="pill pill-running">Approval needed</span>{" "}
                      {a.tool_name}
                    </div>
                    <div className="cand-meta small muted">
                      {a.summary} · reply <code>approve {a.short_id}</code>
                    </div>
                  </div>
                  <div className="cand-actions">
                    <button
                      className="btn btn-primary"
                      disabled={busy}
                      onClick={() =>
                        void sendInbound(threadChannel, `approve ${a.short_id}`)
                      }
                    >
                      Approve
                    </button>
                    <button
                      className="btn"
                      disabled={busy}
                      onClick={() =>
                        void sendInbound(threadChannel, `reject ${a.short_id}`)
                      }
                    >
                      Reject
                    </button>
                  </div>
                </article>
              ))}
            </section>
          )}
        </div>
      </main>
    </div>
  );
}
