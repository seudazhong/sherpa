import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import {
  api,
  type Candidate,
  type Notification,
  type PendingApproval,
  type Todo,
} from "../api";
import { useAuth } from "../auth";
import Sidebar from "../components/Sidebar";

function priorityPill(priority: string): string {
  if (priority === "high") return "pill pill-error";
  if (priority === "low") return "pill pill-idle";
  return "pill pill-running";
}

function outcomePill(outcome: string | null): string {
  if (outcome === "delivered") return "pill pill-success";
  if (outcome === "missed") return "pill pill-idle";
  return "pill pill-error";
}

function confidenceLabel(confidence: number): string {
  if (confidence >= 0.85) return "High confidence";
  if (confidence >= 0.65) return "Worth a glance";
  return "Review details";
}

export default function TodayView() {
  const { csrf } = useAuth();
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [todos, setTodos] = useState<Todo[]>([]);
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [approvals, setApprovals] = useState<PendingApproval[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const openTodoCount = todos.filter(
    (todo) => todo.status !== "completed",
  ).length;

  const load = async () => {
    try {
      const [c, t, n, p] = await Promise.all([
        api.listCandidates(),
        api.listTodos(),
        api.listNotifications(),
        api.listPermissions(),
      ]);
      setCandidates(c.items);
      setTodos(t.items);
      setNotifications(n.items);
      setApprovals(p.items);
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

  const completeTodo = async (t: Todo) => {
    if (!csrf) return;
    setBusy(t.id);
    setError(null);
    try {
      await api.patchTodo(csrf, t.id, {
        if_version: t.version,
        status: "completed",
      });
      await load();
    } catch {
      setError("Could not complete the todo.");
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
            <span className="page-eyebrow">Workspace</span>
            <h2>Today</h2>
            <p className="page-sub small">
              What needs you today — suggestions, follow-through, and updates
            </p>
          </div>
        </header>

        <div className="inbox page-content">
          {error && <div className="auth-error">{error}</div>}

          <section className="metric-grid" aria-label="Today overview">
            <article className="metric-card">
              <span className="metric-label">Needs review</span>
              <strong>{candidates.length}</strong>
              <span>candidate{candidates.length === 1 ? "" : "s"}</span>
            </article>
            <article className="metric-card">
              <span className="metric-label">Waiting on you</span>
              <strong>{approvals.length}</strong>
              <span>approval{approvals.length === 1 ? "" : "s"}</span>
            </article>
            <article className="metric-card">
              <span className="metric-label">Open work</span>
              <strong>{openTodoCount}</strong>
              <span>todo{openTodoCount === 1 ? "" : "s"}</span>
            </article>
            <article className="metric-card">
              <span className="metric-label">Updates</span>
              <strong>{notifications.length}</strong>
              <span>notification{notifications.length === 1 ? "" : "s"}</span>
            </article>
          </section>

          <section className="content-section">
            <div className="section-head">
              <span>Suggested actions</span>
              <span className="count">{candidates.length}</span>
            </div>
            {candidates.length === 0 && (
              <div className="empty-state">
                <span className="empty-icon" aria-hidden="true">
                  ↳
                </span>
                <strong>Your review queue is clear</strong>
                <span>
                  Connect a source and sync it to let Sherpa surface actionable
                  items.
                </span>
                <Link className="btn" to="/integrations">
                  Manage connectors
                </Link>
              </div>
            )}
            {candidates.map((c) => (
              <article className="cand-card" key={c.id}>
                <div className="cand-main">
                  <div className="cand-title">{c.title}</div>
                  {c.description && (
                    <div className="cand-desc small muted">{c.description}</div>
                  )}
                  {c.source.excerpt && (
                    <blockquote className="source-excerpt">
                      {c.source.excerpt}
                    </blockquote>
                  )}
                  <div className="cand-meta small">
                    <span className={priorityPill(c.priority)}>
                      {c.priority}
                    </span>
                    <span className="muted">
                      {confidenceLabel(c.confidence)}
                    </span>
                    {c.due_at && (
                      <span className="muted">
                        due {new Date(c.due_at).toLocaleDateString()}
                      </span>
                    )}
                    {c.source.subject && (
                      <span className="muted">{c.source.subject}</span>
                    )}
                    {c.source.sender && (
                      <span className="muted">from {c.source.sender}</span>
                    )}
                    {c.source.deep_link && (
                      <a
                        href={c.source.deep_link}
                        target="_blank"
                        rel="noreferrer"
                      >
                        Open source ↗
                      </a>
                    )}
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
                  <button
                    className="btn"
                    disabled={busy === c.id}
                    onClick={() => void dismiss(c)}
                  >
                    Dismiss
                  </button>
                </div>
              </article>
            ))}
          </section>

          <section className="content-section">
            <div className="section-head">
              <span>Approvals</span>
              <span className="section-head-right">
                <span className="count">{approvals.length}</span>
                {/* Read-only roll-up: the decision itself is made on /approvals. */}
                <Link className="section-link" to="/approvals">
                  Open Approvals
                </Link>
              </span>
            </div>
            {approvals.length === 0 && (
              <div className="empty-state compact">
                <strong>Nothing is waiting for approval</strong>
                <span>
                  External actions stay paused until you make the call.
                </span>
              </div>
            )}
            {approvals.map((a) => (
              <article className="cand-card" key={a.correlation_id}>
                <div className="cand-main">
                  <div className="cand-title">
                    <span className="pill pill-error">Approval needed</span>{" "}
                    {a.tool_name}
                  </div>
                  <div className="cand-desc small muted">
                    {a.human_readable_preview.summary}
                  </div>
                  <div className="cand-meta small">
                    {a.human_readable_preview.details.map((d) => (
                      <span className="muted" key={d.label}>
                        · {d.label}: {d.value}
                      </span>
                    ))}
                    <span className="muted">
                      · expires {new Date(a.expires_at).toLocaleString()}
                    </span>
                  </div>
                </div>
                <div className="cand-actions">
                  <Link className="btn btn-primary" to="/approvals">
                    Review
                  </Link>
                </div>
              </article>
            ))}
          </section>

          <section className="content-section">
            <div className="section-head">
              <span>Todos</span>
              <span className="count">{todos.length}</span>
            </div>
            {todos.length === 0 && (
              <div className="empty-state compact">
                <strong>No todos yet</strong>
                <span>Accept a suggestion or ask Sherpa to create one.</span>
              </div>
            )}
            {todos.map((t) => (
              <article className="todo-row" key={t.id}>
                <span
                  className={
                    t.status === "completed"
                      ? "pill pill-success"
                      : "pill pill-idle"
                  }
                >
                  {t.status}
                </span>
                <span className="todo-title">{t.title}</span>
                {t.due_at && (
                  <span className="small muted">
                    due {new Date(t.due_at).toLocaleDateString()}
                  </span>
                )}
                {t.status !== "completed" && (
                  <button
                    className="btn todo-action"
                    disabled={busy === t.id}
                    onClick={() => void completeTodo(t)}
                  >
                    Complete
                  </button>
                )}
              </article>
            ))}
          </section>

          <section className="content-section">
            <div className="section-head">
              <span>Notifications</span>
              <span className="count">{notifications.length}</span>
            </div>
            {notifications.length === 0 && (
              <div className="empty-state compact">
                <strong>No updates yet</strong>
                <span>
                  Reminder and digest delivery receipts will appear here.
                </span>
              </div>
            )}
            {notifications.map((n) => (
              <article className="todo-row" key={n.firing_id}>
                <span className={outcomePill(n.delivery_outcome)}>
                  {n.delivery_outcome ?? n.status}
                </span>
                <span className="todo-title">{n.schedule_name}</span>
                <span className="small muted">
                  {n.channel} · {new Date(n.scheduled_for).toLocaleString()}
                </span>
              </article>
            ))}
          </section>
        </div>
      </main>
    </div>
  );
}
