import { useState } from "react";
import { COMPANY_NAMES } from "../companyNames.js";
import HoverTooltip, { TipRow, TipHeader } from "./HoverTooltip";

function PositionRow({ p }) {
  const [tip, setTip] = useState(null);
  const pnlPct = p.unrealized_pct ?? 0;
  const sign   = pnlPct >= 0 ? "+" : "";
  const color  = pnlPct >= 0 ? "var(--green)" : "var(--red)";
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
        <TipHeader>{p.ticker}{COMPANY_NAMES[p.ticker] ? ` — ${COMPANY_NAMES[p.ticker]}` : ""}</TipHeader>
        <TipRow label="Avg Entry"      value={p.avg_entry_price != null ? `$${p.avg_entry_price.toFixed(2)}` : "—"} />
        <TipRow label="Current"        value={p.current_price != null ? `$${p.current_price.toFixed(2)}` : "—"} />
        {p.days_held != null && <TipRow label="Days Held" value={`${p.days_held}d`} />}
        <TipRow label="Unrealized P&L" value={`${sign}${(pnlPct * 100).toFixed(2)}%`} color={color} />
        {p.market_value != null && <TipRow label="Market Value" value={`$${p.market_value.toFixed(2)}`} />}
      </HoverTooltip>
      <div>
        <div style={{ fontFamily: "'Inconsolata', monospace", fontSize: 16, color: "var(--text-1)", fontWeight: 700 }}>
          {p.ticker}
        </div>
        <div style={{ fontFamily: "'Inconsolata', monospace", fontSize: 12, color: "var(--text-3)", marginTop: 3 }}>
          ${p.avg_entry_price.toFixed(2)} → {p.current_price ? `$${p.current_price.toFixed(2)}` : "—"}
          {p.days_held != null && <span style={{ marginLeft: 8 }}>{p.days_held}d</span>}
        </div>
      </div>
      <div style={{ textAlign: "right" }}>
        <div style={{ fontFamily: "'Inconsolata', monospace", fontSize: 18, color, fontWeight: 700 }}>
          {sign}{(pnlPct * 100).toFixed(2)}%
        </div>
        {p.market_value != null && (
          <div style={{ fontFamily: "'Inconsolata', monospace", fontSize: 12, color: "var(--text-3)", marginTop: 3 }}>
            ${p.market_value.toFixed(2)}
          </div>
        )}
      </div>
    </div>
  );
}

export default function LivePositions({ positions }) {
  return (
    <div className="card">
      <div className="card-header">
        <div className="card-title">Open Positions</div>
        <div className="card-meta">{positions.length} open</div>
      </div>
      {positions.length === 0 ? (
        <div className="card-body"><p className="muted">No open positions.</p></div>
      ) : (
        positions.map(p => <PositionRow key={p.ticker} p={p} />)
      )}
    </div>
  );
}
