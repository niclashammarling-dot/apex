import React, { useEffect, useState } from "react";

// Dashboard surface for the slot evidence the gate stores (2026-11-07, Job 1), from /api/ops/gate-capacity: per book
// the latest cycle's held count against the cap, the cycles at the cap in the last 7 days, and that cycle's serial
// order (regular before pre-rotation). Cycles before the build carry nothing, so a book with none recorded says
// "not recorded yet" instead of zero. Shows its as-of time; when the backend can't be reached the last good response
// stays on screen, marked stale.
const sthlm = ts => new Date(ts).toLocaleString("sv-SE", { timeZone: "Europe/Stockholm", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });

export default function GateCapacityPanel() {
  const [data, setData]   = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => {
    const load = () => fetch("/api/ops/gate-capacity")
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
      <div className="t-meta" style={{ marginBottom: 10 }}>AS OF {sthlm(data.as_of)} · LAST {data.days} DAYS</div>
      {Object.entries(data.books).map(([book, b]) => (
        <div key={book} style={{ marginBottom: 12 }}>
          <div className="t-meta">
            <strong>{book.toUpperCase()}</strong>
            {b.last_cycle
              ? <> · HELD <strong>{b.last_cycle.held_at_start}</strong> / CAP <strong>{b.last_cycle.l2_risk_max_positions_count}</strong> at {sthlm(b.last_cycle.started_at)}
                  {" · "}AT CAP <strong style={{ color: b.cycles_at_cap ? "var(--t-amber)" : undefined }}>{b.cycles_at_cap}</strong>/{b.cycles_recorded} cycles</>
              : " · not recorded yet (cycles before 2026-11-07 store no held count)"}
          </div>
          {b.last_order.length > 0 && (
            <table className="t-tbl">
              <thead><tr><th>Rank</th><th>Ticker</th><th>Pre-rot</th><th>Open</th><th>Overflow thr</th><th>Decision</th></tr></thead>
              <tbody>
                {b.last_order.map(r => (
                  <tr key={r.exec_rank}>
                    <td>{r.exec_rank}</td>
                    <td><strong>{r.ticker}</strong></td>
                    <td>{r.pre_rotation ? "yes" : ""}</td>
                    <td>{r.l2_risk_open_count}</td>
                    <td>{r.l2_risk_overflow_threshold == null ? "—" : r.l2_risk_overflow_threshold.toFixed(4)}</td>
                    <td>{r.gate_decision}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      ))}
    </div>
  );
}
