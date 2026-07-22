import { useEffect, useState } from "react";

import { api, type ActivityReceipt, exportUrl } from "../api";
import { useAuth } from "../auth";
import Sidebar from "../components/Sidebar";

function typePill(receiptType: string): string {
  if (receiptType === "action") return "pill pill-error";
  if (receiptType === "inference") return "pill pill-running";
  return "pill pill-idle";
}

export default function ActivityView() {
  const { csrf } = useAuth();
  const [receipts, setReceipts] = useState<ActivityReceipt[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = async () => {
    try {
      const a = await api.listActivity();
      setReceipts(a.items);
    } catch {
      setError("Could not load activity. Is the backend running?");
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const doExport = () => {
    // Same-origin download; the cookie session authorizes the request.
    window.open(exportUrl(), "_blank");
  };

  const doDelete = async () => {
    if (!csrf) return;
    if (
      !window.confirm(
        "Delete all imported data (emails, candidates, todos)? This cannot be undone.",
      )
    ) {
      return;
    }
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      const res = await api.deleteImported(csrf);
      const total = Object.values(res.deleted).reduce((a, b) => a + b, 0);
      setNote(
        `Imported data deleted. ${total} record${total === 1 ? "" : "s"} removed.`,
      );
      await load();
    } catch {
      setError("Delete failed.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="app">
      <Sidebar />
      <main className="main">
        <header className="topbar">
          <div className="page-heading">
            <span className="page-eyebrow">Trust center</span>
            <h2>Activity &amp; data</h2>
            <p className="page-sub small">
              A clear record of what Sherpa read, inferred, and changed
            </p>
          </div>
        </header>

        <div className="inbox page-content">
          {error && <div className="auth-error">{error}</div>}
          {note && <div className="notice notice-success">{note}</div>}

          <section className="data-control-card">
            <div>
              <span className="section-kicker">Your data</span>
              <h3>Portable by default</h3>
              <p>
                Download a copy anytime. Deleting imported data removes
                connected content and its derived candidates and todos.
              </p>
            </div>
            <div className="data-control-actions">
              <button className="btn" onClick={doExport}>
                Export data
              </button>
              <button
                className="btn btn-danger"
                disabled={busy}
                onClick={() => void doDelete()}
              >
                Delete imported data
              </button>
            </div>
          </section>

          <section className="content-section">
            <div className="section-head">
              <span>Recent activity</span>
              <span className="count">{receipts.length}</span>
            </div>
            {receipts.length === 0 && (
              <div className="empty-state">
                <span className="empty-icon" aria-hidden="true">
                  ✓
                </span>
                <strong>No activity yet</strong>
                <span>
                  Reads, inferences, and actions will appear here with a
                  timestamp.
                </span>
              </div>
            )}
            {receipts.map((r) => (
              <article className="activity-row" key={r.id}>
                <span className="activity-marker" aria-hidden="true" />
                <div className="activity-copy">
                  <div className="row wrap-row">
                    <span className={typePill(r.receipt_type)}>
                      {r.receipt_type}
                    </span>
                    <strong>{r.action}</strong>
                  </div>
                  <span className="small muted">
                    {r.outcome} · {new Date(r.occurred_at).toLocaleString()}
                  </span>
                </div>
              </article>
            ))}
          </section>
        </div>
      </main>
    </div>
  );
}
