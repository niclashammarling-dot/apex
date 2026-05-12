import { useState } from "react";
import { COMPANY_NAMES } from "../companyNames.js";
import HoverTooltip, { TipRow, TipHeader } from "./HoverTooltip";

function PositionRow({ p }) {
  const [tip, setTip] = useState(null);
  const pnlPct = p.unrealized_pct ?? 0;
  const sign   = pnlPct >= 0 ? "+" : "";
  const color  = pnlPct >= 0 ? "var(--green)" : "var(--red)";
  const value  = p.current_price != null ? p.shares * p.current_price : p.amount;
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
        <TipRow label="Entry Price"    value={p.price != null ? `$${p.price.toFixed(2)}` : "—"} />
        <TipRow label="Current Price"  value={p.current_price != null ? `$${p.current_price.toFixed(2)}` : "—"} />
        {p.shares != null && <TipRow label="Shares" value={p.shares} />}
        <TipRow label="Days Held"      value={p.days_held != null ? `${p.days_held}d` : "—"} />
        <TipRow label="Unrealized P&L" value={`${sign}${(pnlPct * 100).toFixed(2)}%`} color={color} />
        <TipRow label="Position Value" value={`$${value.toFixed(2)}`} />
      </HoverTooltip>
      <div>
        <div style={{ fontFamily: "'Inconsolata', monospace", fontSize: 16, color: "var(--text-1)", fontWeight: 700 }}>
          {p.ticker}
        </div>
        <div style={{ fontFamily: "'Inconsolata', monospace", fontSize: 12, color: "var(--text-3)", marginTop: 3 }}>
          ${p.price.toFixed(2)} → {p.current_price != null ? `$${p.current_price.toFixed(2)}` : "—"}
          <span style={{ marginLeft: 8 }}>{p.days_held}d</span>
        </div>
      </div>
      <div style={{ textAlign: "right" }}>
        <div style={{ fontFamily: "'Inconsolata', monospace", fontSize: 18, color, fontWeight: 700 }}>
          {sign}{(pnlPct * 100).toFixed(2)}%
        </div>
        <div style={{ fontFamily: "'Inconsolata', monospace", fontSize: 12, color: "var(--text-3)", marginTop: 3 }}>
          ${value.toFixed(2)}
        </div>
      </div>
    </div>
  );
}

export default function DemoPositions({ positions }) {
  return (
    <div className="card">
      <div className="card-header">
        <div className="card-title">Open Positions</div>
        <div className="card-meta">{positions.length} open</div>
      </div>
      {positions.length === 0 ? (
        <div className="card-body"><p className="muted">No open positions.</p></div>
      ) : (
        positions.map(p => <PositionRow key={p.id} p={p} />)
      )}
    </div>
  );
}
