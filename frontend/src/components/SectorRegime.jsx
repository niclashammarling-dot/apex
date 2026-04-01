import React, { useState, useEffect } from "react";

const SIGNAL_META = {
  breakout:   { label: "BREAKOUT",   color: "#00d48c" },
  trending:   { label: "TRENDING",   color: "#4488ff" },
  rising:     { label: "RISING",     color: "#7ab8ff" },
  extended:   { label: "EXTENDED",   color: "#ffaa00" },
  recovering: { label: "RECOVERING", color: "#a78bfa" },
  breakdown:  { label: "BREAKDOWN",  color: "#ff3355" },
  weak:       { label: "WEAK",       color: "#4a5568" },
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
  return `${Math.round(days / 5)}w`;
}

function TickerRows({ tickers }) {
  if (!tickers.length) return (
    <div style={{ fontFamily: "'DM Mono', monospace", fontSize: 10, color: "var(--text-3)", padding: "4px 2px" }}>
      No tickers tracked in this sector yet.
    </div>
  );
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
      {tickers.map((t, i) => {
        const tsm = SIGNAL_META[t.signal] ?? SIGNAL_META.weak;
        return (
          <div key={t.ticker} style={{
            display: "flex", alignItems: "center", justifyContent: "space-between",
            background: i === 0 ? "#ffffff05" : "transparent",
            borderRadius: 3, padding: "3px 6px",
          }}>
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <span style={{
                fontFamily: "'DM Mono', monospace", fontSize: 12,
                color: "var(--text-1)", fontWeight: 600, minWidth: 52,
              }}>
                {t.ticker}
              </span>
              <span style={{
                fontFamily: "'DM Mono', monospace", fontSize: 9, fontWeight: 700,
                color: tsm.color, background: `${tsm.color}18`,
                border: `1px solid ${tsm.color}44`, borderRadius: 3, padding: "1px 5px",
              }}>
                {tsm.label}
              </span>
              {t.pre_rotation && (
                <span style={{
                  fontFamily: "'DM Mono', monospace", fontSize: 8, fontWeight: 700,
                  color: "#00d48c", background: "#00d48c12",
                  border: "1px solid #00d48c44", borderRadius: 3, padding: "1px 4px",
                }}>
                  PRE-ROT
                </span>
              )}
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
              {t.velocity === "accelerating" && <span style={{ fontSize: 11, color: "#00d48c" }}>↑</span>}
              {t.velocity === "decelerating" && <span style={{ fontSize: 11, color: "#ff3355" }}>↓</span>}
              <span style={{
                fontFamily: "'DM Mono', monospace", fontSize: 11,
                color: tsm.color, fontWeight: 600,
              }}>
                {t.rotation_score != null ? (t.rotation_score * 100).toFixed(0) + "%" : "—"}
              </span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

export default function SectorRegime() {
  const [data,     setData]     = useState(null);
  const [tickers,  setTickers]  = useState([]);
  const [error,    setError]    = useState(null);
  const [loading,  setLoading]  = useState(true);
  const [expanded, setExpanded] = useState({});

  useEffect(() => {
    Promise.all([
      fetch("/api/sectors/regime").then(r => r.ok ? r.json() : Promise.reject(r.status)),
      fetch("/api/sectors/rotation-leaderboard?limit=50").then(r => r.ok ? r.json() : []),
    ])
      .then(([regime, lb]) => { setData(regime); setTickers(lb); setLoading(false); })
      .catch(e => { setError(`Failed to load regime (${e})`); setLoading(false); });
  }, []);

  function toggle(sector) {
    setExpanded(prev => ({ ...prev, [sector]: !prev[sector] }));
  }

  function sectorTickers(sector) {
    return tickers.filter(t => t.sector === sector);
  }

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
      <div style={{ display: "flex", alignItems: "center", gap: 16, marginBottom: 8, flexWrap: "wrap" }}>
        <div style={{ fontFamily: "'DM Mono', monospace", fontSize: 11, color: "var(--text-3)", letterSpacing: "0.08em", textTransform: "uppercase", fontWeight: 500 }}>
          Regime
        </div>

        <div style={{
          fontFamily: "'DM Mono', monospace", fontSize: 12, fontWeight: 700,
          color: regime.color, background: regime.bg,
          padding: "3px 10px", borderRadius: 4, letterSpacing: "0.06em",
        }}>
          {regime.label}
        </div>

        {data.regime_confidence != null && data.regime !== "neutral" && (
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

        <div style={{ fontFamily: "'DM Mono', monospace", fontSize: 11, color: "var(--text-3)" }}>
          Cyclical <span style={{ color: "#4488ff" }}>{data.cyclical_avg?.toFixed(3)}</span>
          {" vs "}
          Defensive <span style={{ color: "#00ccaa" }}>{data.defensive_avg?.toFixed(3)}</span>
          <span style={{ color: data.spread > 0 ? "#4488ff" : data.spread < 0 ? "#00ccaa" : "var(--text-3)", marginLeft: 6 }}>
            ({data.spread > 0 ? "+" : ""}{data.spread?.toFixed(3)})
          </span>
        </div>

      </div>

      {/* Clarification subtitle */}
      <div style={{
        fontFamily: "'DM Mono', monospace", fontSize: 10, color: "var(--text-3)",
        marginBottom: 14, lineHeight: 1.5,
      }}>
        Sector-level trend signals derived from 365d of daily averages —
        distinct from the live per-ticker scores shown in the sector cards.
      </div>

      {/* Sector columns */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
        {[
          { label: "Cyclical",  color: "#4488ff", entries: cyclical  },
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
                const sm       = SIGNAL_META[stat.signal] ?? SIGNAL_META.weak;
                const score    = stat.score?.toFixed(3) ?? "—";
                const isOpen   = !!expanded[sector];
                const st       = sectorTickers(sector);
                return (
                  <div key={sector} style={{
                    background: "#0e1525", borderRadius: 5,
                    border: `1px solid ${sm.color}22`, overflow: "hidden",
                  }}>
                    {/* Sector header row — clickable */}
                    <div
                      onClick={() => toggle(sector)}
                      style={{
                        display: "flex", alignItems: "center", justifyContent: "space-between",
                        padding: "6px 10px", cursor: "pointer",
                      }}
                    >
                      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                        <div>
                          <div style={{ fontFamily: "'DM Mono', monospace", fontSize: 11, color: "var(--text-2)", fontWeight: 500 }}>{sector}</div>
                          <div style={{ marginTop: 1 }}>
                            <span style={{ fontFamily: "'DM Mono', monospace", fontSize: 10, color: sm.color }}>{sm.label}</span>
                          </div>
                        </div>
                      </div>
                      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
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

                    {/* Ticker list — shown when expanded */}
                    {isOpen && (
                      <div style={{
                        borderTop: `1px solid ${sm.color}22`,
                        padding: "6px 10px 8px",
                        background: "#0a1020",
                      }}>
                        <TickerRows tickers={st} />
                      </div>
                    )}
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
