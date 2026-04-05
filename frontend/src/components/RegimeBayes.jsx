import React, { useState, useEffect } from "react";

function AllocationBar({ allocation, color }) {
  const pct = Math.round(allocation * 100);
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
      <div style={{ background: "#1e2538", borderRadius: 3, height: 5, width: 72 }}>
        <div style={{ width: `${pct}%`, height: "100%", background: color, borderRadius: 3 }} />
      </div>
      <span style={{
        fontFamily: "'DM Mono', monospace", fontSize: 13,
        color, fontWeight: 700, minWidth: 38, textAlign: "right",
      }}>
        {pct}%
      </span>
    </div>
  );
}

function PostBar({ value, label }) {
  const pct = Math.round(value * 100);
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
      <div style={{ background: "#1e2538", borderRadius: 3, height: 3, width: 48 }}>
        <div style={{
          width: `${pct}%`, height: "100%", borderRadius: 3,
          background: pct >= 60 ? "#4488ff" : pct >= 40 ? "#7ab8ff" : "#4a5568",
        }} />
      </div>
      <span style={{
        fontFamily: "'DM Mono', monospace", fontSize: 10, color: "var(--text-3)",
        minWidth: 28,
      }}>
        {label ?? `${pct}%`}
      </span>
    </div>
  );
}

function TraceRow({ label, value, highlight }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", padding: "1px 0" }}>
      <span style={{ fontFamily: "'DM Mono', monospace", fontSize: 10, color: "var(--text-3)" }}>
        {label}
      </span>
      <span style={{
        fontFamily: "'DM Mono', monospace", fontSize: 10,
        color: highlight ? "#4488ff" : "var(--text-2)", fontWeight: highlight ? 600 : 400,
      }}>
        {value}
      </span>
    </div>
  );
}

