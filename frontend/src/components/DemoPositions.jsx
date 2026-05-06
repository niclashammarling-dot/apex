export default function DemoPositions({ positions }) {
  return (
    <div className="card">
      <div className="card-header">
        <div className="card-title">Open Positions</div>
        <div className="card-meta">{positions.length} open</div>
      </div>
      {positions.length === 0 ? (
        <div className="card-body"><p className="muted">No open positions.</p></div>
      ) : (
        <table className="wallet-table">
          <thead>
            <tr>
              <th>Ticker</th>
              <th>Qty</th>
              <th>Entry</th>
              <th>Current</th>
              <th>Value</th>
              <th>P&amp;L</th>
              <th>Days</th>
            </tr>
          </thead>
          <tbody>
            {positions.map(p => {
              const pnl    = p.unrealized_pnl ?? 0;
              const pnlPct = p.unrealized_pct ?? 0;
              const sign   = pnl >= 0 ? "+" : "";
              const color  = pnl >= 0 ? "var(--green)" : "var(--red)";
              const value  = p.current_price != null
                ? p.shares * p.current_price
                : p.amount;
              return (
                <tr key={p.id}>
                  <td><strong>{p.ticker}</strong></td>
                  <td>{p.shares.toFixed(4)}</td>
                  <td>${p.price.toFixed(2)}</td>
                  <td>{p.current_price != null ? `$${p.current_price.toFixed(2)}` : "—"}</td>
                  <td>${value.toFixed(2)}</td>
                  <td style={{ color }}>
                    {sign}${Math.abs(pnl).toFixed(2)}
                    <span style={{ opacity: 0.7, fontSize: 11, marginLeft: 4 }}>
                      ({sign}{(pnlPct * 100).toFixed(2)}%)
                    </span>
                  </td>
                  <td>{p.days_held}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}
