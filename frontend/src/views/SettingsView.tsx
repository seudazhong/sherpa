import { useEffect, useState } from "react";

import { api, type Settings } from "../api";
import { useAuth } from "../auth";
import Sidebar from "../components/Sidebar";

function tzValid(tz: string): boolean {
  try {
    new Intl.DateTimeFormat("en", { timeZone: tz });
    return true;
  } catch {
    return false;
  }
}

export default function SettingsView() {
  const { csrf } = useAuth();
  const [s, setS] = useState<Settings | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = async () => {
    try {
      setS(await api.getSettings());
    } catch {
      setError("Could not load settings. Is the backend running?");
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const set = <K extends keyof Settings>(key: K, value: Settings[K]) => {
    setS((prev) => (prev ? { ...prev, [key]: value } : prev));
  };

  const save = async () => {
    if (!csrf || !s) return;
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      const updated = await api.updateSettings(csrf, {
        if_version: s.version,
        notifications_enabled: s.notifications_enabled,
        web_enabled: s.web_enabled,
        email_digest_enabled: s.email_digest_enabled,
        timezone: s.timezone,
        quiet_hours_enabled: s.quiet_hours_enabled,
        quiet_hours_start: s.quiet_hours_start,
        quiet_hours_end: s.quiet_hours_end,
        daily_cap: s.daily_cap,
      });
      setS(updated);
      setNote("Saved.");
    } catch {
      setError("Save failed (someone else may have changed settings — reload).");
    } finally {
      setBusy(false);
    }
  };

  const toggleRow = (key: keyof Settings, label: string, desc: string) => (
    <div className="setting-row">
      <div>
        <strong>{label}</strong>
        <div className="small muted">{desc}</div>
      </div>
      <label className="switch">
        <input
          type="checkbox"
          checked={Boolean(s?.[key])}
          onChange={(e) => set(key, e.target.checked as never)}
        />
        <span className="switch-slider" />
      </label>
    </div>
  );

  return (
    <div className="app">
      <Sidebar />
      <main className="main">
        <header className="topbar">
          <div>
            <h2>Settings</h2>
            <p className="page-sub small">Notification preferences</p>
          </div>
          <div className="cand-actions">
            <button
              className="btn btn-primary"
              disabled={busy || !s || (!!s && !tzValid(s.timezone))}
              onClick={() => void save()}
            >
              Save
            </button>
          </div>
        </header>

        <div className="inbox">
          {error && <div className="auth-error">{error}</div>}
          {note && <div className="empty small muted">{note}</div>}
          {!s && !error && <div className="empty small muted">Loading…</div>}

          {s && (
            <section>
              <div className="section-head">Notifications</div>
              <div className="settings-card">
                {toggleRow(
                  "notifications_enabled",
                  "Enable notifications",
                  "Master switch for all reminders and digests.",
                )}
                {toggleRow(
                  "web_enabled",
                  "Web notifications",
                  "Show notifications in your web inbox.",
                )}
                {toggleRow(
                  "email_digest_enabled",
                  "Email digest",
                  "Deliver the daily digest by email.",
                )}
                {toggleRow(
                  "quiet_hours_enabled",
                  "Quiet hours",
                  `Suppress delivery ${s.quiet_hours_start.slice(0, 5)}–${s.quiet_hours_end.slice(0, 5)}.`,
                )}
                {s.quiet_hours_enabled && (
                  <div className="setting-row">
                    <div>
                      <strong>Quiet hours window</strong>
                      <div className="small muted">Start and end must differ.</div>
                    </div>
                    <div className="cand-meta small">
                      <input
                        type="time"
                        value={s.quiet_hours_start.slice(0, 5)}
                        onChange={(e) => set("quiet_hours_start", e.target.value as never)}
                        aria-label="Quiet hours start"
                      />
                      &nbsp;–&nbsp;
                      <input
                        type="time"
                        value={s.quiet_hours_end.slice(0, 5)}
                        onChange={(e) => set("quiet_hours_end", e.target.value as never)}
                        aria-label="Quiet hours end"
                      />
                    </div>
                  </div>
                )}
                <div className="setting-row">
                  <div>
                    <strong>Daily cap</strong>
                    <div className="small muted">Max notifications per day (0–100).</div>
                  </div>
                  <input
                    type="number"
                    min={0}
                    max={100}
                    value={s.daily_cap}
                    onChange={(e) => set("daily_cap", Number(e.target.value) as never)}
                    aria-label="Daily cap"
                    style={{ width: 72 }}
                  />
                </div>
                <div className="setting-row">
                  <div>
                    <strong>Timezone</strong>
                    <div className="small muted">Used for digest and reminder times.</div>
                    {!tzValid(s.timezone) && (
                      <div className="small" style={{ color: "var(--accent)" }}>
                        Unknown timezone — e.g. use “Asia/Shanghai” or “UTC”.
                      </div>
                    )}
                  </div>
                  <input
                    value={s.timezone}
                    onChange={(e) => set("timezone", e.target.value as never)}
                    placeholder="e.g. Asia/Shanghai"
                    aria-label="Timezone"
                  />
                </div>
              </div>
              <div className="small muted" style={{ padding: "10px 2px 0" }}>
                version {s.version}
              </div>
            </section>
          )}
        </div>
      </main>
    </div>
  );
}
