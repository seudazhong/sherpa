import { useEffect, useState } from "react";

import { api, type Settings } from "../api";
import { useAuth } from "../auth";
import { ModelsPanel } from "../components/ModelsPanel";
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
      setError(
        "Save failed (someone else may have changed settings — reload).",
      );
    } finally {
      setBusy(false);
    }
  };

  const toggleRow = (key: keyof Settings, label: string, desc: string) => (
    <div className="setting-row">
      <div className="setting-copy">
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
          <div className="page-heading">
            <span className="page-eyebrow">Preferences</span>
            <h2>Settings</h2>
            <p className="page-sub small">
              Choose how and when Sherpa should get your attention
            </p>
          </div>
          <div className="topbar-actions">
            <button
              className="btn btn-primary"
              disabled={busy || !s || (!!s && !tzValid(s.timezone))}
              onClick={() => void save()}
            >
              Save
            </button>
          </div>
        </header>

        <div className="inbox page-content">
          {error && <div className="auth-error">{error}</div>}
          {note && <div className="notice notice-success">{note}</div>}
          {!s && !error && (
            <div className="empty-state compact">Loading preferences…</div>
          )}

          {s && (
            <>
              <section className="settings-grid">
                <article className="settings-panel">
                  <div className="settings-panel-head">
                    <span className="form-card-icon" aria-hidden="true">
                      ◉
                    </span>
                    <div>
                      <h3>Delivery</h3>
                      <p>Decide which notification surfaces are allowed.</p>
                    </div>
                  </div>
                  <div className="settings-card">
                    {toggleRow(
                      "notifications_enabled",
                      "Enable notifications",
                      "Master switch for reminders and digests.",
                    )}
                    {toggleRow(
                      "web_enabled",
                      "Web notifications",
                      "Keep delivery receipts in your Sherpa inbox.",
                    )}
                    {toggleRow(
                      "email_digest_enabled",
                      "Email digest",
                      "Send the daily digest to your connected inbox.",
                    )}
                  </div>
                </article>

                <article className="settings-panel">
                  <div className="settings-panel-head">
                    <span className="form-card-icon" aria-hidden="true">
                      ◷
                    </span>
                    <div>
                      <h3>Timing</h3>
                      <p>Keep notifications respectful of your day.</p>
                    </div>
                  </div>
                  <div className="settings-card">
                    {toggleRow(
                      "quiet_hours_enabled",
                      "Quiet hours",
                      `Pause delivery ${s.quiet_hours_start.slice(0, 5)}–${s.quiet_hours_end.slice(0, 5)}.`,
                    )}
                    {s.quiet_hours_enabled && (
                      <div className="setting-row setting-row-stacked">
                        <div className="setting-copy">
                          <strong>Quiet hours window</strong>
                          <div className="small muted">
                            Start and end must differ.
                          </div>
                        </div>
                        <div className="control-grid two compact-controls">
                          <label className="control">
                            <span>Start</span>
                            <input
                              type="time"
                              value={s.quiet_hours_start.slice(0, 5)}
                              onChange={(e) =>
                                set(
                                  "quiet_hours_start",
                                  e.target.value as never,
                                )
                              }
                              aria-label="Quiet hours start"
                            />
                          </label>
                          <label className="control">
                            <span>End</span>
                            <input
                              type="time"
                              value={s.quiet_hours_end.slice(0, 5)}
                              onChange={(e) =>
                                set("quiet_hours_end", e.target.value as never)
                              }
                              aria-label="Quiet hours end"
                            />
                          </label>
                        </div>
                      </div>
                    )}
                    <div className="setting-row">
                      <div className="setting-copy">
                        <strong>Daily cap</strong>
                        <div className="small muted">
                          Maximum notifications per day.
                        </div>
                      </div>
                      <input
                        className="number-input"
                        type="number"
                        min={0}
                        max={100}
                        value={s.daily_cap}
                        onChange={(e) =>
                          set("daily_cap", Number(e.target.value) as never)
                        }
                        aria-label="Daily cap"
                      />
                    </div>
                    <div className="setting-row setting-row-stacked">
                      <div className="setting-copy">
                        <strong>Timezone</strong>
                        <div className="small muted">
                          Used for digests, reminders, and quiet hours.
                        </div>
                        {!tzValid(s.timezone) && (
                          <div className="field-error">
                            Unknown timezone. Try “Asia/Shanghai” or “UTC”.
                          </div>
                        )}
                      </div>
                      <label className="control">
                        <span className="sr-only">Timezone</span>
                        <input
                          value={s.timezone}
                          onChange={(e) =>
                            set("timezone", e.target.value as never)
                          }
                          placeholder="e.g. Asia/Shanghai"
                          aria-label="Timezone"
                          list="settings-timezones"
                        />
                      </label>
                    </div>
                  </div>
                </article>
              </section>
              <ModelsPanel />
              <datalist id="settings-timezones">
                <option value="UTC" />
                <option value="Asia/Shanghai" />
                <option value="Asia/Tokyo" />
                <option value="Europe/London" />
                <option value="America/Los_Angeles" />
                <option value="America/New_York" />
              </datalist>
              <div className="version-note">
                Preferences version {s.version}
              </div>
            </>
          )}
        </div>
      </main>
    </div>
  );
}
