import React, { useEffect, useState } from "react";

// Dashboard surface for runtime config writes (2026-11-07, Job 2), from /api/ops/config-changes: one row per key a
// Settings save, Promote, corrupt-file reset or default materialisation changed, with old and new values. Writes
// before 2026-11-07 were not recorded, so an empty table says so. Shows its as-of time; when the backend can't be
// reached the last good response stays on screen, marked stale.
const sthlm = ts => new Date(ts).toLocaleString("sv-SE", { timeZone: "Europe/Stockholm", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });

export default function ConfigChangesPanel() {
  const [data, setData]   = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => {
    const load = () => fetch("/api/ops/config-changes?limit=20")
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
      <div className="t-meta" style={{ marginBottom: 10 }}>AS OF {sthlm(data.as_of)} · LATEST {data.rows.length}</div>
      {data.rows.length === 0
        ? <div className="t-meta">no runtime config write recorded yet (recording began 2026-11-07)</div>
        : (
          <table className="t-tbl">
            <thead><tr><th>When</th><th>File</th><th>Source</th><th>Key</th><th>Old</th><th>New</th></tr></thead>
            <tbody>
              {data.rows.map((r, i) => (
                <tr key={i}>
                  <td>{sthlm(r.ts)}</td>
                  <td>{r.file}</td>
                  <td>{r.source}</td>
                  <td><strong>{r.key}</strong></td>
                  <td>{r.old ?? "—"}</td>
                  <td>{r.new ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
    </div>
  );
}
