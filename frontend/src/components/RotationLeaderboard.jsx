import React, { useState, useEffect } from "react";

const SIGNAL_META = {
  recovering: { label: "RECOVERING", color: "#a78bfa" },
  rising:     { label: "RISING",     color: "#7ab8ff" },
  breakout:   { label: "BREAKOUT",   color: "#00d48c" },
  trending:   { label: "TRENDING",   color: "#4488ff" },
  extended:   { label: "EXTENDED",   color: "#ffaa00" },
  breakdown:  { label: "BREAKDOWN",  color: "#ff3355" },
  weak:       { label: "WEAK",       color: "#4a5568" },
};

function scoreColor(rs) {
  if (rs >= 0.60) return "#00d48c";
  if (rs >= 0.40) return "#4488ff";
  return "#4a5568";
}

function ScoreBar({ score }) {
  const pct   = Math.round(score * 100);
  const color = scoreColor(score);
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6, minWidth: 80 }}>
      <div style={{ background: "#1e2538", borderRadius: 3, height: 4, width: 48, flexShrink: 0 }}>
        <div style={{ width: `${pct}%`, height: "100%", background: color, borderRadius: 3 }} />
      </div>
      <span style={{
        fontFamily: "'DM Mono', monospace", fontSize: 11,
        color, fontWeight: 600, minWidth: 28,
      }}>
        {pct}%
      </span>
    </div>
  );
}

export default function RotationLeaderboard({ limit = 10 }) {
  const [rows,    setRows]    = useState([]);
  const [loading, setLoading] = useState(true);
  const [error,   setError]   = useState(null);

  useEffect(() => {
    fetch(`/api/sectors/rotation-leaderboard?limit=${limit}`)
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then(d => { setRows(d); setLoading(false); })
      .catch(e => { setError(`Failed to load leaderboard (${e})`); setLoading(false); });
  }, [limit]);

  if (loading) return null;
  if (error) return (
    <div style={{ fontFamily: "'DM Mono', monospace", fontSize: 11, color: "var(--red)", padding: "8px 0" }}>
      {error}
    </div>
  );
  if (!rows.length) return (
    <div style={{ fontFamily: "'DM Mono', monospace", fontSize: 11, color: "var(--text-3)", padding: "8px 0" }}>
      No rotation data yet — available after first backfill.
    </div>
  );

  return (
    <div style={{
      background: "var(--bg-card)", border: "1px solid var(--border)",
      borderRadius: 8, padding: "18px 20px", marginTop: 14,
    }}>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 14 }}>
        <div style={{
          fontFamily: "'DM Mono', monospace", fontSize: 11, color: "var(--text-3)",
          letterSpacing: "0.08em", textTransform: "uppercase", fontWeight: 500,
        }}>
          Top Rotation Setups
        </div>
        <div style={{ fontFamily: "'DM Mono', monospace", fontSize: 10, color: "var(--text-3)" }}>
          — ranked by composite rotation score
        </div>
      </div>

      {/* Column headers */}
      <div style={{
        display: "grid",
        gridTemplateColumns: "90px 1fr 110px 20px 88px 52px",
        gap: 8, alignItems: "center",
        padding: "0 4px 6px",
        borderBottom: "1px solid var(--border)",
        marginBottom: 6,
      }}>
        {["Ticker", "Sector", "Signal", "", "Rot. Score", ""].map((h, i) => (
          <div key={i} style={{
            fontFamily: "'DM Mono', monospace", fontSize: 9,
            color: "var(--text-3)", letterSpacing: "0.06em", textTransform: "uppercase",
          }}>
            {h}
          </div>
        ))}
      </div>

      {/* Rows */}
      <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
        {rows.map((row, i) => {
          const sm       = SIGNAL_META[row.signal] ?? SIGNAL_META.weak;
          const isTop3   = i < 3;
          const rowColor = row.pre_rotation ? "#00d48c08" : isTop3 ? "#ffffff04" : "transparent";

          return (
            <div key={row.ticker} style={{
              display: "grid",
              gridTemplateColumns: "90px 1fr 110px 20px 88px 52px",
              gap: 8, alignItems: "center",
              background: rowColor,
              border: `1px solid ${row.pre_rotation ? "#00d48c33" : "transparent"}`,
              borderRadius: 4, padding: "5px 4px",
            }}>
              {/* Ticker */}
              <div>
                <span style={{
                  fontFamily: "'DM Mono', monospace", fontSize: 13,
                  color: "var(--text-1)", fontWeight: 600,
                }}>
                  {row.ticker}
                </span>
              </div>

              {/* Sector */}
              <div style={{
                fontFamily: "'DM Mono', monospace", fontSize: 10,
                color: "var(--text-3)", overflow: "hidden",
                textOverflow: "ellipsis", whiteSpace: "nowrap",
              }}>
                {row.sector}
              </div>

              {/* Signal badge */}
              <div style={{
                fontFamily: "'DM Mono', monospace", fontSize: 9, fontWeight: 700,
                color: sm.color, background: `${sm.color}18`,
                border: `1px solid ${sm.color}44`, borderRadius: 3,
                padding: "2px 6px", whiteSpace: "nowrap",
                display: "inline-block",
              }}>
                {sm.label}
              </div>

              {/* Velocity */}
              <div style={{ textAlign: "center" }}>
                {row.velocity === "accelerating" && (
                  <span style={{ fontSize: 12, color: "#00d48c" }}
                        title={`+${row.velocity_5d?.toFixed(3)} 5d`}>↑</span>
                )}
                {row.velocity === "decelerating" && (
                  <span style={{ fontSize: 12, color: "#ff3355" }}
                        title={`${row.velocity_5d?.toFixed(3)} 5d`}>↓</span>
                )}
                {row.velocity === "flat" && (
                  <span style={{ fontSize: 12, color: "var(--text-3)" }}>→</span>
                )}
              </div>

              {/* Rotation score bar */}
              <ScoreBar score={row.rotation_score} />

              {/* Pre-rotation badge */}
              <div>
                {row.pre_rotation && (
                  <div style={{
                    fontFamily: "'DM Mono', monospace", fontSize: 8, fontWeight: 700,
                    color: "#00d48c", background: "#00d48c12",
                    border: "1px solid #00d48c44", borderRadius: 3,
                    padding: "1px 5px", whiteSpace: "nowrap", display: "inline-block",
                  }}>
                    PRE-ROT
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
