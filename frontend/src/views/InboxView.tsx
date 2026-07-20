import { useEffect, useState } from "react";

import { api, type Candidate, type Todo } from "../api";
import { useAuth } from "../auth";
import Sidebar from "../components/Sidebar";

function priorityPill(priority: string): string {
  if (priority === "high") return "pill pill-error";
  if (priority === "low") return "pill pill-idle";
  return "pill pill-running";
}

export default function InboxView() {
  const { csrf } = useAuth();
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [todos, setTodos] = useState<Todo[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = async () => {
    try {
      const [c, t] = await Promise.all([api.listCandidates(), api.listTodos()]);
      setCandidates(c.items);
      setTodos(t.items);
    } catch {
      setError("Could not load your inbox. Is the backend running?");
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const accept = async (c: Candidate) => {
    if (!csrf) return;
    setBusy(c.id);
    setError(null);
    try {
      await api.acceptCandidate(csrf, c.id, c.version);
      await load();
    } catch {
      setError("Accept failed.");
    } finally {
      setBusy(null);
    }
  };

  const dismiss = async (c: Candidate) => {
    if (!csrf) return;
    setBusy(c.id);
    setError(null);
    try {
      await api.dismissCandidate(csrf, c.id, c.version);
      await load();
    } catch {
      setError("Dismiss failed.");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="app">
      <Sidebar />
      <main className="main">
        <header className="topbar">
          <div>
            <h2>Candidate Inbox</h2>
            <p className="page-sub small">Actions Sherpa extracted from your connected accounts</p>
          </div>
        </header>

        <div className="inbox">
          {error && <div className="auth-error">{error}</div>}

          <section>
            <div className="section-head">
              Pending candidates <span className="count">{candidates.length}</span>
            </div>
            {candidates.length === 0 && (
              <div className="empty small muted">
                No pending candidates. Connect Gmail and sync to generate action candidates.
              </div>
            )}
            {candidates.map((c) => (
              <article className="cand-card" key={c.id}>
                <div className="cand-main">
                  <div className="cand-title">{c.title}</div>
                  {c.description && <div className="cand-desc small muted">{c.description}</div>}
                  <div className="cand-meta small">
                    <span className={priorityPill(c.priority)}>{c.priority}</span>
                    <span className="muted">confidence {Math.round(c.confidence * 100)}%</span>
                    {c.due_at && (
                      <span className="muted">due {new Date(c.due_at).toLocaleDateString()}</span>
                    )}
                    {c.source.subject && <span className="muted">· {c.source.subject}</span>}
                    {c.source.sender && <span className="muted">· {c.source.sender}</span>}
                  </div>
                </div>
                <div className="cand-actions">
                  <button
                    className="btn btn-primary"
                    disabled={busy === c.id}
                    onClick={() => void accept(c)}
                  >
                    Accept
                  </button>
                  <button className="btn" disabled={busy === c.id} onClick={() => void dismiss(c)}>
                    Dismiss
                  </button>
                </div>
              </article>
            ))}
          </section>

          <section>
            <div className="section-head">
              Todos <span className="count">{todos.length}</span>
            </div>
            {todos.length === 0 && (
              <div className="empty small muted">Accepted candidates become todos here.</div>
            )}
            {todos.map((t) => (
              <article className="todo-row" key={t.id}>
                <span className={t.status === "completed" ? "pill pill-success" : "pill pill-idle"}>
                  {t.status}
                </span>
                <span className="todo-title">{t.title}</span>
                {t.due_at && (
                  <span className="small muted">due {new Date(t.due_at).toLocaleDateString()}</span>
                )}
              </article>
            ))}
          </section>
        </div>
      </main>
    </div>
  );
}
