import { useEffect, useRef, useState } from "react";

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
  const threadChannel = (thread?.channel === "email" ? "email" : "qq") as "qq" | "email";

  return (
    <div className="app">
      <Sidebar />
      <main className="main">
        <header className="topbar">
          <div>
            <h2>Messaging</h2>
            <p className="page-sub small">
              Reach Sherpa over QQ / IM and email — inbound messages run the same agent loop, and
              you can approve actions right from the conversation.
            </p>
          </div>
        </header>

        <div className="inbox">
          {error && <div className="auth-error">{error}</div>}

          <section>
            <div className="section-head">QQ / IM</div>
            <article className="cand-card">
              <div className="cand-main">
                <div className="cand-title">
                  {qq?.configured ? (
                    <span className="pill pill-success">Connected</span>
                  ) : (
                    <span className="pill pill-idle">Not connected</span>
                  )}
                </div>
                <div className="cand-meta small muted">
                  <div>
                    Official QQ bot (WebSocket) · AppID <code>{qq?.app_id || "(none)"}</code>
                  </div>
                  {!qq?.configured && (
                    <div>
                      Connect a bot on the <a href="/integrations">Connectors</a> page (scan QR or
                      paste AppID/Secret). You can try the flow below without a bot.
                    </div>
                  )}
                </div>
              </div>
            </article>
            <div className="composer">
              <textarea
                rows={2}
                value={qqText}
                onChange={(e) => setQqText(e.target.value)}
                placeholder="Simulate an inbound QQ message, e.g. what can you do?"
                aria-label="Simulated inbound QQ message"
              />
              <button
                className="btn btn-primary"
                disabled={busy || !qqText.trim()}
                onClick={() => void sendInbound("qq", qqText)}
              >
                Send as QQ
              </button>
            </div>
          </section>

          <section>
            <div className="section-head">Agentic email</div>
            <article className="cand-card">
              <div className="cand-main">
                <div className="cand-title">
                  {email?.configured ? (
                    <span className="pill pill-success">Connected</span>
                  ) : email?.enabled ? (
                    <span className="pill pill-running">Enabled · needs inbox</span>
                  ) : (
                    <span className="pill pill-idle">Not configured</span>
                  )}
                </div>
                <div className="cand-meta small muted">
                  <div>
                    Inbox: <code>{email?.inbox_id || "(none — set AGENTMAIL_INBOX_ID)"}</code> ·
                    backend <code>{email?.kind ?? "…"}</code> (AgentMail)
                  </div>
                  <div>
                    Webhook: <code>{email?.webhook_path ?? "/channels/email/webhook"}</code> ·
                    signature {email?.webhook_secret_set ? "set" : "not set"} · owner allowlist{" "}
                    {email?.owner_email ? <code>{email.owner_email}</code> : "any"}
                  </div>
                  {!email?.configured && (
                    <div>
                      Set <code>EMAIL_KIND=agentmail</code>, <code>AGENTMAIL_API_KEY</code>,{" "}
                      <code>AGENTMAIL_INBOX_ID</code> (and <code>AGENTMAIL_WEBHOOK_SECRET</code> for
                      inbound). Try it below without a live mailbox.
                    </div>
                  )}
                </div>
              </div>
            </article>
            <div className="composer">
              <textarea
                rows={2}
                value={emailText}
                onChange={(e) => setEmailText(e.target.value)}
                placeholder="Simulate an inbound email, e.g. summarize my open tasks"
                aria-label="Simulated inbound email"
              />
              <button
                className="btn btn-primary"
                disabled={busy || !emailText.trim()}
                onClick={() => void sendInbound("email", emailText)}
              >
                Send as email
              </button>
            </div>
            <p className="small muted">
              Injecting a message runs the agent and shows the reply below. Approvals appear as an{" "}
              <code>approve &lt;id&gt;</code> prompt — reply (or click the button) to authorize.
            </p>
          </section>

          <section>
            <div className="section-head">
              Threads <span className="count">{status?.threads.length ?? 0}</span>
            </div>
            {(status?.threads.length ?? 0) === 0 && (
              <div className="empty small muted">No threads yet. Send a message above.</div>
            )}
            {status?.threads.map((t) => (
              <article
                className={"todo-row" + (active === t.session_id ? " active" : "")}
                key={t.session_id}
              >
                <span className="todo-title">
                  <span className="pill pill-idle">{t.channel === "email" ? "email" : "QQ"}</span>{" "}
                  {t.external_id}
                </span>
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
                Transcript · {thread.channel === "email" ? "email" : "QQ"} {thread.external_id}
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

              {thread.pending_approvals.length > 0 && (
                <div className="section-head" style={{ marginTop: "1rem" }}>
                  Pending approvals <span className="count">{thread.pending_approvals.length}</span>
                </div>
              )}
              {thread.pending_approvals.map((a) => (
                <article className="cand-card" key={a.correlation_id}>
                  <div className="cand-main">
                    <div className="cand-title">
                      <span className="pill pill-running">approval</span> {a.tool_name}
                    </div>
                    <div className="cand-meta small muted">
                      {a.summary} · reply <code>approve {a.short_id}</code>
                    </div>
                  </div>
                  <div className="cand-actions">
                    <button
                      className="btn btn-primary"
                      disabled={busy}
                      onClick={() => void sendInbound(threadChannel, `approve ${a.short_id}`)}
                    >
                      Approve
                    </button>
                    <button
                      className="btn"
                      disabled={busy}
                      onClick={() => void sendInbound(threadChannel, `reject ${a.short_id}`)}
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
