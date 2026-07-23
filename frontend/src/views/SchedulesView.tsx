import { useEffect, useState } from "react";

import {
  api,
  type Schedule,
  type ScheduleFiring,
  type Todo,
} from "../api";
import { useAuth } from "../auth";
import Sidebar from "../components/Sidebar";

function statusPill(status: string): string {
  if (status === "active") return "pill pill-success";
  if (status === "paused") return "pill pill-running";
  if (status === "disabled" || status === "completed") return "pill pill-idle";
  return "pill pill-running";
}

function kindLabel(s: Schedule): string {
  if (s.kind === "daily_digest") return "Daily digest";
  if (s.kind === "agent_task") return "Agent task";
  return "Reminder";
}

function cadenceSummary(s: Schedule): string {
  if (s.kind !== "agent_task") {
    return s.local_time ? `daily ${s.local_time.slice(0, 5)}` : "one-time";
  }
  switch (s.cadence_kind) {
    case "cron":
      return `cron ${s.cron_expr ?? ""}`;
    case "interval":
      return `every ${Math.round((s.interval_seconds ?? 0) / 60)} min`;
    case "daily":
      return `daily ${s.local_time?.slice(0, 5) ?? ""}`;
    case "weekly":
      return `weekly ${s.weekly_days ?? ""} ${s.local_time?.slice(0, 5) ?? ""}`;
    case "monthly":
      return `monthly day ${s.monthly_day ?? ""}`;
    default:
      return s.cadence_kind;
  }
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

  // Agent task (general cron) form.
  const [taskName, setTaskName] = useState("");
  const [taskPrompt, setTaskPrompt] = useState("");
  const [cadence, setCadence] = useState<"cron" | "daily" | "interval">("daily");
  const [cronExpr, setCronExpr] = useState("0 9 * * 1-5");
  const [taskTime, setTaskTime] = useState("08:00");
  const [intervalMin, setIntervalMin] = useState(60);
  const [taskChannel, setTaskChannel] = useState("web");

  const [firings, setFirings] = useState<Record<string, ScheduleFiring[]>>({});
  const [expanded, setExpanded] = useState<string | null>(null);

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

  const addTask = async () => {
    if (!csrf || !taskName.trim() || !taskPrompt.trim()) return;
    setBusy("task");
    setError(null);
    try {
      const body: Parameters<typeof api.createScheduledTask>[1] = {
        name: taskName.trim(),
        prompt: taskPrompt.trim(),
        cadence_kind: cadence,
        timezone: tz,
        delivery_channel: taskChannel,
      };
      if (cadence === "cron") body.cron_expr = cronExpr.trim();
      else if (cadence === "daily") body.local_time = taskTime;
      else body.interval_seconds = intervalMin * 60;
      await api.createScheduledTask(csrf, body);
      setTaskName("");
      setTaskPrompt("");
      await load();
    } catch {
      setError("Could not create the task (check the cadence and prompt).");
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

  const toggleStatus = async (s: Schedule) => {
    if (!csrf) return;
    setBusy(s.id);
    setError(null);
    try {
      await api.setScheduleStatus(
        csrf,
        s.id,
        s.version,
        s.status === "active" ? "paused" : "active",
      );
      await load();
    } catch {
      setError("Could not change status.");
    } finally {
      setBusy(null);
    }
  };

  const runNow = async (s: Schedule) => {
    if (!csrf) return;
    setBusy(s.id);
    setError(null);
    try {
      await api.runScheduleNow(csrf, s.id);
      await loadFirings(s.id);
      setExpanded(s.id);
    } catch {
      setError("Run now failed.");
    } finally {
      setBusy(null);
    }
  };

  const loadFirings = async (id: string) => {
    try {
      const page = await api.listScheduleFirings(id);
      setFirings((prev) => ({ ...prev, [id]: page.items }));
    } catch {
      /* non-fatal */
    }
  };

  const toggleHistory = async (s: Schedule) => {
    if (expanded === s.id) {
      setExpanded(null);
      return;
    }
    await loadFirings(s.id);
    setExpanded(s.id);
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
              Reminders, digests, and recurring tasks Sherpa runs on a rhythm
            </p>
          </div>
        </header>

        <div className="inbox page-content">
          {error && <div className="auth-error">{error}</div>}

          <section className="form-card-grid">
            <article className="form-card form-card-wide">
              <div className="form-card-head">
                <span className="form-card-icon" aria-hidden="true">
                  ⚡
                </span>
                <div>
                  <h3>Scheduled task</h3>
                  <p>
                    Sherpa runs your prompt on a schedule and delivers the
                    result. External actions still ask for approval.
                  </p>
                </div>
              </div>
              <div className="control-grid">
                <label className="control">
                  <span>Name</span>
                  <input
                    value={taskName}
                    onChange={(e) => setTaskName(e.target.value)}
                    placeholder="e.g. Weekday inbox triage"
                    aria-label="Task name"
                  />
                </label>
                <label className="control">
                  <span>Prompt</span>
                  <textarea
                    value={taskPrompt}
                    onChange={(e) => setTaskPrompt(e.target.value)}
                    placeholder="What should Sherpa do each run?"
                    aria-label="Task prompt"
                    rows={2}
                  />
                </label>
                <div className="control-grid two">
                  <label className="control">
                    <span>Cadence</span>
                    <select
                      value={cadence}
                      onChange={(e) =>
                        setCadence(e.target.value as typeof cadence)
                      }
                      aria-label="Cadence type"
                    >
                      <option value="daily">Daily at a time</option>
                      <option value="cron">Cron expression</option>
                      <option value="interval">Every N minutes</option>
                    </select>
                  </label>
                  {cadence === "cron" && (
                    <label className="control">
                      <span>Cron (5-field)</span>
                      <input
                        value={cronExpr}
                        onChange={(e) => setCronExpr(e.target.value)}
                        placeholder="0 9 * * 1-5"
                        aria-label="Cron expression"
                      />
                    </label>
                  )}
                  {cadence === "daily" && (
                    <label className="control">
                      <span>Time</span>
                      <input
                        type="time"
                        value={taskTime}
                        onChange={(e) => setTaskTime(e.target.value)}
                        aria-label="Daily time"
                      />
                    </label>
                  )}
                  {cadence === "interval" && (
                    <label className="control">
                      <span>Every (minutes)</span>
                      <input
                        type="number"
                        min={5}
                        value={intervalMin}
                        onChange={(e) => setIntervalMin(Number(e.target.value))}
                        aria-label="Interval minutes"
                      />
                    </label>
                  )}
                </div>
                <div className="control-grid two">
                  <label className="control">
                    <span>Deliver to</span>
                    <select
                      value={taskChannel}
                      onChange={(e) => setTaskChannel(e.target.value)}
                      aria-label="Delivery channel"
                    >
                      <option value="web">Web inbox</option>
                      <option value="email">Email</option>
                      <option value="qq">QQ</option>
                    </select>
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
              </div>
              <button
                className="btn btn-primary"
                disabled={busy === "task" || !taskName.trim() || !taskPrompt.trim()}
                onClick={() => void addTask()}
              >
                Create task
              </button>
            </article>

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
              <span>Schedules</span>
              <span className="count">{items.length}</span>
            </div>
            {items.length === 0 && (
              <div className="empty-state">
                <span className="empty-icon" aria-hidden="true">
                  ◷
                </span>
                <strong>No schedules yet</strong>
                <span>
                  Create a task, digest, or reminder above. Sherpa keeps an
                  honest delivery record.
                </span>
              </div>
            )}
            {items.map((s) => (
              <div key={s.id}>
                <article className="todo-row schedule-row">
                  <span className={statusPill(s.status)}>{s.status}</span>
                  <span className="todo-title">
                    {kindLabel(s)} · {s.name}
                    <span className="item-subtitle">
                      {cadenceSummary(s)} · {s.timezone} · next{" "}
                      {new Date(s.next_fire_at).toLocaleString()}
                      {s.kind === "agent_task" && ` · → ${s.delivery_channel}`}
                    </span>
                  </span>
                  <span className="schedule-actions">
                    {(s.status === "active" || s.status === "paused") && (
                      <>
                        <button
                          className="btn btn-quiet todo-action"
                          disabled={busy === s.id}
                          onClick={() => void runNow(s)}
                        >
                          Run now
                        </button>
                        <button
                          className="btn btn-quiet todo-action"
                          disabled={busy === s.id}
                          onClick={() => void toggleStatus(s)}
                        >
                          {s.status === "active" ? "Pause" : "Resume"}
                        </button>
                        <button
                          className="btn btn-quiet todo-action"
                          disabled={busy === s.id}
                          onClick={() => void cancel(s)}
                        >
                          Cancel
                        </button>
                      </>
                    )}
                    <button
                      className="btn btn-quiet todo-action"
                      onClick={() => void toggleHistory(s)}
                    >
                      History
                    </button>
                  </span>
                </article>
                {expanded === s.id && (
                  <div className="drive-version-list">
                    {(firings[s.id] ?? []).length === 0 && (
                      <span className="drive-version-empty">No runs yet.</span>
                    )}
                    {(firings[s.id] ?? []).map((f) => (
                      <div className="drive-version-row" key={f.id}>
                        <span>
                          {new Date(f.scheduled_for).toLocaleString()} ·{" "}
                          {f.status}
                          {f.delivery_outcome ? ` (${f.delivery_outcome})` : ""}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </section>
        </div>
      </main>
    </div>
  );
}
