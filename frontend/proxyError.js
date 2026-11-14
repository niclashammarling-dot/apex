// A proxy error says which backend is missing and how to reach one (2026-11-14: on a Saturday the dev
// server answered only "proxy error"; the serving backend runs in the weekday market windows only). The
// body has FastAPI's {"detail"} shape, so panels that show an error body (OverviewPyramid) name the cause.
// vite registers its own error listener after `configure`, so this one answers first and vite only logs.

export function proxyErrorDetail(port, code) {
  if (code !== "ECONNREFUSED") return `proxy to :${port} failed (${code || "unknown error"})`;
  if (port === "8000") {
    return "no backend on :8000 (the serving backend runs only in the weekday market windows). " +
      "To view outside them: `uvicorn backend.main:app --port 8001` and `APEX_API_PORT=8001 npm run dev` " +
      "(never on :8000: the next market window sees the port bound and exits)";
  }
  return `no backend on :${port}: start it with \`uvicorn backend.main:app --port ${port}\``;
}

export function proxyErrorHandler(port) {
  return (err, req, res) => {
    if (!res || !("req" in res) || res.headersSent || res.writableEnded) return;  // websocket or already answered
    res.writeHead(502, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ detail: proxyErrorDetail(port, err && err.code) }));
  };
}
