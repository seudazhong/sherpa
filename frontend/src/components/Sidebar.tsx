import { useEffect, useState, type ReactNode } from "react";
import { Link, NavLink, useLocation } from "react-router-dom";

import { api } from "../api";
import { useAuth } from "../auth";

type IconName =
  | "activity"
  | "approvals"
  | "chat"
  | "connectors"
  | "files"
  | "inbox"
  | "knowledge"
  | "memory"
  | "messaging"
  | "projects"
  | "schedules"
  | "sessions"
  | "settings";

interface NavItem {
  label: string;
  path: string;
  icon: IconName;
}

const navGroups: Array<{ label: string; items: NavItem[] }> = [
  {
    label: "Workspace",
    items: [
      { label: "Chat", path: "/", icon: "chat" },
      { label: "Sessions", path: "/history", icon: "sessions" },
      { label: "Inbox", path: "/inbox", icon: "inbox" },
      { label: "Approvals", path: "/approvals", icon: "approvals" },
      { label: "Activity", path: "/data", icon: "activity" },
    ],
  },
  {
    label: "Organize",
    items: [
      { label: "Schedules", path: "/reminders", icon: "schedules" },
      { label: "Memory", path: "/remember", icon: "memory" },
      { label: "Knowledge", path: "/library", icon: "knowledge" },
      { label: "Projects", path: "/work/projects", icon: "projects" },
      { label: "Drive", path: "/workspace", icon: "files" },
    ],
  },
  {
    label: "Channels",
    items: [
      { label: "Messaging", path: "/messaging", icon: "messaging" },
      { label: "Connectors", path: "/integrations", icon: "connectors" },
    ],
  },
];

const settingsItem: NavItem = {
  label: "Settings",
  path: "/preferences",
  icon: "settings",
};

function NavIcon({ name }: { name: IconName }) {
  const paths: Record<IconName, ReactNode> = {
    chat: (
      <>
        <path d="M4 5.5A2.5 2.5 0 0 1 6.5 3h11A2.5 2.5 0 0 1 20 5.5v7a2.5 2.5 0 0 1-2.5 2.5H10l-4.5 4v-4.2A2.5 2.5 0 0 1 4 12.5z" />
        <path d="M8 8h8M8 11h5" />
      </>
    ),
    inbox: (
      <>
        <path d="M4 5h16v13H4z" />
        <path d="M4 13h4l1.5 2h5L16 13h4M7 9h10" />
      </>
    ),
    sessions: (
      <>
        <circle cx="11" cy="11" r="6.5" />
        <path d="m21 21-4.5-4.5M11 8v3l2 1.5" />
      </>
    ),
    activity: (
      <>
        <path d="M5 4h14v16H5z" />
        <path d="M8 8h8M8 12h8M8 16h5" />
      </>
    ),
    approvals: (
      <>
        <path d="M12 3 5 6v5c0 4 3 7 7 8 4-1 7-4 7-8V6z" />
        <path d="m9 12 2 2 4-4" />
      </>
    ),
    schedules: (
      <>
        <circle cx="12" cy="12" r="8.5" />
        <path d="M12 7v5l3 2M8 2.8V5M16 2.8V5" />
      </>
    ),
    memory: (
      <>
        <path d="M8 5.5A3.5 3.5 0 0 1 14.4 3.6 3.5 3.5 0 0 1 18 9.5a3.5 3.5 0 0 1-1 6.7A3.5 3.5 0 0 1 10.5 19 3.5 3.5 0 0 1 6 14.2 3.5 3.5 0 0 1 8 5.5Z" />
        <path d="M10 7.5v9M14 7.5v9M8 11h8" />
      </>
    ),
    files: (
      <>
        <path d="M5 3.5h8l4 4V20H5z" />
        <path d="M13 3.5V8h4M8 12h6M8 15h6" />
      </>
    ),
    projects: (
      <>
        <path d="M4 6.5A1.5 1.5 0 0 1 5.5 5H10l2 2.2h6.5A1.5 1.5 0 0 1 20 8.7v8.8A1.5 1.5 0 0 1 18.5 19h-13A1.5 1.5 0 0 1 4 17.5z" />
        <path d="M8 12.5h8M8 15h5" />
      </>
    ),
    knowledge: (
      <>
        <path d="M5 4.5h6a2 2 0 0 1 2 2V20a2.2 2.2 0 0 0-2-1.2H5z" />
        <path d="M19 4.5h-6a2 2 0 0 0-2 2V20a2.2 2.2 0 0 1 2-1.2h6z" />
      </>
    ),
    messaging: (
      <>
        <path d="M4 4.5h16v11H9l-5 4z" />
        <path d="M8 8.5h8M8 11.5h5" />
      </>
    ),
    connectors: (
      <>
        <path d="M8.5 8.5 6 6a3 3 0 1 0-4.2 4.2l3 3A3 3 0 0 0 9 13" />
        <path d="m15.5 15.5 2.5 2.5a3 3 0 1 0 4.2-4.2l-3-3A3 3 0 0 0 15 11" />
        <path d="m8 16 8-8" />
      </>
    ),
    settings: (
      <>
        <circle cx="12" cy="12" r="3" />
        <path d="M19 13.5v-3l-2-.7a7 7 0 0 0-.7-1.7l.9-1.9-2.1-2.1-1.9.9a7 7 0 0 0-1.7-.7L10.5 2h-3l-.7 2.1a7 7 0 0 0-1.7.7l-1.9-.9-2.1 2.1.9 1.9a7 7 0 0 0-.7 1.7L0 10.5v3l2.1.7a7 7 0 0 0 .7 1.7l-.9 1.9L4 19.9l1.9-.9a7 7 0 0 0 1.7.7l.7 2.1h3l.7-2.1a7 7 0 0 0 1.7-.7l1.9.9 2.1-2.1-.9-1.9a7 7 0 0 0 .7-1.7z" />
      </>
    ),
  };

  return (
    <svg className="nav-icon" viewBox="0 0 24 24" aria-hidden="true">
      {paths[name]}
    </svg>
  );
}

