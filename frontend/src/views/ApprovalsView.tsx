import { useCallback, useEffect, useState } from "react";

import { api, type Grant, type PendingApproval } from "../api";
import { useAuth } from "../auth";
import Sidebar from "../components/Sidebar";

function expiresLabel(iso: string): { text: string; overdue: boolean } {
  const ms = new Date(iso).getTime() - Date.now();
  if (ms <= 0) return { text: "expired", overdue: true };
  const mins = Math.round(ms / 60000);
  if (mins < 60) return { text: `expires in ${mins}m`, overdue: false };
  return { text: `expires ${new Date(iso).toLocaleString()}`, overdue: false };
}

function grantLabel(g: Grant): string {
  if (g.tool_name === "email_send") {
    const r = g.match_json.recipients;
    const list = Array.isArray(r) ? r.join(", ") : "";
    return `Send email to ${list}`;
  }
  return `${g.tool_name}: ${JSON.stringify(g.match_json)}`;
}

export default function ApprovalsView() {
  const { csrf } = useAuth();
  const [items, setItems] = useState<PendingApproval[]>([]);
  const [grants, setGrants] = useState<Grant[]>([]);
  const [newEmail, setNewEmail] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [perms, gr] = await Promise.all([
        api.listPermissions(),
        api.listGrants(),
      ]);
      setItems(perms.items);
      setGrants(gr.items);
    } catch {
      setError("Could not load approvals. Is the backend running?");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const resolve = async (
    p: PendingApproval,
    choice: "allow_once" | "always" | "reject",
  ) => {
    if (!csrf) return;
    setBusy(p.correlation_id);
    setError(null);
    try {
      // Background/scheduled approvals have no SSE nonce; web owner resolution is
      // authorized by session + CSRF + binding (ADR-034).
      await api.resolvePermission(csrf, p, null, choice);
      await load();
    } catch {
      setError("Could not resolve this approval (it may have expired).");
    } finally {
      setBusy(null);
    }
  };

  const addEmailGrant = async () => {
    if (!csrf || !newEmail.trim()) return;
    setBusy("grant");
    setError(null);
    try {
      await api.createGrant(csrf, "email_send", {
        recipients: [newEmail.trim().toLowerCase()],
      });
      setNewEmail("");
      await load();
    } catch {
      setError("Could not add the trusted recipient.");
    } finally {
      setBusy(null);
    }
  };

  const removeGrant = async (g: Grant) => {
    if (!csrf) return;
    setBusy(g.id);
    setError(null);
    try {
      await api.deleteGrant(csrf, g.id);
      await load();
    } catch {
      setError("Could not remove the grant.");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="app">
      <Sidebar />
      <main className="main">
        <header className="topbar">
          <div className="page-heading">
            <span className="page-eyebrow">Safety</span>
            <h2>Approvals</h2>
            <p className="page-sub small">
              External actions Sherpa paused for your decision — including from
              scheduled tasks
            </p>
          </div>
          <button className="btn" onClick={() => void load()}>
            Refresh
          </button>
        </header>

        <div className="inbox page-content">
          {error && <div className="auth-error">{error}</div>}

          <section className="content-section">
            <div className="section-head">
              <span>Pending</span>
              <span className="count">{items.length}</span>
            </div>

            {items.length === 0 && (
              <div className="empty-state">
                <span className="empty-icon" aria-hidden="true">
                  ✓
                </span>
                <strong>Nothing waiting</strong>
                <span>
                  When Sherpa needs your approval for an external action, it
                  appears here.
                </span>
              </div>
            )}

            {items.map((p) => {
              const exp = expiresLabel(p.expires_at);
              return (
                <article className="approval-card" key={p.correlation_id}>
                  <div className="approval-head">
                    <span className="approval-tool">
                      {p.human_readable_preview.action || p.tool_name}
                    </span>
                    <span
                      className={
                        "pill " + (exp.overdue ? "pill-error" : "pill-idle")
                      }
                    >
                      {exp.text}
                    </span>
                  </div>
                  <p className="approval-summary">
                    {p.human_readable_preview.summary}
                  </p>
                  {p.human_readable_preview.details.length > 0 && (
                    <dl className="approval-details">
                      {p.human_readable_preview.details.map((d) => (
                        <div key={d.label}>
                          <dt>{d.label}</dt>
                          <dd>{d.value}</dd>
                        </div>
                      ))}
                    </dl>
                  )}
                  {p.human_readable_preview.risk && (
                    <p className="approval-risk">
                      {p.human_readable_preview.risk}
                    </p>
                  )}
                  <div className="approval-actions">
                    <button
                      className="btn btn-quiet"
                      disabled={busy === p.correlation_id}
                      onClick={() => void resolve(p, "reject")}
                    >
                      Reject
                    </button>
                    <button
                      className="btn btn-quiet"
                      disabled={busy === p.correlation_id}
                      onClick={() => void resolve(p, "always")}
                      title="Approve and stop asking for matching actions"
                    >
                      Always allow
                    </button>
                    <button
                      className="btn btn-primary"
                      disabled={busy === p.correlation_id}
                      onClick={() => void resolve(p, "allow_once")}
                    >
                      Approve once
                    </button>
                  </div>
                </article>
              );
            })}
          </section>

          <section className="content-section">
            <div className="section-head">
              <span>Pre-authorized (no approval needed)</span>
              <span className="count">{grants.length}</span>
            </div>
            <p className="page-sub small" style={{ marginTop: 0 }}>
              Trusted email recipients: Sherpa sends to these without asking —
              handy for scheduled emails to yourself.
            </p>
            <label className="session-search-box drive-search">
              <span aria-hidden="true">@</span>
              <input
                value={newEmail}
                onChange={(e) => setNewEmail(e.target.value)}
                placeholder="Add a trusted email recipient…"
                aria-label="Trusted email recipient"
                type="email"
                onKeyDown={(e) => {
                  if (e.key === "Enter") void addEmailGrant();
                }}
              />
              <button
                className="btn btn-primary"
                disabled={busy === "grant" || !newEmail.trim()}
                onClick={() => void addEmailGrant()}
              >
                Add
              </button>
            </label>

            {grants.length === 0 && (
              <div className="empty-state compact">
                <strong>No pre-authorizations</strong>
                <span>
                  Add a trusted recipient above, or choose “Always allow” on a
                  pending approval.
                </span>
              </div>
            )}
            {grants.map((g) => (
              <article className="todo-row file-row" key={g.id}>
                <span className="todo-title">
                  {grantLabel(g)}
                  <span className="item-subtitle">
                    {g.created_via === "always"
                      ? "from an approval"
                      : "added manually"}
                  </span>
                </span>
                <button
                  className="btn btn-quiet todo-action danger"
                  disabled={busy === g.id}
                  onClick={() => void removeGrant(g)}
                >
                  Remove
                </button>
              </article>
            ))}
          </section>
        </div>
      </main>
    </div>
  );
}

