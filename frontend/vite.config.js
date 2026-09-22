import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The scheduled market-window task owns :8000 on weekdays (scripts/market_window.sh,
// no --reload). To only *view* the live system, run `npm run dev` alone: the default
// proxy target is the :8000 window. For backend development run a second backend and
// point the proxy at it: `uvicorn backend.main:app --port 8001 --reload` and
// `APEX_API_PORT=8001 npm run dev`. A dev backend never schedules or trades — only a
// process started with APEX_SERVE=1 (the launcher scripts) may take the scheduler.
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
