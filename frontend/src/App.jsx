import React, { useState, useEffect, useMemo, Component } from "react";
import RotationForecast  from "./components/RotationForecast.jsx";
import SectorRotation    from "./components/SectorRotation.jsx";
import ApexTerminal      from "./components/ApexTerminal.jsx";
import ApexAtlas         from "./components/ApexAtlas.jsx";
import { useTweaks, TweaksPanel, TweakSection, TweakRadio, TweakSelect, TweakColor } from "./components/TweaksPanel.jsx";

// ── Modal / overlay CSS only ──────────────────────────────────────────────────
const CSS = `
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'Inter', system-ui, sans-serif; font-size: 14px; min-height: 100vh; -webkit-font-smoothing: antialiased; }

  :root {
    --green: #faff69; --red: #ff7575;
    --bg: #151515; --bg-card: #1f1f1c; --bg-header: #282828;
    --border: #343434; --text-1: #ffffff; --text-2: #bcbcbb; --text-3: #a0a0a0;
  }
  @keyframes ping  { 0%{opacity:0.6;transform:scale(1)} 100%{opacity:0;transform:scale(2.4)} }

  /* ── Supplemental panels ───────────────────────── */
  .supp-grid {
    display: grid; grid-template-columns: 1fr 1fr; gap: 18px; align-items: start;
  }

  /* ── Test overlay ──────────────────────────────── */
  .test-overlay {
    position: fixed; inset: 0; z-index: 1000;
    background: var(--bg); overflow: auto;
    display: flex; flex-direction: column;
  }
  .test-overlay-header {
    background: var(--bg-header); border-bottom: 1px solid var(--border);
    padding: 0 28px; height: 48px; display: flex; align-items: center;
    justify-content: space-between; position: sticky; top: 0; z-index: 10;
  }
  .test-overlay-title { font-family: 'Inconsolata', monospace; font-size: 13px; color: var(--text-2); letter-spacing: 0.08em; }
  .test-close-btn {
    background: transparent; border: 1px solid var(--border); color: var(--text-3);
    border-radius: 4px; font-family: 'Inconsolata', monospace; font-size: 11px;
    letter-spacing: 0.06em; padding: 5px 12px; cursor: pointer; transition: all 0.15s;
  }
  .test-close-btn:hover { color: var(--text-1); border-color: var(--text-2); }
  .test-main { padding: 24px 28px; display: grid; grid-template-columns: 1fr 360px; gap: 20px; align-items: start; }
  .test-left, .test-right { display: flex; flex-direction: column; gap: 20px; }

  /* ── Modal overlay ─────────────────────────────── */
  .modal-overlay {
    position: fixed; inset: 0; background: rgba(0,0,0,0.7);
    z-index: 2000; display: flex; align-items: center; justify-content: center;
  }
  .modal {
    background: #1a1a18; border: 1px solid var(--border); border-radius: 8px;
    padding: 24px; min-width: 520px; max-width: 90vw; max-height: 90vh;
    overflow-y: auto; color: var(--text-1);
  }
  .modal-title { font-family: 'Inconsolata', monospace; font-size: 14px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; color: var(--green); margin-bottom: 8px; }
  .modal-sub { font-size: 12px; color: var(--text-3); margin-bottom: 16px; }
  .modal-actions { display: flex; gap: 10px; justify-content: flex-end; margin-top: 16px; }
  .modal-table { width: 100%; border-collapse: collapse; font-family: 'Inconsolata', monospace; font-size: 12px; }
  .modal-table th { color: var(--text-3); text-align: left; padding: 6px 8px; font-weight: 400; font-size: 10px; letter-spacing: 0.08em; text-transform: uppercase; border-bottom: 1px solid var(--border); }
  .modal-table td { padding: 5px 8px; color: var(--text-2); border-bottom: 1px solid #252523; }
  .modal-table td.changed { color: var(--green); }
  .btn-confirm {
    background: #1a1a00; border: 1px solid #3a3a00; color: var(--green);
    border-radius: 5px; font-family: 'Inconsolata', monospace; font-size: 12px;
    letter-spacing: 0.06em; padding: 8px 18px; cursor: pointer;
    text-transform: uppercase; transition: all 0.15s;
  }
  .btn-confirm:hover { background: #2a2a00; }
  .btn-cancel {
    background: transparent; border: 1px solid var(--border); color: var(--text-3);
    border-radius: 5px; font-family: 'Inconsolata', monospace; font-size: 12px;
    letter-spacing: 0.06em; padding: 8px 18px; cursor: pointer;
    text-transform: uppercase; transition: all 0.15s;
  }
  .btn-cancel:hover { color: var(--text-1); border-color: var(--text-2); }

  /* ── Settings modal ──────────────────────────────── */
  .settings-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; margin-bottom: 20px; }
  .settings-col-title { font-family: 'Inconsolata', monospace; font-size: 11px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; color: var(--text-3); margin-bottom: 12px; }
  .settings-field { margin-bottom: 12px; }
  .settings-label { font-family: 'Inconsolata', monospace; font-size: 11px; color: var(--text-3); margin-bottom: 4px; text-transform: uppercase; letter-spacing: 0.04em; }
  .settings-input { width: 100%; background: var(--bg); border: 1px solid var(--border); border-radius: 4px; color: var(--text-1); font-family: 'Inconsolata', monospace; font-size: 13px; padding: 6px 10px; outline: none; transition: border-color 0.15s; }
  .settings-input:focus { border-color: #4a4a38; }
  .settings-save-btn { width: 100%; background: #1a1a00; border: 1px solid #3a3a00; color: var(--text-2); border-radius: 4px; font-family: 'Inconsolata', monospace; font-size: 11px; letter-spacing: 0.06em; padding: 7px; cursor: pointer; text-transform: uppercase; transition: all 0.15s; margin-top: 4px; }
  .settings-save-btn:hover { background: #252500; border-color: #4a4a00; }
  .settings-save-btn.saved { background: #1a1a00; border-color: var(--green); color: var(--green); }
  .sector-thresholds-toggle { width: 100%; background: transparent; border: 1px solid var(--border); border-radius: 5px; color: var(--text-3); cursor: pointer; font-family: 'Inconsolata', monospace; font-size: 11px; letter-spacing: 0.07em; padding: 8px 12px; text-align: left; margin-top: 20px; display: flex; justify-content: space-between; align-items: center; transition: border-color 0.15s, color 0.15s; }
  .sector-thresholds-toggle:hover { border-color: #3a3a38; color: var(--text-2); }
  .sector-thresholds-body { margin-top: 8px; border: 1px solid var(--border); border-radius: 5px; padding: 12px 14px; }
  .sector-thresh-row { display: flex; justify-content: space-between; align-items: center; padding: 4px 0; border-bottom: 1px solid #2a2a28; font-size: 11px; }
  .sector-thresh-row:last-child { border-bottom: none; }
  .sector-thresh-name { color: var(--text-2); font-family: 'Inconsolata', monospace; }
  .sector-thresh-val  { color: var(--text-1); font-weight: 600; font-family: 'Inconsolata', monospace; }
  .sector-thresh-bar  { height: 3px; background: #2a2a28; border-radius: 2px; flex: 1; margin: 0 10px; overflow: hidden; }
  .sector-thresh-fill { height: 100%; background: var(--green); border-radius: 2px; opacity: 0.55; }
  .sector-thresh-fallback { color: var(--text-3); font-size: 10px; margin-top: 10px; font-family: 'Inconsolata', monospace; }
  .sector-thresh-recal { background: transparent; border: 1px solid #2a2a28; color: var(--text-3); border-radius: 4px; font-family: 'Inconsolata', monospace; font-size: 10px; letter-spacing: 0.06em; padding: 4px 10px; cursor: pointer; margin-top: 10px; transition: all 0.15s; }
  .sector-thresh-recal:hover { background: #1a1a00; border-color: #3a3a00; color: var(--green); }
  .sector-thresh-recal:disabled { opacity: 0.5; cursor: not-allowed; }
  .settings-tabs { display: flex; gap: 2px; margin-bottom: 20px; border-bottom: 1px solid var(--border); }
  .settings-tab { background: transparent; border: none; border-bottom: 2px solid transparent; color: var(--text-3); cursor: pointer; font-family: 'Inconsolata', monospace; font-size: 11px; letter-spacing: 0.08em; padding: 8px 16px; text-transform: uppercase; transition: all 0.15s; }
  .settings-tab:hover { color: var(--text-2); }
  .settings-tab.active { color: var(--green); border-bottom-color: var(--green); }
  .ticker-sector { margin-bottom: 16px; }
  .ticker-sector-name { font-family: 'Inconsolata', monospace; font-size: 11px; color: var(--text-3); text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 6px; }
  .ticker-chips { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 6px; }
  .ticker-chip { display: flex; align-items: center; gap: 5px; background: #141414; border: 1px solid #2a2a28; border-radius: 4px; padding: 3px 8px; font-family: 'Inconsolata', monospace; font-size: 12px; color: var(--text-2); }
  .ticker-chip-remove { background: none; border: none; color: var(--text-3); cursor: pointer; font-size: 13px; line-height: 1; padding: 0; transition: color 0.15s; }
  .ticker-chip-remove:hover { color: var(--red); }
  .ticker-add-row { display: flex; gap: 6px; }
  .ticker-add-input { background: var(--bg); border: 1px solid var(--border); border-radius: 4px; color: var(--text-1); font-family: 'Inconsolata', monospace; font-size: 12px; padding: 4px 8px; width: 80px; outline: none; text-transform: uppercase; }
  .ticker-add-input:focus { border-color: #4a4a38; }
  .ticker-add-btn { background: #1a1a00; border: 1px solid #3a3a00; border-radius: 4px; color: var(--green); font-family: 'Inconsolata', monospace; font-size: 11px; letter-spacing: 0.06em; padding: 4px 10px; cursor: pointer; text-transform: uppercase; transition: all 0.15s; }
  .ticker-add-btn:hover { background: #252500; }
  .error-banner { color: var(--red); font-family: 'Inconsolata', monospace; font-size: 12px; padding: 8px; }
`;

