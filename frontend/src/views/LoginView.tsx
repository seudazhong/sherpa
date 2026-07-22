import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";

import { ApiError } from "../api";
import { useAuth } from "../auth";

export default function LoginView() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("owner@localhost");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email, password);
      navigate("/");
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 401
          ? "Invalid email or password."
          : "Sign-in failed. Is the backend running?",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth-page">
      <section className="auth-story">
        <div className="auth-brand">
          <span className="logo">S</span>
          <span className="brand-name">Sherpa</span>
        </div>
        <div className="auth-story-copy">
          <span className="page-eyebrow">Your private agent workspace</span>
          <h1>Move work forward without adding more noise.</h1>
          <p>
            Sherpa keeps conversations, tasks, memory, files, and connected
            channels in one calm, self-hosted space.
          </p>
        </div>
        <div className="auth-trust">
          <span>Private by default</span>
          <span>Approval before external actions</span>
          <span>Durable activity history</span>
        </div>
      </section>

      <section className="auth-card">
        <div>
          <span className="section-kicker">Welcome back</span>
          <h2 className="auth-title">Sign in to Sherpa</h2>
          <p className="auth-sub">Continue to your personal workspace.</p>
        </div>
        <form onSubmit={submit} className="auth-form">
          <label className="field">
            <span>Email</span>
            <input
              type="email"
              value={email}
              autoComplete="username"
              onChange={(e) => setEmail(e.target.value)}
              required
            />
          </label>
          <label className="field">
            <span>Password</span>
            <input
              type="password"
              value={password}
              autoComplete="current-password"
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </label>
          {error && <div className="auth-error">{error}</div>}
          <button
            className="btn btn-primary btn-block"
            type="submit"
            disabled={busy}
          >
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </form>
        <p className="auth-footnote">Your session stays on this device.</p>
      </section>
    </div>
  );
}
