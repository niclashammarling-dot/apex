import React, { useState } from "react";
import { COMPANY_NAMES } from "../companyNames.js";

function scoreColor(score) {
  if (score === null || score === undefined) return "var(--text-3)";
  if (score >= 0.55) return "var(--green)";
  if (score >= 0.40) return "#e8a020";
  return "var(--red)";
}

function scoreLabel(score) {
  if (score === null || score === undefined) return "—";
  if (score >= 0.55) return "STRONG";
  if (score >= 0.40) return "WATCH";
  return "WEAK";
}

function ScoreBar({ score }) {
  const pct = score !== null && score !== undefined ? Math.round(score * 100) : 0;
  const color = scoreColor(score);
  return (
    <div style={{ background: "#1e2538", borderRadius: 3, height: 5, marginTop: 10 }}>
      <div style={{ width: `${pct}%`, height: "100%", background: color, borderRadius: 3, transition: "width 0.4s ease" }} />
    </div>
  );
}

function MiniBar({ value, max = 1, color = "#3a4a6a" }) {
  const pct = value !== null && value !== undefined ? Math.round((value / max) * 100) : 0;
  return (
    <div style={{ background: "#1e2538", borderRadius: 2, height: 4, width: 40 }}>
      <div style={{ width: `${Math.min(100, Math.max(0, pct))}%`, height: "100%", background: color, borderRadius: 2 }} />
    </div>
  );
}

function TickerRow({ t }) {
  const color = scoreColor(t.signal_score);
  const rsiColor = t.rsi >= 70 ? "#e8a020" : t.rsi <= 30 ? "var(--red)" : "#5a8fe8";
  return (
    <div style={{
      display: "flex", alignItems: "center", justifyContent: "space-between",
      padding: "8px 0", borderTop: "1px solid #1e2538",
    }}>
      <div style={{ width: 52 }}>
        <div style={{ fontFamily: "'DM Mono', monospace", fontSize: 13, color: "var(--text-1)", fontWeight: 500 }}>{t.ticker}</div>
        {COMPANY_NAMES[t.ticker] && <div style={{ fontFamily: "'DM Mono', monospace", fontSize: 10, color: "var(--text-3)", marginTop: 1 }}>{COMPANY_NAMES[t.ticker]}</div>}
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1, margin: "0 12px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span style={{ fontFamily: "'DM Mono', monospace", fontSize: 10, color: "var(--text-3)", width: 22 }}>MOM</span>
          <MiniBar value={t.momentum_score} color="#5a8fe8" />
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span style={{ fontFamily: "'DM Mono', monospace", fontSize: 10, color: "var(--text-3)", width: 22 }}>VOL</span>
          <MiniBar value={t.volume_score} color="#8a5ae8" />
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span style={{ fontFamily: "'DM Mono', monospace", fontSize: 10, color: "var(--text-3)", width: 22 }}>RSI</span>
          <MiniBar value={t.rsi} max={100} color={rsiColor} />
        </div>
      </div>
      <div style={{ textAlign: "right" }}>
        <div style={{ fontFamily: "'DM Mono', monospace", fontSize: 14, color, fontWeight: 600 }}>
          {t.signal_score?.toFixed(3) ?? "—"}
        </div>
        {t.rsi !== null && t.rsi !== undefined && (
          <div style={{ fontFamily: "'DM Mono', monospace", fontSize: 11, color: rsiColor, marginTop: 2 }}>
            RSI {Math.round(t.rsi)}
          </div>
        )}
      </div>
    </div>
  );
}

export default function SectorGrid({ sectors }) {
  const [expanded, setExpanded] = useState({});

  if (!sectors.length) {
    return (
      <div style={{ color: "var(--text-3)", fontFamily: "'DM Mono', monospace", fontSize: 13, padding: 24 }}>
        No sector data yet — waiting for first poll…
      </div>
    );
  }

  return (
    <div style={{
      display: "grid",
      gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))",
      gap: 14,
    }}>
      {sectors.map((s) => {
        const color = scoreColor(s.avg_signal);
        const score = s.avg_signal !== null ? s.avg_signal.toFixed(3) : "—";
        const updated = s.last_updated
          ? new Date(s.last_updated).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" })
          : null;
        const isExpanded = expanded[s.sector];

        return (
          <div key={s.sector} style={{
            background: "var(--bg-card)",
            border: `1px solid var(--border)`,
            borderRadius: 8,
            padding: "16px 18px",
            borderTop: `3px solid ${color}`,
            cursor: s.tickers?.length ? "pointer" : "default",
          }}
            onClick={() => s.tickers?.length && setExpanded(e => ({ ...e, [s.sector]: !e[s.sector] }))}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
              <div>
                <div style={{ fontFamily: "'DM Mono', monospace", fontSize: 12, color: "var(--text-2)", letterSpacing: "0.08em", textTransform: "uppercase", marginBottom: 4, fontWeight: 500 }}>
                  {s.sector}
                </div>
                <div style={{ fontFamily: "'DM Mono', monospace", fontSize: 12, color: "var(--text-3)" }}>
                  {s.etf}
                </div>
              </div>
              <div style={{ textAlign: "right" }}>
                <div style={{ display: "flex", alignItems: "baseline", gap: 6, justifyContent: "flex-end" }}>
                  <div style={{ fontFamily: "'DM Mono', monospace", fontSize: 24, fontWeight: 600, color, lineHeight: 1 }}>
                    {score}
                  </div>
                  {s.trend === "up"   && <span style={{ fontSize: 14, color: "var(--green)" }}>↑</span>}
                  {s.trend === "down" && <span style={{ fontSize: 14, color: "var(--red)" }}>↓</span>}
                  {s.trend === "flat" && <span style={{ fontSize: 14, color: "var(--text-3)" }}>→</span>}
                </div>
                <div style={{ fontFamily: "'DM Mono', monospace", fontSize: 11, color, marginTop: 3, letterSpacing: "0.06em", fontWeight: 600 }}>
                  {scoreLabel(s.avg_signal)}
                </div>
              </div>
            </div>

            <ScoreBar score={s.avg_signal} />

            <div style={{ marginTop: 12, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <div style={{ fontFamily: "'DM Mono', monospace", fontSize: 11, color: "var(--text-3)" }}>
                {s.tickers?.length ? (isExpanded ? "▲ hide tickers" : "▼ show tickers") : "no data"}
              </div>
              {updated && (
                <div style={{ fontFamily: "'DM Mono', monospace", fontSize: 11, color: "var(--text-3)" }}>
                  {updated}
                </div>
              )}
            </div>

            {isExpanded && s.tickers?.length > 0 && (
              <div style={{ marginTop: 8 }}>
                {s.tickers.map(t => <TickerRow key={t.ticker} t={t} />)}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