// ── Helpers ────────────────────────────────────────────────────────────────────

function isMarketOpen() {
  const now = new Date();
  const ny  = new Date(now.toLocaleString("en-US", { timeZone: "America/New_York" }));
  const day = ny.getDay();
  if (day === 0 || day === 6) return false;
  const mins = ny.getHours() * 60 + ny.getMinutes();
  return mins >= 9 * 60 + 30 && mins < 16 * 60;
}

class ErrorBoundary extends Component {
  constructor(props) { super(props); this.state = { hasError: false, message: "" }; }
  static getDerivedStateFromError(err) { return { hasError: true, message: err?.message || "Unknown error" }; }
  componentDidCatch(err, info) { console.error("ErrorBoundary caught:", err, info); }
  render() {
    if (this.state.hasError) {
      return <div className="error-banner">Render error: {this.state.message}</div>;
    }
    return this.props.children;
  }
}

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

// ── Sector meta — exact strings from config.py ────────────────────────────────

const SECTOR_META = {
  Technology:      { code: "TECH",  etf: "XLK",  displayName: "Technology",    excluded: false },
  Healthcare:      { code: "HLTH",  etf: "XLV",  displayName: "Healthcare",     excluded: false },
  Energy:          { code: "ENER",  etf: "XLE",  displayName: "Energy",         excluded: false },
  Industrials:     { code: "INDU",  etf: "XLI",  displayName: "Industrials",    excluded: false },
  Financials:      { code: "FIN",   etf: "XLF",  displayName: "Financials",     excluded: true  },
  ConsumerDisc:    { code: "CDIS",  etf: "XLY",  displayName: "Consumer Disc.", excluded: false },
  ConsumerStaples: { code: "CSTP",  etf: "XLP",  displayName: "Cons. Staples",  excluded: true  },
  Communication:   { code: "COMM",  etf: "XLC",  displayName: "Communication",  excluded: false },
  Utilities:       { code: "UTIL",  etf: "XLU",  displayName: "Utilities",      excluded: true  },
  Materials:       { code: "MATR",  etf: "XLB",  displayName: "Materials",      excluded: false },
  RealEstate:      { code: "REIT",  etf: "XLRE", displayName: "Real Estate",    excluded: false },
  Semiconductors:  { code: "SEMI",  etf: "SOXX", displayName: "Semiconductors", excluded: false },
  Defense:         { code: "DEF",   etf: "ITA",  displayName: "Defense",        excluded: false },
  Homebuilders:    { code: "HOME",  etf: "ITB",  displayName: "Homebuilders",   excluded: false },
  Transportation:  { code: "TRAN",  etf: "IYT",  displayName: "Transportation", excluded: false },
};