export default function Sidebar() {
  const { email, logout } = useAuth();
  const { pathname } = useLocation();
  const [open, setOpen] = useState(false);
  const [approvalCount, setApprovalCount] = useState(0);
  const allItems = [...navGroups.flatMap((group) => group.items), settingsItem];
  const currentPage =
    allItems.find((item) => item.path === pathname)?.label ?? "Sherpa";

  useEffect(() => {
    setOpen(false);
  }, [pathname]);

  // Keep the Approvals badge fresh (pending external actions incl. from scheduled tasks).
  useEffect(() => {
    let active = true;
    const refresh = () =>
      api
        .listPermissions()
        .then((p) => active && setApprovalCount(p.items.length))
        .catch(() => {});
    void refresh();
    const t = setInterval(refresh, 20000);
    return () => {
      active = false;
      clearInterval(t);
    };
  }, [pathname]);

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open]);

  const navItem = (item: NavItem) => (
    <NavLink
      className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}
      to={item.path}
      key={item.path}
      end={item.path === "/"}
    >
      <NavIcon name={item.icon} />
      <span>{item.label}</span>
      {item.path === "/approvals" && approvalCount > 0 && (
        <span className="nav-badge">{approvalCount}</span>
      )}
    </NavLink>
  );

  return (
    <>
      <header className="mobile-bar">
        <button
          className="icon-button mobile-menu-button"
          type="button"
          aria-label="Open navigation"
          aria-expanded={open}
          aria-controls="app-navigation"
          onClick={() => setOpen(true)}
        >
          <span />
          <span />
          <span />
        </button>
        <Link className="mobile-brand" to="/">
          <span className="logo">S</span>
          <span>{currentPage}</span>
        </Link>
        <span className="mobile-avatar">
          {(email ?? "?").slice(0, 1).toUpperCase()}
        </span>
      </header>

      {open && (
        <button
          className="sidebar-backdrop"
          type="button"
          aria-label="Close navigation"
          onClick={() => setOpen(false)}
        />
      )}

      <aside id="app-navigation" className={`sidebar${open ? " open" : ""}`}>
        <div className="brand-row">
          <Link className="brand-link" to="/">
            <span className="logo">S</span>
            <span>
              <span className="brand-name">Sherpa</span>
              <span className="brand-caption">Personal agent</span>
            </span>
          </Link>
          <button
            className="icon-button sidebar-close"
            type="button"
            aria-label="Close navigation"
            onClick={() => setOpen(false)}
          >
            ×
          </button>
        </div>

        <div className="workspace-card">
          <span className="workspace-dot" />
          <span>
            <strong>Personal workspace</strong>
            <small>Private · self-hosted</small>
          </span>
        </div>

        <nav className="nav-list" aria-label="Main navigation">
          {navGroups.map((group) => (
            <div className="nav-group" key={group.label}>
              <div className="nav-label">{group.label}</div>
              {group.items.map(navItem)}
            </div>
          ))}
        </nav>

        <div className="nav-spacer" />
        <div className="nav-settings">{navItem(settingsItem)}</div>
        <div className="nav-user">
          <span className="avatar">
            {(email ?? "?").slice(0, 1).toUpperCase()}
          </span>
          <div className="nav-user-copy">
            <strong>{email ?? "Owner"}</strong>
            <span>Workspace owner</span>
          </div>
          <button
            className="icon-button signout-button"
            onClick={() => void logout()}
            title="Sign out"
          >
            ↗
          </button>
        </div>
      </aside>
    </>
  );
}
