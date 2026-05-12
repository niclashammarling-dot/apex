import React, { useState, useEffect } from "react";
import HoverTooltip, { TipRow, TipHeader } from "./HoverTooltip";

const SIGNAL_META = {
  breakout:   { label: "BREAKOUT",   color: "#00d48c" },
  trending:   { label: "TRENDING",   color: "#6aa0f0" },
  rising:     { label: "RISING",     color: "#7ab8ff" },
  extended:   { label: "EXTENDED",   color: "#ffaa00" },
  recovering: { label: "RECOVERING", color: "#a78bfa" },
  breakdown:  { label: "BREAKDOWN",  color: "#ff3355" },
  weak:       { label: "WEAK",       color: "#404040" },
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
  if (!days) return null;
  if (days < 7) return `${days}d`;
  return `${Math.round(days / 5)}w`;
}

function TickerRow({ t }) {
  const [tip, setTip] = useState(null);
  const sm = SIGNAL_META[t.signal] ?? SIGNAL_META.weak;
  const velocityColor = t.velocity === "accelerating" ? "#00d48c" : t.velocity === "decelerating" ? "#ff3355" : "var(--text-3)";
  return (
    <div
      onMouseMove={e => setTip({ x: e.clientX, y: e.clientY })}
      onMouseLeave={() => setTip(null)}
      style={{
        display: "flex", alignItems: "center", justifyContent: "space-between",
        padding: "4px 12px 4px 20px", borderTop: "1px solid #1e1e1c",
      }}
    >
      <HoverTooltip pos={tip}>
        <TipHeader>{t.ticker}</TipHeader>
        <TipRow label="Signal"         value={sm.label} color={sm.color} />
        <TipRow label="Pre-rotation"   value={t.pre_rotation ? "YES" : "NO"} color={t.pre_rotation ? "var(--green)" : "var(--text-3)"} />
        <TipRow label="Velocity"       value={t.velocity ?? "—"} color={velocityColor} />
        <TipRow label="Rotation Score" value={t.rotation_score != null ? (t.rotation_score * 100).toFixed(0) + "%" : "—"} />
      </HoverTooltip>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ fontFamily: "'Inconsolata', monospace", fontSize: 12, color: "var(--text-1)", fontWeight: 600, minWidth: 44 }}>
          {t.ticker}
        </span>
        <span style={{
          fontFamily: "'Inconsolata', monospace", fontSize: 9, fontWeight: 700,
          color: sm.color, background: `${sm.color}18`, border: `1px solid ${sm.color}44`,
          borderRadius: 3, padding: "1px 5px",
        }}>
          {sm.label}
        </span>
        {t.pre_rotation && (
          <span style={{
            fontFamily: "'Inconsolata', monospace", fontSize: 8, fontWeight: 700,
            color: "var(--green)", background: "#faff6910",
            border: "1px solid #faff6938", borderRadius: 3, padding: "1px 4px",
          }}>
            PRE-ROT
          </span>
        )}
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        {t.velocity === "accelerating" && <span style={{ fontSize: 10, color: "#00d48c" }}>↑</span>}
        {t.velocity === "decelerating" && <span style={{ fontSize: 10, color: "#ff3355" }}>↓</span>}
        <span style={{ fontFamily: "'Inconsolata', monospace", fontSize: 11, color: sm.color, fontWeight: 600 }}>
          {t.rotation_score != null ? (t.rotation_score * 100).toFixed(0) + "%" : "—"}
        </span>
      </div>
    </div>
  );
}

function SectorRow({ sector, stat, tickers, expanded, onToggle }) {
  const [tip, setTip] = useState(null);
  const sm      = SIGNAL_META[stat.signal] ?? SIGNAL_META.weak;
  const score   = stat.score?.toFixed(3) ?? "—";
  const streak  = weeks(stat.streak_days);
  const isOpen  = !!expanded[sector];
  const st      = tickers.filter(t => t.sector === sector);
  const velocityColor = stat.velocity === "accelerating" ? "#00d48c" : stat.velocity === "decelerating" ? "#ff3355" : "var(--text-3)";

  return (
    <>
      <div
        onMouseMove={e => setTip({ x: e.clientX, y: e.clientY })}
        onMouseLeave={() => setTip(null)}
        onClick={() => onToggle(sector)}
        style={{
          display: "flex", alignItems: "center", justifyContent: "space-between",
          padding: "8px 12px", borderTop: "1px solid var(--border)",
          cursor: "pointer", background: isOpen ? "#1a1a18" : "transparent",
        }}
      >
        <HoverTooltip pos={tip}>
          <TipHeader>{sector}</TipHeader>
          <TipRow label="Signal"   value={sm.label} color={sm.color} />
          <TipRow label="Streak"   value={streak ?? "—"} />
          <TipRow label="Score"    value={score} />
          <TipRow label="Velocity" value={stat.velocity ?? "—"} color={velocityColor} />
          {stat.etf && <TipRow label="ETF" value={stat.etf} />}
        </HoverTooltip>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <div style={{ width: 3, height: 20, background: sm.color, borderRadius: 2, flexShrink: 0, opacity: 0.7 }} />
          <span style={{ fontFamily: "'Inconsolata', monospace", fontSize: 12, color: "var(--text-2)", fontWeight: 500 }}>
            {sector}
          </span>
          <span style={{
            fontFamily: "'Inconsolata', monospace", fontSize: 9, fontWeight: 700,
            color: sm.color, background: `${sm.color}18`, border: `1px solid ${sm.color}44`,
            borderRadius: 3, padding: "1px 5px",
          }}>
            {sm.label}
          </span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          {stat.velocity === "accelerating" && <span style={{ fontSize: 11, color: "#00d48c" }} title={`+${stat.velocity_5d?.toFixed(3)} 5d`}>↑</span>}
          {stat.velocity === "decelerating" && <span style={{ fontSize: 11, color: "#ff3355" }} title={`${stat.velocity_5d?.toFixed(3)} 5d`}>↓</span>}
          {stat.velocity === "flat"         && <span style={{ fontSize: 11, color: "var(--text-3)" }}>→</span>}
          <div style={{ background: "#2a2a28", borderRadius: 2, height: 4, width: 40 }}>
            <div style={{ width: `${Math.round((stat.score ?? 0) * 100)}%`, height: "100%", background: sm.color, borderRadius: 2, opacity: 0.8 }} />
          </div>
          <span style={{ fontFamily: "'Inconsolata', monospace", fontSize: 12, color: sm.color, fontWeight: 600, minWidth: 38, textAlign: "right" }}>
            {score}
          </span>
          {streak && <span style={{ fontFamily: "'Inconsolata', monospace", fontSize: 10, color: "var(--text-3)", minWidth: 20 }}>{streak}</span>}
          <span style={{ fontFamily: "'Inconsolata', monospace", fontSize: 10, color: "var(--text-3)", minWidth: 8 }}>
            {isOpen ? "▲" : "▼"}
          </span>
        </div>
      </div>
      {isOpen && st.length > 0 && st.map(t => <TickerRow key={t.ticker} t={t} />)}
      {isOpen && st.length === 0 && (
        <div style={{ fontFamily: "'Inconsolata', monospace", fontSize: 10, color: "var(--text-3)", padding: "4px 20px 6px" }}>
          No tickers tracked yet.
        </div>
      )}
    </>
  );
}

