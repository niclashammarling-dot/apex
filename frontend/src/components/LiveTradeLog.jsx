import { useState, useMemo } from "react";
import { COMPANY_NAMES } from "../companyNames.js";
import HoverTooltip, { TipRow, TipHeader } from "./HoverTooltip";

const WIN_COLOR  = "#4ade80";
const LOSS_COLOR = "#ff7575";

const REASON_COLOR = {
  TP:     "#faff69",
  SL:     "#ff7575",
  TSL:    "#e8a020",
  REGIME: "#6aa0f0",
};

function fmtDate(iso) {
  if (!iso) return "—";
  return iso.slice(0, 10);
}

// live_trades stores open rows as outcome "OPEN" (db.py), not null; both mean open.
const isOpenRow = t => !t.outcome || t.outcome === "OPEN";

function TradeRow({ t }) {
  const [tip, setTip] = useState(null);
  const win    = t.outcome === "WIN";
  const loss   = t.outcome === "LOSS";
  const isOpen = isOpenRow(t);
  const pnlColor = win ? WIN_COLOR : loss ? LOSS_COLOR : "#76766f";
  const pnlPct = t.exit_price && t.entry_price
    ? (t.exit_price - t.entry_price) / t.entry_price
    : t.pnl_pct ?? null;

  return (
    <div
      onMouseMove={e => setTip({ x: e.clientX, y: e.clientY })}
      onMouseLeave={() => setTip(null)}
      style={{ display: "flex", alignItems: "center", gap: 10, borderTop: "1px solid var(--t-border)", padding: "8px 14px" }}
    >
      <HoverTooltip pos={tip}>
        <TipHeader>{t.ticker}{COMPANY_NAMES[t.ticker] ? ` — ${COMPANY_NAMES[t.ticker]}` : ""}</TipHeader>
        <TipRow label="Sector"      value={t.sector ?? "—"} />
        <TipRow label="Entry"       value={fmtDate(t.timestamp)} />
        <TipRow label="Exit"        value={t.timestamp_exit ? fmtDate(t.timestamp_exit) : isOpen ? "open" : "—"} />
        <TipRow label="Entry Price" value={t.entry_price != null ? `$${t.entry_price.toFixed(2)}` : "—"} />
        <TipRow label="Exit Price"  value={t.exit_price != null ? `$${t.exit_price.toFixed(2)}` : "—"} />
        {t.exit_reason && <TipRow label="Exit Reason" value={t.exit_reason} color={REASON_COLOR[t.exit_reason] ?? "#76766f"} />}
        <TipRow label="P&L %" value={pnlPct != null ? `${pnlPct >= 0 ? "+" : ""}${(pnlPct * 100).toFixed(2)}%` : isOpen ? "open" : "—"} color={pnlColor} />
        <TipRow label="P&L $" value={t.pnl != null ? `${t.pnl >= 0 ? "+" : "-"}$${Math.abs(t.pnl).toFixed(2)}` : isOpen ? "open" : "—"} color={pnlColor} />
      </HoverTooltip>

      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontFamily: "var(--mono)", fontSize: 13, color: "var(--t-text-1)", fontWeight: 700 }}>{t.ticker}</span>
          {COMPANY_NAMES[t.ticker] && (
            <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--t-text-3)" }}>{COMPANY_NAMES[t.ticker]}</span>
          )}
        </div>
        <div style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--t-text-3)", marginTop: 2 }}>
          {fmtDate(t.timestamp)}
          {t.sector && <span style={{ marginLeft: 8 }}>{t.sector}</span>}
          {t.exit_reason && <span style={{ marginLeft: 8, color: REASON_COLOR[t.exit_reason] ?? "var(--t-text-3)" }}>{t.exit_reason}</span>}
        </div>
      </div>

      <div style={{ textAlign: "right", flexShrink: 0 }}>
        {isOpen ? (
          <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--t-text-3)" }}>OPEN</span>
        ) : (
          <>
            <span style={{ fontFamily: "var(--mono)", fontSize: 13, color: pnlColor, fontWeight: 700 }}>
              {pnlPct != null ? `${pnlPct >= 0 ? "+" : ""}${(pnlPct * 100).toFixed(2)}%` : "—"}
            </span>
            {t.pnl != null && (
              <div style={{ fontFamily: "var(--mono)", fontSize: 10, color: pnlColor, marginTop: 1 }}>
                {t.pnl >= 0 ? "+" : "-"}${Math.abs(t.pnl).toFixed(2)}
              </div>
            )}
          </>
        )}
        <div style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--t-text-3)", marginTop: 2 }}>
          {t.entry_price != null ? `$${t.entry_price.toFixed(2)}` : "—"}
          {t.exit_price != null && <span> → ${t.exit_price.toFixed(2)}</span>}
        </div>
      </div>
    </div>
  );
}

// Rows and counts cover the canonical live sample only (Niclas 2026-10-21: "go with
// only after the reset"): rows from stats.since (live_account_since), and the W/L
// header from live_trade_stats, the same set the WALLET card's win rate reads. Before
// this the header counted every row since 2026-04-06 (54W 34L against the wallet's 37/57).
export default function LiveTradeLog({ trades = [], stats = null }) {
  const [open, setOpen] = useState(false);
  const since = stats?.since ?? null;

  const shown = useMemo(
    () => since ? trades.filter(t => isOpenRow(t) || (t.timestamp ?? "") >= since) : trades,
    [trades, since],
  );

  const sorted = useMemo(() => {
    const openTrades   = shown.filter(isOpenRow);
    const closedTrades = shown.filter(t => !isOpenRow(t))
                              .sort((a, b) => (b.timestamp ?? "") < (a.timestamp ?? "") ? -1 : 1);
    return [...openTrades, ...closedTrades];
  }, [shown]);

  const o      = stats?.overall;
  const wins   = o ? o.wins : null;
  const losses = o ? o.trades - o.wins : null;
  const openN  = shown.filter(isOpenRow).length;

  return (
    <div className="t-card">
      <div className="t-card-head" style={{ cursor: "pointer", userSelect: "none" }} onClick={() => setOpen(v => !v)}>
        <div className="t-card-title">TRADE HISTORY · LIVE</div>
        <div className="t-row" style={{ gap: 10 }}>
          {shown.length === 0 ? (
            <span className="t-meta">NO TRADES YET</span>
          ) : (
            <>
              {o ? (
                <>
                  <span className="t-pos" style={{ fontFamily: "var(--mono)", fontSize: 11 }}>{wins}W</span>
                  <span className="t-neg" style={{ fontFamily: "var(--mono)", fontSize: 11 }}>{losses}L</span>
                  <span className="t-meta">SINCE {since}</span>
                </>
              ) : (
                <span className="t-meta">STATS UNAVAILABLE</span>
              )}
              {openN > 0 && <span className="t-meta">{openN} OPEN</span>}
            </>
          )}
          <span className="t-meta">{open ? "▲" : "▼"}</span>
        </div>
      </div>

      {open && shown.length === 0 && (
        <div className="t-card-body" style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--t-text-3)" }}>
          Trades will appear once live bracket orders are placed and filled.
        </div>
      )}

      {open && sorted.map((t, i) => <TradeRow key={i} t={t} />)}
    </div>
  );
}
