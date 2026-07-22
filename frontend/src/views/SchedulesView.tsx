import { useEffect, useState } from "react";

import { api, type Schedule, type Todo } from "../api";
import { useAuth } from "../auth";
import Sidebar from "../components/Sidebar";

function statusPill(status: string): string {
  if (status === "active") return "pill pill-success";
  if (status === "disabled" || status === "completed") return "pill pill-idle";
  return "pill pill-running";
}

function scheduleLabel(s: Schedule): string {
  const kindLabel = s.kind === "daily_digest" ? "Daily digest" : "Reminder";
  return s.name && s.name !== kindLabel
    ? `${kindLabel} · ${s.name}`
    : kindLabel;
}

export default function SchedulesView() {
  const { csrf } = useAuth();
  const [items, setItems] = useState<Schedule[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [time, setTime] = useState("08:00");
  const [tz, setTz] = useState("UTC");
  const [todos, setTodos] = useState<Todo[]>([]);
  const [todoId, setTodoId] = useState("");
  const [remTime, setRemTime] = useState("");
  const [remKind, setRemKind] = useState("due_soon");

  const load = async () => {
    try {
      const [page, tp] = await Promise.all([
        api.listSchedules(),
        api.listTodos(),
      ]);
      setItems(page.items);
      setTodos(tp.items.filter((t) => t.status !== "completed"));
    } catch {
      setError("Could not load schedules. Is the backend running?");
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const addDigest = async () => {
    if (!csrf) return;
    setBusy("new");
    setError(null);
    try {
      await api.createDigest(csrf, time, tz);
      await load();
    } catch {
      setError("Could not create the digest (check the time/timezone).");
    } finally {
      setBusy(null);
    }
  };

  const cancel = async (s: Schedule) => {
    if (!csrf) return;
    setBusy(s.id);
    setError(null);
    try {
      await api.cancelSchedule(csrf, s.id, s.version);
      await load();
    } catch {
      setError("Cancel failed.");
    } finally {
      setBusy(null);
    }
  };

  const addReminder = async () => {
    if (!csrf || !todoId || !remTime) return;
    setBusy("reminder");
    setError(null);
    try {
      const iso = new Date(remTime).toISOString();
      const todo = todos.find((t) => t.id === todoId);
      await api.createReminder(
        csrf,
        todoId,
        iso,
        remKind,
        tz,
        todo ? todo.title : "Reminder",
      );
      setTodoId("");
      setRemTime("");
      await load();
    } catch {
      setError("Could not create the reminder (check the to-do and time).");
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
            <span className="page-eyebrow">Follow-through</span>
            <h2>Schedules</h2>
            <p className="page-sub small">
              Set a rhythm for reminders without losing track of delivery
            </p>
          </div>
        </header>

        <div className="inbox page-content">
          {error && <div className="auth-error">{error}</div>}

          <section className="form-card-grid">
            <article className="form-card">
              <div className="form-card-head">
                <span className="form-card-icon" aria-hidden="true">
                  ☀
                </span>
                <div>
                  <h3>Daily digest</h3>
                  <p>A calm summary of open work at the time you choose.</p>
                </div>
              </div>
              <div className="control-grid two">
                <label className="control">
                  <span>Delivery time</span>
                  <input
                    type="time"
                    value={time}
                    onChange={(e) => setTime(e.target.value)}
                    aria-label="Digest time"
                  />
                </label>
                <label className="control">
                  <span>Timezone</span>
                  <input
                    value={tz}
                    onChange={(e) => setTz(e.target.value)}
                    placeholder="e.g. Asia/Shanghai"
                    aria-label="Timezone"
                    list="timezone-options"
                  />
                </label>
              </div>
              <button
                className="btn btn-primary"
                disabled={busy === "new"}
                onClick={() => void addDigest()}
              >
                Create digest
              </button>
            </article>

            <article className="form-card">
              <div className="form-card-head">
                <span className="form-card-icon" aria-hidden="true">
                  ◷
                </span>
                <div>
                  <h3>Todo reminder</h3>
                  <p>Attach a one-time reminder to an open todo.</p>
                </div>
              </div>
              {todos.length === 0 ? (
                <div className="empty-state compact embedded">
                  <strong>No open todos</strong>
                  <span>Create or accept a todo first, then return here.</span>
                </div>
              ) : (
                <>
                  <div className="control-grid">
                    <label className="control">
                      <span>Todo</span>
                      <select
                        value={todoId}
                        onChange={(e) => setTodoId(e.target.value)}
                        aria-label="Reminder to-do"
                      >
                        <option value="">Choose a todo…</option>
                        {todos.map((t) => (
                          <option key={t.id} value={t.id}>
                            {t.title}
                          </option>
                        ))}
                      </select>
                    </label>
                    <div className="control-grid two">
                      <label className="control">
                        <span>When</span>
                        <input
                          type="datetime-local"
                          value={remTime}
                          onChange={(e) => setRemTime(e.target.value)}
                          aria-label="Reminder time"
                        />
                      </label>
                      <label className="control">
                        <span>Type</span>
                        <select
                          value={remKind}
                          onChange={(e) => setRemKind(e.target.value)}
                          aria-label="Reminder kind"
                        >
                          <option value="due_soon">Due soon</option>
                          <option value="overdue">Overdue</option>
                        </select>
                      </label>
                    </div>
                  </div>
                  <button
                    className="btn btn-primary"
                    disabled={busy === "reminder" || !todoId || !remTime}
                    onClick={() => void addReminder()}
                  >
                    Create reminder
                  </button>
                </>
              )}
            </article>
          </section>

          <datalist id="timezone-options">
            <option value="UTC" />
            <option value="Asia/Shanghai" />
            <option value="Asia/Tokyo" />
            <option value="Europe/London" />
            <option value="America/Los_Angeles" />
            <option value="America/New_York" />
          </datalist>

          <section className="content-section">
            <div className="section-head">
              <span>Upcoming</span>
              <span className="count">{items.length}</span>
            </div>
            {items.length === 0 && (
              <div className="empty-state">
                <span className="empty-icon" aria-hidden="true">
                  ◷
                </span>
                <strong>No schedules yet</strong>
                <span>
                  Create a digest or reminder above. Sherpa will keep an honest
                  delivery record.
                </span>
              </div>
            )}
            {items.map((s) => (
              <article className="todo-row schedule-row" key={s.id}>
                <span className={statusPill(s.status)}>{s.status}</span>
                <span className="todo-title">
                  {scheduleLabel(s)}
                  <span className="item-subtitle">
                    {s.timezone} · next{" "}
                    {new Date(s.next_fire_at).toLocaleString()}
                  </span>
                </span>
                {s.status === "active" && (
                  <button
                    className="btn btn-quiet"
                    disabled={busy === s.id}
                    onClick={() => void cancel(s)}
                  >
                    Cancel
                  </button>
                )}
              </article>
            ))}
          </section>
        </div>
      </main>
    </div>
  );
}
