import { useEffect, useRef, useState } from "react";
import { QRCodeSVG } from "qrcode.react";
import { Link } from "react-router-dom";

import { api, type ChannelsStatus } from "../api";
import { useAuth } from "../auth";
import Sidebar from "../components/Sidebar";

export default function ConnectorsView() {
  const { csrf } = useAuth();
  const [status, setStatus] = useState<ChannelsStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<"qr" | "manual">("qr");
  const [busy, setBusy] = useState(false);
  const [test, setTest] = useState<string | null>(null);

  // Manual form
  const [appId, setAppId] = useState("");
  const [secret, setSecret] = useState("");
  const [ownerOpenid, setOwnerOpenid] = useState("");

  // QR bind
  const [qr, setQr] = useState<{ task_id: string; qr_url: string } | null>(
    null,
  );
  const [qrState, setQrState] = useState<string>("");
  const pollRef = useRef<number | null>(null);

  const load = async () => {
    try {
      const s = await api.channelsStatus();
      setStatus(s);
      setAppId((prev) => prev || s.qq.app_id);
    } catch {
      setError("Could not load connectors. Is the backend running?");
    }
  };

  useEffect(() => {
    void load();
    return () => {
      if (pollRef.current) window.clearInterval(pollRef.current);
    };
  }, []);

  const saveManual = async () => {
    if (!csrf || !appId.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await api.putQQConfig(csrf, {
        app_id: appId.trim(),
        enabled: true,
        owner_openid: ownerOpenid.trim(),
        secret: secret.trim(),
      });
      setSecret("");
      await load();
    } catch {
      setError("Save failed. Check the AppID/Secret.");
    } finally {
      setBusy(false);
    }
  };

  const runTest = async () => {
    if (!csrf) return;
    setBusy(true);
    setTest(null);
    try {
      const r = await api.testQQ(csrf);
      setTest(r.ok ? `✓ Connected: ${r.detail}` : `✗ ${r.detail}`);
    } catch {
      setTest("✗ Not configured yet.");
    } finally {
      setBusy(false);
    }
  };

  const startQr = async () => {
    if (!csrf) return;
    setBusy(true);
    setError(null);
    setQrState("Waiting for scan…");
    try {
      const start = await api.qqBindStart(csrf);
      setQr(start);
      if (pollRef.current) window.clearInterval(pollRef.current);
      pollRef.current = window.setInterval(async () => {
        try {
          const res = await api.qqBindPoll(csrf, start.task_id);
          if (res.status === "completed") {
            if (pollRef.current) window.clearInterval(pollRef.current);
            setQrState(`✓ Bound bot ${res.app_id}`);
            setQr(null);
            await load();
          } else if (res.status === "expired") {
            if (pollRef.current) window.clearInterval(pollRef.current);
            setQrState("QR expired — start again.");
            setQr(null);
          }
        } catch {
          /* keep polling */
        }
      }, 2000);
    } catch {
      setError("Could not reach the QQ bind service.");
      setQrState("");
    } finally {
      setBusy(false);
    }
  };

  const qq = status?.qq;
  const email = status?.email;

  return (
    <div className="app">
      <Sidebar />
      <main className="main">
        <header className="topbar">
          <div className="page-heading">
            <span className="page-eyebrow">Connections</span>
            <h2>Connectors</h2>
            <p className="page-sub small">
              Bring Sherpa closer to where work arrives, without exposing your
              credentials
            </p>
          </div>
        </header>

        <div className="inbox page-content">
          {error && <div className="auth-error">{error}</div>}

          <section className="connector-grid">
            <article className="connector-card featured">
              <header className="connector-card-head">
                <span className="connector-icon qq" aria-hidden="true">
                  QQ
                </span>
                <div>
                  <span className="section-kicker">Messaging</span>
                  <h3>QQ official bot</h3>
                  <p>
                    Talk to Sherpa from QQ through Tencent's official WebSocket
                    gateway.
                  </p>
                </div>
                <span
                  className={
                    qq?.configured ? "pill pill-success" : "pill pill-idle"
                  }
                >
                  {qq?.configured ? "Connected" : "Not connected"}
                </span>
              </header>

              <div className="connector-summary">
                <div>
                  <span>Connection</span>
                  <strong>
                    {qq?.configured ? "Ready for messages" : "Setup required"}
                  </strong>
                </div>
                <div>
                  <span>Owner access</span>
                  <strong>
                    {qq?.owner_openid_set ? "Restricted" : "Not restricted"}
                  </strong>
                </div>
                <button
                  className="btn"
                  disabled={busy}
                  onClick={() => void runTest()}
                >
                  Test connection
                </button>
              </div>
              {test && <div className="notice">{test}</div>}

              <details className="disclosure">
                <summary>Connection details</summary>
                <div className="technical-grid">
                  <span>
                    AppID <code>{qq?.app_id || "Not set"}</code>
                  </span>
                  <span>
                    Secret {qq?.secret_set ? "stored securely" : "not set"}
                  </span>
                  <span>
                    Owner {qq?.owner_openid_set ? "bound" : "not bound"}
                  </span>
                </div>
              </details>

              <div className="connector-setup">
                <div
                  className="seg segmented-control"
                  aria-label="QQ setup method"
                >
                  <button
                    className={"btn" + (mode === "qr" ? " btn-primary" : "")}
                    onClick={() => setMode("qr")}
                  >
                    Scan QR
                  </button>
                  <button
                    className={
                      "btn" + (mode === "manual" ? " btn-primary" : "")
                    }
                    onClick={() => setMode("manual")}
                  >
                    Manual
                  </button>
                </div>

                {mode === "qr" && (
                  <div className="setup-panel">
                    {!qr && (
                      <>
                        <div>
                          <h4>Bind in one scan</h4>
                          <p>
                            Create a bot at{" "}
                            <a
                              href="https://q.qq.com"
                              target="_blank"
                              rel="noreferrer"
                            >
                              q.qq.com
                            </a>
                            , then scan a secure one-time QR code.
                          </p>
                        </div>
                        <button
                          className="btn btn-primary"
                          disabled={busy}
                          onClick={() => void startQr()}
                        >
                          Generate QR code
                        </button>
                      </>
                    )}
                    {qr && (
                      <div className="qr-panel">
                        <div className="qr-code">
                          <QRCodeSVG value={qr.qr_url} size={160} />
                        </div>
                        <div>
                          <h4>Scan with mobile QQ</h4>
                          <p>
                            The AppID and secret are stored automatically after
                            you choose the bot.
                          </p>
                          <a href={qr.qr_url} target="_blank" rel="noreferrer">
                            Open binding link ↗
                          </a>
                        </div>
                      </div>
                    )}
                    {qrState && <div className="notice">{qrState}</div>}
                  </div>
                )}

                {mode === "manual" && (
                  <div className="setup-panel manual-panel">
                    <div className="control-grid">
                      <label className="control">
                        <span>AppID</span>
                        <input
                          value={appId}
                          onChange={(e) => setAppId(e.target.value)}
                        />
                      </label>
                      <label className="control">
                        <span>
                          AppSecret{" "}
                          {qq?.secret_set && "· leave blank to keep current"}
                        </span>
                        <input
                          type="password"
                          value={secret}
                          onChange={(e) => setSecret(e.target.value)}
                          placeholder={
                            qq?.secret_set ? "•••••• (unchanged)" : ""
                          }
                        />
                      </label>
                      <label className="control">
                        <span>Owner QQ openid · optional</span>
                        <input
                          value={ownerOpenid}
                          onChange={(e) => setOwnerOpenid(e.target.value)}
                        />
                      </label>
                    </div>
                    <button
                      className="btn btn-primary"
                      disabled={busy || !appId.trim()}
                      onClick={() => void saveManual()}
                    >
                      Save
                    </button>
                  </div>
                )}
              </div>
            </article>

            <article className="connector-card">
              <header className="connector-card-head">
                <span className="connector-icon email" aria-hidden="true">
                  @
                </span>
                <div>
                  <span className="section-kicker">Email</span>
                  <h3>Agentic email</h3>
                  <p>
                    Receive requests and send approved messages through a
                    dedicated inbox.
                  </p>
                </div>
                <span
                  className={
                    email?.configured ? "pill pill-success" : "pill pill-idle"
                  }
                >
                  {email?.configured ? "Connected" : "Environment setup"}
                </span>
              </header>
              <div className="connector-summary single">
                <div>
                  <span>Inbox</span>
                  <strong>{email?.inbox_id || "Not configured"}</strong>
                </div>
              </div>
              <p className="connector-note">
                Email credentials are managed by the host environment. Use
                Messaging to review channel health and test the loop.
              </p>
              <Link className="btn" to="/messaging">
                Open Messaging
              </Link>
            </article>
          </section>

          <section className="trust-note">
            <span aria-hidden="true">⌁</span>
            <div>
              <strong>Credentials stay sealed</strong>
              <p>
                Secrets are encrypted at rest and never shown again after they
                are saved.
              </p>
            </div>
          </section>
        </div>
      </main>
    </div>
  );
}
