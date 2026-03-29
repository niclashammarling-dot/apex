const STATUS_STYLE = {
  filled:           { background: "#0e2a1a", color: "var(--green)" },
  canceled:         { background: "#1e2030", color: "var(--text-3)" },
  cancelled:        { background: "#1e2030", color: "var(--text-3)" },
  partially_filled: { background: "#1a2800", color: "#a0c040" },
  new:              { background: "#0e1e38", color: "#6aa0f0" },
  accepted:         { background: "#0e1e38", color: "#6aa0f0" },
  pending_new:      { background: "#0e1e38", color: "#6aa0f0" },
  expired:          { background: "#2a1e00", color: "#d4a020" },
};

function statusBadge(status) {
  const style = STATUS_STYLE[status?.toLowerCase()] ?? { background: "#1e2030", color: "var(--text-3)" };
  return (
    <span style={{
      ...style,
      display: "inline-block",
      padding: "2px 8px",
      borderRadius: 4,
      fontSize: 11,
      fontWeight: 600,
      letterSpacing: "0.04em",
      textTransform: "uppercase",
    }}>
      {status}
    </span>
  );
}

export default function LiveOrderLog({ orders }) {
  return (
    <div className="card">
      <div className="card-header">
        <div className="card-title">Order History</div>
        <div className="card-meta">{orders.length} orders</div>
      </div>
      {orders.length === 0 ? (
        <div className="card-body"><p className="muted">No orders yet.</p></div>
      ) : (
        <table className="feed-table">
          <thead>
            <tr>
              <th>Time</th>
              <th>Ticker</th>
              <th>Side</th>
              <th>Qty</th>
              <th>Fill Price</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {orders.map(o => {
              const ts = o.submitted_at
                ? new Date(o.submitted_at).toLocaleString("en-US", {
                    month: "2-digit", day: "2-digit",
                    hour: "2-digit", minute: "2-digit", hour12: false,
                  })
                : "—";
              const sideColor = o.side?.includes("buy") ? "var(--green)" : "var(--red)";
              return (
                <tr key={o.id}>
                  <td style={{ fontFamily: "'DM Mono', monospace", fontSize: 12 }}>{ts}</td>
                  <td><strong>{o.ticker}</strong></td>
                  <td style={{ color: sideColor, textTransform: "uppercase", fontSize: 12 }}>{o.side}</td>
                  <td>{o.filled_qty ?? o.qty ?? "—"}</td>
                  <td>{o.filled_price ? `$${parseFloat(o.filled_price).toFixed(2)}` : "—"}</td>
                  <td>{statusBadge(o.status)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}
