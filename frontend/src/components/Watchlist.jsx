import React, { useState, useEffect, useCallback } from "react";

const SIGNAL_META = {
  recovering: { label: "RECOVERING", color: "#a78bfa" },
  breakout:   { label: "BREAKOUT",   color: "#00d48c" },
  trending:   { label: "TRENDING",   color: "#4488ff" },
  rising:     { label: "RISING",     color: "#7ab8ff" },
  extended:   { label: "EXTENDED",   color: "#ffaa00" },
  breakdown:  { label: "BREAKDOWN",  color: "#ff3355" },
  weak:       { label: "WEAK",       color: "#4a5568" },
};

function weeks(days) {
  if (!days) return null;
  if (days < 7) return `${days}d`;
  return `${Math.round(days / 5)}w`;
}

function scoreColor(score) {
  if (score === null || score === undefined) return "var(--text-3)";
  if (score >= 0.55) return "var(--green)";
  if (score >= 0.40) return "#e8a020";
  return "var(--red)";
}


export default function Watchlist() {
  const [items,   setItems]   = useState([]);
  const [loading, setLoading] = useState(true);
  const [error,   setError]   = useState(null);
  const [input,   setInput]   = useState("");
  const [adding,  setAdding]  = useState(false);
  const [addErr,  setAddErr]  = useState(null);

  const load = useCallback(() => {
    fetch("/api/watchlist")
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then(d => { setItems(d); setLoading(false); setError(null); })
      .catch(e => { setError(`Failed to load watchlist (${e})`); setLoading(false); });
  }, []);

  useEffect(() => { load(); }, [load]);

  function handleAdd(e) {
    e.preventDefault();
    const ticker = input.trim().toUpperCase();
    if (!ticker) return;
    setAdding(true);
    setAddErr(null);
    fetch(`/api/watchlist/${ticker}`, { method: "POST" })
      .then(r => r.ok ? r.json() : r.json().then(d => Promise.reject(d.detail)))
      .then(() => { setInput(""); load(); })
      .catch(err => setAddErr(typeof err === "string" ? err : "Failed to add"))
      .finally(() => setAdding(false));
  }

  function handleRemove(ticker) {
    fetch(`/api/watchlist/${ticker}`, { method: "DELETE" })
      .then(r => r.ok ? r.json() : Promise.reject())
      .then(() => setItems(prev => prev.filter(i => i.ticker !== ticker)))
      .catch(() => {});
  }

  if (loading) return null;

  return (
    <div style={{
      background: "var(--bg-card)", border: "1px solid var(--border)",
      borderRadius: 8, padding: "18px 20px", marginTop: 14,
    }}>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16, flexWrap: "wrap" }}>
        <div style={{
          fontFamily: "'DM Mono', monospace", fontSize: 11, color: "var(--text-3)",
          letterSpacing: "0.08em", textTransform: "uppercase", fontWeight: 500,
        }}>
          Watchlist
        </div>
        <div style={{
          fontFamily: "'DM Mono', monospace", fontSize: 10, color: "var(--text-3)",
        }}>
          — tickers recovering below threshold, likely pre-breakout
        </div>

        {/* Manual add */}
        <form onSubmit={handleAdd} style={{ marginLeft: "auto", display: "flex", gap: 6, alignItems: "center" }}>
          <input
            value={input}
            onChange={e => { setInput(e.target.value.toUpperCase()); setAddErr(null); }}
            placeholder="ADD TICKER"
            maxLength={5}
            style={{
              fontFamily: "'DM Mono', monospace", fontSize: 11,
              background: "#0e1525", border: "1px solid var(--border)",
              borderRadius: 4, padding: "4px 8px", color: "var(--text-1)",
              width: 100, textTransform: "uppercase",
              outline: "none",
            }}
          />
          <button type="submit" disabled={adding || !input.trim()} style={{
            fontFamily: "'DM Mono', monospace", fontSize: 11, fontWeight: 600,
            background: "#1e2d45", border: "1px solid #4488ff44",
            borderRadius: 4, padding: "4px 10px", color: "#4488ff",
            cursor: adding || !input.trim() ? "default" : "pointer",
            opacity: adding || !input.trim() ? 0.5 : 1,
          }}>
            {adding ? "…" : "+ Watch"}
          </button>
        </form>
      </div>

      {addErr && (
        <div style={{ fontFamily: "'DM Mono', monospace", fontSize: 11, color: "var(--red)", marginBottom: 10 }}>
          {addErr}
        </div>
      )}

      {error && (
        <div style={{ fontFamily: "'DM Mono', monospace", fontSize: 11, color: "var(--red)" }}>{error}</div>
      )}

      {!error && items.length === 0 && (
        <div style={{ fontFamily: "'DM Mono', monospace", fontSize: 11, color: "var(--text-3)" }}>
          No tickers on watchlist. RECOVERING tickers are added automatically after each poll.
        </div>
      )}

      {items.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          {items.map(item => {
            const sm      = SIGNAL_META[item.signal] ?? SIGNAL_META.weak;
            const color   = scoreColor(item.score);
            const streak  = weeks(item.streak_days);
            const isBreakout = item.signal === "breakout" || item.signal === "trending";
            const rowBg   = item.pre_rotation ? "#00d48c06"
                          : isBreakout        ? `${sm.color}0d`
                          :                    "#0e1525";
            const rowBorder = item.pre_rotation ? "#00d48c44" : `${sm.color}33`;
            return (
              <div key={item.ticker} style={{
                display: "flex", alignItems: "center", gap: 10,
                background: rowBg, border: `1px solid ${rowBorder}`,
                borderRadius: 5, padding: "7px 12px",
              }}>
                {/* Ticker + sector */}
                <div style={{ minWidth: 52 }}>
                  <div style={{ fontFamily: "'DM Mono', monospace", fontSize: 13, color: "var(--text-1)", fontWeight: 600 }}>
                    {item.ticker}
                  </div>
                  <div style={{ fontFamily: "'DM Mono', monospace", fontSize: 10, color: "var(--text-3)", marginTop: 1 }}>
                    {item.sector}
                  </div>
                </div>

                {/* Signal badge */}
                <div style={{
                  fontFamily: "'DM Mono', monospace", fontSize: 10, fontWeight: 700,
                  color: sm.color, background: `${sm.color}18`,
                  border: `1px solid ${sm.color}44`, borderRadius: 4,
                  padding: "2px 7px", whiteSpace: "nowrap",
                }}>
                  {sm.label}
                  {streak && <span style={{ fontWeight: 400, opacity: 0.7 }}> · {streak}</span>}
                </div>

                {/* Velocity arrow */}
                {item.velocity === "accelerating" && <span style={{ fontSize: 12, color: "#00d48c" }} title={`+${item.velocity_5d?.toFixed(3)} 5d`}>↑</span>}
                {item.velocity === "decelerating" && <span style={{ fontSize: 12, color: "#ff3355" }} title={`${item.velocity_5d?.toFixed(3)} 5d`}>↓</span>}

                {/* Score + trend */}
                <div style={{ display: "flex", alignItems: "baseline", gap: 4, marginLeft: "auto" }}>
                  <span style={{ fontFamily: "'DM Mono', monospace", fontSize: 14, color, fontWeight: 600 }}>
                    {item.score?.toFixed(3) ?? "—"}
                  </span>
                  {item.trend === "up"   && <span style={{ fontSize: 12, color: "var(--green)" }}>↑</span>}
                  {item.trend === "down" && <span style={{ fontSize: 12, color: "var(--red)" }}>↓</span>}
                  {item.trend === "flat" && <span style={{ fontSize: 12, color: "var(--text-3)" }}>→</span>}
                </div>

                {/* Pre-rotation badge */}
                {item.pre_rotation && (
                  <div style={{
                    fontFamily: "'DM Mono', monospace", fontSize: 9, fontWeight: 700,
                    color: "#00d48c", background: "#00d48c12",
                    border: "1px solid #00d48c44", borderRadius: 3, padding: "1px 5px",
                    whiteSpace: "nowrap",
                  }}>
                    PRE-ROT
                  </div>
                )}

                {/* Source badge */}
                <div style={{
                  fontFamily: "'DM Mono', monospace", fontSize: 9,
                  color: item.source === "manual" ? "#4488ff" : "var(--text-3)",
                  border: `1px solid ${item.source === "manual" ? "#4488ff44" : "var(--border)"}`,
                  borderRadius: 3, padding: "1px 5px",
                }}>
                  {item.source}
                </div>

                {/* Remove */}
                <button
                  onClick={() => handleRemove(item.ticker)}
                  style={{
                    fontFamily: "'DM Mono', monospace", fontSize: 12,
                    background: "none", border: "none",
                    color: "var(--text-3)", cursor: "pointer", padding: "0 2px",
                    lineHeight: 1,
                  }}
                  title="Remove from watchlist"
                >×</button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
