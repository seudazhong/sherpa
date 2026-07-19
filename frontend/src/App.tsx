import { useEffect, useState } from "react";

// Walking-skeleton landing: proves the UI is a client of the backend.
// Real surfaces (chat/inbox/connectors/…) follow the design in docs/design-bright.
export default function App() {
  const [status, setStatus] = useState("…");

  useEffect(() => {
    fetch("/health")
      .then((r) => r.json())
      .then((d) => setStatus(d.status))
      .catch(() => setStatus("offline"));
  }, []);

  return (
    <main style={{ fontFamily: "system-ui", padding: "2rem", maxWidth: 640 }}>
      <h1>Sherpa</h1>
      <p>
        Backend health: <b>{status}</b>
      </p>
      <p style={{ color: "#5A7581" }}>
        The UI is a client of the core event stream. See design mockups in
        <code> docs/design-bright/</code> and the build plan in
        <code> docs/IMPLEMENTATION.md</code>.
      </p>
    </main>
  );
}
