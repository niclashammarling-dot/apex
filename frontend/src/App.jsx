import React, { useState, useEffect, Component } from "react";
import SectorGrid    from "./components/SectorGrid.jsx";
import WalletPanel   from "./components/WalletPanel.jsx";
import GateFeed      from "./components/GateFeed.jsx";
import EquityCurve   from "./components/EquityCurve.jsx";
import BacktestPanel from "./components/BacktestPanel.jsx";
import TradeLog      from "./components/TradeLog.jsx";
import LiveAccount   from "./components/LiveAccount.jsx";
import LiveTradeLog  from "./components/LiveTradeLog.jsx";
import LiveGateFeed  from "./components/LiveGateFeed.jsx";
import ComparePanel  from "./components/ComparePanel.jsx";
import DemoTradeLog  from "./components/DemoTradeLog.jsx";

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

  /* ── Tab bar ─────────────────────────────────── */
  .tab-bar {
    display: flex;
    align-items: center;
    gap: 4px;
    background: var(--bg-header);
    border-bottom: 1px solid var(--border);
    padding: 0 28px;
  }

  .tab-btn {
    background: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    color: var(--text-3);
    cursor: pointer;
    font-family: 'DM Mono', monospace;
    font-size: 12px;
    letter-spacing: 0.08em;
    padding: 12px 16px;
    text-transform: uppercase;
    transition: color 0.15s, border-color 0.15s;
  }
  .tab-btn:hover { color: var(--text-2); }
  .tab-btn.active {
    color: var(--text-1);
    border-bottom-color: var(--green);
  }

  .live-dot {
    display: inline-block;
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--green);
    box-shadow: 0 0 6px #34c77a88;
    margin-right: 6px;
    vertical-align: middle;
  }

  /* ── Promote modal ───────────────────────────────── */
  .modal-overlay {
    position: fixed; inset: 0;
    background: rgba(0,0,0,0.65);
    z-index: 100;
    display: flex; align-items: center; justify-content: center;
  }
  .modal {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 24px;
    width: 480px;
    max-width: 95vw;
    max-height: 80vh;
    overflow-y: auto;
  }
  .modal-title {
    font-family: 'DM Mono', monospace;
    font-size: 14px;
    font-weight: 600;
    color: var(--text-1);
    margin-bottom: 6px;
  }
  .modal-sub {
    font-size: 13px;
    color: var(--text-3);
    margin-bottom: 18px;
    line-height: 1.5;
  }
  .modal-table {
    width: 100%;
    border-collapse: collapse;
    font-family: 'DM Mono', monospace;
    font-size: 12px;
    margin-bottom: 20px;
  }
  .modal-table th {
    color: var(--text-3); font-weight: 400;
    text-align: left; padding: 5px 8px;
    border-bottom: 1px solid var(--border);
    text-transform: uppercase; letter-spacing: 0.04em;
  }
  .modal-table td { padding: 7px 8px; color: var(--text-2); border-bottom: 1px solid #1e2538; }
  .modal-table td.changed { color: var(--green); font-weight: 600; }
  .modal-actions { display: flex; gap: 10px; justify-content: flex-end; }
  .btn-confirm {
    background: #0e2a1a; border: 1px solid var(--green);
    color: var(--green); border-radius: 5px;
    font-family: 'DM Mono', monospace; font-size: 12px;
    letter-spacing: 0.06em; padding: 8px 18px; cursor: pointer;
    text-transform: uppercase; transition: background 0.15s;
  }
  .btn-confirm:hover { background: #153d26; }
  .btn-cancel {
    background: transparent; border: 1px solid var(--border);
    color: var(--text-3); border-radius: 5px;
    font-family: 'DM Mono', monospace; font-size: 12px;
    letter-spacing: 0.06em; padding: 8px 18px; cursor: pointer;
    text-transform: uppercase; transition: all 0.15s;
  }
  .btn-cancel:hover { color: var(--text-1); border-color: var(--text-2); }
  .promote-btn {
    background: transparent; border: 1px solid #2a4020;
    color: #a0c040; border-radius: 5px;
    font-family: 'DM Mono', monospace; font-size: 11px;
    letter-spacing: 0.06em; padding: 5px 12px; cursor: pointer;
    text-transform: uppercase; transition: all 0.15s;
  }
  .promote-btn:hover { background: #1a2800; border-color: #4a6030; }

  .settings-btn {
    background: transparent; border: 1px solid var(--border);
    color: var(--text-3); border-radius: 5px;
    font-family: 'DM Mono', monospace; font-size: 12px;
    letter-spacing: 0.06em; padding: 6px 12px; cursor: pointer;
    transition: all 0.15s;
  }
  .settings-btn:hover { background: #232d44; color: var(--text-1); border-color: #3a4a68; }

  /* ── Settings modal ──────────────────────────────── */
  .settings-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 24px;
    margin-bottom: 20px;
  }
  .settings-col-title {
    font-family: 'DM Mono', monospace;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--text-3);
    margin-bottom: 12px;
  }
  .settings-field { margin-bottom: 12px; }
  .settings-label {
    font-family: 'DM Mono', monospace;
    font-size: 11px;
    color: var(--text-3);
    margin-bottom: 4px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }
  .settings-input {
    width: 100%;
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 4px;
    color: var(--text-1);
    font-family: 'DM Mono', monospace;
    font-size: 13px;
    padding: 6px 10px;
    outline: none;
    transition: border-color 0.15s;
  }
  .settings-input:focus { border-color: #3a5a8a; }
  .settings-save-btn {
    width: 100%;
    background: #0e1e38; border: 1px solid #2a4a6a;
    color: #6aa0f0; border-radius: 4px;
    font-family: 'DM Mono', monospace; font-size: 11px;
    letter-spacing: 0.06em; padding: 7px; cursor: pointer;
    text-transform: uppercase; transition: all 0.15s; margin-top: 4px;
  }
  .settings-save-btn:hover { background: #132a4a; border-color: #4a6a9a; }
  .settings-save-btn.saved { background: #0e2a1a; border-color: var(--green); color: var(--green); }

  .settings-tabs { display: flex; gap: 2px; margin-bottom: 20px; border-bottom: 1px solid var(--border); }
  .settings-tab {
    background: transparent; border: none; border-bottom: 2px solid transparent;
    color: var(--text-3); cursor: pointer;
    font-family: 'DM Mono', monospace; font-size: 11px;
    letter-spacing: 0.08em; padding: 8px 16px;
    text-transform: uppercase; transition: all 0.15s;
  }
  .settings-tab:hover { color: var(--text-2); }
  .settings-tab.active { color: var(--text-1); border-bottom-color: var(--green); }

  .ticker-sector { margin-bottom: 16px; }
  .ticker-sector-name {
    font-family: 'DM Mono', monospace; font-size: 11px;
    color: var(--text-3); text-transform: uppercase;
    letter-spacing: 0.06em; margin-bottom: 6px;
  }
  .ticker-chips { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 6px; }
  .ticker-chip {
    display: flex; align-items: center; gap: 5px;
    background: #0e1e38; border: 1px solid #2a3a58;
    border-radius: 4px; padding: 3px 8px;
    font-family: 'DM Mono', monospace; font-size: 12px; color: var(--text-2);
  }
  .ticker-chip-remove {
    background: none; border: none; color: var(--text-3);
    cursor: pointer; font-size: 13px; line-height: 1; padding: 0;
    transition: color 0.15s;
  }
  .ticker-chip-remove:hover { color: var(--red); }
  .ticker-add-row { display: flex; gap: 6px; }
  .ticker-add-input {
    background: var(--bg); border: 1px solid var(--border);
    border-radius: 4px; color: var(--text-1);
    font-family: 'DM Mono', monospace; font-size: 12px;
    padding: 4px 8px; width: 80px; outline: none; text-transform: uppercase;
  }
  .ticker-add-input:focus { border-color: #3a5a8a; }
  .ticker-add-btn {
    background: #0e1e38; border: 1px solid #2a4a6a; border-radius: 4px;
    color: #6aa0f0; font-family: 'DM Mono', monospace; font-size: 11px;
    letter-spacing: 0.06em; padding: 4px 10px; cursor: pointer;
    text-transform: uppercase; transition: all 0.15s;
  }
  .ticker-add-btn:hover { background: #132a4a; }
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

// ── Settings modal ────────────────────────────────────────────────────────────

const SETTINGS_FIELDS = [
  { key: "lock1_threshold",      label: "Lock 1 Threshold",      step: 0.01, min: 0,    max: 1   },
  { key: "lock2_sentiment_min",  label: "Lock 2 Sentiment Min",  step: 0.01, min: -1,   max: 1   },
  { key: "lock3_confidence_min", label: "Lock 3 Confidence Min", step: 0.01, min: 0,    max: 1   },
  { key: "take_profit_pct",      label: "Take Profit %",         step: 0.01, min: 0.01, max: 1   },
  { key: "stop_loss_pct",        label: "Stop Loss %",           step: 0.01, min: 0.01, max: 0.5 },
  { key: "max_positions",        label: "Max Positions",         step: 1,    min: 1,    max: 20  },
  { key: "max_position_size",    label: "Max Position Size",     step: 0.01, min: 0.01, max: 0.5 },
  { key: "daily_loss_cap",       label: "Daily Loss Cap ($)",    step: 10,   min: 10,   max: 10000 },
];

function SettingsCol({ title, config, accentColor, onSave }) {
  const [values, setValues] = React.useState(config || {});
  const [saved,  setSaved]  = React.useState(false);

  React.useEffect(() => { setValues(config || {}); }, [config]);

  function handleChange(key, val) {
    setValues(prev => ({ ...prev, [key]: parseFloat(val) }));
    setSaved(false);
  }

  function handleSave() {
    onSave(values);
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  }

  return (
    <div>
      <div className="settings-col-title" style={{ color: accentColor }}>{title}</div>
      {SETTINGS_FIELDS.map(f => (
        <div className="settings-field" key={f.key}>
          <div className="settings-label">{f.label}</div>
          <input
            className="settings-input"
            type="number"
            step={f.step}
            min={f.min}
            max={f.max}
            value={values[f.key] ?? ""}
            onChange={e => handleChange(f.key, e.target.value)}
          />
        </div>
      ))}
      <button
        className={`settings-save-btn ${saved ? "saved" : ""}`}
        onClick={handleSave}
      >
        {saved ? "✓ Saved" : `Save ${title}`}
      </button>
    </div>
  );
}

function TickersTab({ tickers, onAdd, onRemove }) {
  const [inputs, setInputs] = React.useState({});
  if (!tickers) return <p className="muted">Loading…</p>;
  return (
    <div style={{ maxHeight: 400, overflowY: "auto", paddingRight: 4 }}>
      {Object.entries(tickers).map(([sector, cfg]) => (
        <div className="ticker-sector" key={sector}>
          <div className="ticker-sector-name">{sector} <span style={{ color: "var(--text-3)" }}>· ETF: {cfg.etf}</span></div>
          <div className="ticker-chips">
            {cfg.tickers.map(t => (
              <div className="ticker-chip" key={t}>
                {t}
                <button className="ticker-chip-remove" onClick={() => onRemove(sector, t)} title={`Remove ${t}`}>×</button>
              </div>
            ))}
          </div>
          <div className="ticker-add-row">
            <input
              className="ticker-add-input"
              placeholder="AAPL"
              value={inputs[sector] || ""}
              onChange={e => setInputs(p => ({ ...p, [sector]: e.target.value.toUpperCase() }))}
              onKeyDown={e => {
                if (e.key === "Enter" && inputs[sector]?.trim()) {
                  onAdd(sector, inputs[sector].trim());
                  setInputs(p => ({ ...p, [sector]: "" }));
                }
              }}
            />
            <button
              className="ticker-add-btn"
              onClick={() => {
                if (inputs[sector]?.trim()) {
                  onAdd(sector, inputs[sector].trim());
                  setInputs(p => ({ ...p, [sector]: "" }));
                }
              }}
            >+ Add</button>
          </div>
        </div>
      ))}
    </div>
  );
}

function SettingsModal({ settings, tickers, onSave, onTickerAdd, onTickerRemove, onClose }) {
  const [activeTab, setActiveTab] = React.useState("thresholds");
  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" style={{ width: 660 }} onClick={e => e.stopPropagation()}>
        <div className="modal-title" style={{ marginBottom: 12 }}>Settings</div>

        <div className="settings-tabs">
          <button className={`settings-tab ${activeTab === "thresholds" ? "active" : ""}`} onClick={() => setActiveTab("thresholds")}>Thresholds</button>
          <button className={`settings-tab ${activeTab === "tickers"    ? "active" : ""}`} onClick={() => setActiveTab("tickers")}>Tickers</button>
        </div>

        {activeTab === "thresholds" && (
          <>
            <div className="modal-sub" style={{ marginBottom: 16 }}>
              Changes take effect on the next gate cycle. No restart required.
            </div>
            <div className="settings-grid">
              <SettingsCol title="Demo" config={settings?.demo} accentColor="var(--text-2)" onSave={vals => onSave("demo", vals)} />
              <SettingsCol title="Live" config={settings?.live} accentColor="#a0c040"        onSave={vals => onSave("live", vals)} />
            </div>
          </>
        )}

        {activeTab === "tickers" && (
          <>
            <div className="modal-sub" style={{ marginBottom: 16 }}>
              Add or remove tickers per sector. Changes apply on the next poll cycle.
            </div>
            <TickersTab tickers={tickers} onAdd={onTickerAdd} onRemove={onTickerRemove} />
          </>
        )}

        <div className="modal-actions" style={{ marginTop: 16 }}>
          <button className="btn-cancel" onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  );
}


// ── Promote modal ─────────────────────────────────────────────────────────────

const PROMOTE_LABELS = {
  lock1_threshold:      "Lock 1 Threshold",
  lock2_sentiment_min:  "Lock 2 Sentiment Min",
  lock3_confidence_min: "Lock 3 Confidence Min",
  take_profit_pct:      "Take Profit %",
  stop_loss_pct:        "Stop Loss %",
  max_positions:        "Max Positions",
  max_position_size:    "Max Position Size",
  daily_loss_cap:       "Daily Loss Cap ($)",
};

function PromoteModal({ config, onConfirm, onCancel }) {
  const { live, demo } = config;
  const pct = v => `${(v * 100).toFixed(1)}%`;
  const fmt = (key, v) => {
    if (key === "daily_loss_cap")  return `$${v}`;
    if (key === "max_positions")   return v;
    if (typeof v === "number" && v < 2) return pct(v);
    return v;
  };

  return (
    <div className="modal-overlay" onClick={onCancel}>
      <div className="modal" onClick={e => e.stopPropagation()}>
        <div className="modal-title">Promote Demo Settings to Live</div>
        <div className="modal-sub">
          This will overwrite the live gate config with the current demo thresholds.
          Changes take effect on the next gate cycle.
        </div>
        <table className="modal-table">
          <thead>
            <tr><th>Setting</th><th>Current Live</th><th>New (Demo)</th></tr>
          </thead>
          <tbody>
            {Object.keys(PROMOTE_LABELS).map(key => {
              const liveVal = live?.[key];
              const demoVal = demo?.[key];
              const changed = liveVal !== demoVal;
              return (
                <tr key={key}>
                  <td>{PROMOTE_LABELS[key]}</td>
                  <td>{liveVal != null ? fmt(key, liveVal) : "—"}</td>
                  <td className={changed ? "changed" : ""}>{demoVal != null ? fmt(key, demoVal) : "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <div className="modal-actions">
          <button className="btn-cancel" onClick={onCancel}>Cancel</button>
          <button className="btn-confirm" onClick={onConfirm}>Confirm Promote</button>
        </div>
      </div>
    </div>
  );
}


// ── App ───────────────────────────────────────────────────────────────────────

export default function App() {
  const [activeTab,     setActiveTab]     = useState("demo");

  // ── Demo state ────────────────────────────────────────────────────────────
  const [sectors,       setSectors]       = useState([]);
  const [wallet,        setWallet]        = useState(null);
  const [gateHist,      setGateHist]      = useState([]);
  const [demoTrades,    setDemoTrades]    = useState([]);
  const [equity,        setEquity]        = useState([]);
  const [sectorError,   setSectorError]   = useState(null);
  const [walletError,   setWalletError]   = useState(null);
  const [gateError,     setGateError]     = useState(null);
  const [equityError,   setEquityError]   = useState(null);
  const [lastFetch,     setLastFetch]     = useState(null);
  const [backtestResult, setBacktestResult] = useState(null);

  // ── Live state ────────────────────────────────────────────────────────────
  const [liveStatus,    setLiveStatus]    = useState(null);
  const [liveAccount,   setLiveAccount]   = useState(null);
  const [liveTrades,    setLiveTrades]    = useState([]);
  const [liveGateHist,  setLiveGateHist]  = useState([]);
  const [liveEquity,    setLiveEquity]    = useState([]);
  const [compareData,   setCompareData]   = useState(null);
  const [liveConfig,    setLiveConfig]    = useState(null);
  const [showPromote,   setShowPromote]   = useState(false);
  const [settings,      setSettings]      = useState(null);
  const [showSettings,  setShowSettings]  = useState(false);
  const [tickers,       setTickers]       = useState(null);

  // Each section loads independently so a slow or failing endpoint
  // doesn't block the rest of the dashboard from rendering.
  function fetchDemoData() {
    fetchWithRetry("/api/sectors")
      .then(data => { setSectors(data || []); setSectorError(null); })
      .catch(() => setSectorError("Failed to load sector signals"));

    fetchWithRetry("/api/wallet")
      .then(data => { setWallet(data || null); setWalletError(null); })
      .catch(() => setWalletError("Failed to load wallet"));

    fetchWithRetry("/api/gate/history")
      .then(data => { setGateHist(data || []); setGateError(null); })
      .catch(() => setGateError("Failed to load gate history"));

    fetchWithRetry("/api/demo/trades")
      .then(data => setDemoTrades(data || []))
      .catch(() => setDemoTrades([]));

    fetchWithRetry("/api/wallet/equity")
      .then(data => { setEquity(data || []); setEquityError(null); })
      .catch(() => setEquityError("Failed to load equity curve"));

    setLastFetch(new Date());
  }

  function fetchLiveData() {
    fetchWithRetry("/api/live/status")
      .then(data => setLiveStatus(data))
      .catch(() => setLiveStatus({ enabled: false, connected: false, message: "Status fetch failed" }));

    fetchWithRetry("/api/live/account")
      .then(data => setLiveAccount(data))
      .catch(() => setLiveAccount(null));


    fetchWithRetry("/api/live/trades")
      .then(data => setLiveTrades(data || []))
      .catch(() => setLiveTrades([]));

    fetchWithRetry("/api/live/gate/history")
      .then(data => setLiveGateHist(data || []))
      .catch(() => setLiveGateHist([]));

    fetchWithRetry("/api/live/equity")
      .then(data => setLiveEquity(data || []))
      .catch(() => setLiveEquity([]));

    fetchWithRetry("/api/live/compare")
      .then(data => setCompareData(data || null))
      .catch(() => setCompareData(null));

    fetchWithRetry("/api/live/config")
      .then(data => setLiveConfig(data || null))
      .catch(() => setLiveConfig(null));

    fetchWithRetry("/api/settings")
      .then(data => setSettings(data || null))
      .catch(() => setSettings(null));

    fetchWithRetry("/api/tickers")
      .then(data => setTickers(data || null))
      .catch(() => setTickers(null));
  }

  function fetchData() {
    fetchDemoData();
    fetchLiveData();
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
          {activeTab === "demo" && wallet && (
            <div className="header-balance">
              ${wallet.balance.toLocaleString("en-US", { minimumFractionDigits: 2 })}
              {" "}<span className={pnlCls}>{sign}${pnl.toFixed(2)}</span>
            </div>
          )}
          {activeTab === "live" && liveAccount && (
            <div className="header-balance">
              ${liveAccount.equity.toLocaleString("en-US", { minimumFractionDigits: 2 })}
              {" "}
              <span className={liveAccount.day_pnl >= 0 ? "pnl-pos" : "pnl-neg"}>
                {liveAccount.day_pnl >= 0 ? "+" : ""}${liveAccount.day_pnl.toFixed(2)}
              </span>
            </div>
          )}
          <div style={{ fontFamily: "'DM Mono', monospace", fontSize: 10, color: "#3a4060" }}>
            {updatedStr}
          </div>
          <button className="settings-btn" onClick={() => setShowSettings(true)}>⚙ Settings</button>
          <button className="refresh-btn" onClick={fetchData}>Refresh</button>
        </div>
      </header>

      {/* ── Tab bar ───────────────────────────────────────────────────────── */}
      <nav className="tab-bar">
        <button
          className={`tab-btn ${activeTab === "demo" ? "active" : ""}`}
          onClick={() => setActiveTab("demo")}
        >
          Demo
        </button>
        <button
          className={`tab-btn ${activeTab === "live" ? "active" : ""}`}
          onClick={() => setActiveTab("live")}
        >
          {liveStatus?.enabled && liveStatus?.connected && (
            <span className="live-dot" />
          )}
          Live
          {liveStatus?.enabled && liveStatus?.connected && (
            <span style={{
              marginLeft: 8,
              fontFamily: "'DM Mono', monospace",
              fontSize: 10,
              fontWeight: 700,
              letterSpacing: "0.06em",
              padding: "1px 6px",
              borderRadius: 3,
              ...(liveStatus?.paper
                ? { background: "#1a2800", color: "#a0c040" }
                : { background: "#2a0e0e", color: "var(--red)" }
              ),
            }}>
              {liveStatus?.paper ? "PAPER" : "LIVE"}
            </span>
          )}
        </button>
      </nav>

      {/* ── Demo tab ──────────────────────────────────────────────────────── */}
      {activeTab === "demo" && (
        <>
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

              <ErrorBoundary label="Compare">
                <ComparePanel data={compareData} />
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

          <div style={{ padding: "0 28px 28px" }}>
            <ErrorBoundary label="Demo Trade Log">
              <DemoTradeLog trades={demoTrades} />
            </ErrorBoundary>
          </div>

          {backtestResult && (
            <div style={{ padding: "0 28px 28px" }}>
              <TradeLog result={backtestResult} />
            </div>
          )}
        </>
      )}

      {/* ── Settings modal ───────────────────────────────────────────────── */}
      {showSettings && (
        <SettingsModal
          settings={settings}
          tickers={tickers}
          onSave={(side, updates) => {
            fetch(`/api/settings/${side}`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify(updates),
            }).then(() => fetchData());
          }}
          onTickerAdd={(sector, ticker) =>
            fetch(`/api/tickers/${sector}/add?ticker=${ticker}`, { method: "POST" })
              .then(() => fetchData())
          }
          onTickerRemove={(sector, ticker) =>
            fetch(`/api/tickers/${sector}/remove?ticker=${ticker}`, { method: "POST" })
              .then(() => fetchData())
          }
          onClose={() => setShowSettings(false)}
        />
      )}

      {/* ── Promote modal ────────────────────────────────────────────────── */}
      {showPromote && liveConfig && (
        <PromoteModal
          config={liveConfig}
          onConfirm={async () => {
            await fetch("/api/live/config/promote", { method: "POST" });
            setShowPromote(false);
            fetchData();
          }}
          onCancel={() => setShowPromote(false)}
        />
      )}

      {/* ── Live tab ──────────────────────────────────────────────────────── */}
      {activeTab === "live" && (
        <>
        <main className="main">
          <div className="main-left">
            <ErrorBoundary label="Live Gate Feed">
              <LiveGateFeed history={liveGateHist} />
            </ErrorBoundary>

            <ErrorBoundary label="Compare">
              <ComparePanel data={compareData} />
            </ErrorBoundary>
          </div>

          <div className="main-right">
            {liveStatus?.enabled && (
              <div style={{ display: "flex", justifyContent: "flex-end" }}>
                <button className="promote-btn" onClick={() => setShowPromote(true)}>
                  ↑ Promote Demo → Live
                </button>
              </div>
            )}

            <ErrorBoundary label="Live Account">
              <LiveAccount account={liveAccount} status={liveStatus} />
            </ErrorBoundary>

            <ErrorBoundary label="Live Equity Curve">
              <EquityCurve equity={liveEquity} />
            </ErrorBoundary>

          </div>
        </main>

        <div style={{ padding: "0 28px 32px" }}>
          <ErrorBoundary label="Live Trade Log">
            <LiveTradeLog trades={liveTrades} />
          </ErrorBoundary>
        </div>
        </>
      )}
    </>
  );
}