const EXCLUDED_CODES = new Set(
  Object.values(SECTOR_META).filter(m => m.excluded).map(m => m.code)
);

// ── Data adapters ─────────────────────────────────────────────────────────────

function adaptTickers(sectors) {
  const out = [];
  (sectors || []).forEach(s => {
    const meta = SECTOR_META[s.sector];
    if (!meta) return;
    (s.tickers || []).forEach(t => {
      const score = t.signal_score ?? 0;
      const momentum = t.momentum_score ?? score;
      const volume = t.volume_score ?? score;
      out.push({
        sym:        t.ticker,
        sector:     meta.code,
        sectorName: meta.displayName,
        etf:        s.etf || meta.etf,
        score,
        momentum,
        volume,
        ev:         t.ev ?? score,
        trend:      t.trend_score ?? momentum,
        rs:         t.rs_score ?? volume,
        rsi:        t.rsi ?? null,
        chg:        t.chg ?? 0,
        price:      t.price ?? 0,
        signal:     t.signal ?? "weak",
        streak_days: t.streak_days ?? 0,
        trendDir:   t.trend ?? "flat",
        watchlist:  false,
      });
    });
  });
  return out;
}

function buildSECTORS(sectors) {
  const tickersBySector = {};
  const scoresBySector  = {};
  const trendBySector   = {};
  (sectors || []).forEach(s => {
    const meta = SECTOR_META[s.sector];
    if (!meta) return;
    tickersBySector[meta.code] = (s.tickers || []).map(t => t.ticker);
    scoresBySector[meta.code]  = s.avg_signal ?? null;
    trendBySector[meta.code]   = s.trend ?? "flat";
  });
  return Object.entries(SECTOR_META)
    .filter(([, meta]) => !meta.excluded)
    .map(([, meta]) => ({
      code:      meta.code,
      name:      meta.displayName,
      etf:       meta.etf,
      tickers:   tickersBySector[meta.code] || [],
      avgSignal: scoresBySector[meta.code] ?? null,
      trend:     trendBySector[meta.code]  ?? "flat",
    }));
}

function adaptRegime(regimeData) {
  if (!regimeData?.leaderboard) return [];
  const seen = new Set();
  const rows = regimeData.leaderboard.map(r => {
    const meta = SECTOR_META[r.sector];
    if (meta) seen.add(meta.code);
    return {
      code:      meta?.code ?? r.sector,
      name:      meta?.displayName ?? r.sector,
      etf:       meta?.etf ?? "",
      excluded:  meta?.excluded ?? false,
      prior:     r.posterior * 0.9,
      posterior: r.posterior ?? 0,
      delta:     r.delta ?? null,
      delta7d:   r.delta_7d ?? null,
      ipo:       r.allocation ?? 0,
    };
  });
  // Inject placeholder rows for active sectors not yet in the backend leaderboard
  // (e.g. newly added sectors with no EOD history yet). posterior=0 is accurate.
  Object.entries(SECTOR_META).forEach(([, meta]) => {
    if (!meta.excluded && !seen.has(meta.code)) {
      rows.push({ code: meta.code, name: meta.displayName, etf: meta.etf,
                  excluded: false, prior: 0, posterior: 0, delta: null, delta7d: null, ipo: 0 });
    }
  });
  return rows;
}

function adaptEquity(equityData, walletBalance) {
  if (!equityData || equityData.length === 0) return [];
  const pts = equityData.map((p, i) => ({
    day:   i,
    value: p.balance ?? p.value ?? 0,
    ts:    p.ts ?? null,
    mtm:   p.mtm ?? false,
  }));
  // Keep MTM point in sync with the wallet balance (avoids price-fetch race between endpoints)
  if (walletBalance != null && pts.length > 0 && pts[pts.length - 1].mtm) {
    pts[pts.length - 1].value = walletBalance;
  }
  return pts;
}

function _maxDD(equityPoints) {
  if (!equityPoints || equityPoints.length < 2) return 0;
  let peak = equityPoints[0].value;
  let dd = 0;
  for (const p of equityPoints) {
    if (p.value > peak) peak = p.value;
    if (peak > 0) dd = Math.max(dd, (peak - p.value) / peak);
  }
  return dd;
}

function adaptWallet(wallet, equityPoints) {
  if (!wallet) return null;
  return {
    balance:    wallet.balance ?? 0,
    starting:   wallet.starting_balance ?? 2000,
    cash:       wallet.cash ?? 0,
    invested:   wallet.invested ?? 0,
    realized:   wallet.realized_pnl ?? 0,
    unrealized: wallet.unrealized_pnl ?? 0,
    winRate:    wallet.win_rate ?? 0,
    trades:     wallet.total_trades ?? 0,
    avgWin:     0,
    avgLoss:    0,
    drawdown:   _maxDD(equityPoints),
    sharpe:     0,
  };
}

function adaptLiveWallet(account, livePositions, settings, equityPoints) {
  const starting = settings?.live?.starting_balance ?? 25000;
  if (!account) {
    return { balance: 0, starting, cash: 0, invested: 0, realized: 0, unrealized: 0, winRate: 0, trades: 0, avgWin: 0, avgLoss: 0, drawdown: 0, sharpe: 0 };
  }
  const unrealized = (livePositions || []).reduce((s, p) => s + (p.unrealized_pnl ?? 0), 0);
  return {
    balance:    account.equity ?? 0,
    starting,
    cash:       account.cash ?? 0,
    invested:   (account.equity ?? 0) - (account.cash ?? 0),
    realized:   account.total_pnl ?? 0,
    unrealized,
    winRate:    0,
    trades:     0,
    avgWin:     0,
    avgLoss:    0,
    drawdown:   _maxDD(equityPoints),
    sharpe:     0,
  };
}

