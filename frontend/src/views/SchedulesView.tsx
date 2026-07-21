import { useEffect, useState } from "react";

import { api, type Schedule } from "../api";
import { useAuth } from "../auth";
import Sidebar from "../components/Sidebar";

function statusPill(status: string): string {
  if (status === "active") return "pill pill-success";
  if (status === "disabled" || status === "completed") return "pill pill-idle";
  return "pill pill-running";
}

export default function SchedulesView() {
  const { csrf } = useAuth();
  const [items, setItems] = useState<Schedule[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [time, setTime] = useState("08:00");
  const [tz, setTz] = useState("UTC");

  const load = async () => {
    try {
      const page = await api.listSchedules();
      setItems(page.items);
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

  return (
    <div className="app">
      <Sidebar />
      <main className="main">
        <header className="topbar">
          <div>
            <h2>Schedules</h2>
            <p className="page-sub small">Your reminders and daily digests</p>
          </div>
        </header>

        <div className="inbox">
          {error && <div className="auth-error">{error}</div>}

          <section>
            <div className="section-head">New daily digest</div>
            <article className="cand-card">
              <div className="cand-main">
                <div className="cand-meta small">
                  <label>
                    Time&nbsp;
                    <input
                      type="time"
                      value={time}
                      onChange={(e) => setTime(e.target.value)}
                      aria-label="Digest time"
                    />
                  </label>
                  <label>
                    &nbsp;Timezone&nbsp;
                    <input
                      value={tz}
                      onChange={(e) => setTz(e.target.value)}
                      placeholder="e.g. Asia/Shanghai"
                      aria-label="Timezone"
                    />
                  </label>
                </div>
              </div>
              <div className="cand-actions">
                <button className="btn btn-primary" disabled={busy === "new"} onClick={() => void addDigest()}>
                  Add digest
                </button>
              </div>
            </article>
            <div className="empty small muted">
              To set a reminder for a specific to-do, ask Sherpa in chat ("remind me about …").
            </div>
          </section>

          <section>
            <div className="section-head">
              Schedules <span className="count">{items.length}</span>
            </div>
            {items.length === 0 && (
              <div className="empty small muted">No schedules yet. Add a digest above.</div>
            )}
            {items.map((s) => (
              <article className="todo-row" key={s.id}>
                <span className={statusPill(s.status)}>{s.status}</span>
                <span className="todo-title">
                  {s.kind === "daily_digest" ? "Daily digest" : "Reminder"} · {s.name}
                </span>
                <span className="small muted">next {new Date(s.next_fire_at).toLocaleString()}</span>
                {s.status === "active" && (
                  <button className="btn" disabled={busy === s.id} onClick={() => void cancel(s)}>
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
