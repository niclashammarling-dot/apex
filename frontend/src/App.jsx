import React, { useState, useEffect, Component } from "react";
import SectorGrid    from "./components/SectorGrid.jsx";
import WalletPanel   from "./components/WalletPanel.jsx";
import GateFeed      from "./components/GateFeed.jsx";
import EquityCurve   from "./components/EquityCurve.jsx";
import BacktestPanel from "./components/BacktestPanel.jsx";
import TradeLog      from "./components/TradeLog.jsx";

const CSS = `
  * { box-sizing: border-box; margin: 0; padding: 0; }

  :root {
    --green: #34c77a;
    --red:   #f06060;
    --bg:        #0e1118;
    --bg-card:   #161c2a;
    --bg-header: #1a2138;
    --border:    #2a3350;
    --text-1:    #edf0ff;
    --text-2:    #9aa5c4;
    --text-3:    #5c6b8a;
  }

  body {
    background: var(--bg);
    color: var(--text-1);
    font-family: 'DM Sans', sans-serif;
    font-size: 14px;
    min-height: 100vh;
    -webkit-font-smoothing: antialiased;
  }

  /* ── Header ─────────────────────────────────── */
  .header {
    background: var(--bg-header);
    border-bottom: 1px solid var(--border);
    padding: 0 28px;
    height: 56px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    position: sticky;
    top: 0;
    z-index: 10;
  }

  .header-left  { display: flex; align-items: center; gap: 20px; }
  .header-right { display: flex; align-items: center; gap: 20px; }

  .logo {
    font-family: 'DM Mono', monospace;
    font-size: 16px;
    font-weight: 600;
    letter-spacing: 0.18em;
    color: var(--text-1);
  }

  .market-badge {
    display: flex;
    align-items: center;
    gap: 7px;
    font-family: 'DM Mono', monospace;
    font-size: 12px;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--text-2);
  }

  .market-dot { width: 8px; height: 8px; border-radius: 50%; }
  .market-dot.open   { background: var(--green); box-shadow: 0 0 8px #34c77a66; }
  .market-dot.closed { background: var(--text-3); }

  .header-balance {
    font-family: 'DM Mono', monospace;
    font-size: 14px;
    color: var(--text-2);
  }

  .header-balance .pnl-pos { color: var(--green); }
  .header-balance .pnl-neg { color: var(--red); }

  .refresh-btn {
    background: transparent;
    border: 1px solid var(--border);
    border-radius: 5px;
    color: var(--text-2);
    font-family: 'DM Mono', monospace;
    font-size: 12px;
    letter-spacing: 0.06em;
    padding: 6px 14px;
    cursor: pointer;
    text-transform: uppercase;
    transition: all 0.15s;
  }
  .refresh-btn:hover { background: #232d44; color: var(--text-1); border-color: #3a4a68; }

  /* ── Layout ──────────────────────────────────── */
  .main {
    padding: 28px;
    max-width: 1440px;
    margin: 0 auto;
    display: grid;
    grid-template-columns: 1fr 380px;
    gap: 24px;
    align-items: start;
  }

  .main-left  { display: flex; flex-direction: column; gap: 24px; }
  .main-right { display: flex; flex-direction: column; gap: 24px; position: sticky; top: 76px; }

  /* ── Shared ──────────────────────────────────── */
  .section-label {
    font-family: 'DM Mono', monospace;
    font-size: 12px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--text-3);
    margin-bottom: 14px;
  }

  .muted { color: var(--text-3); font-size: 13px; }

  .error-banner {
    background: #1f0e0e;
    border: 1px solid #5a2020;
    border-radius: 5px;
    color: #f08080;
    font-family: 'DM Mono', monospace;
    font-size: 12px;
    padding: 12px 16px;
  }

  /* ── Card ────────────────────────────────────── */
  .card {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 8px;
    overflow: hidden;
  }

  .card-header {
    background: var(--bg-header);
    border-bottom: 1px solid var(--border);
    padding: 12px 16px;
    display: flex;
    align-items: center;
    justify-content: space-between;
  }

  .card-title {
    font-family: 'DM Mono', monospace;
    font-size: 12px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--text-2);
    font-weight: 500;
  }

  .card-meta {
    font-family: 'DM Mono', monospace;
    font-size: 12px;
    color: var(--text-3);
  }

  .card-body { padding: 16px; }

  /* ── Wallet ──────────────────────────────────── */
  .wallet-balance-row {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    margin-bottom: 8px;
  }

  .wallet-balance-main {
    font-family: 'DM Mono', monospace;
    font-size: 26px;
    font-weight: 500;
    color: var(--text-1);
  }

  .wallet-pnl { font-family: 'DM Mono', monospace; font-size: 15px; font-weight: 500; }
  .wallet-pnl-pct { font-size: 13px; margin-left: 5px; opacity: 0.85; }

  .wallet-sub-row {
    display: flex;
    align-items: center;
    gap: 8px;
    font-family: 'DM Mono', monospace;
    font-size: 13px;
    color: var(--text-3);
    margin-bottom: 4px;
  }
  .wallet-sub-row span:not(.muted) { color: var(--text-2); }

  .wallet-table {
    width: 100%;
    border-collapse: collapse;
    font-family: 'DM Mono', monospace;
    font-size: 13px;
    margin-top: 10px;
  }
  .wallet-table th {
    color: var(--text-3);
    text-align: left;
    padding: 6px 8px;
    font-weight: 400;
    letter-spacing: 0.04em;
    border-bottom: 1px solid var(--border);
    font-size: 12px;
    text-transform: uppercase;
  }
  .wallet-table td {
    padding: 7px 8px;
    border-bottom: 1px solid #1e2538;
    color: var(--text-2);
  }
  .wallet-table td strong { color: var(--text-1); }

  .outcome-badge {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 11px;
    letter-spacing: 0.04em;
    font-weight: 500;
  }
  .outcome-win     { background: #0e2a1a; color: var(--green); }
  .outcome-loss    { background: #2a0e0e; color: var(--red); }
  .outcome-expired { background: #1e2030; color: var(--text-3); }
  .outcome-open    { background: #0e1e38; color: #6aa0f0; }

  /* ── Gate feed ───────────────────────────────── */
  .feed-table {
    width: 100%;
    border-collapse: collapse;
    font-family: 'DM Mono', monospace;
    font-size: 13px;
  }
  .feed-table th {
    color: var(--text-3);
    text-align: left;
    padding: 10px 16px;
    font-weight: 400;
    letter-spacing: 0.04em;
    border-bottom: 1px solid var(--border);
    font-size: 12px;
    text-transform: uppercase;
  }
  .feed-table td {
    padding: 9px 16px;
    border-bottom: 1px solid #1e2538;
    color: var(--text-2);
  }
  .feed-table td strong { color: var(--text-1); font-weight: 500; }
  .feed-table tr:last-child td { border-bottom: none; }
  .feed-table tr:hover td { background: #1a2035; }

  .gate-badge {
    display: inline-block;
    padding: 3px 9px;
    border-radius: 4px;
    font-size: 11px;
    letter-spacing: 0.04em;
    font-weight: 600;
  }
  .gate-executed { background: #0e2a1a; color: var(--green); }
  .gate-rejected { background: #2a1e00; color: #d4a020; }
  .gate-fail     { background: #1e2030; color: var(--text-3); }
`;

