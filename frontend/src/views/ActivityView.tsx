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
    if (!window.confirm("Delete all imported data (emails, candidates, todos)? This cannot be undone.")) {
      return;
    }
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      const res = await api.deleteImported(csrf);
      const total = Object.values(res.deleted).reduce((a, b) => a + b, 0);
      setNote(`Deleted ${total} record(s): ${JSON.stringify(res.deleted)}`);
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
          <div>
            <h2>Activity &amp; data</h2>
            <p className="page-sub small">What Sherpa did on your behalf, and controls for your data</p>
          </div>
          <div className="cand-actions">
            <button className="btn" onClick={doExport}>
              Export my data
            </button>
            <button className="btn btn-danger" disabled={busy} onClick={() => void doDelete()}>
              Delete imported data
            </button>
          </div>
        </header>

        <div className="inbox">
          {error && <div className="auth-error">{error}</div>}
          {note && <div className="empty small muted">{note}</div>}

          <section>
            <div className="section-head">
              Activity <span className="count">{receipts.length}</span>
            </div>
            {receipts.length === 0 && (
              <div className="empty small muted">
                Reads, inferences, and actions Sherpa performs will appear here.
              </div>
            )}
            {receipts.map((r) => (
              <article className="todo-row" key={r.id}>
                <span className={typePill(r.receipt_type)}>{r.receipt_type}</span>
                <span className="todo-title">{r.action}</span>
                <span className="small muted">{r.outcome}</span>
                <span className="small muted">{new Date(r.occurred_at).toLocaleString()}</span>
              </article>
            ))}
          </section>
        </div>
      </main>
    </div>
  );
}
