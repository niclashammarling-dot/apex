import React, { useState, useEffect } from "react";

const SIGNAL_META = {
  recovering: { label: "RECOVERING", color: "#a78bfa" },
  rising:     { label: "RISING",     color: "#7ab8ff" },
  breakout:   { label: "BREAKOUT",   color: "#00d48c" },
  trending:   { label: "TRENDING",   color: "#4488ff" },
  extended:   { label: "EXTENDED",   color: "#ffaa00" },
  weak:       { label: "WEAK",       color: "#4a5568" },
};

function Pill({ children, color, bg, border }) {
  return (
    <span style={{
      fontFamily: "'DM Mono', monospace", fontSize: 11, fontWeight: 600,
      color, background: bg ?? `${color}18`, border: `1px solid ${border ?? color + "44"}`,
      borderRadius: 4, padding: "2px 8px", whiteSpace: "nowrap",
    }}>
      {children}
    </span>
  );
}

function ProbBar({ probability }) {
  const pct   = Math.round(probability * 100);
  const color = pct >= 40 ? "#00d48c" : pct >= 25 ? "#4488ff" : "#888";
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
      <div style={{ background: "#1e2538", borderRadius: 3, height: 4, width: 60 }}>
        <div style={{ width: `${pct}%`, height: "100%", background: color, borderRadius: 3 }} />
      </div>
      <span style={{ fontFamily: "'DM Mono', monospace", fontSize: 11, color, fontWeight: 600 }}>
        {pct}%
      </span>
    </div>
  );
}

