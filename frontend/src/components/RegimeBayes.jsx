import React, { useState, useEffect } from "react";

const RANK_COLORS = ["#faff69", "#bcbcbb", "#a0a0a0", "#686868", "#686868"];

function accent(i) { return RANK_COLORS[i] ?? "#888"; }

function TraceRow({ label, value, highlight }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", padding: "1px 0" }}>
      <span style={{ fontFamily: "'Inconsolata', monospace", fontSize: 10, color: "var(--text-3)" }}>
        {label}
      </span>
      <span style={{
        fontFamily: "'Inconsolata', monospace", fontSize: 10,
        color: highlight ? "var(--text-1)" : "var(--text-2)", fontWeight: highlight ? 600 : 400,
      }}>
        {value}
      </span>
    </div>
  );
}

export default function RegimeBayes() {
  const [data,       setData]       = useState(null);
  const [loading,    setLoading]    = useState(true);
  const [error,      setError]      = useState(null);
  const [expanded,   setExpanded]   = useState({});
  const [showIpo,    setShowIpo]    = useState(false);
  const [triggering, setTriggering] = useState(false);
  const [triggerMsg, setTriggerMsg] = useState(null);

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
    <div style={{ fontFamily: "'Inconsolata', monospace", fontSize: 11, color: "var(--red)", padding: "8px 0" }}>
      {error}
    </div>
  );

  return (
    <div style={{ background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: 8, overflow: "hidden" }}>

      {/* Header */}
      <div style={{ padding: "12px 14px", borderBottom: "1px solid var(--border)" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <span style={{ fontFamily: "'Inconsolata', monospace", fontSize: 10, color: "var(--text-3)", letterSpacing: "0.08em", textTransform: "uppercase", fontWeight: 500 }}>
            Regime Allocation
          </span>
          <span style={{
            fontFamily: "'Inconsolata', monospace", fontSize: 9, fontWeight: 600,
            color: "var(--text-3)", border: "1px solid var(--border)",
            borderRadius: 3, padding: "1px 6px",
          }}>
            BAYESIAN
          </span>
          {data?.date && (
            <span style={{ fontFamily: "'Inconsolata', monospace", fontSize: 10, color: "var(--text-3)" }}>
              as of {data.date}
            </span>
          )}
          <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 8 }}>
            {triggerMsg && (
              <span style={{ fontFamily: "'Inconsolata', monospace", fontSize: 10, color: triggerMsg === "Done" ? "var(--green)" : "var(--red)" }}>
                {triggerMsg}
              </span>
            )}
            <button
              onClick={triggerRun}
              disabled={triggering}
              title="Run regime update now"
              style={{
                fontFamily: "'Inconsolata', monospace", fontSize: 10,
                padding: "2px 8px", borderRadius: 4, cursor: triggering ? "default" : "pointer",
                border: "1px solid var(--border)", background: "transparent",
                color: "var(--text-3)", opacity: triggering ? 0.5 : 1,
              }}
            >
              {triggering ? "running…" : "⟳ run"}
            </button>
          </div>
        </div>
      </div>

      {/* Not available */}
      {!data?.available && (
        <div style={{ padding: "12px 14px", fontFamily: "'Inconsolata', monospace", fontSize: 11, color: "var(--text-3)" }}>
          {data?.reason ?? "Waiting for first EOD regime run."}
          <span style={{ marginLeft: 8, opacity: 0.6 }}>Use ⟳ run to trigger manually.</span>
        </div>
      )}

      {/* Leaderboard rows */}
      {data?.available && data.leaderboard.map((entry, i) => {
        const hasAlloc  = entry.allocation > 0;
        const col       = accent(i);
        const trace     = entry.signal_trace ?? {};
        const isOpen    = !!expanded[entry.sector];
        const postPct   = Math.round(entry.posterior * 100);
        const allocPct  = Math.round(entry.allocation * 100);

        return (
          <div key={entry.sector} style={{ opacity: hasAlloc ? 1 : 0.45 }}>
            <div
              onClick={() => toggle(entry.sector)}
              style={{
                display: "flex", alignItems: "center", gap: 8,
                padding: "8px 12px", borderTop: "1px solid var(--border)",
                cursor: "pointer", background: isOpen ? "#1a1a18" : "transparent",
              }}
            >
              {/* Rank */}
              <span style={{
                fontFamily: "'Inconsolata', monospace", fontSize: 9, fontWeight: 700,
                color: col, background: col + "18", border: `1px solid ${col}44`,
                borderRadius: 3, padding: "1px 5px", minWidth: 22, textAlign: "center", flexShrink: 0,
              }}>
                #{entry.rank}
              </span>

              {/* Sector */}
              <span style={{
                fontFamily: "'Inconsolata', monospace", fontSize: 12,
                color: hasAlloc ? "var(--text-1)" : "var(--text-3)",
                fontWeight: hasAlloc ? 600 : 400, minWidth: 100, flex: 1,
              }}>
                {entry.sector}
              </span>

              {/* Posterior bar + % */}
              <div style={{ display: "flex", alignItems: "center", gap: 5, flexShrink: 0 }}>
                <div style={{ background: "#2a2a28", borderRadius: 2, height: 3, width: 36 }}>
                  <div style={{
                    width: `${postPct}%`, height: "100%", borderRadius: 2,
                    background: postPct >= 60 ? "var(--text-2)" : postPct >= 40 ? "var(--text-3)" : "#404040",
                  }} />
                </div>
                <span style={{ fontFamily: "'Inconsolata', monospace", fontSize: 10, color: "var(--text-3)", minWidth: 28 }}>
                  {postPct}%
                </span>
              </div>

              {/* adj score */}
              <span style={{ fontFamily: "'Inconsolata', monospace", fontSize: 11, color: "var(--text-2)", minWidth: 38, textAlign: "right", flexShrink: 0 }}>
                {entry.adjusted_score.toFixed(3)}
              </span>

              {/* Allocation */}
              {hasAlloc ? (
                <div style={{ display: "flex", alignItems: "center", gap: 5, flexShrink: 0 }}>
                  <div style={{ background: "#2a2a28", borderRadius: 2, height: 4, width: 40 }}>
                    <div style={{ width: `${allocPct}%`, height: "100%", background: col, borderRadius: 2 }} />
                  </div>
                  <span style={{ fontFamily: "'Inconsolata', monospace", fontSize: 12, color: col, fontWeight: 700, minWidth: 32 }}>
                    {allocPct}%
                  </span>
                </div>
              ) : (
                <span style={{ fontFamily: "'Inconsolata', monospace", fontSize: 10, color: "#404040", minWidth: 72, textAlign: "right" }}>
                  no alloc
                </span>
              )}

              <span style={{ fontFamily: "'Inconsolata', monospace", fontSize: 10, color: "var(--text-3)", minWidth: 8 }}>
                {isOpen ? "▲" : "▼"}
              </span>
            </div>

            {/* Signal trace */}
            {isOpen && (
              <div style={{
                borderTop: `1px solid ${col}22`,
                padding: "8px 14px 10px 40px",
                background: "#111110",
                display: "grid", gridTemplateColumns: "1fr 1fr", gap: "2px 24px",
              }}>
                <TraceRow label="recovering tickers"   value={trace.recovering_tickers ?? "—"} />
                <TraceRow label="ETF consecutive days" value={trace.etf_consecutive_days ?? "—"} />
                <TraceRow label="leader decline days"  value={trace.leader_decline_days ?? "—"} />
                <TraceRow label="IPO share"            value={trace.ipo_share != null ? `${(trace.ipo_share * 100).toFixed(0)}%` : "—"} />
                <TraceRow label="prior"                value={trace.prior != null ? (trace.prior * 100).toFixed(1) + "%" : "—"} />
                <TraceRow label="after tickers"        value={trace.after_tickers != null ? (trace.after_tickers * 100).toFixed(1) + "%" : "—"} highlight />
                <TraceRow label="after ETF"            value={trace.after_etf != null ? (trace.after_etf * 100).toFixed(1) + "%" : "—"} highlight />
                <TraceRow label="after RS"             value={trace.after_rs != null ? (trace.after_rs * 100).toFixed(1) + "%" : "—"} highlight />
                <TraceRow label="after IPO"            value={trace.after_ipo != null ? (trace.after_ipo * 100).toFixed(1) + "%" : "—"} highlight />
                <TraceRow label="LR ticker"            value={trace.lr_ticker != null ? `×${trace.lr_ticker.toFixed(3)}` : "—"} />
                <TraceRow label="LR ETF"               value={trace.lr_etf != null ? `×${trace.lr_etf.toFixed(3)}` : "—"} />
                <TraceRow label="LR RS"                value={trace.lr_rs != null ? `×${trace.lr_rs.toFixed(3)}` : "—"} />
                <TraceRow label="LR IPO"               value={trace.lr_ipo != null ? `×${trace.lr_ipo.toFixed(3)}` : "—"} />
              </div>
            )}
          </div>
        );
      })}

      {/* IPO context */}
      {data?.available && data.ipo_context && (
        <div style={{ padding: "8px 12px 12px", borderTop: "1px solid var(--border)" }}>
          <button
            onClick={() => setShowIpo(v => !v)}
            style={{
              fontFamily: "'Inconsolata', monospace", fontSize: 10, cursor: "pointer",
              background: "none", border: "none", color: "var(--text-3)", padding: 0,
            }}
          >
            {showIpo ? "▲" : "▼"} IPO sentiment
          </button>
          {showIpo && (
            <pre style={{
              fontFamily: "'Inconsolata', monospace", fontSize: 10, color: "var(--text-2)",
              background: "#111110", border: "1px solid var(--border)",
              borderRadius: 4, padding: "8px 12px", marginTop: 6,
              whiteSpace: "pre-wrap", lineHeight: 1.6,
            }}>
              {data.ipo_context}
            </pre>
          )}
        </div>
      )}

    </div>
  );
}
