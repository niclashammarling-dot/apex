import React, { useState, useEffect } from "react";
import HoverTooltip, { TipRow, TipHeader } from "./HoverTooltip";

const EXCLUDED_SECTORS = new Set(["Financials", "ConsumerStaples", "Utilities"]);

// Active sectors not yet in the backend leaderboard get placeholder rows (posterior=0)
// so newly added sectors appear immediately rather than waiting for first EOD run.
const ACTIVE_SECTORS = [
  "Technology","Healthcare","Energy","Industrials","ConsumerDisc",
  "Communication","Materials","RealEstate","Semiconductors","Defense",
];

const RANK_COLORS = ["var(--t-accent)", "#bcbcbb", "#a0a0a0", "#686868", "#686868"];

function accent(i) { return RANK_COLORS[i] ?? "var(--t-text-3)"; }

function TraceRow({ label, value, highlight }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", padding: "1px 0" }}>
      <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--t-text-3)" }}>{label}</span>
      <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: highlight ? "var(--t-text-1)" : "var(--t-text-2)", fontWeight: highlight ? 600 : 400 }}>
        {value}
      </span>
    </div>
  );
}

function LeaderboardRow({ entry, i, isOpen, onToggle }) {
  const [tip, setTip] = useState(null);
  const hasAlloc = entry.allocation > 0;
  const col      = accent(i);
  const trace    = entry.signal_trace ?? {};
  const postPct  = Math.round(entry.posterior * 100);
  const allocPct = Math.round(entry.allocation * 100);

  return (
    <div style={{ opacity: hasAlloc ? 1 : 0.45 }}>
      <div
        onMouseMove={e => setTip({ x: e.clientX, y: e.clientY })}
        onMouseLeave={() => setTip(null)}
        onClick={() => onToggle(entry.sector)}
        style={{
          display: "flex", alignItems: "center", gap: 8,
          padding: "8px 14px", borderTop: "1px solid var(--t-border)",
          cursor: "pointer",
          background: isOpen ? "rgba(255,255,255,0.03)" : "transparent",
        }}
      >
        <HoverTooltip pos={tip}>
          <TipHeader>{entry.sector}</TipHeader>
          <TipRow label="Rank"       value={`#${entry.rank}`} />
          <TipRow label="Posterior"  value={`${postPct}%`} />
          <TipRow label="Adj Score"  value={entry.adjusted_score.toFixed(3)} />
          <TipRow label="Allocation" value={hasAlloc ? `${allocPct}%` : "—"} />
          {trace.lr_ticker != null && <TipRow label="LR Tickers" value={`×${trace.lr_ticker.toFixed(3)}`} />}
          {trace.lr_etf    != null && <TipRow label="LR ETF"     value={`×${trace.lr_etf.toFixed(3)}`} />}
          {trace.lr_rs     != null && <TipRow label="LR RS"      value={`×${trace.lr_rs.toFixed(3)}`} />}
          {trace.lr_ipo    != null && <TipRow label="LR IPO"     value={`×${trace.lr_ipo.toFixed(3)}`} />}
        </HoverTooltip>

        <span className="t-pill" style={{ color: col, background: `${col}18`, borderColor: `${col}44`, fontSize: 9, minWidth: 24, textAlign: "center", flexShrink: 0 }}>
          #{entry.rank}
        </span>

        <span style={{ fontFamily: "var(--mono)", fontSize: 12, color: hasAlloc ? "var(--t-text-1)" : "var(--t-text-3)", fontWeight: hasAlloc ? 600 : 400, flex: 1, minWidth: 0 }}>
          {entry.sector}
        </span>

        <div style={{ display: "flex", alignItems: "center", gap: 5, flexShrink: 0 }}>
          <div style={{ background: "var(--t-grid)", borderRadius: 2, height: 3, width: 36 }}>
            <div style={{ width: `${postPct}%`, height: "100%", borderRadius: 2, background: postPct >= 60 ? "var(--t-text-2)" : postPct >= 40 ? "var(--t-text-3)" : "#404040" }} />
          </div>
          <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--t-text-3)", minWidth: 28 }}>{postPct}%</span>
        </div>

        <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--t-text-2)", minWidth: 38, textAlign: "right", flexShrink: 0 }}>
          {entry.adjusted_score.toFixed(3)}
        </span>

        {hasAlloc ? (
          <div style={{ display: "flex", alignItems: "center", gap: 5, flexShrink: 0 }}>
            <div style={{ background: "var(--t-grid)", borderRadius: 2, height: 4, width: 40 }}>
              <div style={{ width: `${allocPct}%`, height: "100%", background: col, borderRadius: 2 }} />
            </div>
            <span style={{ fontFamily: "var(--mono)", fontSize: 12, color: col, fontWeight: 700, minWidth: 32 }}>{allocPct}%</span>
          </div>
        ) : (
          <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--t-text-3)", minWidth: 72, textAlign: "right" }}>no alloc</span>
        )}

        <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--t-text-3)" }}>{isOpen ? "▲" : "▼"}</span>
      </div>

      {isOpen && (
        <div style={{
          borderTop: `1px solid var(--t-border)`,
          padding: "8px 14px 10px 42px",
          background: "rgba(0,0,0,0.15)",
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
          <TraceRow label="after rank"           value={trace.after_rank != null ? (trace.after_rank * 100).toFixed(1) + "%" : "—"} highlight />
          <TraceRow label="LR ticker"            value={trace.lr_ticker != null ? `×${trace.lr_ticker.toFixed(3)}` : "—"} />
          <TraceRow label="LR ETF"               value={trace.lr_etf != null ? `×${trace.lr_etf.toFixed(3)}` : "—"} />
          <TraceRow label="LR RS"                value={trace.lr_rs != null ? `×${trace.lr_rs.toFixed(3)}` : "—"} />
          <TraceRow label="LR IPO"               value={trace.lr_ipo != null ? `×${trace.lr_ipo.toFixed(3)}` : "—"} />
          <TraceRow label="LR rank"              value={trace.lr_rank != null ? `×${trace.lr_rank.toFixed(3)}` : "—"} />
        </div>
      )}
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
      .then(d => {
        if (d?.leaderboard) {
          const seen = new Set(d.leaderboard.map(e => e.sector));
          ACTIVE_SECTORS.forEach(s => {
            if (!seen.has(s))
              d.leaderboard.push({ sector: s, posterior: 0, adjusted_score: 0,
                                   allocation: 0, rank: 99, signal_trace: {} });
          });
        }
        setData(d); setLoading(false);
      })
      .catch(e => { setError(`Failed to load (${e})`); setLoading(false); });
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
  if (error) return (
    <div style={{ padding: "10px 14px", fontFamily: "var(--mono)", fontSize: 11, color: "var(--t-red)" }}>{error}</div>
  );

  return (
    <div className="t-card">
      <div className="t-card-head">
        <div className="t-card-title">
          SECTOR ALLOCATION
          {data?.date && <span className="t-meta" style={{ marginLeft: 8 }}>as of {data.date}</span>}
        </div>
        <div className="t-row" style={{ gap: 8 }}>
          {triggerMsg && (
            <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: triggerMsg === "Done" ? "var(--t-accent)" : "var(--t-red)" }}>
              {triggerMsg}
            </span>
          )}
          <button className="t-btn" onClick={triggerRun} disabled={triggering} style={{ opacity: triggering ? 0.5 : 1 }}>
            {triggering ? "RUNNING…" : "⟳ RUN"}
          </button>
        </div>
      </div>

      {!data?.available && (
        <div className="t-card-body" style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--t-text-3)" }}>
          {data?.reason ?? "Waiting for first EOD regime run."}
          <span style={{ marginLeft: 8, opacity: 0.6 }}>Use ⟳ RUN to trigger manually.</span>
        </div>
      )}

      {data?.available && data.leaderboard
        .filter(e => !EXCLUDED_SECTORS.has(e.sector))
        .map((entry, i) => (
          <LeaderboardRow key={entry.sector} entry={entry} i={i} isOpen={!!expanded[entry.sector]} onToggle={toggle} />
        ))}

      {data?.available && data.ipo_context && (
        <div style={{ borderTop: "1px solid var(--t-border)", padding: "8px 14px" }}>
          <button
            onClick={() => setShowIpo(v => !v)}
            className="t-btn"
            style={{ fontSize: 10 }}
          >
            {showIpo ? "▲" : "▼"} IPO SENTIMENT
          </button>
          {showIpo && (
            <pre style={{
              fontFamily: "var(--mono)", fontSize: 10, color: "var(--t-text-2)",
              background: "rgba(0,0,0,0.2)", border: "1px solid var(--t-border)",
              borderRadius: 4, padding: "8px 12px", marginTop: 8,
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