function adaptPositions(openPositions) {
  return (openPositions || []).map(p => ({
    sym:    p.ticker,
    sector: (Object.entries(SECTOR_META).find(([, m]) => m.code === (SECTOR_META[p.sector ?? ""]?.code)) || [])[1]?.code ?? "",
    qty:    p.shares ?? p.qty ?? 1,
    entry:  p.avg_entry_price ?? p.price ?? 0,
    mkt:    p.current_price ?? p.avg_entry_price ?? p.price ?? 0,
    pnl:    p.unrealized_pnl ?? 0,
    pct:    (p.unrealized_pct ?? 0) * 100,
    peak:   p.peak_price ?? p.current_price ?? p.avg_entry_price ?? p.price ?? 0,
    held:   p.days_held ?? 0,
    tp:     null,
    sl:     null,
    trail:  false,
  }));
}

function adaptFunnel(funnel) {
  if (!funnel || Object.keys(funnel).length === 0) return [];
  const total      = funnel.total_candidates ?? 0;
  const skippedO   = funnel.skipped_open ?? 0;
  const skippedC   = funnel.skipped_cooloff ?? 0;
  const evaluated  = funnel.evaluated ?? 0;
  const eligibilityFail = funnel.eligibility_fail ?? 0;
  const l1Fail     = funnel.l1_fail ?? 0;
  const l2Fail     = funnel.l2_fail ?? 0;
  const leadFail   = funnel.leading_fail ?? 0;
  const l3Fail     = funnel.l3_fail ?? 0;
  const overFail   = funnel.overflow_fail ?? 0;
  const executed   = funnel.executed ?? 0;
  return [
    { stage: "POLLED",                label: "Tickers polled",    count: total          },
    { stage: "SKIPPED_OPEN",          label: "Position open",     count: skippedO       },
    { stage: "SKIPPED_COOLOFF",       label: "Cooloff",           count: skippedC       },
    { stage: "FILTERED_ELIGIBILITY",  label: "L0 · Eligibility",  count: eligibilityFail },
    { stage: "FILTERED_L1",           label: "L1 · Score",        count: l1Fail         },
    { stage: "FILTERED_L2",           label: "L2 · Quant",        count: l2Fail         },
    { stage: "FILTERED_LEADING",      label: "L4 · Leading",      count: leadFail       },
    { stage: "FILTERED_L3",           label: "L5 · Claude",       count: l3Fail         },
    { stage: "FILTERED_OVERFLOW",     label: "Overflow quant",    count: overFail       },
    { stage: "TRADE_EXECUTED",        label: "Trade executed",    count: executed       },
  ];
}

function adaptGateRows(rows) {
  return (rows || []).map(r => ({
    ts:                  new Date(r.timestamp).getTime(),
    sym:                 r.ticker,
    sector:              SECTOR_META[r.sector]?.code ?? r.sector ?? "",
    score:               r.signal_score ?? 0,
    outcome:             r.gate_decision ?? "",
    l1_threshold:        r.l1_threshold ?? null,
    macro_reason:        r.macro_reason ?? null,
    l2_summary:          r.l2_summary ?? null,
    lock_leading_checks: r.lock_leading_checks ?? null,
    lock3_reasoning:     r.lock3_reasoning ?? null,
  }));
}

function adaptAlerts(alerts) {
  return (alerts || []).map(a => ({
    sev:   a.severity ?? "LOW",
    id:    `DRIFT-${a.id ?? a.parameter}`,
    label: `${a.parameter} · ${a.metric} · dev ${a.deviation_pct != null ? (a.deviation_pct * 100).toFixed(1) + "%" : "?"}`,
    ts:    new Date(a.updated_at ?? a.created_at ?? Date.now()).getTime(),
  }));
}

// ── Settings modal ────────────────────────────────────────────────────────────

