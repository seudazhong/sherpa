import { Navigate, Route, Routes } from "react-router-dom";

import { useAuth } from "./auth";
import ChatView from "./views/ChatView";
import InboxView from "./views/InboxView";
import LoginView from "./views/LoginView";

function Protected({ children }: { children: JSX.Element }) {
  const { ready, authed } = useAuth();
  if (!ready) return <div className="loading">Loading…</div>;
  return authed ? children : <Navigate to="/login" replace />;
}

export default function App() {
  const { ready, authed } = useAuth();
  return (
    <Routes>
      <Route path="/login" element={ready && authed ? <Navigate to="/" replace /> : <LoginView />} />
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
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
