import React, { useState, useEffect } from "react";

const SIGNAL_META = {
  recovering: { label: "RECOVERING", color: "var(--t-accent)" },
  rising:     { label: "RISING",     color: "#7ab8ff" },
  breakout:   { label: "BREAKOUT",   color: "var(--t-accent)" },
  trending:   { label: "TRENDING",   color: "#6aa0f0" },
  extended:   { label: "EXTENDED",   color: "var(--t-amber)" },
  breakdown:  { label: "BREAKDOWN",  color: "var(--t-red)" },
  weak:       { label: "WEAK",       color: "var(--t-text-3)" },
};

const cls = (...a) => a.filter(Boolean).join(" ");

function ProbBar({ probability }) {
  const pct   = Math.round(probability * 100);
  const color = pct >= 40 ? "var(--t-accent)" : pct >= 25 ? "#6aa0f0" : "var(--t-text-3)";
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
      <div style={{ background: "var(--t-grid)", borderRadius: 3, height: 4, width: 60 }}>
        <div style={{ width: `${pct}%`, height: "100%", background: color, borderRadius: 3 }} />
      </div>
      <span style={{ fontFamily: "var(--mono)", fontSize: 11, color, fontWeight: 700, minWidth: 28 }}>
        {pct}%
      </span>
    </div>
  );
}