export default function RegimeBayes() {
  const [data,         setData]         = useState(null);
  const [loading,      setLoading]      = useState(true);
  const [error,        setError]        = useState(null);
  const [expanded,     setExpanded]     = useState({});
  const [showIpo,      setShowIpo]      = useState(false);
  const [triggering,   setTriggering]   = useState(false);
  const [triggerMsg,   setTriggerMsg]   = useState(null);

  function load() {
    setLoading(true);
    fetch("/api/sectors/regime-bayes")
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then(d => { setData(d); setLoading(false); })
      .catch(e => { setError(`Failed to load regime allocation (${e})`); setLoading(false); });
  }

  useEffect(() => { load(); }, []);

  function toggle(sector) {
    setExpanded(prev => ({ ...prev, [sector]: !prev[sector] }));
  }

  function triggerRun() {
    setTriggering(true);
    setTriggerMsg(null);
    fetch("/api/sectors/regime-bayes/run", { method: "POST" })
      .then(r => r.ok ? r.json() : Promise.reject())
      .then(() => { setTriggerMsg("Done"); load(); })
      .catch(() => setTriggerMsg("Failed"))
      .finally(() => setTriggering(false));
  }

  if (loading) return null;
  if (error)   return (
    <div style={{ fontFamily: "'DM Mono', monospace", fontSize: 11, color: "var(--red)", padding: "8px 0" }}>
      {error}
    </div>
  );

  return (
    <div style={{
      background: "var(--bg-card)", border: "1px solid var(--border)",
      borderRadius: 8, padding: "18px 20px", marginTop: 14,
    }}>

      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 14, flexWrap: "wrap" }}>
        <div style={{
          fontFamily: "'DM Mono', monospace", fontSize: 11, color: "var(--text-3)",
          letterSpacing: "0.08em", textTransform: "uppercase", fontWeight: 500,
        }}>
          Regime Allocation
        </div>
        <div style={{
          fontFamily: "'DM Mono', monospace", fontSize: 9, fontWeight: 600,
          color: "#4488ff", background: "#4488ff12", border: "1px solid #4488ff44",
          borderRadius: 3, padding: "1px 6px",
        }}>
          BAYESIAN
        </div>
        {data?.date && (
          <span style={{ fontFamily: "'DM Mono', monospace", fontSize: 10, color: "var(--text-3)" }}>
            {data.date}
          </span>
        )}
        <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 8 }}>
          {triggerMsg && (
            <span style={{
              fontFamily: "'DM Mono', monospace", fontSize: 10,
              color: triggerMsg === "Done" ? "var(--green)" : "var(--red)",
            }}>
              {triggerMsg}
            </span>
          )}
          <button
            onClick={triggerRun}
            disabled={triggering}
            title="Run regime update now (manual trigger)"
            style={{
              fontFamily: "'DM Mono', monospace", fontSize: 10,
              padding: "2px 8px", borderRadius: 4, cursor: triggering ? "default" : "pointer",
              border: "1px solid var(--border)", background: "transparent",
              color: "var(--text-3)", opacity: triggering ? 0.5 : 1,
            }}
          >
            {triggering ? "running…" : "⟳ run"}
          </button>
        </div>
      </div>

      {/* Not available yet */}
      {!data?.available && (
        <div style={{ fontFamily: "'DM Mono', monospace", fontSize: 11, color: "var(--text-3)" }}>
          {data?.reason ?? "Waiting for first EOD regime run."}
          <span style={{ marginLeft: 8, opacity: 0.6 }}>Use ⟳ run to trigger manually.</span>
        </div>
      )}

      {/* Leaderboard */}
      {data?.available && (
        <>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {data.leaderboard.map((entry, i) => {
              const hasAlloc   = entry.allocation > 0;
              const rankColors = ["#00d48c", "#4488ff", "#7ab8ff", "#888", "#888"];
              const accentCol  = rankColors[i] ?? "#888";
              const trace      = entry.signal_trace ?? {};
              const isOpen     = !!expanded[entry.sector];

              return (
                <div key={entry.sector} style={{
                  background: hasAlloc ? "#0e1525" : "#0a0e18",
                  border: `1px solid ${hasAlloc ? accentCol + "44" : "#1e2538"}`,
                  borderRadius: 5, overflow: "hidden",
                  opacity: hasAlloc ? 1 : 0.55,
                }}>
                  {/* Row */}
                  <div
                    onClick={() => toggle(entry.sector)}
                    style={{
                      display: "flex", alignItems: "center",
                      padding: "7px 10px", gap: 10, cursor: "pointer",
                    }}
                  >
                    {/* Rank */}
                    <span style={{
                      fontFamily: "'DM Mono', monospace", fontSize: 9, fontWeight: 700,
                      color: accentCol, background: accentCol + "18",
                      border: `1px solid ${accentCol}44`,
                      borderRadius: 3, padding: "1px 5px", minWidth: 18, textAlign: "center",
                    }}>
                      #{entry.rank}
                    </span>

                    {/* Sector name */}
                    <span style={{
                      fontFamily: "'DM Mono', monospace", fontSize: 12,
                      color: hasAlloc ? "var(--text-1)" : "var(--text-3)",
                      fontWeight: hasAlloc ? 600 : 400, minWidth: 110,
                    }}>
                      {entry.sector}
                    </span>

                    {/* Posterior conviction bar */}
                    <div style={{ flex: 1 }}>
                      <PostBar
                        value={entry.posterior}
                        label={`${(entry.posterior * 100).toFixed(0)}% post`}
                      />
                    </div>

                    {/* agg × post = adj */}
                    <span style={{
                      fontFamily: "'DM Mono', monospace", fontSize: 10, color: "var(--text-3)",
                      whiteSpace: "nowrap",
                    }}>
                      {entry.aggregate_score.toFixed(3)} × {(entry.posterior).toFixed(2)}
                      {" = "}
                      <span style={{ color: "var(--text-2)" }}>{entry.adjusted_score.toFixed(3)}</span>
                    </span>

                    {/* Allocation bar */}
                    {hasAlloc
                      ? <AllocationBar allocation={entry.allocation} color={accentCol} />
                      : <span style={{ fontFamily: "'DM Mono', monospace", fontSize: 10, color: "#4a5568", minWidth: 80, textAlign: "right" }}>no alloc</span>
                    }
                  </div>

                  {/* Signal trace — collapsible */}
                  {isOpen && (
                    <div style={{
                      borderTop: `1px solid ${accentCol}22`,
                      padding: "8px 12px 10px",
                      background: "#070c14",
                      display: "grid", gridTemplateColumns: "1fr 1fr", gap: "2px 24px",
                    }}>
                      <TraceRow label="recovering tickers" value={trace.recovering_tickers ?? "—"} />
                      <TraceRow label="ETF consecutive days" value={trace.etf_consecutive_days ?? "—"} />
                      <TraceRow label="leader decline days" value={trace.leader_decline_days ?? "—"} />
                      <TraceRow label="IPO share" value={trace.ipo_share != null ? `${(trace.ipo_share * 100).toFixed(0)}%` : "—"} />
                      <TraceRow label="prior" value={trace.prior != null ? (trace.prior * 100).toFixed(1) + "%" : "—"} />
                      <TraceRow label="after tickers" value={trace.after_tickers != null ? (trace.after_tickers * 100).toFixed(1) + "%" : "—"} highlight />
                      <TraceRow label="after ETF" value={trace.after_etf != null ? (trace.after_etf * 100).toFixed(1) + "%" : "—"} highlight />
                      <TraceRow label="after RS" value={trace.after_rs != null ? (trace.after_rs * 100).toFixed(1) + "%" : "—"} highlight />
                      <TraceRow label="after IPO" value={trace.after_ipo != null ? (trace.after_ipo * 100).toFixed(1) + "%" : "—"} highlight />
                      <TraceRow label="LR ticker" value={trace.lr_ticker != null ? `×${trace.lr_ticker.toFixed(3)}` : "—"} />
                      <TraceRow label="LR ETF" value={trace.lr_etf != null ? `×${trace.lr_etf.toFixed(3)}` : "—"} />
                      <TraceRow label="LR RS" value={trace.lr_rs != null ? `×${trace.lr_rs.toFixed(3)}` : "—"} />
                      <TraceRow label="LR IPO" value={trace.lr_ipo != null ? `×${trace.lr_ipo.toFixed(3)}` : "—"} />
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          {/* IPO context */}
          {data.ipo_context && (
            <div style={{ marginTop: 10 }}>
              <button
                onClick={() => setShowIpo(v => !v)}
                style={{
                  fontFamily: "'DM Mono', monospace", fontSize: 10, cursor: "pointer",
                  background: "none", border: "none", color: "var(--text-3)", padding: 0,
                }}
              >
                {showIpo ? "▾" : "▸"} IPO sentiment
              </button>
              {showIpo && (
                <pre style={{
                  fontFamily: "'DM Mono', monospace", fontSize: 10, color: "var(--text-2)",
                  background: "#070c14", border: "1px solid var(--border)",
                  borderRadius: 4, padding: "8px 12px", marginTop: 6,
                  whiteSpace: "pre-wrap", lineHeight: 1.6,
                }}>
                  {data.ipo_context}
                </pre>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
