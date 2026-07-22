import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import {
  api,
  type ChatMessage,
  type MessagePart,
  type ResumeState,
  type SessionSummary,
} from "../api";
import { useAuth } from "../auth";
import Sidebar from "../components/Sidebar";

const STATE_LABEL: Record<ResumeState, string> = {
  ready: "Ready",
  running: "Running",
  stale: "Interrupted",
  approval: "Waiting for you",
  approval_expired: "Approval expired",
  interrupted: "Interrupted",
  effect_unknown: "Outcome unknown",
  failed: "Failed",
  archived: "Archived",
};

const STATE_PILL: Record<ResumeState, string> = {
  ready: "pill pill-success",
  running: "pill pill-live",
  stale: "pill pill-running",
  approval: "pill pill-running",
  approval_expired: "pill pill-idle",
  interrupted: "pill pill-running",
  effect_unknown: "pill pill-error",
  failed: "pill pill-error",
  archived: "pill pill-idle",
};

const FILTERS: Array<{
  key: string;
  label: string;
  match: (s: ResumeState) => boolean;
}> = [
  { key: "all", label: "All", match: () => true },
  { key: "ready", label: "Ready", match: (s) => s === "ready" },
  { key: "running", label: "Running", match: (s) => s === "running" },
  { key: "approval", label: "Waiting for you", match: (s) => s === "approval" },
  {
    key: "attention",
    label: "Needs attention",
    match: (s) =>
      [
        "stale",
        "interrupted",
        "effect_unknown",
        "failed",
        "approval_expired",
      ].includes(s),
  },
];

function channelBadge(channel: string): { text: string; cls: string } {
  if (channel === "email") return { text: "@", cls: "channel-mini-icon email" };
  if (channel === "qq") return { text: "QQ", cls: "channel-mini-icon qq" };
  return { text: "WEB", cls: "channel-mini-icon qq" };
}

function label(s: SessionSummary): string {
  return s.title || s.last_message_preview || "Untitled chat";
}

interface Banner {
  title: string;
  detail: string;
  cls: string;
  action: { text: string; run: () => void } | null;
  secondary?: { text: string; run: () => void };
}

