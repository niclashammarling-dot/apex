import { useState } from "react";

function decisionBadge(decision, outcome) {
  if (outcome === "TRADE_EXECUTED")  return { cls: "gate-executed", label: "EXECUTED" };
  if (outcome === "TRADE_REJECTED")  return { cls: "gate-fail",     label: "REJECTED" };
  if (outcome === "TRADE_FAILED")    return { cls: "gate-rejected", label: "FAILED"   };
  if (outcome === "FILTERED_MACRO")  return { cls: "gate-macro",    label: "MACRO"    };
  if (decision === "BUY")            return { cls: "gate-executed", label: "BUY"      };
  if (decision === "SELL")           return { cls: "gate-rejected", label: "SELL"     };
  if (decision === "L1_FAIL")        return { cls: "gate-fail",     label: "L1 FAIL"  };
  if (decision === "L2_FAIL")        return { cls: "gate-fail",     label: "L2 FAIL"  };
  return { cls: "gate-fail", label: decision ?? "HOLD" };
}

export default function LiveGateFeed({ history }) {
  const [expanded,  setExpanded]  = useState(null);
  const [collapsed, setCollapsed] = useState(false);

  if (!history || history.length === 0) {
    return (
      <div className="card">
        <div className="card-header"><div className="card-title">Live Gate Activity</div></div>
        <div className="card-body"><p className="muted">No live gate evaluations yet.</p></div>
      </div>
    );
  }

  return (
    <div className="card">
      <div
        className="card-header"
        onClick={() => setCollapsed(c => !c)}
        style={{ cursor: "pointer", userSelect: "none" }}
      >
        <div className="card-title">Live Gate Activity</div>
        <div className="card-meta" style={{ display: "flex", gap: 12, alignItems: "center" }}>
          <span>{history.length} evaluations</span>
          <span style={{ fontSize: 11, color: "var(--text-3)" }}>{collapsed ? "▶" : "▼"}</span>
        </div>
      </div>
      {!collapsed && <table className="feed-table">
        <thead>
          <tr>
            <th>Time</th>
            <th>Ticker</th>
            <th>Score</th>
            <th>L1</th>
            <th>L2</th>
            <th>L3</th>
            <th>Decision</th>
          </tr>
        </thead>
        <tbody>
          {history.map((row, i) => {
            const ts = row.timestamp
              ? new Date(row.timestamp).toLocaleString("en-US", {
                  month: "2-digit", day: "2-digit",
                  hour: "2-digit", minute: "2-digit", hour12: false,
                })
              : "—";
            const { cls, label } = decisionBadge(row.gate_decision, row.outcome);
            const hasReasoning   = !!row.lock3_reasoning;
            const isOpen         = expanded === i;

            return [
              <tr
                key={i}
                onClick={() => hasReasoning && setExpanded(isOpen ? null : i)}
                style={{ cursor: hasReasoning ? "pointer" : "default" }}
              >
                <td style={{ fontFamily: "'DM Mono', monospace", fontSize: 12 }}>{ts}</td>
                <td><strong>{row.ticker}</strong></td>
                <td style={{ fontFamily: "'DM Mono', monospace" }}>
                  {row.signal_score?.toFixed(3) ?? "—"}
                </td>
                <td>{row.lock1_pass ? "✓" : "✗"}</td>
                <td>{row.lock2_pass ? "✓" : row.lock1_pass ? "✗" : "—"}</td>
                <td>{row.lock3_pass ? "✓" : row.lock2_pass ? "✗" : "—"}</td>
                <td>
                  <span className={`gate-badge ${cls}`}>{label}</span>
                  {row.alpaca_order_id && (
                    <span style={{ marginLeft: 8, fontSize: 11, color: "var(--text-3)", fontFamily: "'DM Mono', monospace" }}>
                      #{row.alpaca_order_id.slice(0, 8)}
                    </span>
                  )}
                </td>
              </tr>,
              isOpen && (
                <tr key={`${i}-reasoning`}>
                  <td colSpan={7} style={{
                    background: "#111827",
                    padding: "10px 16px",
                    borderBottom: "1px solid var(--border)",
                    color: "var(--text-2)",
                    fontStyle: "italic",
                    fontSize: 13,
                    lineHeight: 1.5,
                  }}>
                    {row.lock3_reasoning}
                  </td>
                </tr>
              ),
            ];
          })}
        </tbody>
      </table>}
    </div>
  );
}
