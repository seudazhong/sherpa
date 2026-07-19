import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// UI is a client of the backend core; dev-proxy API + health to the web service.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8000",
      "/health": "http://localhost:8000",
    },
  },
});
