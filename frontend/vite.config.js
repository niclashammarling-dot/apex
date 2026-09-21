import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The scheduled market-window task owns :8000 on weekdays (scripts/market_window.sh,
// no --reload). Development runs its own backend on another port and points the
// proxy at it: APEX_API_PORT=8001 npm run dev  (backend: APEX_NO_SCHEDULER=1 uvicorn ... --port 8001 --reload,
// API only — a second scheduler would trade against the same DB). To only *view* the
// live system, run npm run dev alone: the default proxy target is the :8000 window.
const apiPort = process.env.APEX_API_PORT || "8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: `http://localhost:${apiPort}`,
        changeOrigin: true,
      },
      "/health": {
        target: `http://localhost:${apiPort}`,
        changeOrigin: true,
      },
    },
  },
});
