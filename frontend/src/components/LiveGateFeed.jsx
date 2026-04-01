import { useState } from "react";

function ScoreVsThreshold({ score, threshold }) {
  if (score == null) return <span>—</span>;
  const color = threshold == null ? "var(--text-1)"
    : score >= threshold + 0.05 ? "var(--green)"
    : score >= threshold        ? "#e8a020"
    : "var(--red)";
  return (
    <div>
      <span style={{ color, fontWeight: 600 }}>{score.toFixed(3)}</span>
      {threshold != null && (
        <div style={{ fontSize: 10, color: "var(--text-3)", marginTop: 1 }}>
          thr {threshold.toFixed(3)}
        </div>
      )}
    </div>
  );
}

function FunnelSummary({ funnel }) {
  if (!funnel) return null;
  const { total_candidates, skipped_open, skipped_cooloff, evaluated,
          macro_fail, l2_fail, leading_fail, l3_fail, executed } = funnel;
  const skipped = (skipped_open || 0) + (skipped_cooloff || 0);
  return (
    <div style={{
      display: "flex", gap: 10, alignItems: "center",
      fontFamily: "'DM Mono', monospace", fontSize: 11, color: "var(--text-3)",
      flexWrap: "wrap",
    }}>
      <span>{total_candidates} candidates</span>
      {skipped > 0 && <span>→ {skipped} skipped ({skipped_open} held, {skipped_cooloff} cooloff)</span>}
      <span>→ {evaluated} evaluated</span>
      {macro_fail > 0    && <span>→ {macro_fail} macro</span>}
      {l2_fail > 0       && <span>→ {l2_fail} L2 fail</span>}
      {leading_fail > 0  && <span>→ {leading_fail} LD fail</span>}
      {l3_fail > 0       && <span>→ {l3_fail} L3 fail</span>}
      <span style={{ color: executed > 0 ? "var(--green)" : "var(--text-3)" }}>→ {executed} traded</span>
    </div>
  );
}

function Lock({ pass }) {
  return (
    <span style={{ color: pass ? "var(--green)" : "var(--text-3)", fontSize: 14, fontWeight: 600 }}>
      {pass ? "✓" : "✗"}
    </span>
  );
}

const LEADING_CHECK_LABELS = {
  relative_strength: "RS",
  put_call_ratio:    "PC",
  unusual_calls:     "UC",
  insider_cluster:   "IN",
};

function decisionBadge(decision) {
  if (decision === "TRADE_EXECUTED")   return { cls: "gate-executed", label: "EXECUTED" };
  if (decision === "TRADE_REJECTED")   return { cls: "gate-fail",     label: "REJECTED" };
  if (decision === "TRADE_FAILED")     return { cls: "gate-rejected", label: "FAILED"   };
  if (decision === "FILTERED_MACRO")   return { cls: "gate-macro",    label: "MACRO"    };
  if (decision === "FILTERED_L1")      return { cls: "gate-fail",     label: "L1 FAIL"  };
  if (decision === "FILTERED_L2")      return { cls: "gate-fail",     label: "L2 FAIL"  };
  if (decision === "FILTERED_LEADING") return { cls: "gate-fail",     label: "LD FAIL"  };
  if (decision === "FILTERED_L3")      return { cls: "gate-fail",     label: "L3 FAIL"  };
  if (decision === "SKIPPED_OPEN")     return { cls: "gate-skipped",  label: "HELD"     };
  if (decision === "SKIPPED_COOLOFF")  return { cls: "gate-skipped",  label: "COOLOFF"  };
  return { cls: "gate-fail", label: decision ?? "—" };
}

