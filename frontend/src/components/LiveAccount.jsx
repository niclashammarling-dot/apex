export default function LiveAccount({ account, status }) {
  if (!status?.enabled) {
    return (
      <div className="card">
        <div className="card-header"><div className="card-title">Live Account</div></div>
        <div className="card-body">
          <p className="muted" style={{ textAlign: "center", padding: "12px 0" }}>
            Live trading is disabled.<br />
            Set <code style={{ color: "#6aa0f0" }}>LIVE_ENABLED=true</code> in .env to activate.
          </p>
        </div>
      </div>
    );
  }

  if (!status?.connected) {
    return (
      <div className="card">
        <div className="card-header"><div className="card-title">Live Account</div></div>
        <div className="card-body error-banner">
          Alpaca unreachable: {status?.message || "unknown error"}
        </div>
      </div>
    );
  }

  if (!account) {
    return (
      <div className="card">
        <div className="card-header"><div className="card-title">Live Account</div></div>
        <div className="card-body"><p className="muted">Loading…</p></div>
      </div>
    );
  }

  const pnlSign = account.day_pnl >= 0 ? "+" : "";
  const pnlCls  = account.day_pnl >= 0 ? "pnl-pos" : "pnl-neg";

  return (
    <div className="card">
      <div className="card-header">
        <div className="card-title">Live Account</div>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span style={{
            fontFamily: "'Inconsolata', monospace",
            fontSize: 11,
            fontWeight: 700,
            letterSpacing: "0.08em",
            padding: "2px 8px",
            borderRadius: 4,
            ...(status?.paper
              ? { background: "#202000", color: "#faff69", border: "1px solid #3a3a00" }
              : { background: "#2a0e0e", color: "var(--red)",   border: "1px solid #5a2020" }
            ),
          }}>
            {status?.paper ? "PAPER" : "LIVE"}
          </span>
          <div className="card-meta" style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <div style={{
              width: 7, height: 7, borderRadius: "50%",
              background: account.trading_blocked ? "var(--red)" : "var(--green)",
              boxShadow: account.trading_blocked ? undefined : "0 0 6px rgba(250,255,105,0.5)",
            }} />
            {account.trading_blocked ? "Blocked" : "Active"}
          </div>
        </div>
      </div>
      <div className="card-body">
        <div className="wallet-balance-row">
          <div className="wallet-balance-main">
            ${account.equity.toLocaleString("en-US", { minimumFractionDigits: 2 })}
          </div>
          <div className={`wallet-pnl ${pnlCls}`}>
            {pnlSign}${Math.abs(account.day_pnl).toFixed(2)}
            <span className="wallet-pnl-pct">
              ({pnlSign}{(account.day_pnl_pct * 100).toFixed(2)}%)
            </span>
          </div>
        </div>

        <div className="wallet-sub-row">
          <span className="muted">Cash</span>
          <span>${account.cash.toLocaleString("en-US", { minimumFractionDigits: 2 })}</span>
          <span className="muted" style={{ marginLeft: 12 }}>Buying Power</span>
          <span>${account.buying_power.toLocaleString("en-US", { minimumFractionDigits: 2 })}</span>
        </div>
      </div>
    </div>
  );
}
