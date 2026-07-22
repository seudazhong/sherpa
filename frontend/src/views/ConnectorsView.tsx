import { useEffect, useRef, useState } from "react";
import { QRCodeSVG } from "qrcode.react";

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
  const [qr, setQr] = useState<{ task_id: string; qr_url: string } | null>(null);
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

  return (
    <div className="app">
      <Sidebar />
      <main className="main">
        <header className="topbar">
          <div>
            <h2>Connectors</h2>
            <p className="page-sub small">
              Connect Sherpa to external services. Credentials are encrypted at rest and never
              shown again.
            </p>
          </div>
        </header>

        <div className="inbox">
          {error && <div className="auth-error">{error}</div>}

          <section>
            <div className="section-head">
              QQ official bot
              {qq?.configured ? (
                <span className="pill pill-success">Connected</span>
              ) : (
                <span className="pill pill-idle">Not connected</span>
              )}
            </div>

            <article className="cand-card">
              <div className="cand-main">
                <div className="cand-meta small muted">
                  <div>
                    AppID: <code>{qq?.app_id || "(none)"}</code> · secret{" "}
                    {qq?.secret_set ? "set" : "not set"} · owner{" "}
                    {qq?.owner_openid_set ? "bound" : "not bound"}
                  </div>
                  <div>
                    Connects over the official WebSocket gateway — no public URL needed. Create a
                    bot at <code>q.qq.com</code>, then bind it below.
                  </div>
                </div>

                <div className="seg" style={{ margin: "0.5rem 0" }}>
                  <button
                    className={"btn" + (mode === "qr" ? " btn-primary" : "")}
                    onClick={() => setMode("qr")}
                  >
                    Scan QR
                  </button>
                  <button
                    className={"btn" + (mode === "manual" ? " btn-primary" : "")}
                    onClick={() => setMode("manual")}
                  >
                    Manual
                  </button>
                </div>

                {mode === "qr" && (
                  <div>
                    {!qr && (
                      <button
                        className="btn btn-primary"
                        disabled={busy}
                        onClick={() => void startQr()}
                      >
                        Start QR bind
                      </button>
                    )}
                    {qr && (
                      <div style={{ display: "flex", gap: "1rem", alignItems: "center" }}>
                        <div style={{ background: "#fff", padding: 8, borderRadius: 8 }}>
                          <QRCodeSVG value={qr.qr_url} size={160} />
                        </div>
                        <div className="small muted">
                          Scan with mobile QQ, then pick the bot to bind. The AppID/Secret fill in
                          automatically.
                          <br />
                          <a href={qr.qr_url} target="_blank" rel="noreferrer">
                            Open link
                          </a>
                        </div>
                      </div>
                    )}
                    {qrState && <p className="small muted">{qrState}</p>}
                  </div>
                )}

                {mode === "manual" && (
                  <div className="stack">
                    <label className="small muted">
                      AppID
                      <input value={appId} onChange={(e) => setAppId(e.target.value)} />
                    </label>
                    <label className="small muted">
                      AppSecret {qq?.secret_set && "(leave blank to keep current)"}
                      <input
                        type="password"
                        value={secret}
                        onChange={(e) => setSecret(e.target.value)}
                        placeholder={qq?.secret_set ? "•••••• (unchanged)" : ""}
                      />
                    </label>
                    <label className="small muted">
                      Owner QQ openid (optional — restricts who can drive the agent)
                      <input
                        value={ownerOpenid}
                        onChange={(e) => setOwnerOpenid(e.target.value)}
                      />
                    </label>
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

              <div className="cand-actions">
                <button className="btn" disabled={busy} onClick={() => void runTest()}>
                  Test connection
                </button>
                {test && <span className="small muted">{test}</span>}
              </div>
            </article>
          </section>

          <section>
            <div className="section-head">Agentic email</div>
            <article className="cand-card">
              <div className="cand-main">
                <div className="cand-meta small muted">
                  Email inbox is configured via environment for now. Runtime editing is planned —
                  see the Messaging page for status.
                </div>
              </div>
            </article>
          </section>
        </div>
      </main>
    </div>
  );
}
