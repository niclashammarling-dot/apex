import React, { useState, useEffect } from "react";

const SIGNAL_META = {
  breakout:  { label: "BREAKOUT",  color: "#00d48c", desc: "Fresh crossover after extended weakness" },
  trending:  { label: "TRENDING",  color: "#4488ff", desc: "Confirmed multi-week uptrend"            },
  rising:    { label: "RISING",    color: "#7ab8ff", desc: "Early move, not yet confirmed"            },
  extended:  { label: "EXTENDED",  color: "#ffaa00", desc: "Long uptrend — late stage"               },
  breakdown: { label: "BREAKDOWN", color: "#ff3355", desc: "Fresh cross below threshold"             },
  weak:      { label: "WEAK",      color: "#4a5568", desc: "Below threshold"                         },
};

const REGIME_META = {
  risk_on:  { label: "RISK-ON",  color: "#00d48c", bg: "#00d48c18" },
  risk_off: { label: "RISK-OFF", color: "#ff3355", bg: "#ff335518" },
  neutral:  { label: "NEUTRAL",  color: "#888",    bg: "#88888818" },
};

const CYCLICAL  = new Set(["Technology", "Financials", "Industrials", "ConsumerDisc",
                            "Energy", "Materials", "Communication"]);
const DEFENSIVE = new Set(["Utilities", "Healthcare", "ConsumerStaples", "RealEstate"]);

function weeks(days) {
  if (!days) return "—";
  if (days < 7)  return `${days}d`;
  return `${Math.round(days / 5)}w`;   // trading weeks
}