const SETTINGS_FIELDS = [
  { key: "lock1_threshold",      label: "Lock 1 Fallback Threshold", step: 0.01, min: 0,    max: 1,
    hint: "Fallback only — applies to sectors with insufficient history for calibration. Dead zone below 0.65.",
    warnBelow: 0.65, critBelow: 0.60,
    warnMsg: v => v < 0.60 ? "Critical: in dead zone (< 0.60)" : "Below recommended minimum (0.65)" },
  { key: "lock2_sentiment_min",  label: "Lock 2 Sentiment Min",  step: 0.01, min: -1, max: 1,
    hint: "Minimum Grok sentiment score to pass Lock 2. 0.0 = neutral or better." },
  { key: "lock3_confidence_min", label: "Lock 5 (Claude) Confidence Min", step: 0.01, min: 0, max: 1,
    hint: "Minimum Claude confidence score to pass Lock 5. At 0.7 Claude must be fairly certain." },
  { key: "lock_leading_min_pass", label: "Lock 4 (Leading) Min Checks", step: 1, min: 1, max: 4,
    hint: "Minimum sub-checks that must pass in Lock 4. Out of 4. Default 2 = majority required." },
  { key: "max_drawdown_pct",     label: "Max Drawdown Alert",        step: 0.01, min: 0.01, max: 1,
    hint: "Drawdown threshold passed to Claude in Lock 5 context." },
  { key: "take_profit_pct",      label: "Take Profit %",         step: 0.01, min: 0.01, max: 1, nullable: false,
    hint: "At 15% only 28% of trades hit target — 6–8% hits 55%+",
    warnAbove: 0.08, warnMsg: () => "Above 8% — most trades won't reach target" },
  { key: "stop_loss_pct",        label: "Stop Loss %",           step: 0.01, min: 0.01, max: 0.5, nullable: false,
    hint: "Hard exit if price falls this % below entry." },
  { key: "profit_lock_trigger_pct", label: "Profit-Lock Trigger %", step: 0.005, min: 0.005, max: 0.5, nullable: true,
    hint: "Once peak gain reaches this level, tighten trailing stop to Profit-Lock Trail %." },
  { key: "profit_lock_trail_pct",   label: "Profit-Lock Trail %",   step: 0.005, min: 0.005, max: 0.5, nullable: true,
    hint: "Trailing stop distance after profit-lock activates." },
  { key: "max_positions",        label: "Max Positions",         step: 1, min: 1, max: 20,
    hint: "Above 6 dilutes signal quality", warnAbove: 6, warnMsg: () => "Above 6 — signal quality degrades" },
  { key: "overflow_quant_increment", label: "Overflow Quant Step", step: 0.01, min: 0, max: 0.5,
    hint: "Each position beyond max_positions raises the quant threshold by this multiplier." },
  { key: "max_position_size",    label: "Max Position Size",     step: 0.01, min: 0.01, max: 0.5,
    hint: "Maximum fraction of account balance in a single position." },
  { key: "daily_loss_cap",       label: "Daily Loss Cap ($)",    step: 10,   min: 10,   max: 10000,
    hint: "Stops new entries for the rest of the day once losses hit this amount." },
  { key: "starting_balance",     label: "Starting Balance ($)",  step: 100,  min: 100,  max: 10000000,
    hint: "Account starting balance for position sizing and drawdown calculations." },
  { key: "max_hold_days",        label: "Time Stop (days)",      step: 1,    min: 1,    max: 90,
    hint: "Force-exits a position after this many calendar days." },
  { key: "vix_threshold",        label: "VIX Block Threshold",   step: 1,    min: 10,   max: 80,
    hint: "At 25 blocked 52–61% of trading days in 2020/2022", warnBelow: 30, warnMsg: () => "Below 30 — blocks recovery rallies" },
  { key: "macro_event_blackout_days",    label: "CPI/NFP Pre-Event Block (days)", step: 1, min: 0, max: 5,
    hint: "Blocks new entries N days before CPI and NFP releases only. FOMC always uses a fixed 2-day pre-event window — post-event sessions are never blocked." },
  { key: "macro_earnings_blackout_days", label: "Earnings Hard Block (days)", step: 1, min: 0, max: 7,
    hint: "Hard block: new entries within this many days of earnings are rejected. Pre-event only. Pairs with the near-term window below." },
  { key: "macro_earnings_near_days",    label: "Earnings Near-Term Window (days)", step: 1, min: 0, max: 30,
    hint: "Entries within this window (but outside the hard block) pass but receive a score penalty. Range: hard_block+1 to near_days." },
  { key: "macro_earnings_near_penalty", label: "Earnings Near-Term Penalty", step: 0.05, min: 0, max: 0.5,
    hint: "Score multiplier reduction applied in the near-term window. 0.15 = 15% score reduction before L2." },
  { key: "gate_cooloff_hours",   label: "Gate Cooloff (hours)",  step: 1,    min: 0,   max: 24,
    hint: "Prevents gate from re-evaluating same ticker within this many hours." },
  { key: "max_sector_exposure",  label: "Max Sector Exposure",   step: 0.05, min: 0.05, max: 1,
    hint: "Maximum fraction of total account value in a single sector." },
];

function SectorThresholds({ fallback }) {
  const [open,       setOpen]       = React.useState(false);
  const [thresholds, setThresholds] = React.useState(null);
  const [recaling,   setRecaling]   = React.useState(false);

  React.useEffect(() => {
    fetch("/api/calibrate/ticker-thresholds").then(r => r.json()).then(d => setThresholds(d.thresholds || {})).catch(() => {});
  }, []);

  async function recalibrate() {
    setRecaling(true);
    try {
      const r = await fetch("/api/calibrate/ticker-thresholds", { method: "POST" });
      const d = await r.json();
      setThresholds(d.thresholds || {});
    } finally { setRecaling(false); }
  }

  const sorted = thresholds
    ? Object.entries(thresholds).sort((a, b) => b[1] - a[1])
    : null;

  return (
    <>
      <button className="sector-thresholds-toggle" onClick={() => setOpen(o => !o)}>
        <span>Per-Sector Thresholds</span>
        <span>{open ? "▲" : "▼"}</span>
      </button>
      {open && (
        <div className="sector-thresholds-body">
          {sorted && sorted.map(([k, v]) => (
            <div className="sector-thresh-row" key={k}>
              <span className="sector-thresh-name">{k}</span>
              <span className="sector-thresh-bar"><span className="sector-thresh-fill" style={{ width: `${v * 100}%` }} /></span>
              <span className="sector-thresh-val">{v.toFixed(3)}</span>
            </div>
          ))}
          {fallback != null && (
            <div className="sector-thresh-fallback">Fallback threshold: {fallback}</div>
          )}
          <button className="sector-thresh-recal" disabled={recaling} onClick={recalibrate}>
            {recaling ? "Recalibrating…" : "↻ Recalibrate"}
          </button>
        </div>
      )}
    </>
  );
}