function TickerList({ sectorTickers, accentColor }) {
  if (!sectorTickers.length) return (
    <div style={{ fontFamily: "'DM Mono', monospace", fontSize: 10, color: "var(--text-3)", padding: "4px 2px" }}>
      No tickers tracked in this sector yet.
    </div>
  );
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
      {sectorTickers.map((t, i) => {
        const tsm      = SIGNAL_META[t.signal] ?? SIGNAL_META.weak;
        const pct      = Math.round(t.rotation_score * 100);
        const barColor = pct >= 60 ? "#00d48c" : pct >= 40 ? "#4488ff" : "#4a5568";
        return (
          <div key={t.ticker} style={{
            display: "flex", alignItems: "center", justifyContent: "space-between",
            background: i === 0 ? "#ffffff05" : "transparent",
            borderRadius: 3, padding: "4px 6px",
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
              {t.velocity === "accelerating" && <span style={{ fontSize: 11, color: "#00d48c" }} title={`+${t.velocity_5d?.toFixed(3)} 5d`}>↑</span>}
              {t.velocity === "decelerating" && <span style={{ fontSize: 11, color: "#ff3355" }} title={`${t.velocity_5d?.toFixed(3)} 5d`}>↓</span>}
              <div style={{ background: "#1e2538", borderRadius: 3, height: 3, width: 40 }}>
                <div style={{ width: `${pct}%`, height: "100%", background: barColor, borderRadius: 3 }} />
              </div>
              <span style={{
                fontFamily: "'DM Mono', monospace", fontSize: 10,
                color: barColor, fontWeight: 600, minWidth: 28, textAlign: "right",
              }}>
                {pct}%
              </span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

export default function RotationForecast() {
  const [data,     setData]     = useState(null);
  const [tickers,  setTickers]  = useState([]);
  const [loading,  setLoading]  = useState(true);
  const [error,    setError]    = useState(null);
  const [expanded, setExpanded] = useState(null);  // null = not yet initialised

  useEffect(() => {
    Promise.all([
      fetch("/api/sectors/rotation-forecast").then(r => r.ok ? r.json() : Promise.reject(r.status)),
      fetch("/api/sectors/rotation-leaderboard?limit=50").then(r => r.ok ? r.json() : []),
    ])
      .then(([forecast, lb]) => {
        setData(forecast);
        setTickers(lb);
        // Default top 3 to open on first load
        if (forecast?.likely_next) {
          const init = {};
          forecast.likely_next.slice(0, 3).forEach(({ sector }) => { init[sector] = true; });
          setExpanded(init);
        }
        setLoading(false);
      })
      .catch(e => { setError(`Failed to load forecast (${e})`); setLoading(false); });
  }, []);

  function toggle(sector) {
    setExpanded(prev => ({ ...(prev ?? {}), [sector]: !prev?.[sector] }));
  }

  function sectorTickers(sector, limit = 5) {
    return tickers.filter(t => t.sector === sector).slice(0, limit);
  }

  if (loading) return null;
  if (error)   return <div style={{ fontFamily: "'DM Mono', monospace", fontSize: 11, color: "var(--red)", padding: "8px 0" }}>{error}</div>;
  if (!data?.available) return (
    <div style={{ fontFamily: "'DM Mono', monospace", fontSize: 11, color: "var(--text-3)", padding: "8px 0" }}>
      Rotation forecast available after backfill.
    </div>
  );

  const ct           = data.confirmed_transition;
  const predecessors = data.predecessors ?? [];

  return (
    <div style={{
      background: "var(--bg-card)", border: "1px solid var(--border)",
      borderRadius: 8, padding: "18px 20px", marginTop: 14,
    }}>

      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 16, flexWrap: "wrap" }}>
        <div style={{ fontFamily: "'DM Mono', monospace", fontSize: 11, color: "var(--text-3)", letterSpacing: "0.08em", textTransform: "uppercase", fontWeight: 500 }}>
          Rotation Forecast
        </div>
        {data.regime_conditioned != null && (
          <div style={{
            fontFamily: "'DM Mono', monospace", fontSize: 9, fontWeight: 600,
            color:      data.regime_conditioned ? "#00d48c" : "#888",
            background: data.regime_conditioned ? "#00d48c12" : "#88888812",
            border:    `1px solid ${data.regime_conditioned ? "#00d48c44" : "#88888844"}`,
            borderRadius: 3, padding: "1px 6px", whiteSpace: "nowrap",
          }}>
            {data.regime_conditioned
              ? `✓ regime-conditioned · n=${data.regime_sample_size}`
              : `unconditional matrix${data.regime_sample_size != null ? ` · n=${data.regime_sample_size}` : ""}`}
          </div>
        )}
      </div>

      {/* Rotation path */}
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", marginBottom: 16 }}>
        {predecessors.map((p, i) => (
          <React.Fragment key={p}>
            <Pill color={i === 0 ? "#2a3350" : "#4a5568"}
                  bg={i === 0 ? "#1a2030" : undefined}
                  border={i === 0 ? "#2a3350" : undefined}>
              {p}
            </Pill>
            <span style={{ color: "var(--text-3)", fontSize: 14 }}>→</span>
          </React.Fragment>
        ))}
        <Pill color="#00d48c" bg="#00d48c14" border="#00d48c66">
          {data.leader} ▶ NOW
        </Pill>
        <span style={{ color: "var(--text-3)", fontSize: 14 }}>→</span>
        <span style={{ fontFamily: "'DM Mono', monospace", fontSize: 11, color: "var(--text-3)" }}>?</span>
        {ct && (
          <div style={{ marginLeft: 8 }}>
            <Pill color="#00d48c" bg="#00d48c0a" border="#00d48c33">
              ✓ {ct.from}→{ct.to} confirmed ({Math.round(ct.probability * 100)}% historical)
            </Pill>
          </div>
        )}
      </div>

      {/* Likely next */}
      <div>
        <div style={{ fontFamily: "'DM Mono', monospace", fontSize: 10, color: "var(--text-3)", letterSpacing: "0.08em", textTransform: "uppercase", marginBottom: 8 }}>
          When {data.leader} fades
        </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {data.likely_next.map(({ sector, probability, historical_prob, sector_score, sector_signal }, i) => {
              const isTop3      = i < 3;
              const isWatching  = data.watching?.some(w => w.sector === sector);
              const isOpen      = !!(expanded ?? {})[sector];
              // Score label mirrors SectorGrid: STRONG ≥0.55, WATCH ≥0.40, WEAK otherwise
              const sectorLabel = sector_signal ? (SIGNAL_META[sector_signal] ?? null) : null;
              const st         = sectorTickers(sector, isTop3 ? 3 : 5);
              const pct        = Math.round(probability * 100);
              const probColor  = pct >= 40 ? "#00d48c" : pct >= 25 ? "#4488ff" : "#888";
              const rankColors = ["#00d48c", "#4488ff", "#7ab8ff"];

              return (
                <div key={sector} style={{
                  background: isWatching ? "#00d48c08" : "#0e1525",
                  border: `1px solid ${isTop3 ? probColor + "44" : isWatching ? "#00d48c33" : "#1e2538"}`,
                  borderRadius: 5, overflow: "hidden",
                }}>
                  {/* Header */}
                  <div
                    onClick={() => toggle(sector)}
                    style={{
                      display: "flex", alignItems: "center", justifyContent: "space-between",
                      padding: isTop3 ? "8px 10px" : "6px 10px",
                      cursor: "pointer",
                    }}
                  >
                    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                      {isTop3 && (
                        <span style={{
                          fontFamily: "'DM Mono', monospace", fontSize: 9, fontWeight: 700,
                          color: rankColors[i], background: rankColors[i] + "18",
                          border: `1px solid ${rankColors[i]}44`,
                          borderRadius: 3, padding: "1px 5px", minWidth: 18, textAlign: "center",
                        }}>
                          #{i + 1}
                        </span>
                      )}
                      <span style={{
                        fontFamily: "'DM Mono', monospace", fontSize: 12,
                        color: isTop3 ? "var(--text-1)" : isWatching ? "var(--text-1)" : "var(--text-2)",
                        fontWeight: isTop3 || isWatching ? 600 : 400,
                      }}>
                        {sector}
                      </span>
                      {sectorLabel && (
                        <span style={{
                          fontFamily: "'DM Mono', monospace", fontSize: 9, fontWeight: 700,
                          color: sectorLabel.color, background: `${sectorLabel.color}18`,
                          border: `1px solid ${sectorLabel.color}44`,
                          borderRadius: 3, padding: "1px 5px",
                        }}>
                          {sectorLabel.label}
                        </span>
                      )}
                    </div>
                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 2 }}>
                        <ProbBar probability={probability} />
                        {historical_prob != null && (
                          <span style={{ fontFamily: "'DM Mono', monospace", fontSize: 9, color: "var(--text-3)", opacity: 0.6 }}>
                            {Math.round(historical_prob * 100)}% hist
                          </span>
                        )}
                      </div>
                    </div>
                  </div>

                  {/* Ticker list — always open for top 3, toggle for rest */}
                  {isOpen && (
                    <div style={{
                      borderTop: `1px solid ${isTop3 ? probColor + "22" : isWatching ? "#00d48c22" : "#1e2538"}`,
                      padding: "6px 10px 8px",
                      background: "#0a1020",
                    }}>
                      <TickerList sectorTickers={st} accentColor={probColor} />
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
    </div>
  );
}
