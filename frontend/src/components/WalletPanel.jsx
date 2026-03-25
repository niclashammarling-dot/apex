export default function WalletPanel({ wallet }) {
  if (!wallet) return (
    <div className="card">
      <div className="card-header"><div className="card-title">WALLET</div></div>
      <div className="card-body muted">Loading…</div>
    </div>
  );

  const pnlColor = wallet.total_pnl >= 0 ? "var(--green)" : "var(--red)";
  const pnlSign  = wallet.total_pnl >= 0 ? "+" : "";

  return (
    <div className="card">
      <div className="card-header">
        <div className="card-title">WALLET</div>
        <div className="card-meta">
          {wallet.total_trades} trades
          {wallet.win_rate != null && ` · ${(wallet.win_rate * 100).toFixed(0)}% win`}
        </div>
      </div>
      <div className="card-body">

        {/* Balance row */}
        <div className="wallet-balance-row">
          <div className="wallet-balance-main">
            ${wallet.balance.toLocaleString("en-US", { minimumFractionDigits: 2 })}
          </div>
          <div className="wallet-pnl" style={{ color: pnlColor }}>
            {pnlSign}{wallet.total_pnl.toFixed(2)}
            <span className="wallet-pnl-pct">
              ({pnlSign}{(wallet.total_pnl_pct * 100).toFixed(2)}%)
            </span>
          </div>
        </div>

        <div className="wallet-sub-row">
          <span className="muted">Cash</span>
          <span>${wallet.cash.toLocaleString("en-US", { minimumFractionDigits: 2 })}</span>
          <span className="muted" style={{ marginLeft: 16 }}>Invested</span>
          <span>${wallet.invested.toLocaleString("en-US", { minimumFractionDigits: 2 })}</span>
        </div>

        {/* Open positions */}
        {wallet.open_positions.length > 0 && (
          <>
            <div className="section-label" style={{ marginTop: 16 }}>OPEN POSITIONS</div>
            <table className="wallet-table">
              <thead>
                <tr>
                  <th>Ticker</th>
                  <th>Entry</th>
                  <th>Current</th>
                  <th>P&amp;L</th>
                  <th>Days</th>
                </tr>
              </thead>
              <tbody>
                {wallet.open_positions.map(p => {
                  const color = (p.unrealized_pnl || 0) >= 0 ? "var(--green)" : "var(--red)";
                  return (
                    <tr key={p.id}>
                      <td><strong>{p.ticker}</strong></td>
                      <td>${p.price.toFixed(2)}</td>
                      <td>{p.current_price != null ? `$${p.current_price.toFixed(2)}` : "—"}</td>
                      <td style={{ color }}>
                        {p.unrealized_pnl != null
                          ? `${p.unrealized_pnl >= 0 ? "+" : ""}${p.unrealized_pnl.toFixed(2)}`
                          : "—"}
                      </td>
                      <td>{p.days_held}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </>
        )}

        {/* Closed trades */}
        {wallet.closed_trades.length > 0 && (
          <>
            <div className="section-label" style={{ marginTop: 16 }}>RECENT CLOSED</div>
            <table className="wallet-table">
              <thead>
                <tr>
                  <th>Ticker</th>
                  <th>Outcome</th>
                  <th>Exit</th>
                  <th>P&amp;L</th>
                </tr>
              </thead>
              <tbody>
                {wallet.closed_trades.slice(0, 10).map(t => {
                  const color = (t.pnl || 0) >= 0 ? "var(--green)" : "var(--red)";
                  return (
                    <tr key={t.id}>
                      <td><strong>{t.ticker}</strong></td>
                      <td>
                        <span className={`outcome-badge outcome-${t.outcome.toLowerCase()}`}>
                          {t.outcome}
                        </span>
                      </td>
                      <td className="muted">{t.exit_reason || "—"}</td>
                      <td style={{ color }}>
                        {t.pnl != null
                          ? `${t.pnl >= 0 ? "+" : ""}${t.pnl.toFixed(2)}`
                          : "—"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </>
        )}

        {wallet.open_positions.length === 0 && wallet.closed_trades.length === 0 && (
          <div className="muted" style={{ marginTop: 12 }}>No trades yet.</div>
        )}
      </div>
    </div>
  );
}
