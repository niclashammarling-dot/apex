import { useState, useMemo } from "react";
import { COMPANY_NAMES } from "../companyNames.js";
import HoverTooltip, { TipRow, TipHeader } from "./HoverTooltip";

const REASON_COLOR = {
  TP:   "var(--green)",
  SL:   "var(--red)",
  TSL:  "#e8a020",
  TIME: "var(--text-3)",
  END_OF_BACKTEST: "var(--text-3)",
};

const COLS = [
  { col: "ticker",       label: "Ticker"  },
  { col: "sector",       label: "Sector"  },
  { col: "entry_date",   label: "Entry"   },
  { col: "exit_date",    label: "Exit"    },
  { col: "exit_reason",  label: "Reason"  },
  { col: "pnl_pct",      label: "P&L %"  },
  { col: "pnl",          label: "P&L $"  },
  { col: "signal_score", label: "Score"  },
];

function TradeRow({ t, i, sort }) {
  const [tip, setTip] = useState(null);
  const win  = t.outcome === "WIN";
  const loss = t.outcome === "LOSS";
  const pnlColor = win ? "var(--green)" : loss ? "var(--red)" : "var(--text-3)";
  return (
    <tr
      onMouseMove={e => setTip({ x: e.clientX, y: e.clientY })}
      onMouseLeave={() => setTip(null)}
      style={{ borderBottom: "1px solid #242422" }}
    >
      <HoverTooltip pos={tip}>
        <TipHeader>{t.ticker}{COMPANY_NAMES[t.ticker] ? ` — ${COMPANY_NAMES[t.ticker]}` : ""}</TipHeader>
        <TipRow label="Sector"       value={t.sector ?? "—"} />
        <TipRow label="Entry"        value={t.entry_date ?? "—"} />
        <TipRow label="Exit"         value={t.exit_date ?? "—"} />
        <TipRow label="Exit Reason"  value={t.exit_reason ?? "—"} color={REASON_COLOR[t.exit_reason] ?? "var(--text-3)"} />
        <TipRow label="P&L %"        value={t.pnl_pct != null ? `${t.pnl_pct >= 0 ? "+" : ""}${(t.pnl_pct * 100).toFixed(2)}%` : "—"} color={pnlColor} />
        <TipRow label="P&L $"        value={t.pnl != null ? `${t.pnl >= 0 ? "+" : ""}$${Math.abs(t.pnl).toFixed(2)}` : "—"} color={pnlColor} />
        <TipRow label="Signal Score" value={t.signal_score?.toFixed(3) ?? "—"} />
      </HoverTooltip>
      <td style={{ padding: "7px 14px" }}>
        <div style={{ color: "var(--text-1)", fontWeight: 500 }}>{t.ticker}</div>
        {COMPANY_NAMES[t.ticker] && (
          <div style={{ color: "var(--text-3)", fontSize: 10, marginTop: 1 }}>{COMPANY_NAMES[t.ticker]}</div>
        )}
      </td>
      <td style={{ padding: "7px 14px", color: "var(--text-3)" }}>{t.sector}</td>
      <td style={{ padding: "7px 14px", color: "var(--text-3)" }}>{t.entry_date}</td>
      <td style={{ padding: "7px 14px", color: "var(--text-3)" }}>{t.exit_date ?? "—"}</td>
      <td style={{ padding: "7px 14px", color: REASON_COLOR[t.exit_reason] ?? "var(--text-3)" }}>{t.exit_reason ?? "—"}</td>
      <td style={{ padding: "7px 14px", color: pnlColor }}>
        {t.pnl_pct != null ? `${t.pnl_pct >= 0 ? "+" : ""}${(t.pnl_pct * 100).toFixed(2)}%` : "—"}
      </td>
      <td style={{ padding: "7px 14px", color: pnlColor }}>
        {t.pnl != null ? `${t.pnl >= 0 ? "+" : ""}$${Math.abs(t.pnl).toFixed(2)}` : "—"}
      </td>
      <td style={{ padding: "7px 14px", color: "var(--text-3)" }}>{t.signal_score?.toFixed(3) ?? "—"}</td>
    </tr>
  );
}

export default function TradeLog({ result }) {
  const [sort, setSort] = useState({ col: "entry_date", dir: 1 });

  const trades = useMemo(() => {
    if (!result?.trade_log) return [];
    return [...result.trade_log].sort((a, b) => {
      const av = a[sort.col] ?? "";
      const bv = b[sort.col] ?? "";
      return av < bv ? -sort.dir : av > bv ? sort.dir : 0;
    });
  }, [result, sort]);

  if (!result?.trade_log?.length) return null;

  function toggleSort(col) {
    setSort(s => s.col === col ? { col, dir: -s.dir } : { col, dir: 1 });
  }

  const wins   = trades.filter(t => t.outcome === "WIN").length;
  const losses = trades.filter(t => t.outcome === "LOSS").length;
  const expired = trades.filter(t => t.outcome === "EXPIRED").length;

  return (
    <div className="card" style={{ marginTop: 16 }}>
      <div className="card-header">
        <div className="card-title">Trade Log</div>
        <div className="card-meta" style={{ display: "flex", gap: 16 }}>
          <span style={{ color: "var(--green)" }}>{wins} wins</span>
          <span style={{ color: "var(--red)" }}>{losses} losses</span>
          <span style={{ color: "var(--text-3)" }}>{expired} expired</span>
        </div>
      </div>
      <div className="card-body" style={{ padding: 0, overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontFamily: "'Inconsolata', monospace", fontSize: 12 }}>
          <thead>
            <tr style={{ borderBottom: "1px solid var(--border)" }}>
              {COLS.map(({ col, label }) => (
                <th
                  key={col}
                  onClick={() => toggleSort(col)}
                  style={{
                    textAlign: "left", padding: "8px 14px", cursor: "pointer",
                    color: sort.col === col ? "var(--text-2)" : "var(--text-3)",
                    fontWeight: 500, whiteSpace: "nowrap", userSelect: "none",
                    letterSpacing: "0.04em",
                  }}
                >
                  {label}{sort.col === col ? (sort.dir === 1 ? " ↑" : " ↓") : ""}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {trades.map((t, i) => <TradeRow key={i} t={t} i={i} sort={sort} />)}
          </tbody>
        </table>
      </div>
    </div>
  );
}
