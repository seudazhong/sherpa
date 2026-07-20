import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// UI is a client of the backend core. The dev server proxies API + health to the
// web service. Inside docker the target is `http://web:8000` (set via
// VITE_API_PROXY); locally it defaults to http://localhost:8000.
declare const process: { env: Record<string, string | undefined> };
const target = process.env.VITE_API_PROXY ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    proxy: {
      "/auth": target,
      "/sessions": target,
      "/health": target,
      "/readyz": target,
    },
  },
});