function SettingsCol({ title, config, accentColor, onSave }) {
  const [vals, setVals] = React.useState({});
  const [saved, setSaved] = React.useState(false);

  React.useEffect(() => {
    if (config) setVals(config);
  }, [config]);

  function handleSave() {
    onSave(vals);
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  }

  return (
    <div>
      <div className="settings-col-title" style={{ color: accentColor }}>{title}</div>
      {SETTINGS_FIELDS.map(f => {
        const val = vals[f.key] ?? "";
        const isEnabled = val !== null && val !== "";
        const isNullable = f.nullable === true;
        const isCrit = f.critBelow != null && Number(val) < f.critBelow;
        const isWarn = (!isCrit) && (
          (f.warnBelow != null && Number(val) < f.warnBelow) ||
          (f.warnAbove != null && Number(val) > f.warnAbove)
        );
        const borderColor = isCrit ? "var(--red)" : isWarn ? "#e8a020" : undefined;
        return (
          <div className="settings-field" key={f.key}>
            <div className="settings-label">
              {f.label}
              {f.hint && <span style={{ marginLeft: 6, opacity: 0.5, fontSize: 9, fontStyle: "italic" }} title={f.hint}>?</span>}
            </div>
            <input
              className="settings-input" type="number"
              step={f.step} min={f.min} max={f.max}
              value={val}
              placeholder={isNullable ? "disabled" : ""}
              onChange={e => {
                const raw = e.target.value;
                setVals(p => ({ ...p, [f.key]: raw === "" && isNullable ? null : parseFloat(raw) || raw }));
              }}
              style={{
                ...(isNullable && !isEnabled ? { opacity: 0.35 } : {}),
                ...(borderColor ? { borderColor, boxShadow: `0 0 0 1px ${borderColor}` } : {}),
              }}
            />
            {(isCrit || isWarn) && f.warnMsg && (
              <div style={{ fontSize: 10, color: borderColor, marginTop: 3, fontWeight: 600 }}>⚠ {f.warnMsg(val)}</div>
            )}
          </div>
        );
      })}
      <button className={`settings-save-btn ${saved ? "saved" : ""}`} onClick={handleSave}>
        {saved ? "✓ Saved" : `Save ${title}`}
      </button>
    </div>
  );
}