function isMarketOpen() {
  const now = new Date();
  const ny  = new Date(now.toLocaleString("en-US", { timeZone: "America/New_York" }));
  const day = ny.getDay();
  if (day === 0 || day === 6) return false;
  const mins = ny.getHours() * 60 + ny.getMinutes();
  return mins >= 9 * 60 + 30 && mins < 16 * 60;
}

// ── Error boundary ────────────────────────────────────────────────────────────
// Catches render errors in child components so one broken section doesn't
// unmount the entire dashboard.

class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, message: "" };
  }
  static getDerivedStateFromError(err) {
    return { hasError: true, message: err?.message || "Unknown error" };
  }
  componentDidCatch(err, info) {
    console.error("ErrorBoundary caught:", err, info);
  }
  render() {
    if (this.state.hasError) {
      return (
        <div className="card">
          <div className="card-header"><div className="card-title">{this.props.label || "Section"}</div></div>
          <div className="card-body error-banner">
            Render error: {this.state.message}
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

// ── Fetch helper with exponential-backoff retry ───────────────────────────────

async function fetchWithRetry(url, maxAttempts = 3) {
  let delay = 1000;
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    try {
      const res = await fetch(url);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return await res.json();
    } catch (err) {
      if (attempt === maxAttempts) throw err;
      await new Promise(r => setTimeout(r, delay));
      delay *= 2;
    }
  }
}

// ── App ───────────────────────────────────────────────────────────────────────

export default function App() {
  const [sectors,       setSectors]       = useState([]);
  const [wallet,        setWallet]        = useState(null);
  const [gateHist,      setGateHist]      = useState([]);
  const [equity,        setEquity]        = useState([]);
  const [sectorError,   setSectorError]   = useState(null);
  const [walletError,   setWalletError]   = useState(null);
  const [gateError,     setGateError]     = useState(null);
  const [equityError,   setEquityError]   = useState(null);
  const [lastFetch,     setLastFetch]     = useState(null);
  const [backtestResult, setBacktestResult] = useState(null);

  // Each section loads independently so a slow or failing endpoint
  // doesn't block the rest of the dashboard from rendering.
  function fetchData() {
    fetchWithRetry("/api/sectors")
      .then(data => { setSectors(data || []); setSectorError(null); })
      .catch(() => setSectorError("Failed to load sector signals"));

    fetchWithRetry("/api/wallet")
      .then(data => { setWallet(data || null); setWalletError(null); })
      .catch(() => setWalletError("Failed to load wallet"));

    fetchWithRetry("/api/gate/history")
      .then(data => { setGateHist(data || []); setGateError(null); })
      .catch(() => setGateError("Failed to load gate history"));

    fetchWithRetry("/api/wallet/equity")
      .then(data => { setEquity(data || []); setEquityError(null); })
      .catch(() => setEquityError("Failed to load equity curve"));

    setLastFetch(new Date());
  }

  useEffect(() => {
    fetchData();
    const iv = setInterval(fetchData, 30_000);
    return () => clearInterval(iv);
  }, []);

  const marketOpen = isMarketOpen();
  const updatedStr = lastFetch
    ? lastFetch.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", second: "2-digit" })
    : "—";

  const pnl    = wallet?.total_pnl ?? 0;
  const sign   = pnl >= 0 ? "+" : "";
  const pnlCls = pnl >= 0 ? "pnl-pos" : "pnl-neg";

  return (
    <>
      <style>{CSS}</style>

      <header className="header">
        <div className="header-left">
          <div className="logo">APEX</div>
          <div className="market-badge">
            <div className={`market-dot ${marketOpen ? "open" : "closed"}`} />
            <span style={{ color: marketOpen ? "#2a9d5c" : "#555" }}>
              {marketOpen ? "Market Open" : "Market Closed"}
            </span>
          </div>
        </div>
        <div className="header-right">
          {wallet && (
            <div className="header-balance">
              ${wallet.balance.toLocaleString("en-US", { minimumFractionDigits: 2 })}
              {" "}<span className={pnlCls}>{sign}${pnl.toFixed(2)}</span>
            </div>
          )}
          <div style={{ fontFamily: "'DM Mono', monospace", fontSize: 10, color: "#3a4060" }}>
            {updatedStr}
          </div>
          <button className="refresh-btn" onClick={fetchData}>Refresh</button>
        </div>
      </header>

      <main className="main">
        <div className="main-left">
          <div>
            <div className="section-label">Sector Signals</div>
            {sectorError
              ? <div className="error-banner">{sectorError}</div>
              : (
                <ErrorBoundary label="Sector Signals">
                  <SectorGrid sectors={sectors} />
                </ErrorBoundary>
              )
            }
          </div>

          <ErrorBoundary label="Gate Activity">
            {gateError
              ? <div className="error-banner">{gateError}</div>
              : <GateFeed history={gateHist} />
            }
          </ErrorBoundary>
        </div>

        <div className="main-right">
          <ErrorBoundary label="Wallet">
            {walletError
              ? <div className="error-banner">{walletError}</div>
              : <WalletPanel wallet={wallet} />
            }
          </ErrorBoundary>

          <ErrorBoundary label="Equity Curve">
            {equityError
              ? <div className="error-banner">{equityError}</div>
              : <EquityCurve equity={equity} />
            }
          </ErrorBoundary>

          <ErrorBoundary label="Backtest">
            <BacktestPanel onResult={setBacktestResult} />
          </ErrorBoundary>
        </div>
      </main>

      {backtestResult && (
        <div style={{ padding: "0 28px 28px" }}>
          <TradeLog result={backtestResult} />
        </div>
      )}
    </>
  );
}
