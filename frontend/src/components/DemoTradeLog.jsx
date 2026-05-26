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

function TradeRow({ t }) {
  const [tip, setTip] = useState(null);
  const win    = t.outcome === "WIN";
  const loss   = t.outcome === "LOSS";
  const isOpen = !t.outcome || t.outcome === "OPEN";
  const pnlColor = win ? WIN_COLOR : loss ? LOSS_COLOR : "#76766f";
  const pnlPct = t.price_exit && t.price ? (t.price_exit - t.price) / t.price : null;

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
        <TipRow label="Entry Price" value={t.price != null ? `$${t.price.toFixed(2)}` : "—"} />
        <TipRow label="Exit Price"  value={t.price_exit != null ? `$${t.price_exit.toFixed(2)}` : "—"} />
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
          {t.price != null ? `$${t.price.toFixed(2)}` : "—"}
          {t.price_exit != null && <span> → ${t.price_exit.toFixed(2)}</span>}
        </div>
      </div>
    </div>
  );
}

export default function DemoTradeLog({ trades = [] }) {
  const [open, setOpen] = useState(false);

  const sorted = useMemo(() => {
    const openTrades   = trades.filter(t => !t.outcome || t.outcome === "OPEN");
    const closedTrades = trades.filter(t =>  t.outcome && t.outcome !== "OPEN")
                               .sort((a, b) => (b.timestamp ?? "") < (a.timestamp ?? "") ? -1 : 1);
    return [...openTrades, ...closedTrades];
  }, [trades]);

  const wins   = trades.filter(t => t.outcome === "WIN").length;
  const losses = trades.filter(t => t.outcome === "LOSS").length;
  const openN  = trades.filter(t => !t.outcome || t.outcome === "OPEN").length;

  return (
    <div className="t-card">
      <div className="t-card-head" style={{ cursor: "pointer", userSelect: "none" }} onClick={() => setOpen(v => !v)}>
        <div className="t-card-title">TRADE HISTORY · DEMO</div>
        <div className="t-row" style={{ gap: 10 }}>
          {trades.length === 0 ? (
            <span className="t-meta">NO TRADES YET</span>
          ) : (
            <>
              <span className="t-pos" style={{ fontFamily: "var(--mono)", fontSize: 11 }}>{wins}W</span>
              <span className="t-neg" style={{ fontFamily: "var(--mono)", fontSize: 11 }}>{losses}L</span>
              {openN > 0 && <span className="t-meta">{openN} OPEN</span>}
            </>
          )}
          <span className="t-meta">{open ? "▲" : "▼"}</span>
        </div>
      </div>

      {open && trades.length === 0 && (
        <div className="t-card-body" style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--t-text-3)" }}>
          Trades will appear once the demo gate queues its first position.
        </div>
      )}

      {open && sorted.map((t, i) => <TradeRow key={i} t={t} />)}
    </div>
  );
}
