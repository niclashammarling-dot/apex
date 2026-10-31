import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App.jsx";

// Finding 3 (2026-10-31): the backend refuses a non-GET /api call without this header (backend/main.py
// require_client_header), so a page outside the dashboard cannot fire one. Every call site goes through here.
const CLIENT_HEADER = "X-APEX-Client";
const _fetch = window.fetch.bind(window);
window.fetch = (input, init = {}) => {
  const url = typeof input === "string" ? input : input.url;
  const method = (init.method || (typeof input === "string" ? "GET" : input.method) || "GET").toUpperCase();
  if (method !== "GET" && method !== "HEAD" && url.startsWith("/api")) {
    const headers = new Headers(init.headers || (typeof input === "string" ? undefined : input.headers));
    headers.set(CLIENT_HEADER, "dashboard");
    init = { ...init, headers };
  }
  return _fetch(input, init);
};

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
