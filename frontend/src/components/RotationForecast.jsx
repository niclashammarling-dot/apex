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
  const pct = Math.round(probability * 100);
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

export default function RotationForecast() {
  const [data,    setData]    = useState(null);
  const [loading, setLoading] = useState(true);
  const [error,   setError]   = useState(null);

  useEffect(() => {
    fetch("/api/sectors/rotation-forecast")
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then(d => { setData(d); setLoading(false); })
      .catch(e => { setError(`Failed to load forecast (${e})`); setLoading(false); });
  }, []);

  if (loading) return null;
  if (error)   return <div style={{ fontFamily: "'DM Mono', monospace", fontSize: 11, color: "var(--red)", padding: "8px 0" }}>{error}</div>;
  if (!data?.available) return (
    <div style={{ fontFamily: "'DM Mono', monospace", fontSize: 11, color: "var(--text-3)", padding: "8px 0" }}>
      Rotation forecast available after backfill.
    </div>
  );

  const ct = data.confirmed_transition;

  return (
    <div style={{
      background: "var(--bg-card)", border: "1px solid var(--border)",
      borderRadius: 8, padding: "18px 20px", marginTop: 14,
    }}>

      {/* Header */}
      <div style={{ fontFamily: "'DM Mono', monospace", fontSize: 11, color: "var(--text-3)", letterSpacing: "0.08em", textTransform: "uppercase", fontWeight: 500, marginBottom: 16 }}>
        Rotation Forecast
      </div>

      {/* Rotation path */}
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", marginBottom: 16 }}>
        {data.predecessor && (
          <>
            <Pill color="#4a5568">{data.predecessor}</Pill>
            <span style={{ color: "var(--text-3)", fontSize: 14 }}>→</span>
          </>
        )}
        <Pill color="#00d48c" bg="#00d48c14" border="#00d48c66">
          {data.leader} ▶ NOW
        </Pill>
        <span style={{ color: "var(--text-3)", fontSize: 14 }}>→</span>
        <span style={{ fontFamily: "'DM Mono', monospace", fontSize: 11, color: "var(--text-3)" }}>?</span>

        {/* Confirmed badge */}
        {ct && (
          <div style={{ marginLeft: 8 }}>
            <Pill color="#00d48c" bg="#00d48c0a" border="#00d48c33">
              ✓ {ct.from}→{ct.to} confirmed ({Math.round(ct.probability * 100)}% historical)
            </Pill>
          </div>
        )}
      </div>

      {/* Likely next + watching */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>

        {/* Likely successors */}
        <div>
          <div style={{ fontFamily: "'DM Mono', monospace", fontSize: 10, color: "var(--text-3)", letterSpacing: "0.08em", textTransform: "uppercase", marginBottom: 8 }}>
            When {data.leader} fades
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {data.likely_next.map(({ sector, probability }) => {
              const isWatching = data.watching?.some(w => w.sector === sector);
              return (
                <div key={sector} style={{
                  display: "flex", alignItems: "center", justifyContent: "space-between",
                  background: isWatching ? "#00d48c08" : "#0e1525",
                  border: `1px solid ${isWatching ? "#00d48c33" : "#1e2538"}`,
                  borderRadius: 5, padding: "6px 10px",
                }}>
                  <div style={{ fontFamily: "'DM Mono', monospace", fontSize: 12, color: isWatching ? "var(--text-1)" : "var(--text-2)", fontWeight: isWatching ? 600 : 400 }}>
                    {sector}
                    {isWatching && <span style={{ color: "#00d48c", marginLeft: 6, fontSize: 10 }}>← WATCH</span>}
                  </div>
                  <ProbBar probability={probability} />
                </div>
              );
            })}
          </div>
        </div>

        {/* Watching — predicted next AND already moving */}
        <div>
          <div style={{ fontFamily: "'DM Mono', monospace", fontSize: 10, color: "var(--text-3)", letterSpacing: "0.08em", textTransform: "uppercase", marginBottom: 8 }}>
            Pre-confirmed setups
          </div>
          {data.watching?.length > 0 ? (
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {data.watching.map(w => {
                const sm = SIGNAL_META[w.signal] ?? SIGNAL_META.weak;
                return (
                  <div key={w.sector} style={{
                    background: `${sm.color}0d`,
                    border: `1px solid ${sm.color}44`,
                    borderRadius: 5, padding: "8px 12px",
                  }}>
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 4 }}>
                      <div style={{ fontFamily: "'DM Mono', monospace", fontSize: 12, color: "var(--text-1)", fontWeight: 600 }}>
                        {w.sector}
                      </div>
                      <Pill color={sm.color}>{sm.label}</Pill>
                    </div>
                    <div style={{ fontFamily: "'DM Mono', monospace", fontSize: 10, color: "var(--text-3)" }}>
                      Predicted successor · {Math.round(w.probability * 100)}% historical probability
                    </div>
                  </div>
                );
              })}
            </div>
          ) : (
            <div style={{ fontFamily: "'DM Mono', monospace", fontSize: 11, color: "var(--text-3)", padding: "8px 0" }}>
              No predicted successors are showing early signals yet.
            </div>
          )}
        </div>

      </div>
    </div>
  );
}
