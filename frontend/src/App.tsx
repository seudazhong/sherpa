import { Navigate, Route, Routes } from "react-router-dom";

import { useAuth } from "./auth";
import ActivityView from "./views/ActivityView";
import ApprovalsView from "./views/ApprovalsView";
import ChatView from "./views/ChatView";
import ConnectorsView from "./views/ConnectorsView";
import InboxView from "./views/InboxView";
import LibraryView from "./views/LibraryView";
import LoginView from "./views/LoginView";
import MemoryView from "./views/MemoryView";
import MessagingView from "./views/MessagingView";
import ProjectsView from "./views/ProjectsView";
import SchedulesView from "./views/SchedulesView";
import SessionsView from "./views/SessionsView";
import SettingsView from "./views/SettingsView";
import WorkspaceView from "./views/WorkspaceView";

function Protected({ children }: { children: JSX.Element }) {
  const { ready, authed } = useAuth();
  if (!ready) return <div className="loading">Loading…</div>;
  return authed ? children : <Navigate to="/login" replace />;
}

export default function App() {
  const { ready, authed } = useAuth();
  return (
    <Routes>
      <Route
        path="/login"
        element={ready && authed ? <Navigate to="/" replace /> : <LoginView />}
      />
      <Route
        path="/"
        element={
          <Protected>
            <ChatView />
          </Protected>
        }
      />
      <Route
        path="/inbox"
        element={
          <Protected>
            <InboxView />
          </Protected>
        }
      />
      <Route
        path="/approvals"
        element={
          <Protected>
            <ApprovalsView />
          </Protected>
        }
      />
      <Route
        path="/history"
        element={
          <Protected>
            <SessionsView />
          </Protected>
        }
      />
      <Route
        path="/data"
        element={
          <Protected>
            <ActivityView />
          </Protected>
        }
      />
      <Route
        path="/reminders"
        element={
          <Protected>
            <SchedulesView />
          </Protected>
        }
      />
      <Route
        path="/preferences"
        element={
          <Protected>
            <SettingsView />
          </Protected>
        }
      />
      <Route
        path="/remember"
        element={
          <Protected>
            <MemoryView />
          </Protected>
        }
      />
      <Route
        path="/workspace"
        element={
          <Protected>
            <WorkspaceView />
          </Protected>
        }
      />
      <Route
        path="/work/projects"
        element={
          <Protected>
            <ProjectsView />
          </Protected>
        }
      />
      <Route
        path="/library"
        element={
          <Protected>
            <LibraryView />
          </Protected>
        }
      />
      <Route
        path="/messaging"
        element={
          <Protected>
            <MessagingView />
          </Protected>
        }
      />
      <Route
        path="/integrations"
        element={
          <Protected>
            <ConnectorsView />
          </Protected>
        }
      />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
