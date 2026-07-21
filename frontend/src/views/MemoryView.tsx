import { useEffect, useState } from "react";

import { api, type MemoryItem, type PassageItem } from "../api";
import { useAuth } from "../auth";
import Sidebar from "../components/Sidebar";

export default function MemoryView() {
  const { csrf } = useAuth();
  const [items, setItems] = useState<MemoryItem[]>([]);
  const [passages, setPassages] = useState<PassageItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [key, setKey] = useState("");
  const [value, setValue] = useState("");
  const [note, setNote] = useState("");

  const load = async () => {
    try {
      const [m, p] = await Promise.all([api.listMemory(), api.listPassages()]);
      setItems(m.items);
      setPassages(p.items);
    } catch {
      setError("Could not load memory. Is the backend running?");
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const save = async () => {
    if (!csrf || !key.trim() || !value.trim()) return;
    setBusy("new");
    setError(null);
    try {
      await api.setMemory(csrf, key.trim(), value.trim());
      setKey("");
      setValue("");
      await load();
    } catch {
      setError("Could not save (key must be lowercase letters/digits/._- , ≤64 chars).");
    } finally {
      setBusy(null);
    }
  };

  const remove = async (k: string) => {
    if (!csrf) return;
    setBusy(k);
    setError(null);
    try {
      await api.deleteMemory(csrf, k);
      await load();
    } catch {
      setError("Delete failed.");
    } finally {
      setBusy(null);
    }
  };

  const addNote = async () => {
    if (!csrf || !note.trim()) return;
    setBusy("note");
    setError(null);
    try {
      await api.addPassage(csrf, note.trim());
      setNote("");
      await load();
    } catch {
      setError("Could not save the note.");
    } finally {
      setBusy(null);
    }
  };

  const removePassage = async (id: string) => {
    if (!csrf) return;
    setBusy(id);
    setError(null);
    try {
      await api.deletePassage(csrf, id);
      await load();
    } catch {
      setError("Delete failed.");
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
            <h2>Memory</h2>
            <p className="page-sub small">
              Durable facts Sherpa remembers about you — injected into every chat.
            </p>
          </div>
        </header>

        <div className="inbox">
          {error && <div className="auth-error">{error}</div>}

          <section>
            <div className="section-head">New memory</div>
            <article className="cand-card">
              <div className="cand-main">
                <div className="cand-meta small">
                  <label>
                    Key&nbsp;
                    <input
                      value={key}
                      onChange={(e) => setKey(e.target.value)}
                      placeholder="e.g. timezone"
                      aria-label="Memory key"
                    />
                  </label>
                  <label>
                    &nbsp;Value&nbsp;
                    <input
                      value={value}
                      onChange={(e) => setValue(e.target.value)}
                      placeholder="e.g. Asia/Shanghai"
                      aria-label="Memory value"
                    />
                  </label>
                </div>
              </div>
              <div className="cand-actions">
                <button
                  className="btn btn-primary"
                  disabled={busy === "new" || !key.trim() || !value.trim()}
                  onClick={() => void save()}
                >
                  Save
                </button>
              </div>
            </article>
          </section>

          <section>
            <div className="section-head">
              Stored memories <span className="count">{items.length}</span>
            </div>
            {items.length === 0 && (
              <div className="empty small muted">
                Nothing stored yet. Add a fact above, or just tell Sherpa in chat ("remember
                that …").
              </div>
            )}
            {items.map((m) => (
              <article className="todo-row" key={m.key}>
                <span className="pill pill-idle">{m.key}</span>
                <span className="todo-title">{m.value}</span>
                <button
                  className="btn todo-action"
                  disabled={busy === m.key}
                  onClick={() => void remove(m.key)}
                >
                  Delete
                </button>
              </article>
            ))}
          </section>

          <section>
            <div className="section-head">
              Notes (semantic memory) <span className="count">{passages.length}</span>
            </div>
            <article className="cand-card">
              <div className="cand-main" style={{ flex: 1 }}>
                <textarea
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  placeholder="A longer note Sherpa can recall by meaning (e.g. project context, a decision)…"
                  rows={2}
                  aria-label="New note"
                  style={{ width: "100%" }}
                />
              </div>
              <div className="cand-actions">
                <button
                  className="btn btn-primary"
                  disabled={busy === "note" || !note.trim()}
                  onClick={() => void addNote()}
                >
                  Save note
                </button>
              </div>
            </article>
            {passages.length === 0 && (
              <div className="empty small muted">
                No notes yet. Add one above, or ask Sherpa to note something in chat.
              </div>
            )}
            {passages.map((p) => (
              <article className="todo-row" key={p.id}>
                <span className="todo-title">{p.text}</span>
                <button
                  className="btn todo-action"
                  disabled={busy === p.id}
                  onClick={() => void removePassage(p.id)}
                >
                  Delete
                </button>
              </article>
            ))}
          </section>
        </div>
      </main>
    </div>
  );
}
