import { useState, useMemo } from "react";
import { COMPANY_NAMES } from "../companyNames.js";

const REASON_COLOR = {
  TP:  "var(--green)",
  SL:  "var(--red)",
  TSL: "#e8a020",
};

function fmtDate(iso) {
  if (!iso) return "—";
  return iso.slice(0, 10);
}

export default function DemoTradeLog({ trades = [] }) {
  const [open, setOpen] = useState(false);

  const sorted = useMemo(() => {
    const open   = trades.filter(t => !t.outcome || t.outcome === "OPEN");
    const closed = trades.filter(t =>  t.outcome && t.outcome !== "OPEN")
                         .sort((a, b) => (b.timestamp ?? "") < (a.timestamp ?? "") ? -1 : 1);
    return [...open, ...closed];
  }, [trades]);

  const wins   = trades.filter(t => t.outcome === "WIN").length;
  const losses = trades.filter(t => t.outcome === "LOSS").length;
  const openN  = trades.filter(t => !t.outcome || t.outcome === "OPEN").length;

  return (
    <div className="card" style={{ marginTop: 16 }}>
      <div className="card-header" onClick={() => setOpen(v => !v)} style={{ cursor: "pointer", userSelect: "none" }}>
        <div className="card-title">Demo Trade Log</div>
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
          <p className="muted">Trades will appear here once the demo gate queues its first position.</p>
        </div>
      )}

      {open && trades.length > 0 && sorted.map((t, i) => {
          const win      = t.outcome === "WIN";
          const loss     = t.outcome === "LOSS";
          const isOpen   = !t.outcome || t.outcome === "OPEN";
          const pnlColor = win ? "var(--green)" : loss ? "var(--red)" : "var(--text-3)";
          const pnlPct   = t.price_exit && t.price
            ? (t.price_exit - t.price) / t.price
            : null;

          return (
            <div key={i} style={{
              display: "flex", alignItems: "center", justifyContent: "space-between",
              padding: "10px 16px", borderTop: "1px solid var(--border)",
            }}>
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
                  {t.price != null ? `$${t.price.toFixed(2)}` : "—"}
                  {t.price_exit != null && <span> → ${t.price_exit.toFixed(2)}</span>}
                </div>
              </div>
            </div>
          );
        })}
    </div>
  );
}
