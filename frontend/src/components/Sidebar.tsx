import { Link, useLocation } from "react-router-dom";

import { useAuth } from "../auth";

export default function Sidebar() {
  const { email, logout } = useAuth();
  const { pathname } = useLocation();
  const cls = (p: string) => "nav-item" + (pathname === p ? " active" : "");
  return (
    <aside className="sidebar">
      <div className="brand-row">
        <span className="logo">S</span>
        <span className="brand-name">Sherpa</span>
      </div>
      <div className="nav-label">Workspace</div>
      <Link className={cls("/")} to="/">
        ◌ Chat
      </Link>
      <Link className={cls("/inbox")} to="/inbox">
        ✉ Inbox
      </Link>
      <Link className={cls("/data")} to="/data">
        ▤ Activity
      </Link>
      <Link className={cls("/reminders")} to="/reminders">
        ◷ Schedules
      </Link>
      <Link className={cls("/preferences")} to="/preferences">
        ⚙ Settings
      </Link>
      <Link className={cls("/remember")} to="/remember">
        ◈ Memory
      </Link>
      <span
        className="nav-item muted"
        title="Connectors — deferred in v1 (needs Google OAuth setup); manage Gmail via chat for now"
      >
        ⌁ Connectors <span className="nav-soon">soon</span>
      </span>
      <div className="nav-spacer" />
      <div className="nav-user">
        <span className="avatar">{(email ?? "?").slice(0, 1).toUpperCase()}</span>
        <div>
          <strong>{email ?? "Owner"}</strong>
          <br />
          <button className="linklike" onClick={() => void logout()}>
            Sign out
          </button>
        </div>
      </div>
    </aside>
  );
}
