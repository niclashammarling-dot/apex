import { useState } from "react";
import HoverTooltip, { TipRow, TipHeader } from "./HoverTooltip";

const STATUS_META = {
  filled:           { label: "FILLED",   color: "var(--t-accent)" },
  partially_filled: { label: "PARTIAL",  color: "var(--t-amber)" },
  canceled:         { label: "CANCELED", color: "var(--t-text-3)" },
  cancelled:        { label: "CANCELED", color: "var(--t-text-3)" },
  expired:          { label: "EXPIRED",  color: "var(--t-amber)" },
  new:              { label: "NEW",      color: "var(--t-text-3)" },
  accepted:         { label: "ACCEPTED", color: "var(--t-text-3)" },
  pending_new:      { label: "PENDING",  color: "var(--t-text-3)" },
};

function OrderRow({ o }) {
  const [tip, setTip] = useState(null);
  const meta = STATUS_META[o.status?.toLowerCase()] ?? { label: o.status, color: "var(--t-text-3)" };
  const sideColor = o.side?.includes("buy") ? "var(--t-accent)" : "var(--t-red)";
  const ts = o.submitted_at
    ? new Date(o.submitted_at).toLocaleString("en-US", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false })
    : "—";

  return (
    <div
      onMouseMove={e => setTip({ x: e.clientX, y: e.clientY })}
      onMouseLeave={() => setTip(null)}
      style={{ display: "flex", alignItems: "center", gap: 10, borderTop: "1px solid var(--t-border)", padding: "8px 14px" }}
    >
      <HoverTooltip pos={tip}>
        <TipHeader>{o.side?.toUpperCase()} {o.ticker}</TipHeader>
        <TipRow label="Qty"          value={o.filled_qty ?? o.qty ?? "—"} />
        <TipRow label="Submitted"    value={ts} />
        <TipRow label="Filled Price" value={o.filled_price ? `$${parseFloat(o.filled_price).toFixed(2)}` : "—"} />
        <TipRow label="Status"       value={meta.label} color={meta.color} />
        <TipRow label="Order ID"     value={o.id ?? "—"} />
      </HoverTooltip>

      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontFamily: "var(--mono)", fontSize: 11, fontWeight: 700, color: sideColor, letterSpacing: "0.06em" }}>
            {o.side?.toUpperCase()}
          </span>
          <span style={{ fontFamily: "var(--mono)", fontSize: 13, color: "var(--t-text-1)", fontWeight: 700 }}>
            {o.ticker}
          </span>
          <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--t-text-3)" }}>
            {(o.filled_qty ?? o.qty ?? "—")} shares
          </span>
        </div>
        <div style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--t-text-3)", marginTop: 2 }}>{ts}</div>
      </div>

      <div style={{ textAlign: "right", flexShrink: 0 }}>
        <div style={{ fontFamily: "var(--mono)", fontSize: 13, color: "var(--t-text-1)", fontWeight: 600 }}>
          {o.filled_price ? `$${parseFloat(o.filled_price).toFixed(2)}` : "—"}
        </div>
        <div style={{ marginTop: 2 }}>
          <span className="t-pill" style={{ color: meta.color, borderColor: `${meta.color}44`, fontSize: 9 }}>
            {meta.label}
          </span>
        </div>
      </div>
    </div>
  );
}

export default function LiveOrderLog({ orders = [] }) {
  const [open, setOpen] = useState(false);

  return (
    <div className="t-card">
      <div className="t-card-head" style={{ cursor: "pointer", userSelect: "none" }} onClick={() => setOpen(v => !v)}>
        <div className="t-card-title">ORDER HISTORY · LIVE</div>
        <div className="t-row" style={{ gap: 10 }}>
          <span className="t-meta">{orders.length} ORDERS</span>
          <span className="t-meta">{open ? "▲" : "▼"}</span>
        </div>
      </div>

      {open && orders.length === 0 && (
        <div className="t-card-body" style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--t-text-3)" }}>
          No orders yet.
        </div>
      )}

      {open && orders.map(o => <OrderRow key={o.id} o={o} />)}
    </div>
  );
}