function TickersTab({ tickers, onAdd, onRemove }) {
  const [inputs, setInputs] = React.useState({});
  if (!tickers) return <p style={{ color: "var(--text-3)" }}>Loading…</p>;
  return (
    <div style={{ maxHeight: 400, overflowY: "auto", paddingRight: 4 }}>
      {Object.entries(tickers).map(([sector, cfg]) => (
        <div className="ticker-sector" key={sector}>
          <div className="ticker-sector-name">{sector} <span style={{ color: "var(--text-3)" }}>· ETF: {cfg.etf}</span></div>
          <div className="ticker-chips">
            {cfg.tickers.map(t => (
              <div className="ticker-chip" key={t}>
                {t}
                <button className="ticker-chip-remove" onClick={() => onRemove(sector, t)}>×</button>
              </div>
            ))}
          </div>
          <div className="ticker-add-row">
            <input className="ticker-add-input" placeholder="AAPL"
              value={inputs[sector] || ""}
              onChange={e => setInputs(p => ({ ...p, [sector]: e.target.value.toUpperCase() }))}
              onKeyDown={e => {
                if (e.key === "Enter" && inputs[sector]?.trim()) {
                  onAdd(sector, inputs[sector].trim());
                  setInputs(p => ({ ...p, [sector]: "" }));
                }
              }}
            />
            <button className="ticker-add-btn" onClick={() => {
              if (inputs[sector]?.trim()) {
                onAdd(sector, inputs[sector].trim());
                setInputs(p => ({ ...p, [sector]: "" }));
              }
            }}>+ Add</button>
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
            <div className="modal-sub" style={{ marginBottom: 16 }}>Changes take effect on the next gate cycle. No restart required.</div>
            <div className="settings-grid">
              <SettingsCol title="Demo" config={settings?.demo} accentColor="var(--text-2)" onSave={vals => onSave("demo", vals)} />
              <SettingsCol title="Live" config={settings?.live} accentColor="var(--green)"  onSave={vals => onSave("live", vals)} />
              <SectorThresholds fallback={settings?.demo?.lock1_threshold} />
            </div>
          </>
        )}
        {activeTab === "tickers" && (
          <>
            <div className="modal-sub" style={{ marginBottom: 16 }}>Add or remove tickers per sector. Changes apply on the next poll cycle.</div>
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
  lock1_threshold: "Lock 1 Threshold", lock2_sentiment_min: "Lock 2 Sentiment Min",
  lock3_confidence_min: "Lock 5 (Claude) Confidence Min", lock_leading_min_pass: "Lock 4 (Leading) Min Checks",
  max_drawdown_pct: "Max Drawdown Alert", take_profit_pct: "Take Profit %", stop_loss_pct: "Stop Loss %",
  profit_lock_trigger_pct: "Profit-Lock Trigger %",
  profit_lock_trail_pct: "Profit-Lock Trail %", max_positions: "Max Positions",
  overflow_quant_increment: "Overflow Quant Step", max_position_size: "Max Position Size",
  max_hold_days: "Time Stop (days)", vix_threshold: "VIX Block Threshold",
  macro_event_blackout_days: "CPI/NFP Pre-Event Block (days)", macro_earnings_blackout_days: "Earnings Hard Block (days)",
  macro_earnings_near_days: "Earnings Near-Term Window (days)", macro_earnings_near_penalty: "Earnings Near-Term Penalty",
  gate_cooloff_hours: "Gate Cooloff (hours)", max_sector_exposure: "Max Sector Exposure",
};

function PromoteModal({ config, onConfirm, onCancel }) {
  const { live, demo } = config;
  const pct = v => `${(v * 100).toFixed(1)}%`;
  const fmt = (key, v) => {
    if (key === "max_positions")  return v;
    if (typeof v === "number" && v < 2) return pct(v);
    return v;
  };
  return (
    <div className="modal-overlay" onClick={onCancel}>
      <div className="modal" onClick={e => e.stopPropagation()}>
        <div className="modal-title">Promote Demo Settings to Live</div>
        <div className="modal-sub">This will overwrite the live gate config with the current demo thresholds. Changes take effect on the next gate cycle.</div>
        <table className="modal-table">
          <thead><tr><th>Setting</th><th>Demo (source)</th><th>Live (after promote)</th></tr></thead>
          <tbody>
            {Object.keys(PROMOTE_LABELS).map(key => {
              const liveVal = live?.[key], demoVal = demo?.[key];
              const changed = liveVal !== demoVal;
              return (
                <tr key={key}>
                  <td>{PROMOTE_LABELS[key]}</td>
                  <td>{demoVal != null ? fmt(key, demoVal) : "—"}</td>
                  <td className={changed ? "changed" : ""}>{liveVal != null ? fmt(key, liveVal) : "—"}</td>
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

// ── Tweaks defaults ───────────────────────────────────────────────────────────

const TWEAK_DEFAULTS = {
  direction:       "terminal",
  density:         "comfortable",
  themeTerminal:   "dark",
  themeAtlas:      "dark",
  paletteTerminal: { value: "mint",  swatch: "#7ce0a1" },
  paletteAtlas:    { value: "ember", swatch: "#c8451f" },
  fontTerminal:    "mono",
  fontAtlas:       "serif",
  layout:          "regime-hero",
};

// ── App ───────────────────────────────────────────────────────────────────────

export default function App() {
  // ── Demo state ─────────────────────────────────────────────────────────────
  const [sectors,     setSectors]     = useState([]);
  const [wallet,      setWallet]      = useState(null);
  const [gateHist,    setGateHist]    = useState({ rows: [], funnel: null });
  const [equity,      setEquity]      = useState([]);
  const [regimeData,  setRegimeData]  = useState(null);
  const [driftAlerts, setDriftAlerts] = useState([]);
  const [demoTrades,  setDemoTrades]  = useState([]);

  // ── Live state ─────────────────────────────────────────────────────────────
  const [liveStatus,    setLiveStatus]    = useState(null);
  const [liveAccount,   setLiveAccount]   = useState(null);
  const [livePositions, setLivePositions] = useState([]);
  const [liveOrders,    setLiveOrders]    = useState([]);
  const [liveTrades,    setLiveTrades]    = useState([]);
  const [liveGateHist,  setLiveGateHist]  = useState({ rows: [], funnel: null });
  const [liveEquity,    setLiveEquity]    = useState([]);
  const [compareData,   setCompareData]   = useState(null);

  // ── UI state ────────────────────────────────────────────────────────────────
  const [settings,      setSettings]      = useState(null);
  const [tickers,       setTickers]       = useState(null);
  const [showSettings,  setShowSettings]  = useState(false);
  const [showPromote,   setShowPromote]   = useState(false);
  const [auditReports,   setAuditReports]   = useState([]);

  // ── Tweaks ──────────────────────────────────────────────────────────────────
  const [t, setTweak] = useTweaks(TWEAK_DEFAULTS);

  // ── Fetch ───────────────────────────────────────────────────────────────────
  function fetchDemoData() {
    fetchWithRetry("/api/sectors").then(d => setSectors(d || [])).catch(() => {});
    fetchWithRetry("/api/wallet").then(d => setWallet(d || null)).catch(() => {});
    fetchWithRetry("/api/gate/history").then(d => setGateHist(d || { rows: [], funnel: null })).catch(() => {});
    fetchWithRetry("/api/wallet/equity").then(d => setEquity(d || [])).catch(() => {});
    fetchWithRetry("/api/sectors/regime-bayes").then(d => setRegimeData(d || null)).catch(() => {});
    fetchWithRetry("/api/drift/alerts").then(d => setDriftAlerts(d?.alerts || [])).catch(() => {});
    fetchWithRetry("/api/demo/trades").then(d => setDemoTrades(Array.isArray(d) ? d : d?.trades || [])).catch(() => {});
  }

  function fetchLiveData() {
    fetchWithRetry("/api/live/status").then(d => setLiveStatus(d)).catch(() => setLiveStatus({ enabled: false, connected: false }));
    fetchWithRetry("/api/live/account").then(d => setLiveAccount(d)).catch(() => {});
    fetchWithRetry("/api/live/positions").then(d => setLivePositions(d || [])).catch(() => {});
    fetchWithRetry("/api/live/orders").then(d => setLiveOrders(d || [])).catch(() => {});
    fetchWithRetry("/api/live/trades").then(d => setLiveTrades(d || [])).catch(() => {});
    fetchWithRetry("/api/live/gate/history").then(d => setLiveGateHist(d || { rows: [], funnel: null })).catch(() => {});
    fetchWithRetry("/api/live/equity").then(d => setLiveEquity(d || [])).catch(() => {});
    fetchWithRetry("/api/live/compare").then(d => setCompareData(d || null)).catch(() => {});
  }

  function fetchSettings() {
    fetchWithRetry("/api/settings").then(d => setSettings(d || null)).catch(() => {});
    fetchWithRetry("/api/tickers").then(d => setTickers(d || null)).catch(() => {});
  }

  function fetchAuditReports() {
    fetch("/api/audit/reports").then(r => r.json()).then(d => setAuditReports(d.reports || [])).catch(() => {});
  }

  useEffect(() => {
    fetchDemoData();
    fetchLiveData();
    fetchSettings();
    fetchAuditReports();
    const iv = setInterval(() => { fetchDemoData(); fetchLiveData(); }, 30_000);
    return () => clearInterval(iv);
  }, []);

  // ── Adapted data ────────────────────────────────────────────────────────────
  const adaptedData = useMemo(() => {
    const eq     = adaptEquity(equity,     wallet?.balance);
    const liveEq = adaptEquity(liveEquity, liveAccount?.equity);
    return {
      SECTORS:       buildSECTORS(sectors),
      EXCLUDED:      EXCLUDED_CODES,
      tickers:       adaptTickers(sectors),
      regime:        adaptRegime(regimeData),
      regimeDate:    regimeData?.date ?? null,
      equity:        eq,
      liveEq,
      funnel:        adaptFunnel(gateHist.funnel),
      gateRows:      adaptGateRows(gateHist.rows),
      liveGateRows:    adaptGateRows(liveGateHist.rows),
      liveGateBlocked: liveGateHist.blocked_reason || null,
      positions:     adaptPositions(wallet?.open_positions ?? []),
      livePositions: adaptPositions(livePositions),
      wallet:        adaptWallet(wallet, eq),
      liveWallet:    adaptLiveWallet(liveAccount, livePositions, settings, liveEq),
      alerts:        adaptAlerts(driftAlerts),
      demoTrades,
      liveTrades,
      settings,
      auditReports,
      liveOrders,
    };
  }, [sectors, wallet, gateHist, liveGateHist, equity, regimeData, driftAlerts, liveEquity, liveAccount, livePositions, settings, demoTrades, liveTrades, auditReports, liveOrders]);

  // ── Tweaks helpers ──────────────────────────────────────────────────────────
  const unwrap = v => (v && typeof v === "object" && "value" in v) ? v.value : v;
  const marketOpen = isMarketOpen();

  const tTerminal = {
    density: t.density,
    theme:   t.themeTerminal,
    palette: unwrap(t.paletteTerminal),
    font:    t.fontTerminal,
    layout:  t.layout,
  };
  const tAtlas = {
    density: t.density,
    theme:   t.themeAtlas,
    palette: unwrap(t.paletteAtlas),
    font:    t.fontAtlas,
    layout:  t.layout,
  };

  const supplementalPanels = (
    <div className="supp-grid">
      <ErrorBoundary label="Rotation Forecast"><RotationForecast /></ErrorBoundary>
      <ErrorBoundary label="Sector Rotation"><SectorRotation /></ErrorBoundary>
    </div>
  );

  return (
    <>
      <style>{CSS}</style>

      {/* ── Main dashboard ──────────────────────────────────────────────── */}
      {/* TODO: remove Atlas entirely — Terminal is the only design going forward */}
      {t.direction === "terminal" ? (
        <ErrorBoundary label="Terminal">
          <ApexTerminal
            tweaks={tTerminal}
            data={adaptedData}
            marketOpen={marketOpen}
            onSettings={() => setShowSettings(true)}
            onPromote={liveStatus?.enabled ? () => setShowPromote(true) : null}
            supplemental={supplementalPanels}
          />
        </ErrorBoundary>
      ) : (
        <ErrorBoundary label="Atlas">
          <ApexAtlas
            tweaks={tAtlas}
            data={adaptedData}
            marketOpen={marketOpen}
            onSettings={() => setShowSettings(true)}
            onPromote={liveStatus?.enabled ? () => setShowPromote(true) : null}
            supplemental={supplementalPanels}
          />
        </ErrorBoundary>
      )}

      {/* ── Tweaks panel ────────────────────────────────────────────────── */}
      {/* TODO: remove TweaksPanel entirely — direction/Atlas toggle is dead, remaining knobs (layout, density, theme) should move into Settings modal */}
      <TweaksPanel
        title="Tweaks"
        onSettings={() => setShowSettings(true)}
        onPromote={liveStatus?.enabled ? () => setShowPromote(true) : null}
      >
        <TweakSection title="Direction">
          <TweakRadio label="Design" value={t.direction}
            onChange={v => setTweak("direction", v)}
            options={[{value:"terminal",label:"Terminal"},{value:"atlas",label:"Atlas"}]} />
        </TweakSection>

        <TweakSection title="Layout">
          <TweakSelect label="Hero panel" value={t.layout}
            onChange={v => setTweak("layout", v)}
            options={[
              {value:"equity-hero",  label:"Equity curve"},
              {value:"regime-hero",  label:"Sector regime"},
              {value:"funnel-hero",  label:"Gate funnel"},
            ]} />
          <TweakRadio label="Density" value={t.density}
            onChange={v => setTweak("density", v)}
            options={[{value:"comfortable",label:"Comfortable"},{value:"compact",label:"Compact"}]} />
        </TweakSection>

        <TweakSection title="A · Terminal">
          <TweakRadio label="Theme" value={t.themeTerminal}
            onChange={v => setTweak("themeTerminal", v)}
            options={[{value:"dark",label:"Dark"},{value:"light",label:"Light"}]} />
          <TweakColor label="Accent" value={t.paletteTerminal}
            onChange={v => setTweak("paletteTerminal", v)}
            options={[
              {value:"neon",  swatch:"#faff69"},
              {value:"cyan",  swatch:"#6ce4ff"},
              {value:"ember", swatch:"#ff8a3d"},
              {value:"mint",  swatch:"#7ce0a1"},
            ]} />
          <TweakRadio label="Type" value={t.fontTerminal}
            onChange={v => setTweak("fontTerminal", v)}
            options={[{value:"mono",label:"Mono+Sans"},{value:"sans",label:"All sans"}]} />
        </TweakSection>

        <TweakSection title="B · Atlas">
          <TweakRadio label="Theme" value={t.themeAtlas}
            onChange={v => setTweak("themeAtlas", v)}
            options={[{value:"light",label:"Paper"},{value:"dark",label:"Ink"}]} />
          <TweakColor label="Accent" value={t.paletteAtlas}
            onChange={v => setTweak("paletteAtlas", v)}
            options={[
              {value:"ink",    swatch:"#b8470f"},
              {value:"oxide",  swatch:"#1d6a55"},
              {value:"cobalt", swatch:"#244aa3"},
              {value:"ember",  swatch:"#c8451f"},
            ]} />
          <TweakRadio label="Type" value={t.fontAtlas}
            onChange={v => setTweak("fontAtlas", v)}
            options={[{value:"serif",label:"Serif+Mono"},{value:"sans",label:"All sans"}]} />
        </TweakSection>
      </TweaksPanel>

      {/* ── Modals ──────────────────────────────────────────────────────── */}
      {showSettings && (
        <SettingsModal
          settings={settings}
          tickers={tickers}
          onSave={(side, updates) => {
            fetch(`/api/settings/${side}`, {
              method: "POST", headers: { "Content-Type": "application/json" },
              body: JSON.stringify(updates),
            }).then(() => fetchSettings());
          }}
          onTickerAdd={(sector, ticker) =>
            fetch(`/api/tickers/${sector}/add?ticker=${ticker}`, { method: "POST" }).then(() => fetchDemoData())
          }
          onTickerRemove={(sector, ticker) =>
            fetch(`/api/tickers/${sector}/remove?ticker=${ticker}`, { method: "POST" }).then(() => fetchDemoData())
          }
          onClose={() => setShowSettings(false)}
        />
      )}

      {showPromote && settings && (
        <PromoteModal
          config={settings}
          onConfirm={async () => {
            await fetch("/api/live/config/promote", { method: "POST" });
            setShowPromote(false);
            fetchLiveData();
          }}
          onCancel={() => setShowPromote(false)}
        />
      )}
    </>
  );
}
