import React, { useEffect, useState } from "react";

// Dashboard surface for the gate's session bound (Finding 1, 2026-10-10; CHECK 87),
// from /api/ops/freshness. Per session: scheduler cycles, cycles that refused
// candidates as stale, cycles where every would-be candidate was stale, and gate
// rows written before the open (must be 0), and — observed, not enforced until the
// 2026-10-17 review — in-session rows on a non-session bar (bar_date). Shows its as-of time, and says
// so when the backend can't be reached instead of rendering an empty card: the
// last good response stays on screen, marked stale.
const sthlm = ts => new Date(ts).toLocaleString("sv-SE", { timeZone: "Europe/Stockholm", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });

export default function SignalFreshnessPanel() {
  const [data, setData]   = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => {
    const load = () => fetch("/api/ops/freshness?days=10")
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

  const violations = data.sessions.reduce((n, s) => n + s.live.violations + s.demo.violations, 0);
  const mism = data.sessions.reduce((n, s) => n + s.live.bar_mismatch + s.demo.bar_mismatch, 0);
  return (
    <div>
      {banner}
      <div className="t-meta" style={{ marginBottom: 10 }}>
        AS OF {sthlm(data.as_of)}
        {" · "}BYPASSED ROWS{" "}
        <strong style={{ color: violations ? "var(--t-red)" : "var(--t-text-1)" }}>{violations}</strong>
        {" · "}NON-SESSION BAR (OBSERVED) <strong>{mism}</strong>
      </div>
      {data.sessions.length === 0 ? <div className="t-meta">no gate cycles in 10 days</div> : (
        <table className="t-tbl">
          <thead><tr><th>Session</th><th>Cycles</th><th>Stale refusals</th><th>No fresh</th><th>Live rows</th><th>Demo rows</th><th>Non-session bar</th></tr></thead>
          <tbody>
            {data.sessions.map(s => (
              <tr key={s.date}>
                <td><strong>{s.date.slice(5)}</strong></td>
                <td>{s.cycles}</td>
                <td>{s.stale_cycles ? `${s.stale_cycles} cyc · ${s.stale_ticker_cycles}` : "—"}</td>
                <td>{s.no_fresh || "—"}</td>
                {["live", "demo"].map(b => (
                  <td key={b} style={{ color: s[b].violations ? "var(--t-red)" : undefined }}>
                    {s[b].checked}{s[b].violations ? ` · ${s[b].violations} bypassed` : ""}
                    {s[b].unrecorded ? <span className="t-meta"> ({s[b].unrecorded} pre-bound)</span> : null}
                  </td>
                ))}
                <td>{["live", "demo"].map(b => s[b].bar_mismatch
                  ? <span key={b} style={{ marginRight: 8 }}>{b} {s[b].bar_mismatch} ({s[b].bar_mismatch_entered} entered: {
                      Object.entries(s[b].by_sector).map(([k, v]) => `${k} ${v.rows}`).join(", ")})</span>
                  : null)}{!s.live.bar_mismatch && !s.demo.bar_mismatch ? "—" : null}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
