import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The scheduled market-window task owns :8000 on weekdays (scripts/market_window.sh,
// no --reload). Development runs its own backend on another port and points the
// proxy at it: APEX_API_PORT=8001 npm run dev  (backend: uvicorn ... --port 8001 --reload).
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
