import { useState, useMemo } from "react";
import { COMPANY_NAMES } from "../companyNames.js";
import HoverTooltip, { TipRow, TipHeader } from "./HoverTooltip";

const REASON_COLOR = {
  TP:  "var(--green)",
  SL:  "var(--red)",
  TSL: "#e8a020",
};

function fmtDate(iso) {
  if (!iso) return "—";
  return iso.slice(0, 10);
}

function TradeRow({ t, i }) {
  const [tip, setTip] = useState(null);
  const win    = t.outcome === "WIN";
  const loss   = t.outcome === "LOSS";
  const isOpen = !t.outcome;
  const pnlColor = win ? "var(--green)" : loss ? "var(--red)" : "var(--text-3)";
  const pnlPct   = t.exit_price && t.entry_price
    ? (t.exit_price - t.entry_price) / t.entry_price
    : t.pnl_pct ?? null;
  return (
    <div
      onMouseMove={e => setTip({ x: e.clientX, y: e.clientY })}
      onMouseLeave={() => setTip(null)}
      style={{
        display: "flex", alignItems: "center", justifyContent: "space-between",
        padding: "10px 16px", borderTop: "1px solid var(--border)",
      }}
    >
      <HoverTooltip pos={tip}>
        <TipHeader>{t.ticker}{COMPANY_NAMES[t.ticker] ? ` — ${COMPANY_NAMES[t.ticker]}` : ""}</TipHeader>
        <TipRow label="Sector"      value={t.sector ?? "—"} />
        <TipRow label="Entry"       value={fmtDate(t.timestamp)} />
        <TipRow label="Exit"        value={t.timestamp_exit ? fmtDate(t.timestamp_exit) : isOpen ? "open" : "—"} />
        <TipRow label="Entry Price" value={t.entry_price != null ? `$${t.entry_price.toFixed(2)}` : "—"} />
        <TipRow label="Exit Price"  value={t.exit_price != null ? `$${t.exit_price.toFixed(2)}` : "—"} />
        {t.exit_reason && <TipRow label="Exit Reason" value={t.exit_reason} color={REASON_COLOR[t.exit_reason] ?? "var(--text-3)"} />}
        <TipRow label="P&L %"       value={pnlPct != null ? `${pnlPct >= 0 ? "+" : ""}${(pnlPct * 100).toFixed(2)}%` : isOpen ? "open" : "—"} color={pnlColor} />
      </HoverTooltip>
      <div>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontFamily: "'Inconsolata', monospace", fontSize: 15, color: "var(--text-1)", fontWeight: 700 }}>
            {t.ticker}
          </span>
          {COMPANY_NAMES[t.ticker] && (
            <span style={{ fontFamily: "'Inconsolata', monospace", fontSize: 11, color: "var(--text-3)" }}>
              {COMPANY_NAMES[t.ticker]}
            </span>
          )}
        </div>
        <div style={{ fontFamily: "'Inconsolata', monospace", fontSize: 11, color: "var(--text-3)", marginTop: 3 }}>
          {fmtDate(t.timestamp)}
          {t.sector && <span style={{ marginLeft: 8 }}>{t.sector}</span>}
          {t.exit_reason && (
            <span style={{ marginLeft: 8, color: REASON_COLOR[t.exit_reason] ?? "var(--text-3)" }}>
              {t.exit_reason}
            </span>
          )}
        </div>
      </div>

      <div style={{ textAlign: "right" }}>
        {isOpen ? (
          <div style={{ fontFamily: "'Inconsolata', monospace", fontSize: 13, color: "var(--text-3)", fontWeight: 600 }}>
            OPEN
          </div>
        ) : (
          <div style={{ fontFamily: "'Inconsolata', monospace", fontSize: 16, color: pnlColor, fontWeight: 700 }}>
            {pnlPct != null ? `${pnlPct >= 0 ? "+" : ""}${(pnlPct * 100).toFixed(2)}%` : "—"}
          </div>
        )}
        <div style={{ fontFamily: "'Inconsolata', monospace", fontSize: 11, color: "var(--text-3)", marginTop: 3 }}>
          {t.entry_price != null ? `$${t.entry_price.toFixed(2)}` : "—"}
          {t.exit_price != null && <span> → ${t.exit_price.toFixed(2)}</span>}
        </div>
      </div>
    </div>
  );
}

export default function LiveTradeLog({ trades = [] }) {
  const [open, setOpen] = useState(false);

  const sorted = useMemo(() => {
    const open   = trades.filter(t => !t.outcome);
    const closed = trades.filter(t =>  t.outcome)
                         .sort((a, b) => (b.timestamp ?? "") < (a.timestamp ?? "") ? -1 : 1);
    return [...open, ...closed];
  }, [trades]);

  const wins   = trades.filter(t => t.outcome === "WIN").length;
  const losses = trades.filter(t => t.outcome === "LOSS").length;
  const openN  = trades.filter(t => !t.outcome).length;

  return (
    <div className="card" style={{ marginTop: 16 }}>
      <div className="card-header" onClick={() => setOpen(v => !v)} style={{ cursor: "pointer", userSelect: "none" }}>
        <div className="card-title">Live Trade Log</div>
        <div className="card-meta" style={{ display: "flex", gap: 14, alignItems: "center" }}>
          {trades.length === 0 ? (
            <span style={{ color: "var(--text-3)" }}>no trades yet</span>
          ) : (
            <>
              <span style={{ color: "var(--green)" }}>{wins}W</span>
              <span style={{ color: "var(--red)" }}>{losses}L</span>
              {openN > 0 && <span style={{ color: "var(--text-3)" }}>{openN} open</span>}
            </>
          )}
          <span style={{ fontSize: 11, color: "var(--text-3)" }}>{open ? "▲" : "▼"}</span>
        </div>
      </div>

      {open && trades.length === 0 && (
        <div className="card-body">
          <p className="muted">Trades will appear here once live bracket orders are placed and filled.</p>
        </div>
      )}

      {open && trades.length > 0 && sorted.map((t, i) => <TradeRow key={i} t={t} i={i} />)}
    </div>
  );
}