function GroupHeader({ label, first = false }) {
  return (
    <div style={{
      padding: "6px 12px 4px",
      ...(!first && { borderTop: "1px solid var(--border)" }),
      display: "flex", alignItems: "center", gap: 6,
    }}>
      <span style={{
        fontFamily: "'Inconsolata', monospace", fontSize: 9, fontWeight: 600,
        color: "var(--text-3)", letterSpacing: "0.08em", textTransform: "uppercase",
      }}>
        {label}
      </span>
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

  if (loading) return null;
  if (error)   return <div style={{ fontFamily: "'Inconsolata', monospace", fontSize: 11, color: "var(--red)", padding: "8px 0" }}>{error}</div>;
  if (!data?.available) return (
    <div style={{ background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: 8, padding: "14px 16px" }}>
      <div style={{ fontFamily: "'Inconsolata', monospace", fontSize: 11, color: "var(--text-3)" }}>
        Regime data available after first backfill or 1 week of live data.
      </div>
    </div>
  );

  const regime    = REGIME_META[data.regime] ?? REGIME_META.neutral;
  const sectors   = data.sectors ?? {};
  const cyclical  = Object.entries(sectors).filter(([s]) => CYCLICAL.has(s)).sort((a, b) => b[1].score - a[1].score);
  const defensive = Object.entries(sectors).filter(([s]) => DEFENSIVE.has(s)).sort((a, b) => b[1].score - a[1].score);

  return (
    <div style={{ background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: 8, overflow: "hidden" }}>

      {/* Header */}
      <div style={{ padding: "12px 14px", borderBottom: "1px solid var(--border)" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          <span style={{ fontFamily: "'Inconsolata', monospace", fontSize: 10, color: "var(--text-3)", letterSpacing: "0.08em", textTransform: "uppercase", fontWeight: 500 }}>
            Sector Regime
          </span>
          <span style={{
            fontFamily: "'Inconsolata', monospace", fontSize: 11, fontWeight: 700,
            color: regime.color, background: regime.bg,
            padding: "2px 8px", borderRadius: 4, letterSpacing: "0.06em",
          }}>
            {regime.label}
          </span>
          {data.regime_confidence != null && data.regime !== "neutral" && (
            <>
              <div style={{ background: "#2a2a28", borderRadius: 3, height: 3, width: 44 }}>
                <div style={{ width: `${Math.round(data.regime_confidence * 100)}%`, height: "100%", background: regime.color, borderRadius: 3, opacity: 0.7 }} />
              </div>
              <span style={{ fontFamily: "'Inconsolata', monospace", fontSize: 10, color: "var(--text-3)" }}>
                {Math.round(data.regime_confidence * 100)}%
              </span>
            </>
          )}
          <span style={{ fontFamily: "'Inconsolata', monospace", fontSize: 10, color: "var(--text-3)", marginLeft: "auto" }}>
            {data.cyclical_avg?.toFixed(3)} <span style={{ opacity: 0.5 }}>cyc</span>
            {"  "}
            {data.defensive_avg?.toFixed(3)} <span style={{ opacity: 0.5 }}>def</span>
            <span style={{ color: "var(--text-3)", marginLeft: 6 }}>
              ({data.spread > 0 ? "+" : ""}{data.spread?.toFixed(3)})
            </span>
          </span>
        </div>
      </div>

      {/* Sector rows */}
      <GroupHeader label="Cyclical" first />
      {cyclical.map(([sector, stat]) => (
        <SectorRow key={sector} sector={sector} stat={stat} tickers={tickers} expanded={expanded} onToggle={toggle} />
      ))}

      <GroupHeader label="Defensive" />
      {defensive.map(([sector, stat]) => (
        <SectorRow key={sector} sector={sector} stat={stat} tickers={tickers} expanded={expanded} onToggle={toggle} />
      ))}

    </div>
  );
}
