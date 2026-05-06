import React, { useState } from "react";
import { COMPANY_NAMES } from "../companyNames.js";

const CYCLICAL  = ["Technology", "Financials", "Industrials", "ConsumerDisc",
                   "Energy", "Materials", "Communication"];
const DEFENSIVE = ["Utilities", "Healthcare", "ConsumerStaples", "RealEstate"];

const SIGNAL_META = {
  recovering: { label: "RECOVERING", color: "#a78bfa" },
  breakout:   { label: "BREAKOUT",   color: "#00d48c" },
  trending:   { label: "TRENDING",   color: "#6aa0f0" },
  rising:     { label: "RISING",     color: "#7ab8ff" },
  extended:   { label: "EXTENDED",   color: "#ffaa00" },
  breakdown:  { label: "BREAKDOWN",  color: "#ff3355" },
  weak:       { label: "WEAK",       color: "#404040" },
};

function scoreColor(score) {
  if (score === null || score === undefined) return "var(--text-3)";
  if (score >= 0.55) return "var(--green)";
  if (score >= 0.40) return "#e8a020";
  return "var(--red)";
}

function weeks(days) {
  if (!days) return null;
  if (days < 7) return `${days}d`;
  return `${Math.round(days / 5)}w`;
}

function TickerRow({ t }) {
  const color  = scoreColor(t.signal_score);
  const sm     = SIGNAL_META[t.signal] ?? SIGNAL_META.weak;
  const streak = weeks(t.streak_days);
  return (
    <div style={{
      display: "flex", alignItems: "center", justifyContent: "space-between",
      padding: "5px 12px 5px 20px",
      borderTop: "1px solid #1e1e1c",
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0, flex: 1 }}>
        <span style={{
          fontFamily: "'Inconsolata', monospace", fontSize: 12,
          color: "var(--text-1)", fontWeight: 600, minWidth: 44,
        }}>
          {t.ticker}
        </span>
        {COMPANY_NAMES[t.ticker] && (
          <span style={{
            fontFamily: "'Inconsolata', monospace", fontSize: 10, color: "var(--text-3)",
            overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
          }}>
            {COMPANY_NAMES[t.ticker]}
          </span>
        )}
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
        <span style={{
          fontFamily: "'Inconsolata', monospace", fontSize: 9, fontWeight: 700,
          color: sm.color, background: `${sm.color}18`, border: `1px solid ${sm.color}44`,
          borderRadius: 3, padding: "1px 5px",
        }}>
          {sm.label}{streak ? <span style={{ fontWeight: 400, opacity: 0.7 }}> · {streak}</span> : null}
        </span>
        <span style={{
          fontFamily: "'Inconsolata', monospace", fontSize: 12, color, fontWeight: 600,
          minWidth: 38, textAlign: "right",
        }}>
          {t.signal_score?.toFixed(3) ?? "—"}
        </span>
        <span style={{ fontSize: 11, color: t.trend === "up" ? "var(--green)" : t.trend === "down" ? "var(--red)" : "var(--text-3)", minWidth: 10 }}>
          {t.trend === "up" ? "↑" : t.trend === "down" ? "↓" : "→"}
        </span>
      </div>
    </div>
  );
}

function SectorRow({ s, expanded, setExpanded }) {
  const color     = scoreColor(s.avg_signal);
  const score     = s.avg_signal != null ? s.avg_signal.toFixed(3) : "—";
  const isExpanded = !!expanded[s.sector];
  const hasData   = s.tickers?.length > 0;

  return (
    <>
      <div
        onClick={() => hasData && setExpanded(e => ({ ...e, [s.sector]: !e[s.sector] }))}
        style={{
          display: "flex", alignItems: "center", justifyContent: "space-between",
          padding: "9px 12px",
          borderTop: "1px solid var(--border)",
          cursor: hasData ? "pointer" : "default",
          background: isExpanded ? "#1a1a18" : "transparent",
          transition: "background 0.15s",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          {/* score accent stripe */}
          <div style={{ width: 3, height: 22, background: color, borderRadius: 2, flexShrink: 0 }} />
          <div>
            <div style={{ fontFamily: "'Inconsolata', monospace", fontSize: 12, color: "var(--text-2)", fontWeight: 500 }}>
              {s.sector}
            </div>
            <div style={{ fontFamily: "'Inconsolata', monospace", fontSize: 10, color: "var(--text-3)", marginTop: 1 }}>
              {s.etf}
            </div>
          </div>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div style={{ background: "#2a2a28", borderRadius: 2, height: 4, width: 52 }}>
            <div style={{
              width: `${Math.round((s.avg_signal ?? 0) * 100)}%`,
              height: "100%", background: color, borderRadius: 2,
              transition: "width 0.4s ease",
            }} />
          </div>
          <span style={{
            fontFamily: "'Inconsolata', monospace", fontSize: 13, color, fontWeight: 600,
            minWidth: 40, textAlign: "right",
          }}>
            {score}
          </span>
          <span style={{
            fontSize: 12,
            color: s.trend === "up" ? "var(--green)" : s.trend === "down" ? "var(--red)" : "var(--text-3)",
            minWidth: 12,
          }}>
            {s.trend === "up" ? "↑" : s.trend === "down" ? "↓" : "→"}
          </span>
          {hasData && (
            <span style={{ fontFamily: "'Inconsolata', monospace", fontSize: 10, color: "var(--text-3)", minWidth: 8 }}>
              {isExpanded ? "▲" : "▼"}
            </span>
          )}
        </div>
      </div>

      {isExpanded && s.tickers?.map(t => <TickerRow key={t.ticker} t={t} />)}
    </>
  );
}

function SectorGroup({ label, color, entries, expanded, setExpanded }) {
  return (
    <div style={{
      background: "var(--bg-card)", border: "1px solid var(--border)",
      borderRadius: 8, overflow: "hidden",
    }}>
      <div style={{
        padding: "8px 12px",
        borderBottom: `1px solid ${color}33`,
        display: "flex", alignItems: "center", gap: 8,
      }}>
        <div style={{ width: 3, height: 12, background: color, borderRadius: 2 }} />
        <span style={{
          fontFamily: "'Inconsolata', monospace", fontSize: 10, fontWeight: 600,
          color, letterSpacing: "0.08em", textTransform: "uppercase",
        }}>
          {label}
        </span>
      </div>
      {entries.map(s => (
        <SectorRow key={s.sector} s={s} expanded={expanded} setExpanded={setExpanded} />
      ))}
    </div>
  );
}

export default function SectorGrid({ sectors }) {
  const [expanded, setExpanded] = useState({});

  if (!sectors.length) {
    return (
      <div style={{ color: "var(--text-3)", fontFamily: "'Inconsolata', monospace", fontSize: 13, padding: 24 }}>
        No sector data yet — waiting for first poll…
      </div>
    );
  }

  const cyclical  = sectors.filter(s => CYCLICAL.includes(s.sector)).sort((a, b) => (b.avg_signal ?? 0) - (a.avg_signal ?? 0));
  const defensive = sectors.filter(s => DEFENSIVE.includes(s.sector)).sort((a, b) => (b.avg_signal ?? 0) - (a.avg_signal ?? 0));

  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
      <SectorGroup label="Cyclical"  color="var(--text-3)" entries={cyclical}  expanded={expanded} setExpanded={setExpanded} />
      <SectorGroup label="Defensive" color="var(--text-3)" entries={defensive} expanded={expanded} setExpanded={setExpanded} />
    </div>
  );
}
