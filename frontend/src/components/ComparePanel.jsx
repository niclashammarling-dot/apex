const METRICS = [
  { key: "balance",          label: "Balance",         fmt: v => `$${v.toLocaleString("en-US", { minimumFractionDigits: 2 })}` },
  { key: "total_return_pct", label: "Total Return",    fmt: v => `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`, color: true },
  { key: "realized_pnl",     label: "Realized P&L",   fmt: v => `${v >= 0 ? "+" : ""}$${Math.abs(v).toFixed(2)}`, color: true },
  { key: "total_trades",     label: "Closed Trades",  fmt: v => v },
  { key: "open_positions",   label: "Open Positions", fmt: v => v },
  { key: "win_rate",         label: "Win Rate",        fmt: v => v != null ? `${v.toFixed(1)}%` : "—", nKey: "total_trades" },
  { key: "profit_factor",    label: "Profit Factor",  fmt: v => v != null ? v.toFixed(2) : "—",        nKey: "total_trades" },
  { key: "avg_win",          label: "Avg Win",         fmt: v => v != null ? `$${v.toFixed(2)}` : "—", color: true },
  { key: "avg_loss",         label: "Avg Loss",        fmt: v => v != null ? `-$${v.toFixed(2)}` : "—" },
];

function nColor(n) {
  if (n == null) return "var(--text-3)";
  if (n < 5)  return "var(--text-3)";   // too small — muted
  if (n < 20) return "#e8a020";          // low confidence — amber
  return "var(--text-3)";                // reasonable — normal muted
}

function Cell({ value, fmt, color, n }) {
  if (value == null) return <td style={{ color: "var(--text-3)", fontFamily: "'Inconsolata', monospace" }}>—</td>;
  const display = fmt(value);
  const valueColor = color ? (value >= 0 ? "var(--green)" : "var(--red)") : "var(--text-2)";
  return (
    <td style={{ fontFamily: "'Inconsolata', monospace" }}>
      <span style={{ color: valueColor }}>{display}</span>
      {n != null && (
        <span style={{ fontSize: 10, color: nColor(n), marginLeft: 5 }}>n={n}</span>
      )}
    </td>
  );
}

export default function ComparePanel({ data }) {
  if (!data) return null;
  const { demo, live } = data;

  return (
    <div className="card">
      <div className="card-header">
        <div className="card-title">Demo vs Live</div>
        {live && (
          <div className="card-meta" style={{ fontFamily: "'Inconsolata', monospace", fontSize: 11 }}>
            Day P&L:{" "}
            <span style={{ color: live.day_pnl >= 0 ? "var(--green)" : "var(--red)" }}>
              {live.day_pnl >= 0 ? "+" : ""}${Math.abs(live.day_pnl).toFixed(2)}
              {" "}({live.day_pnl >= 0 ? "+" : ""}{live.day_pnl_pct?.toFixed(2)}%)
            </span>
          </div>
        )}
      </div>
      <table className="wallet-table">
        <thead>
          <tr>
            <th>Metric</th>
            <th>Demo</th>
            <th>{live ? "Live" : <span style={{ color: "var(--text-3)" }}>Live (off)</span>}</th>
          </tr>
        </thead>
        <tbody>
          {METRICS.map(m => (
            <tr key={m.key}>
              <td style={{ color: "var(--text-3)", fontSize: 12 }}>{m.label}</td>
              <Cell value={demo?.[m.key] ?? null} fmt={m.fmt} color={m.color} n={m.nKey ? demo?.[m.nKey] : null} />
              <Cell value={live?.[m.key] ?? null} fmt={m.fmt} color={m.color} n={m.nKey ? live?.[m.nKey] : null} />
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
