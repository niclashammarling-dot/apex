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

      </div>
    </div>
  );
}
