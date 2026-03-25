import { useState } from "react";

const OUTCOME_LABELS = {
  TRADE_EXECUTED: { label: "EXECUTED", cls: "gate-executed" },
  TRADE_REJECTED:  { label: "REJECTED",  cls: "gate-rejected"  },
  FILTERED_L1:     { label: "L1 FAIL",   cls: "gate-fail"      },
  FILTERED_L2:     { label: "L2 FAIL",   cls: "gate-fail"      },
  FILTERED_L3:     { label: "L3 FAIL",   cls: "gate-fail"      },
};

function fmtTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" });
}

function Lock({ pass }) {
  return (
    <span style={{ color: pass ? "var(--green)" : "var(--text-3)", fontSize: 14, fontWeight: 600 }}>
      {pass ? "✓" : "✗"}
    </span>
  );
}

export default function GateFeed({ history }) {
  const [expanded, setExpanded] = useState({});

  if (!history) return null;

  return (
    <div className="card">
      <div className="card-header">
        <div className="card-title">Gate Activity</div>
        <div className="card-meta">{history.length} evaluations</div>
      </div>
      <div className="card-body" style={{ padding: 0 }}>
        {history.length === 0 ? (
          <div className="muted" style={{ padding: "16px" }}>
            No gate evaluations yet — waiting for signals above threshold.
          </div>
        ) : (
          <table className="feed-table">
            <thead>
              <tr>
                <th>Time</th>
                <th>Ticker</th>
                <th>Score</th>
                <th>Locks</th>
                <th>Outcome</th>
              </tr>
            </thead>
            <tbody>
              {history.map((row, i) => {
                const meta = OUTCOME_LABELS[row.gate_decision] || { label: row.gate_decision, cls: "gate-fail" };
                const hasReasoning = !!row.lock3_reasoning;
                const isExpanded = expanded[i];
                return (
                  <>
                    <tr
                      key={i}
                      style={{ cursor: hasReasoning ? "pointer" : "default" }}
                      onClick={() => hasReasoning && setExpanded(e => ({ ...e, [i]: !e[i] }))}
                    >
                      <td className="muted">{fmtTime(row.timestamp)}</td>
                      <td>
                        <strong>{row.ticker}</strong>
                        {hasReasoning && (
                          <span style={{ color: "var(--text-3)", fontSize: 11, marginLeft: 6 }}>
                            {isExpanded ? "▲" : "▼"}
                          </span>
                        )}
                      </td>
                      <td style={{ fontFamily: "'DM Mono', monospace" }}>
                        {row.signal_score?.toFixed(3) ?? "—"}
                      </td>
                      <td style={{ display: "flex", gap: 6, alignItems: "center" }}>
                        <Lock pass={row.lock1_pass} />
                        <Lock pass={row.lock2_pass} />
                        <Lock pass={row.lock3_pass} />
                      </td>
                      <td>
                        <span className={`gate-badge ${meta.cls}`}>{meta.label}</span>
                      </td>
                    </tr>
                    {isExpanded && hasReasoning && (
                      <tr key={`${i}-reason`}>
                        <td colSpan={5} style={{
                          padding: "10px 16px 12px",
                          background: "#111828",
                          borderTop: "1px solid var(--border)",
                          fontFamily: "'DM Sans', sans-serif",
                          fontSize: 13,
                          color: "var(--text-2)",
                          lineHeight: 1.6,
                        }}>
                          <span style={{ color: "var(--text-3)", marginRight: 8, fontSize: 12 }}>L3 reasoning:</span>
                          {row.lock3_reasoning}
                        </td>
                      </tr>
                    )}
                  </>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
