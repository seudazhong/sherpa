import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// UI is a client of the backend core; dev-proxy the API + health to the web service.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/auth": "http://localhost:8000",
      "/sessions": "http://localhost:8000",
      "/health": "http://localhost:8000",
      "/readyz": "http://localhost:8000",
    },
  },
});