export default function SectorRegime() {
  const [data,    setData]    = useState(null);
  const [error,   setError]   = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch("/api/sectors/regime")
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then(d => { setData(d); setLoading(false); })
      .catch(e => { setError(`Failed to load regime (${e})`); setLoading(false); });
  }, []);

  if (loading) return null;
  if (error)   return <div style={{ fontFamily: "'DM Mono', monospace", fontSize: 11, color: "var(--red)", padding: "8px 0" }}>{error}</div>;
  if (!data?.available) return (
    <div style={{ fontFamily: "'DM Mono', monospace", fontSize: 11, color: "var(--text-3)", padding: "8px 0" }}>
      Regime data available after first backfill or 1 week of live data.
    </div>
  );

  const regime    = REGIME_META[data.regime] ?? REGIME_META.neutral;
  const sectors   = data.sectors ?? {};
  const cyclical  = Object.entries(sectors).filter(([s]) => CYCLICAL.has(s)).sort((a, b) => b[1].score - a[1].score);
  const defensive = Object.entries(sectors).filter(([s]) => DEFENSIVE.has(s)).sort((a, b) => b[1].score - a[1].score);

  return (
    <div style={{
      background: "var(--bg-card)", border: "1px solid var(--border)",
      borderRadius: 8, padding: "18px 20px", marginTop: 14,
    }}>

      {/* Header row */}
      <div style={{ display: "flex", alignItems: "center", gap: 16, marginBottom: 16, flexWrap: "wrap" }}>
        <div style={{ fontFamily: "'DM Mono', monospace", fontSize: 11, color: "var(--text-3)", letterSpacing: "0.08em", textTransform: "uppercase", fontWeight: 500 }}>
          Regime
        </div>

        {/* Regime badge */}
        <div style={{
          fontFamily: "'DM Mono', monospace", fontSize: 12, fontWeight: 700,
          color: regime.color, background: regime.bg,
          padding: "3px 10px", borderRadius: 4, letterSpacing: "0.06em",
        }}>
          {regime.label}
        </div>

        {/* Regime confidence bar */}
        {data.regime_confidence != null && (
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <div style={{ background: "#1e2538", borderRadius: 3, height: 4, width: 56 }}>
              <div style={{
                width: `${Math.round(data.regime_confidence * 100)}%`,
                height: "100%", background: regime.color, borderRadius: 3, opacity: 0.8,
              }} />
            </div>
            <span style={{ fontFamily: "'DM Mono', monospace", fontSize: 10, color: "var(--text-3)" }}>
              {Math.round(data.regime_confidence * 100)}% conviction
            </span>
          </div>
        )}

        {/* Cyclical vs Defensive spread */}
        <div style={{ fontFamily: "'DM Mono', monospace", fontSize: 11, color: "var(--text-3)" }}>
          Cyclical <span style={{ color: "#4488ff" }}>{data.cyclical_avg?.toFixed(3)}</span>
          {" vs "}
          Defensive <span style={{ color: "#00ccaa" }}>{data.defensive_avg?.toFixed(3)}</span>
          <span style={{ color: data.spread > 0 ? "#4488ff" : data.spread < 0 ? "#00ccaa" : "var(--text-3)", marginLeft: 6 }}>
            ({data.spread > 0 ? "+" : ""}{data.spread?.toFixed(3)})
          </span>
        </div>

        {/* Leader */}
        {data.leader && (
          <div style={{ fontFamily: "'DM Mono', monospace", fontSize: 11, color: "var(--text-3)", marginLeft: "auto" }}>
            Leader: <span style={{ color: "var(--text-1)", fontWeight: 600 }}>{data.leader}</span>
            <span style={{ color: "var(--text-3)" }}> · {weeks(data.leader_streak)} confirmed</span>
          </div>
        )}
      </div>

      {/* Breakout / Extended alerts */}
      {(data.breakouts?.length > 0 || data.extended?.length > 0 || data.breakdowns?.length > 0) && (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 14 }}>
          {data.breakouts.map(s => (
            <div key={s} style={{
              fontFamily: "'DM Mono', monospace", fontSize: 10, fontWeight: 600,
              color: "#00d48c", background: "#00d48c18",
              border: "1px solid #00d48c44", borderRadius: 4, padding: "2px 8px",
            }}>↑ {s} BREAKOUT</div>
          ))}
          {data.extended.map(s => (
            <div key={s} style={{
              fontFamily: "'DM Mono', monospace", fontSize: 10, fontWeight: 600,
              color: "#ffaa00", background: "#ffaa0018",
              border: "1px solid #ffaa0044", borderRadius: 4, padding: "2px 8px",
            }}>⚠ {s} EXTENDED</div>
          ))}
          {data.breakdowns.map(s => (
            <div key={s} style={{
              fontFamily: "'DM Mono', monospace", fontSize: 10, fontWeight: 600,
              color: "#ff3355", background: "#ff335518",
              border: "1px solid #ff335544", borderRadius: 4, padding: "2px 8px",
            }}>↓ {s} BREAKDOWN</div>
          ))}
        </div>
      )}

      {/* Sector columns */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
        {[
          { label: "Cyclical", color: "#4488ff", entries: cyclical },
          { label: "Defensive", color: "#00ccaa", entries: defensive },
        ].map(({ label, color, entries }) => (
          <div key={label}>
            <div style={{
              fontFamily: "'DM Mono', monospace", fontSize: 10, fontWeight: 600,
              color, letterSpacing: "0.08em", textTransform: "uppercase",
              marginBottom: 6, paddingBottom: 4, borderBottom: `1px solid ${color}33`,
            }}>
              {label}
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              {entries.map(([sector, stat]) => {
                const sm    = SIGNAL_META[stat.signal] ?? SIGNAL_META.weak;
                const score = stat.score?.toFixed(3) ?? "—";
                return (
                  <div key={sector} style={{
                    display: "flex", alignItems: "center", justifyContent: "space-between",
                    background: "#0e1525", borderRadius: 5, padding: "6px 10px",
                    border: `1px solid ${sm.color}22`,
                  }}>
                    <div>
                      <div style={{ fontFamily: "'DM Mono', monospace", fontSize: 11, color: "var(--text-2)", fontWeight: 500 }}>{sector}</div>
                      <div style={{ fontFamily: "'DM Mono', monospace", fontSize: 10, color: sm.color, marginTop: 1 }}>{sm.label}</div>
                    </div>
                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      {/* Velocity arrow */}
                      {stat.velocity === "accelerating" && (
                        <span style={{ fontSize: 13, color: "#00d48c" }} title={`+${stat.velocity_5d?.toFixed(3)} 5d`}>↑</span>
                      )}
                      {stat.velocity === "decelerating" && (
                        <span style={{ fontSize: 13, color: "#ff3355" }} title={`${stat.velocity_5d?.toFixed(3)} 5d`}>↓</span>
                      )}
                      {stat.velocity === "flat" && (
                        <span style={{ fontSize: 13, color: "var(--text-3)" }} title="flat">→</span>
                      )}
                      <div style={{ textAlign: "right" }}>
                        <div style={{ fontFamily: "'DM Mono', monospace", fontSize: 13, color: sm.color, fontWeight: 600 }}>{score}</div>
                        <div style={{ fontFamily: "'DM Mono', monospace", fontSize: 10, color: "var(--text-3)", marginTop: 1 }}>{weeks(stat.streak_days)}</div>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
