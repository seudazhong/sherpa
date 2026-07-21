import { useEffect, useState } from "react";

import { api, type Settings } from "../api";
import { useAuth } from "../auth";
import Sidebar from "../components/Sidebar";

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

  const toggle = (key: keyof Settings, label: string) => (
    <label className="cand-meta small" style={{ display: "block", padding: "6px 0" }}>
      <input
        type="checkbox"
        checked={Boolean(s?.[key])}
        onChange={(e) => set(key, e.target.checked as never)}
      />
      &nbsp;{label}
    </label>
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
            <button className="btn btn-primary" disabled={busy || !s} onClick={() => void save()}>
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
              {toggle("notifications_enabled", "Enable notifications")}
              {toggle("web_enabled", "Web notifications")}
              {toggle("email_digest_enabled", "Email digest")}
              {toggle("quiet_hours_enabled", "Quiet hours")}
              <label className="cand-meta small" style={{ display: "block", padding: "6px 0" }}>
                Daily cap&nbsp;
                <input
                  type="number"
                  min={0}
                  max={100}
                  value={s.daily_cap}
                  onChange={(e) => set("daily_cap", Number(e.target.value) as never)}
                  aria-label="Daily cap"
                />
              </label>
              <label className="cand-meta small" style={{ display: "block", padding: "6px 0" }}>
                Timezone&nbsp;
                <input
                  value={s.timezone}
                  onChange={(e) => set("timezone", e.target.value as never)}
                  placeholder="e.g. Asia/Shanghai"
                  aria-label="Timezone"
                />
              </label>
              <div className="empty small muted">
                Quiet hours {s.quiet_hours_start}–{s.quiet_hours_end} · version {s.version}
              </div>
            </section>
          )}
        </div>
      </main>
    </div>
  );
}