export default function SessionsView() {
  const { csrf } = useAuth();
  const navigate = useNavigate();
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const [selected, setSelected] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [renaming, setRenaming] = useState<string>("");

  const searching = query.trim().length > 0;

  const load = useCallback(async () => {
    try {
      const q = query.trim();
      const page = q
        ? await api.listSessions({ query: q, limit: 30 })
        : await api.listSessions({ limit: 100 });
      setSessions(page.items);
      setSelected((prev) => {
        if (prev && page.items.some((s) => s.id === prev)) return prev;
        return page.items[0]?.id ?? null;
      });
    } catch {
      setError("Could not load sessions. Is the backend running?");
    }
  }, [query]);

  useEffect(() => {
    const t = setTimeout(() => void load(), 200);
    return () => clearTimeout(t);
  }, [load]);

  const visible = useMemo(() => {
    if (searching) return sessions;
    const f = FILTERS.find((x) => x.key === filter) ?? FILTERS[0];
    return sessions.filter((s) => f.match(s.resume_state));
  }, [sessions, filter, searching]);

  const current = useMemo(
    () => sessions.find((s) => s.id === selected) ?? null,
    [sessions, selected],
  );

  useEffect(() => {
    if (!current) {
      setMessages([]);
      return;
    }
    setRenaming(current.title ?? "");
    void api
      .sessionTimeline(current.id, "session", "")
      .then((p) => setMessages(p.items))
      .catch(() => setMessages([]));
  }, [current]);

  const openInChat = (id: string) => navigate(`/?session=${id}`);

  const recover = async (
    id: string,
    action: "recheck" | "verified" | "new_run",
  ) => {
    if (!csrf) return;
    setBusy(true);
    try {
      await api.recoverSession(csrf, id, action);
      await load();
    } catch {
      setError("Recovery failed.");
    } finally {
      setBusy(false);
    }
  };

  const rename = async () => {
    if (!csrf || !current || !renaming.trim()) return;
    setBusy(true);
    try {
      await api.renameSession(csrf, current.id, renaming.trim());
      await load();
    } catch {
      setError("Rename failed.");
    } finally {
      setBusy(false);
    }
  };

  const banner = (s: SessionSummary): Banner => {
    switch (s.resume_state) {
      case "running":
        return {
          title: "Run is active",
          detail: "Reconnect to live progress without starting a new run.",
          cls: "running",
          action: { text: "Reconnect", run: () => openInChat(s.id) },
        };
      case "stale":
        return {
          title: "Interrupted",
          detail:
            "The run stopped and its lease expired. Recover before continuing.",
          cls: "attention",
          action: {
            text: "Recover run",
            run: () => void recover(s.id, "new_run"),
          },
        };
      case "approval":
        return {
          title: "Waiting for your approval",
          detail: "An external action is paused until you decide.",
          cls: "approval",
          action: { text: "Review in chat", run: () => openInChat(s.id) },
        };
      case "approval_expired":
        return {
          title: "Approval expired",
          detail: "The pending approval timed out. Nothing was sent.",
          cls: "attention",
          action: { text: "Open transcript", run: () => openInChat(s.id) },
        };
      case "effect_unknown":
        return {
          title: "Outcome unknown",
          detail:
            "Sherpa cannot prove whether an external action completed. It will not retry automatically.",
          cls: "attention",
          action: {
            text: "Check again",
            run: () => void recover(s.id, "recheck"),
          },
          secondary: {
            text: "I verified it",
            run: () => void recover(s.id, "verified"),
          },
        };
      case "failed":
        return {
          title: "Run failed",
          detail: "The transcript is preserved. Continue with a new message.",
          cls: "attention",
          action: { text: "Open in chat", run: () => openInChat(s.id) },
        };
      case "interrupted":
        return {
          title: "Interrupted safely",
          detail: "Continue from the last completed turn.",
          cls: "attention",
          action: { text: "Continue in chat", run: () => openInChat(s.id) },
        };
      case "archived":
        return {
          title: "Archived session",
          detail: "Kept for reference; open read-only.",
          cls: "ready",
          action: { text: "Open", run: () => openInChat(s.id) },
        };
      default:
        return {
          title: "Ready to continue",
          detail: "Pick up where you left off.",
          cls: "ready",
          action: { text: "Resume session", run: () => openInChat(s.id) },
        };
    }
  };

  return (
    <div className="app">
      <Sidebar />
      <main className="main">
        <header className="topbar">
          <div className="page-heading">
            <span className="page-eyebrow">History</span>
            <h2>Sessions</h2>
            <p className="page-sub small">
              Find past work, inspect what happened, and continue from the right
              state
            </p>
          </div>
          <button className="btn" onClick={() => void load()}>
            Refresh
          </button>
        </header>

        <div className="inbox page-content">
          {error && <div className="auth-error">{error}</div>}

          <label className="session-search-box">
            <span aria-hidden="true">⌕</span>
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search titles, messages, and tool actions…"
              aria-label="Search sessions"
              type="search"
            />
          </label>

          {!searching && (
            <div
              className="session-filters"
              role="tablist"
              aria-label="Session filters"
            >
              {FILTERS.map((f) => (
                <button
                  key={f.key}
                  className={
                    "filter-chip" + (filter === f.key ? " active" : "")
                  }
                  onClick={() => setFilter(f.key)}
                >
                  {f.label}
                </button>
              ))}
            </div>
          )}

          <div className="session-library">
            <section className="content-section session-results">
              <div className="section-head">
                <span>{searching ? "Search results" : "Sessions"}</span>
                <span className="count">{visible.length}</span>
              </div>
              {visible.length === 0 && (
                <div className="empty-state compact">
                  <strong>
                    {searching ? "No matching sessions" : "No sessions here"}
                  </strong>
                  <span>
                    {searching
                      ? "Try a shorter keyword or another term."
                      : "Start a chat, or clear the filter."}
                  </span>
                </div>
              )}
              {visible.map((s) => {
                const badge = channelBadge(s.channel);
                return (
                  <button
                    key={s.id}
                    className={
                      "session-item" + (selected === s.id ? " active" : "")
                    }
                    onClick={() => setSelected(s.id)}
                  >
                    <span className="session-item-top">
                      <span className={badge.cls}>{badge.text}</span>
                      <span className={STATE_PILL[s.resume_state]}>
                        {STATE_LABEL[s.resume_state]}
                      </span>
                      <time>
                        {s.last_activity_at
                          ? new Date(s.last_activity_at).toLocaleString()
                          : new Date(s.created_at).toLocaleDateString()}
                      </time>
                    </span>
                    <strong>{label(s)}</strong>
                    {s.match ? (
                      <span className="session-item-match">
                        <span className="match-kind">
                          {s.match.kind.replace("_", " ")}
                        </span>
                        {s.match.snippet}
                        {s.match.additional_matches > 0 && (
                          <span className="match-more">
                            {" "}
                            +{s.match.additional_matches} more
                          </span>
                        )}
                      </span>
                    ) : (
                      s.last_message_preview && (
                        <span className="session-item-preview">
                          {s.last_message_preview}
                        </span>
                      )
                    )}
                  </button>
                );
              })}
            </section>

            <section className="content-section session-detail">
              {!current && (
                <div className="empty-state">
                  <span className="empty-icon" aria-hidden="true">
                    ⌕
                  </span>
                  <strong>Select a session</strong>
                  <span>Its state, actions, and transcript appear here.</span>
                </div>
              )}
              {current &&
                (() => {
                  const b = banner(current);
                  return (
                    <>
                      <div className="detail-title">
                        <span className="page-eyebrow">
                          {current.channel.toUpperCase()} ·{" "}
                          {STATE_LABEL[current.resume_state]}
                        </span>
                        <h3>{label(current)}</h3>
                      </div>

                      <div className={`status-banner ${b.cls}`}>
                        <div>
                          <strong>{b.title}</strong>
                          <p>{b.detail}</p>
                        </div>
                        <div className="status-actions">
                          {b.secondary && (
                            <button
                              className="btn btn-quiet"
                              disabled={busy}
                              onClick={b.secondary.run}
                            >
                              {b.secondary.text}
                            </button>
                          )}
                          {b.action && (
                            <button
                              className="btn btn-primary"
                              disabled={busy}
                              onClick={b.action.run}
                            >
                              {b.action.text}
                            </button>
                          )}
                        </div>
                      </div>

                      <label className="rename-row">
                        <span>Title</span>
                        <span className="rename-controls">
                          <input
                            value={renaming}
                            onChange={(e) => setRenaming(e.target.value)}
                            placeholder="Name this session"
                            aria-label="Session title"
                          />
                          <button
                            className="btn btn-quiet"
                            disabled={
                              busy ||
                              !renaming.trim() ||
                              renaming === current.title
                            }
                            onClick={() => void rename()}
                          >
                            Save
                          </button>
                        </span>
                      </label>

                      <div className="section-head">
                        <span>Transcript</span>
                        <span className="count">{messages.length}</span>
                      </div>
                      <div className="session-transcript">
                        {messages.length === 0 && (
                          <div className="empty-state compact">
                            No messages yet.
                          </div>
                        )}
                        {messages.map((m) => (
                          <article
                            className={"msg" + (m.role === "user" ? " me" : "")}
                            key={m.id}
                          >
                            <div
                              className={
                                m.role === "user"
                                  ? "bubble-user"
                                  : "bubble-agent"
                              }
                            >
                              {m.parts
                                .map((p: MessagePart) => p.text)
                                .join(" ")}
                            </div>
                          </article>
                        ))}
                      </div>
                    </>
                  );
                })()}
            </section>
          </div>
        </div>
      </main>
    </div>
  );
}
