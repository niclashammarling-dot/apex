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
        positions.map(p => {
          const pnlPct = p.unrealized_pct ?? 0;
          const sign   = pnlPct >= 0 ? "+" : "";
          const color  = pnlPct >= 0 ? "var(--green)" : "var(--red)";
          const value  = p.current_price != null ? p.shares * p.current_price : p.amount;
          return (
            <div key={p.id} style={{
              display: "flex", alignItems: "center", justifyContent: "space-between",
              padding: "10px 16px", borderTop: "1px solid var(--border)",
            }}>
              <div>
                <div style={{ fontFamily: "'Inconsolata', monospace", fontSize: 16, color: "var(--text-1)", fontWeight: 700 }}>
                  {p.ticker}
                </div>
                <div style={{ fontFamily: "'Inconsolata', monospace", fontSize: 12, color: "var(--text-3)", marginTop: 3 }}>
                  ${p.price.toFixed(2)} → {p.current_price != null ? `$${p.current_price.toFixed(2)}` : "—"}
                  <span style={{ marginLeft: 8 }}>{p.days_held}d</span>
                </div>
              </div>
              <div style={{ textAlign: "right" }}>
                <div style={{ fontFamily: "'Inconsolata', monospace", fontSize: 18, color, fontWeight: 700 }}>
                  {sign}{(pnlPct * 100).toFixed(2)}%
                </div>
                <div style={{ fontFamily: "'Inconsolata', monospace", fontSize: 12, color: "var(--text-3)", marginTop: 3 }}>
                  ${value.toFixed(2)}
                </div>
              </div>
            </div>
          );
        })
      )}
    </div>
  );
}
