import React, { useEffect, useState } from "react";

// Live config state (M12, R090 a, built 2026-11-21), from /api/ops/live-config. data/live_config.json is the only
// source of the live levels: when it is missing or unreadable, live entries halt with the cause on each gate_cycles
// row, exits keep running on the broker legs and stored levels, and this card says so in red. Shows its as-of time;
// when the backend can't be reached the last good response stays on screen, marked stale.
const sthlm = ts => new Date(ts).toLocaleString("sv-SE", { timeZone: "Europe/Stockholm", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });

export default function LiveConfigPanel() {
  const [data, setData]   = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => {
    const load = () => fetch("/api/ops/live-config")
      .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then(d => { setData(d); setError(null); })
      .catch(e => setError(e.message || "unreachable"));
    load();
    const iv = setInterval(load, 60_000);
    return () => clearInterval(iv);
  }, []);

  const banner = error && (
    <div className="t-meta" style={{ color: "var(--t-red)", marginBottom: 8 }}>
      BACKEND OFFLINE ({error}){data ? ` — showing data as of ${sthlm(data.as_of)}` : " — no data loaded yet"}
    </div>
  );
  if (!data) return banner || <div className="t-meta">loading…</div>;
  return (
    <div>
      {banner}
      <div className="t-meta" style={{ marginBottom: 8 }}>AS OF {sthlm(data.as_of)} · {data.path}</div>
      {data.available
        ? <div><span className="t-pos">AVAILABLE</span> <span className="t-meta">· {data.keys} keys · live entries use the file's levels</span></div>
        : <div style={{ color: "var(--t-red)" }}>
            <strong>UNAVAILABLE — LIVE ENTRIES HALTED</strong>
            <div className="t-meta" style={{ marginTop: 4 }}>{data.cause}. Exits keep running on the broker legs and stored levels; the time stop and the profit-lock ratchet are off. Restore the file (data/backups or by hand); nothing was written over it.</div>
          </div>}
    </div>
  );
}
