export default function LivePositions({ positions }) {
  return (
    <div className="card">
      <div className="card-header">
        <div className="card-title">Open Positions</div>
        <div className="card-meta">{positions.length} open</div>
      </div>
      {positions.length === 0 ? (
        <div className="card-body"><p className="muted">No open positions.</p></div>
      ) : (
        <div style={{ overflowX: "auto" }}>
        <table className="wallet-table" style={{ minWidth: 480 }}>
          <thead>
            <tr>
              <th>Ticker</th>
              <th>Entry</th>
              <th>Current</th>
              <th>Value</th>
              <th>P&amp;L</th>
              <th>Days</th>
            </tr>
          </thead>
          <tbody>
            {positions.map(p => {
              const pnlPct = p.unrealized_pct ?? 0;
              const sign   = pnlPct >= 0 ? "+" : "";
              const color  = pnlPct >= 0 ? "var(--green)" : "var(--red)";
              return (
                <tr key={p.ticker}>
                  <td><strong>{p.ticker}</strong></td>
                  <td>${p.avg_entry_price.toFixed(2)}</td>
                  <td>{p.current_price ? `$${p.current_price.toFixed(2)}` : "—"}</td>
                  <td>{p.market_value ? `$${p.market_value.toFixed(2)}` : "—"}</td>
                  <td style={{ color }}>{sign}{(pnlPct * 100).toFixed(2)}%</td>
                  <td>{p.days_held ?? "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        </div>
      )}
    </div>
  );
}