function TickerList({ sectorTickers }) {
  if (!sectorTickers.length) return (
    <div style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--t-text-3)", padding: "4px 2px" }}>
      No tickers tracked in this sector yet.
    </div>
  );
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
      {sectorTickers.map((t, i) => {
        const tsm      = SIGNAL_META[t.signal] ?? SIGNAL_META.weak;
        const pct      = Math.round(t.rotation_score * 100);
        const barColor = pct >= 60 ? "var(--t-accent)" : pct >= 40 ? "#6aa0f0" : "var(--t-text-3)";
        return (
          <div key={t.ticker} style={{
            display: "flex", alignItems: "center", justifyContent: "space-between",
            background: i === 0 ? "rgba(255,255,255,0.03)" : "transparent",
            borderRadius: 3, padding: "4px 6px",
          }}>
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <span style={{ fontFamily: "var(--mono)", fontSize: 12, color: "var(--t-text-1)", fontWeight: 600, minWidth: 52 }}>
                {t.ticker}
              </span>
              <span className="t-pill" style={{ color: tsm.color, background: `${tsm.color}18`, borderColor: `${tsm.color}44`, fontSize: 9 }}>
                {tsm.label}
              </span>
              {t.pre_rotation && (
                <span className="t-pill t-pill-paper" style={{ fontSize: 8 }}>PRE-ROT</span>
              )}
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
              {t.velocity === "accelerating" && <span style={{ fontSize: 11, color: "var(--t-accent)" }} title={`+${t.velocity_5d?.toFixed(3)} 5d`}>↑</span>}
              {t.velocity === "decelerating" && <span style={{ fontSize: 11, color: "var(--t-red)" }} title={`${t.velocity_5d?.toFixed(3)} 5d`}>↓</span>}
              <div style={{ background: "var(--t-grid)", borderRadius: 3, height: 3, width: 40 }}>
                <div style={{ width: `${pct}%`, height: "100%", background: barColor, borderRadius: 3 }} />
              </div>
              <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: barColor, fontWeight: 600, minWidth: 28, textAlign: "right" }}>
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
  const [expanded, setExpanded] = useState(null);

  useEffect(() => {
    Promise.all([
      fetch("/api/sectors/rotation-forecast").then(r => r.ok ? r.json() : Promise.reject(r.status)),
      fetch("/api/sectors/rotation-leaderboard?limit=50").then(r => r.ok ? r.json() : []),
    ])
      .then(([forecast, lb]) => {
        setData(forecast);
        setTickers(lb);
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
  if (error) return (
    <div className="t-card">
      <div className="t-card-body" style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--t-red)" }}>{error}</div>
    </div>
  );
  if (!data?.available) return (
    <div className="t-card">
      <div className="t-card-body" style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--t-text-3)" }}>
        Rotation forecast available after backfill.
      </div>
    </div>
  );

  const ct           = data.confirmed_transition;
  const predecessors = data.predecessors ?? [];

  return (
    <div className="t-card">
      <div className="t-card-head">
        <div className="t-card-title">ROTATION FORECAST</div>
        <div className="t-row" style={{ gap: 8 }}>
          {data.regime_conditioned != null && (
            <span className="t-pill" style={{
              color:       data.regime_conditioned ? "var(--t-accent)" : "var(--t-text-3)",
              background:  data.regime_conditioned ? "rgba(0,212,140,0.07)" : "transparent",
              borderColor: data.regime_conditioned ? "rgba(0,212,140,0.27)" : "var(--t-border)",
              fontSize: 9,
            }}>
              {data.regime_conditioned
                ? `REGIME-COND · n=${data.regime_sample_size}`
                : `UNCOND${data.regime_sample_size != null ? ` · n=${data.regime_sample_size}` : ""}`}
            </span>
          )}
        </div>
      </div>

      {/* Rotation path */}
      <div style={{ padding: "10px 14px", borderBottom: "1px solid var(--t-border)", display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        {predecessors.map((p, i) => (
          <React.Fragment key={p}>
            <span className="t-pill" style={{ color: "var(--t-text-3)", fontSize: 10, opacity: i === 0 ? 0.8 : 0.5 }}>{p}</span>
            <span style={{ color: "var(--t-text-3)", fontSize: 13 }}>→</span>
          </React.Fragment>
        ))}
        <span className="t-pill" style={{ color: "var(--t-accent)", background: "rgba(0,212,140,0.08)", borderColor: "rgba(0,212,140,0.4)", fontSize: 10 }}>
          {data.leader} ▶ NOW
        </span>
        <span style={{ color: "var(--t-text-3)", fontSize: 13 }}>→</span>
        <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--t-text-3)" }}>?</span>
        {ct && (
          <span className="t-pill" style={{ color: "var(--t-accent)", background: "rgba(0,212,140,0.05)", borderColor: "rgba(0,212,140,0.2)", fontSize: 9, marginLeft: 4 }}>
            ✓ {ct.from}→{ct.to} confirmed ({Math.round(ct.probability * 100)}% hist)
          </span>
        )}
      </div>

      {/* When leader fades */}
      <div style={{ padding: "10px 14px 4px" }}>
        <div className="t-meta" style={{ marginBottom: 8 }}>WHEN {data.leader.toUpperCase()} FADES</div>
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          {data.likely_next.map(({ sector, probability, historical_prob, sector_signal }, i) => {
            const isTop3     = i < 3;
            const isWatching = data.watching?.some(w => w.sector === sector);
            const isOpen     = !!(expanded ?? {})[sector];
            const sectorLabel = sector_signal ? (SIGNAL_META[sector_signal] ?? null) : null;
            const st         = sectorTickers(sector, isTop3 ? 3 : 5);
            const pct        = Math.round(probability * 100);
            const rankColor  = ["var(--t-accent)", "#6aa0f0", "#7ab8ff"][i] ?? "var(--t-text-3)";

            return (
              <div key={sector} style={{
                border: `1px solid ${isTop3 ? "rgba(255,255,255,0.08)" : "var(--t-border)"}`,
                borderRadius: 4, overflow: "hidden",
                background: isWatching ? "rgba(250,255,105,0.03)" : "rgba(255,255,255,0.01)",
              }}>
                <div
                  onClick={() => toggle(sector)}
                  style={{
                    display: "flex", alignItems: "center", justifyContent: "space-between",
                    padding: isTop3 ? "8px 10px" : "6px 10px", cursor: "pointer",
                  }}
                >
                  <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                    {isTop3 && (
                      <span className="t-pill" style={{ color: rankColor, background: `${rankColor}18`, borderColor: `${rankColor}44`, fontSize: 9, minWidth: 22, textAlign: "center" }}>
                        #{i + 1}
                      </span>
                    )}
                    <span style={{ fontFamily: "var(--mono)", fontSize: 12, color: isTop3 || isWatching ? "var(--t-text-1)" : "var(--t-text-2)", fontWeight: isTop3 || isWatching ? 600 : 400 }}>
                      {sector}
                    </span>
                    {sectorLabel && (
                      <span className="t-pill" style={{ color: sectorLabel.color, background: `${sectorLabel.color}18`, borderColor: `${sectorLabel.color}44`, fontSize: 9 }}>
                        {sectorLabel.label}
                      </span>
                    )}
                  </div>
                  <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 2 }}>
                    <ProbBar probability={probability} />
                    {historical_prob != null && (
                      <span style={{ fontFamily: "var(--mono)", fontSize: 9, color: "var(--t-text-3)", opacity: 0.6 }}>
                        {Math.round(historical_prob * 100)}% hist
                      </span>
                    )}
                  </div>
                </div>

                {isOpen && (
                  <div style={{ borderTop: "1px solid var(--t-border)", padding: "6px 10px 8px", background: "rgba(0,0,0,0.15)" }}>
                    <TickerList sectorTickers={st} />
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
      <div style={{ height: 10 }} />
    </div>
  );
}
