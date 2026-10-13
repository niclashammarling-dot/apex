import React, { useEffect, useState } from "react";

// Dashboard surface for live entries recorded at the fill (2026-10-13; CHECK 89), from
// /api/ops/entry-fills. Since the paper reset: entries at the fill vs still on the quote
// (P&L booked on the signal price), mean |slip| of fill vs quote, the largest, and the
// newest rows. Shows its as-of time, and says so when the backend can't be reached
// instead of rendering an empty card: the last good response stays on screen, marked stale.
const sthlm = ts => new Date(ts).toLocaleString("sv-SE", { timeZone: "Europe/Stockholm", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
const pct = x => x == null ? "—" : `${x >= 0 ? "+" : ""}${(x * 100).toFixed(2)}%`;

export default function EntryFillsPanel() {
  const [data, setData]   = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => {
    const load = () => fetch("/api/ops/entry-fills?limit=15")
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
      <div className="t-meta" style={{ marginBottom: 10 }}>
        AS OF {sthlm(data.as_of)} · SINCE {data.since}
        {" · "}AT FILL <strong>{data.at_fill}/{data.entries}</strong>
        {" · "}ON QUOTE <strong style={{ color: data.on_quote ? "var(--t-red)" : "var(--t-text-1)" }}>{data.on_quote}</strong>
        {" · "}MEAN |SLIP| <strong>{data.mean_abs_slip == null ? "—" : `${(data.mean_abs_slip * 100).toFixed(2)}%`}</strong>
        {data.largest ? <> · LARGEST <strong>{pct(data.largest.slip)}</strong> {data.largest.ticker} {data.largest.timestamp.slice(5, 10)}</> : null}
        {" · "}OVER 1% <strong>{data.over_1pct}</strong>
        {Object.keys(data.excepted || {}).length ? ` · EXCEPTED ${Object.keys(data.excepted).length}` : null}
      </div>
      <table className="t-tbl">
        <thead><tr><th>Entry</th><th>Ticker</th><th>Signal</th><th>Fill</th><th>Slip</th><th>Outcome</th></tr></thead>
        <tbody>
          {data.rows.map(r => (
            <tr key={r.id}>
              <td>{r.timestamp.slice(5, 10)}</td>
              <td><strong>{r.ticker}</strong></td>
              <td>{r.signal_price?.toFixed(2)}</td>
              <td style={{ color: r.entry_filled_at || data.excepted?.[r.id] ? undefined : "var(--t-red)" }}>
                {r.entry_filled_at ? r.entry_price.toFixed(2) : (data.excepted?.[r.id] ? "excepted" : "not captured")}
              </td>
              <td style={{ color: r.slip != null && Math.abs(r.slip) > 0.01 ? "var(--t-amber)" : undefined }}>{pct(r.slip)}</td>
              <td>{r.outcome}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
