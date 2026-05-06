import { useState } from "react";

const STATUS_META = {
  filled:           { label: "FILLED",   color: "var(--green)",  bg: "#1e1e00" },
  partially_filled: { label: "PARTIAL",  color: "#faff69",       bg: "#202000" },
  canceled:         { label: "CANCELED", color: "var(--text-3)", bg: "transparent" },
  cancelled:        { label: "CANCELED", color: "var(--text-3)", bg: "transparent" },
  expired:          { label: "EXPIRED",  color: "#d4a020",       bg: "#2a1e00" },
  new:              { label: "NEW",      color: "var(--text-3)", bg: "transparent" },
  accepted:         { label: "ACCEPTED", color: "var(--text-3)", bg: "transparent" },
  pending_new:      { label: "PENDING",  color: "var(--text-3)", bg: "transparent" },
};

export default function LiveOrderLog({ orders }) {
  const [open, setOpen] = useState(false);

  return (
    <div className="card" style={{ marginTop: 16 }}>
      <div className="card-header" onClick={() => setOpen(v => !v)} style={{ cursor: "pointer", userSelect: "none" }}>
        <div className="card-title">Order History</div>
        <div className="card-meta" style={{ display: "flex", gap: 10, alignItems: "center" }}>
          <span style={{ color: "var(--text-3)" }}>{orders.length} orders</span>
          <span style={{ fontSize: 11, color: "var(--text-3)" }}>{open ? "▲" : "▼"}</span>
        </div>
      </div>

      {open && orders.length === 0 && (
        <div className="card-body"><p className="muted">No orders yet.</p></div>
      )}

      {open && orders.length > 0 && orders.map(o => {
        const meta = STATUS_META[o.status?.toLowerCase()] ?? { label: o.status, color: "var(--text-3)", bg: "transparent" };
        const sideColor = o.side?.includes("buy") ? "var(--green)" : "var(--red)";
        const ts = o.submitted_at
          ? new Date(o.submitted_at).toLocaleString("en-US", {
              month: "2-digit", day: "2-digit",
              hour: "2-digit", minute: "2-digit", hour12: false,
            })
          : "—";

        return (
          <div key={o.id} style={{
            display: "flex", alignItems: "center", justifyContent: "space-between",
            padding: "10px 16px", borderTop: "1px solid var(--border)",
          }}>
            <div>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <span style={{
                  fontFamily: "'Inconsolata', monospace", fontSize: 11, fontWeight: 700,
                  color: sideColor, letterSpacing: "0.06em",
                }}>
                  {o.side?.toUpperCase()}
                </span>
                <span style={{ fontFamily: "'Inconsolata', monospace", fontSize: 15, color: "var(--text-1)", fontWeight: 700 }}>
                  {o.ticker}
                </span>
                <span style={{ fontFamily: "'Inconsolata', monospace", fontSize: 11, color: "var(--text-3)" }}>
                  {(o.filled_qty ?? o.qty ?? "—")} shares
                </span>
              </div>
              <div style={{ fontFamily: "'Inconsolata', monospace", fontSize: 11, color: "var(--text-3)", marginTop: 3 }}>
                {ts}
              </div>
            </div>

            <div style={{ textAlign: "right" }}>
              <div style={{ fontFamily: "'Inconsolata', monospace", fontSize: 15, color: "var(--text-1)", fontWeight: 600 }}>
                {o.filled_price ? `$${parseFloat(o.filled_price).toFixed(2)}` : "—"}
              </div>
              <div style={{ marginTop: 3 }}>
                <span style={{
                  fontFamily: "'Inconsolata', monospace", fontSize: 10, fontWeight: 700,
                  color: meta.color, background: meta.bg,
                  borderRadius: 3, padding: "1px 6px", letterSpacing: "0.04em",
                }}>
                  {meta.label}
                </span>
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