export default function LiveGateFeed({ history, funnel }) {
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
          <span style={{ fontSize: 11, color: "var(--text-3)" }}>{collapsed ? "▶" : "▼"}</span>
        </div>
      </div>
      {!collapsed && funnel && (
        <div style={{ padding: "8px 16px", borderBottom: "1px solid var(--border)" }}>
          <FunnelSummary funnel={funnel} />
        </div>
      )}
      {!collapsed && <table className="feed-table">
        <thead>
          <tr>
            <th>Time</th>
            <th>Ticker</th>
            <th>Score</th>
            <th>Locks</th>
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
            const { cls, label } = decisionBadge(row.gate_decision);
            const isSkipped      = row.gate_decision === "SKIPPED_OPEN" || row.gate_decision === "SKIPPED_COOLOFF";
            const hasDetail      = !isSkipped && !!(row.lock3_reasoning || row.lock_leading_checks || row.l2_summary || row.macro_reason);
            const isOpen         = expanded === i;
            const checks         = row.lock_leading_checks;

            return [
              <tr
                key={i}
                onClick={() => hasDetail && setExpanded(isOpen ? null : i)}
                style={{ cursor: hasDetail ? "pointer" : "default" }}
              >
                <td style={{ fontFamily: "'DM Mono', monospace", fontSize: 12 }}>{ts}</td>
                <td>
                  <strong>{row.ticker}</strong>
                  {hasDetail && (
                    <span style={{ color: "var(--text-3)", fontSize: 11, marginLeft: 6 }}>
                      {isOpen ? "▲" : "▼"}
                    </span>
                  )}
                </td>
                <td style={{ fontFamily: "'DM Mono', monospace" }}>
                  <ScoreVsThreshold score={row.signal_score} threshold={row.l1_threshold} />
                </td>
                <td>
                  {isSkipped ? (
                    <span style={{ color: "var(--text-3)", fontSize: 12 }}>—</span>
                  ) : (
                    <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                      <Lock pass={row.lock1_pass} />
                      <Lock pass={row.lock2_pass} />
                      <Lock pass={row.lock_leading_pass} />
                      <Lock pass={row.lock3_pass} />
                    </div>
                  )}
                </td>
                <td>
                  <span className={`gate-badge ${cls}`}>{label}</span>
                  {row.alpaca_order_id && (
                    <span style={{ marginLeft: 8, fontSize: 11, color: "var(--text-3)", fontFamily: "'DM Mono', monospace" }}>
                      #{row.alpaca_order_id.slice(0, 8)}
                    </span>
                  )}
                </td>
              </tr>,
              isOpen && hasDetail && (
                <tr key={`${i}-detail`}>
                  <td colSpan={5} style={{
                    background: "#111827",
                    padding: "10px 16px 14px",
                    borderTop: "1px solid var(--border)",
                    fontSize: 12,
                    lineHeight: 1.7,
                  }}>
                    {row.macro_reason && (
                      <div style={{ marginBottom: 8 }}>
                        <span style={{ color: "var(--text-3)", marginRight: 8 }}>Macro:</span>
                        <span style={{ color: "var(--text-2)" }}>{row.macro_reason}</span>
                      </div>
                    )}
                    {row.l2_summary && (
                      <div style={{ marginBottom: checks || row.lock3_reasoning ? 8 : 0 }}>
                        <span style={{ color: "var(--text-3)", marginRight: 8 }}>Sentiment:</span>
                        <span style={{ color: "var(--text-2)" }}>{row.l2_summary}</span>
                      </div>
                    )}
                    {checks && (
                      <div style={{ marginBottom: row.lock3_reasoning ? 8 : 0 }}>
                        <span style={{ color: "var(--text-3)", marginRight: 8 }}>Leading:</span>
                        {Object.entries(LEADING_CHECK_LABELS).map(([key, lbl]) => {
                          const c = checks[key];
                          if (!c) return null;
                          return (
                            <span key={key} style={{ marginRight: 12 }}>
                              <span style={{ color: c.pass ? "var(--green)" : "var(--red)", fontWeight: 600 }}>
                                {c.pass ? "✓" : "✗"} {lbl}
                              </span>
                              <span style={{ color: "var(--text-3)", marginLeft: 4 }}>{c.reason}</span>
                            </span>
                          );
                        })}
                      </div>
                    )}
                    {row.lock3_reasoning && (
                      <div>
                        <span style={{ color: "var(--text-3)", marginRight: 8 }}>L3 reasoning:</span>
                        <span style={{ color: "var(--text-2)" }}>{row.lock3_reasoning}</span>
                      </div>
                    )}
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
