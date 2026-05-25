import React, { useState, useEffect, useMemo } from "react";
import "../apex-dashboard.css";
import BacktestPanel from "./BacktestPanel.jsx";
import TradeLog      from "./TradeLog.jsx";
import NightLog      from "./NightLog.jsx";
import RegimeBayes   from "./RegimeBayes.jsx";
import Watchlist     from "./Watchlist.jsx";
import DemoTradeLog  from "./DemoTradeLog.jsx";
import LiveTradeLog  from "./LiveTradeLog.jsx";
import LiveOrderLog  from "./LiveOrderLog.jsx";
import HoverTooltip, { TipRow, TipHeader } from "./HoverTooltip.jsx";
import { COMPANY_NAMES } from "../companyNames.js";

const fmt$ = (n, d = 2) => "$" + Math.abs(n).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
const fmtPct = (n, d = 2) => (n >= 0 ? "+" : "−") + Math.abs(n * 100).toFixed(d) + "%";
const cls = (...a) => a.filter(Boolean).join(" ");

function EquityHero({ D, mode }) {
  const eq = mode === "live" ? D.liveEq : D.equity;
  if (!eq || eq.length < 2) {
    return (
      <div className="t-card">
        <div className="t-card-head"><div className="t-card-title">EQUITY · {mode === "live" ? "LIVE" : "DEMO"}</div></div>
        <div className="t-card-body" style={{ color: "var(--t-text-3)", fontFamily: "var(--mono)", fontSize: 12 }}>No equity data</div>
      </div>
    );
  }
  const w = 760, h = 200;
  const vals = eq.map(p => p.value);
  const min = Math.min(...vals), max = Math.max(...vals);
  const pad = (max - min) * 0.08 || 10;
  const yMin = min - pad, yMax = max + pad;
  const x = i => (i / (eq.length - 1)) * w;
  const y = v => h - ((v - yMin) / (yMax - yMin)) * h;
  const start = eq[0].value, end = eq[eq.length - 1].value;
  const pnl = end - start, pct = pnl / start;
  const path = eq.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(p.value).toFixed(1)}`).join(" ");
  const fillPath = path + ` L${w} ${h} L0 ${h} Z`;
  const peakIdx = eq.reduce((m, p, i) => p.value > eq[m].value ? i : m, 0);
  const peak = eq[peakIdx].value;
  const wallet = mode === "live" ? D.liveWallet : D.wallet;

  // Duration: calendar days from first closed trade to today
  const closedPts = eq.filter(p => p.ts && /^\d{4}-\d{2}-\d{2}/.test(p.ts));
  const firstTs = closedPts[0]?.ts;
  const calDays = firstTs ? Math.round((Date.now() - new Date(firstTs).getTime()) / 86400000) : null;

  // MTD: last closed-trade point before the current month as baseline
  const curYM = new Date().toISOString().slice(0, 7);
  const mtdBase = [...closedPts].reverse().find(p => p.ts.slice(0, 7) < curYM);
  const mtdPct = mtdBase ? (end - mtdBase.value) / mtdBase.value : null;

  return (
    <div className="t-card">
      <div className="t-card-head">
        <div className="t-card-title">EQUITY · {mode === "live" ? "LIVE" : "DEMO"}</div>
        <div className="t-row" style={{ gap: 18 }}>
          {calDays != null && <span className="t-meta">{calDays}D</span>}
          {mtdPct != null && <span className="t-meta">MTD · {fmtPct(mtdPct)}</span>}
          {wallet.sharpe > 0 && <span className="t-meta">SHARPE · {wallet.sharpe.toFixed(2)}</span>}
        </div>
      </div>
      <div className="t-card-body" style={{ paddingTop: 14 }}>
        <div className="t-row" style={{ justifyContent: "space-between", alignItems: "baseline", marginBottom: 10 }}>
          <div>
            <div className="t-num-xl">{fmt$(end)}</div>
            <div style={{ marginTop: 2, fontSize: 12 }}>
              <span className={pnl >= 0 ? "t-pos" : "t-neg"}>{pnl >= 0 ? "+" : "−"}{fmt$(Math.abs(pnl)).replace("$","")}</span>
              <span className="t-meta" style={{ marginLeft: 6 }}>{fmtPct(pct)}</span>
              <span className="t-meta" style={{ marginLeft: 12 }}>vs start {fmt$(start, 0)}</span>
            </div>
          </div>
          <div style={{ textAlign: "right" }}>
            <div className="t-meta">PEAK · {fmt$(peak)}</div>
            <div className="t-meta">DD · <span className="t-neg">{((peak - end)/peak * -100).toFixed(2)}%</span></div>
          </div>
        </div>
        <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" style={{ width: "100%", height: h, display: "block" }}>
          <defs>
            <linearGradient id="t-eq-fill" x1="0" x2="0" y1="0" y2="1">
              <stop offset="0%" stopColor="var(--t-accent)" stopOpacity="0.28" />
              <stop offset="100%" stopColor="var(--t-accent)" stopOpacity="0" />
            </linearGradient>
          </defs>
          {[0.25, 0.5, 0.75].map(g => (
            <line key={g} x1="0" x2={w} y1={h*g} y2={h*g} stroke="var(--t-grid)" strokeDasharray="2 4" />
          ))}
          <line x1="0" x2={w} y1={y(peak * 0.985)} y2={y(peak * 0.985)} stroke="var(--t-accent)" strokeOpacity="0.35" strokeDasharray="3 4" />
          <path d={fillPath} fill="url(#t-eq-fill)" />
          <path d={path} fill="none" stroke="var(--t-accent)" strokeWidth="1.5" />
          <circle cx={x(eq.length - 1)} cy={y(end)} r="3" fill="var(--t-accent)" />
          <circle cx={x(eq.length - 1)} cy={y(end)} r="6" fill="none" stroke="var(--t-accent)" strokeOpacity="0.4">
            <animate attributeName="r" from="3" to="10" dur="1.6s" repeatCount="indefinite" />
            <animate attributeName="stroke-opacity" from="0.55" to="0" dur="1.6s" repeatCount="indefinite" />
          </circle>
          <circle cx={x(peakIdx)} cy={y(peak)} r="2" fill="var(--t-text-2)" />
          <text x={x(peakIdx) + 5} y={y(peak) - 5} fill="var(--t-text-3)" fontFamily="var(--mono)" fontSize="10">PEAK</text>
        </svg>
      </div>
    </div>
  );
}

function Wallet({ D, mode }) {
  const w = mode === "live" ? D.liveWallet : D.wallet;
  if (!w) return null;
  const total = (w.unrealized ?? 0) + (w.realized ?? 0);
  return (
    <div className="t-card">
      <div className="t-card-head">
        <div className="t-card-title">WALLET</div>
        <div className="t-meta">{mode === "live" ? "ALPACA · PAPER" : "SIM · $2,000"}</div>
      </div>
      <div className="t-card-body">
        <div className="t-num-lg">{fmt$(w.balance)}</div>
        <div style={{ marginTop: 4, fontSize: 12 }}>
          <span className={total >= 0 ? "t-pos" : "t-neg"}>{total >= 0 ? "+" : "−"}{fmt$(Math.abs(total)).replace("$","")}</span>
          <span className="t-meta" style={{ marginLeft: 8 }}>{fmtPct((w.balance - w.starting)/w.starting)}</span>
        </div>
        <div style={{ marginTop: 14, display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, fontSize: 12 }}>
          <KV k="CASH"       v={fmt$(w.cash ?? 0)} />
          <KV k="INVESTED"   v={fmt$(w.invested ?? 0)} />
          <KV k="REALIZED"   v={<span className={(w.realized ?? 0) >= 0 ? "t-pos" : "t-neg"}>{fmt$(w.realized ?? 0)}</span>} />
          <KV k="UNREALIZED" v={<span className={(w.unrealized ?? 0) >= 0 ? "t-pos" : "t-neg"}>{fmt$(w.unrealized ?? 0)}</span>} />
          <KV k="WIN RATE"   v={((w.winRate ?? 0) * 100).toFixed(0) + "%"} />
          <KV k="TRADES"     v={w.trades ?? 0} />
          {w.drawdown > 0 && <KV k="DRAWDOWN" v={<span className="t-neg">{fmtPct(-w.drawdown)}</span>} />}
          {w.sharpe > 0 && <KV k="SHARPE" v={w.sharpe.toFixed(2)} />}
        </div>
      </div>
    </div>
  );
}

function KV({ k, v }) {
  return (
    <div>
      <div className="t-meta" style={{ fontSize: 10 }}>{k}</div>
      <div style={{ fontFamily: "var(--mono)", color: "var(--t-text-1)" }}>{v}</div>
    </div>
  );
}

const DIAL_ORDER = ["TECH","HLTH","ENER","INDU","FIN","CDIS","CSTP","COMM","UTIL","MATR","REIT","SEMI","DEF"];

function RegimeDial({ D }) {
  if (!D.regime || D.regime.length === 0) return null;
  const R = 110, cx = 130, cy = 130;
  return (
    <div className="t-card">
      <div className="t-card-head">
        <div className="t-card-title">SECTOR REGIME · BAYES</div>
        <div className="t-meta">EOD · 16:15 ET</div>
      </div>
      <div className="t-card-body" style={{ display: "grid", gridTemplateColumns: "260px 1fr", gap: 16 }}>
        <svg viewBox="0 0 260 260" style={{ width: 260, height: 260 }}>
          {[0.25, 0.5, 0.75, 1].map(r => (
            <circle key={r} cx={cx} cy={cy} r={R * r} fill="none" stroke="var(--t-grid)" strokeDasharray="2 3" />
          ))}
          <circle cx={cx} cy={cy} r={R * 0.5} fill="none" stroke="var(--t-accent)" strokeOpacity="0.35" strokeDasharray="3 3" />
          {D.regime.filter(s => !s.excluded).map((s, i) => {
            const slot = DIAL_ORDER.indexOf(s.code);
            const ang = ((slot >= 0 ? slot : i) / DIAL_ORDER.length) * Math.PI * 2 - Math.PI / 2;
            const rr = R * s.posterior;
            const x2 = cx + Math.cos(ang) * R;
            const y2 = cy + Math.sin(ang) * R;
            const xp = cx + Math.cos(ang) * rr;
            const yp = cy + Math.sin(ang) * rr;
            const lx = cx + Math.cos(ang) * (R + 14);
            const ly = cy + Math.sin(ang) * (R + 14);
            return (
              <g key={s.code}>
                <line x1={cx} y1={cy} x2={x2} y2={y2} stroke="var(--t-grid)" />
                <line x1={cx} y1={cy} x2={xp} y2={yp} stroke={s.excluded ? "var(--t-text-3)" : "var(--t-accent)"} strokeWidth="2" opacity={s.excluded ? 0.35 : 1} />
                <circle cx={xp} cy={yp} r="3" fill={s.excluded ? "var(--t-text-3)" : "var(--t-accent)"} />
                <text x={lx} y={ly} fill="var(--t-text-3)" fontFamily="var(--mono)" fontSize="9" textAnchor="middle" dominantBaseline="middle">{s.code}</text>
              </g>
            );
          })}
          <text x={cx} y={cy - 6} fill="var(--t-text-3)" fontFamily="var(--mono)" fontSize="9" textAnchor="middle">FLOOR</text>
          <text x={cx} y={cy + 8} fill="var(--t-text-2)" fontFamily="var(--mono)" fontSize="12" textAnchor="middle">0.50</text>
        </svg>
        <div>
          <table className="t-tbl">
            <thead><tr><th>RNK</th><th>SECTOR</th><th style={{textAlign:"right"}}>POST</th><th style={{textAlign:"right"}}>Δ</th></tr></thead>
            <tbody>
              {D.regime.filter(s => !s.excluded).slice(0, 8).map((s, i) => (
                <tr key={s.code}>
                  <td>{(i+1).toString().padStart(2,"0")}</td>
                  <td>{s.name}</td>
                  <td style={{textAlign:"right"}}>{s.posterior.toFixed(3)}</td>
                  <td style={{textAlign:"right"}} className={s.delta >= 0 ? "t-pos" : "t-neg"}>
                    {s.delta >= 0 ? "+" : ""}{s.delta.toFixed(3)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function LockChain({ D }) {
  if (!D.funnel || D.funnel.length === 0) return null;
  const total = D.funnel[0].count || 1;
  return (
    <div className="t-card">
      <div className="t-card-head">
        <div className="t-card-title">GATE FUNNEL · 5-LOCK</div>
        <div className="t-meta">SESSION</div>
      </div>
      <div className="t-card-body">
        <div className="t-funnel">
          {D.funnel.map(f => {
            const pct = f.count / total;
            return (
              <div key={f.stage} className="t-funnel-row">
                <div className="t-funnel-label">{f.label}</div>
                <div className="t-funnel-track">
                  <div className="t-funnel-fill" style={{
                    width: (pct * 100) + "%",
                    background: f.stage === "TRADE_EXECUTED" ? "var(--t-accent)"
                      : f.stage.startsWith("SKIPPED") ? "var(--t-text-3)"
                      : "var(--t-amber)",
                    opacity: f.stage === "TRADE_EXECUTED" ? 1 : 0.55,
                  }} />
                </div>
                <div className="t-funnel-count">{f.count.toString().padStart(2, "0")}</div>
                <div className="t-funnel-pct">{(pct * 100).toFixed(0).padStart(2)}%</div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function Positions({ D, mode }) {
  const positions = mode === "live" ? (D.livePositions || []) : (D.positions || []);
  const maxPos = mode === "live" ? (D.settings?.live?.max_positions ?? 6) : (D.settings?.demo?.max_positions ?? 6);
  return (
    <div className="t-card">
      <div className="t-card-head">
        <div className="t-card-title">OPEN POSITIONS · {positions.length}/{maxPos}</div>
        <div className="t-meta">25% SECTOR CAP</div>
      </div>
      <div className="t-card-body" style={{ padding: 0 }}>
        {positions.length === 0 ? (
          <div style={{ padding: "16px 14px", fontFamily: "var(--mono)", fontSize: 11, color: "var(--t-text-3)" }}>
            No open positions
          </div>
        ) : (
          <table className="t-tbl t-tbl-pad">
            <thead>
              <tr><th>SYM</th><th>QTY</th><th>ENTRY</th><th>MKT</th><th>P&L</th><th>%</th><th>HOLD</th><th>STOP</th></tr>
            </thead>
            <tbody>
              {positions.map(p => (
                <tr key={p.sym}>
                  <td><strong>{p.sym}</strong> <span className="t-meta">{p.sector}</span></td>
                  <td>{p.qty}</td>
                  <td>{(p.entry ?? 0).toFixed(2)}</td>
                  <td>{(p.mkt ?? 0).toFixed(2)}</td>
                  <td className={(p.pnl ?? 0) >= 0 ? "t-pos" : "t-neg"}>{(p.pnl ?? 0) >= 0 ? "+" : ""}{(p.pnl ?? 0).toFixed(2)}</td>
                  <td className={(p.pct ?? 0) >= 0 ? "t-pos" : "t-neg"}>{(p.pct ?? 0) >= 0 ? "+" : ""}{(p.pct ?? 0).toFixed(2)}%</td>
                  <td>{p.held}d</td>
                  <td>
                    {p.trail
                      ? <span className="t-pill t-pill-lock">PROFIT-LOCK</span>
                      : <span className="t-meta">{p.sl ? `SL ${p.sl.toFixed(2)}` : "—"}</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

const TIP_GREEN  = "#00d48c";
const TIP_AMBER  = "#ffaa00";
const TIP_RED    = "#ff3355";
const TIP_DIM    = "#666";

const scoreColor = s => s >= 0.7 ? TIP_GREEN : s >= 0.55 ? TIP_AMBER : TIP_DIM;

function TickerTooltip({ t }) {
  const sigColor   = SIGNAL_COLORS[t.signal] ?? TIP_DIM;
  const sigLabel   = SIGNAL_LABELS[t.signal]  ?? "WEAK";
  const streak     = t.streak_days >= 5 ? `${Math.round(t.streak_days / 5)}W` : t.streak_days > 0 ? `${t.streak_days}D` : null;
  const trendColor = t.trendDir === "up" ? TIP_GREEN : t.trendDir === "down" ? TIP_RED : TIP_DIM;
  return (
    <>
      <TipHeader>{t.sym}{COMPANY_NAMES[t.sym] ? ` — ${COMPANY_NAMES[t.sym]}` : ""}</TipHeader>
      <TipRow label="Signal" value={streak ? `${sigLabel} · ${streak}` : sigLabel} color={sigColor} />
      <TipRow label="Score"  value={t.score.toFixed(4)} color={scoreColor(t.score)} />
      <TipRow label="Trend"  value={t.trendDir} color={trendColor} />
      <TipRow label="Price"  value={t.price ? `$${t.price.toFixed(2)}` : "—"} />
      <TipRow label="Chg"    value={`${t.chg >= 0 ? "+" : ""}${t.chg.toFixed(2)}%`} color={t.chg >= 0 ? TIP_GREEN : TIP_RED} />
      <TipRow label="VOL"    value={t.volume != null ? t.volume.toFixed(3) : "—"} color={t.volume >= 0.6 ? TIP_GREEN : t.volume >= 0.35 ? TIP_AMBER : TIP_DIM} />
      <TipRow label="RSI"    value={t.rsi != null ? Math.round(t.rsi).toString() : "—"} color={t.rsi >= 80 ? TIP_AMBER : t.rsi >= 60 ? TIP_GREEN : TIP_DIM} />
      <TipRow label="MOM"    value={t.momentum != null ? t.momentum.toFixed(3) : "—"} color={t.momentum >= 0.55 ? TIP_GREEN : t.momentum >= 0.40 ? TIP_AMBER : TIP_RED} />
    </>
  );
}

function Tile({ t }) {
  const [tip, setTip] = useState(null);
  const c    = t.score >= 0.7 ? "t-accent" : t.score >= 0.55 ? "t-amber" : "t-text-3";
  const volC = t.volume >= 0.6 ? "var(--t-accent)" : t.volume >= 0.35 ? "var(--t-amber)" : "var(--t-text-3)";
  const rsiC = t.rsi >= 80 ? "var(--t-amber)" : t.rsi >= 60 ? "var(--t-accent)" : "var(--t-text-3)";
  const momC = t.momentum >= 0.55 ? "var(--t-accent)" : t.momentum >= 0.40 ? "var(--t-amber)" : "var(--t-neg)";
  return (
    <div
      className={cls("t-tile", t.watchlist && "t-tile-watch")}
      onMouseMove={e => setTip({ x: e.clientX, y: e.clientY })}
      onMouseLeave={() => setTip(null)}
    >
      <HoverTooltip pos={tip}><TickerTooltip t={t} /></HoverTooltip>
      <div className="t-tile-sym">{t.sym}</div>
      <div className="t-tile-score" style={{ color: `var(--${c})` }}>{t.score.toFixed(2)}</div>
      <div className={cls("t-tile-chg", (t.chg ?? 0) >= 0 ? "t-pos" : "t-neg")}>
        {(t.chg ?? 0) >= 0 ? "+" : ""}{(t.chg ?? 0).toFixed(2)}%
      </div>
      <div className="t-tile-spark">
        {[
          { label: "V", value: t.volume,   color: volC, max: 1   },
          { label: "R", value: t.rsi,      color: rsiC, max: 100 },
          { label: "M", value: t.momentum, color: momC, max: 1   },
        ].map(({ label, value, color, max }) => {
          const pct = value != null ? Math.max(2, Math.min(100, (value / max) * 100)) : 2;
          return (
            <div key={label} className="t-tile-spark-col">
              <div className="t-tile-spark-track">
                <div style={{ height: `${pct}%`, background: color, borderRadius: "1px 1px 0 0" }} />
              </div>
              <span className="t-tile-spark-label" style={{ color }}>{label}</span>
            </div>
          );
        })}
      </div>
      <div className="t-tile-signal" style={{ color: SIGNAL_COLORS[t.signal] ?? "var(--t-text-3)" }}>
        {SIGNAL_LABELS[t.signal] ?? "WEK"}{sigStreak(t.streak_days)}
      </div>
    </div>
  );
}

const SIGNAL_COLORS = {
  breakout:   "#00d48c",
  trending:   "#6aa0f0",
  rising:     "#6aa0f0",
  extended:   "#ffaa00",
  breakdown:  "#ff3355",
  recovering: "#ffaa00",
  weak:       null,
};
const SIGNAL_LABELS = {
  breakout:   "BRK",
  trending:   "TRD",
  rising:     "RIS",
  extended:   "EXT",
  breakdown:  "BDN",
  recovering: "REC",
  weak:       "WEK",
};
function sigStreak(days) {
  if (days <= 0) return "";
  return days < 5 ? ` ${days}D` : ` ${Math.round(days / 5)}W`;
}

function SectorGrid({ D }) {
  const bySector = useMemo(() => {
    const m = {};
    (D.tickers || []).forEach(t => (m[t.sector] = m[t.sector] || []).push(t));
    return m;
  }, [D.tickers]);

  const sectors = D.SECTORS || [];
  const excluded = D.EXCLUDED || new Set();

  return (
    <div className="t-card">
      <div className="t-card-head">
        <div className="t-card-title">SIGNAL GRID · {(D.tickers || []).length} TICKERS</div>
        <div className="t-row" style={{ gap: 16, fontSize: 10 }}>
          <Legend swatch="var(--t-accent)" label="≥ 0.70" />
          <Legend swatch="var(--t-amber)"  label="0.55–0.70" />
          <Legend swatch="var(--t-text-3)" label="< 0.55" />
        </div>
      </div>
      <div className="t-card-body">
        <div className="t-sectors">
          {sectors.map(s => (
            <div key={s.code} className={cls("t-sec", excluded.has(s.code) && "t-sec-excluded")}>
              <div className="t-sec-head">
                <div className="t-sec-code">{s.code}</div>
                <div className="t-sec-name">{s.name}</div>
                {s.avgSignal != null && (() => {
                  const sc = s.avgSignal >= 0.55 ? "var(--t-accent)" : s.avgSignal >= 0.40 ? "var(--t-amber)" : "var(--t-text-3)";
                  const arrow = s.trend === "up" ? "↑" : s.trend === "down" ? "↓" : null;
                  return (
                    <span className="t-sec-score" style={{ color: sc }}>
                      {s.avgSignal.toFixed(3)}{arrow && <span style={{ fontSize: 9, marginLeft: 2 }}>{arrow}</span>}
                    </span>
                  );
                })()}
                <div className="t-sec-etf">{s.etf}</div>
              </div>
              <div className="t-sec-tiles">
                {(bySector[s.code] || []).map(t => <Tile key={t.sym} t={t} />)}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function Legend({ swatch, label, border }) {
  return (
    <div style={{ display: "inline-flex", alignItems: "center", gap: 6, fontFamily: "var(--mono)", color: "var(--t-text-3)" }}>
      <span style={{ width: 10, height: 10, background: swatch, border: border ? `1px solid ${border}` : "none", display: "inline-block" }} />
      {label}
    </div>
  );
}

const GATE_OUTCOME = {
  TRADE_EXECUTED:          { label: "EXECUTED",  cls: "t-gate-x"    },
  TRADE_REJECTED:          { label: "REJECTED",  cls: "t-gate-filt" },
  FILTERED_ELIGIBILITY:    { label: "L1 FAIL",   cls: "t-gate-filt" },
  FILTERED_L1:             { label: "L2 FAIL",   cls: "t-gate-filt" },
  FILTERED_L2:             { label: "L3 FAIL",   cls: "t-gate-filt" },
  FILTERED_LEADING:        { label: "L4 FAIL",   cls: "t-gate-filt" },
  FILTERED_L3:             { label: "L5 FAIL",   cls: "t-gate-filt" },
  FILTERED_OVERFLOW_QUANT: { label: "OVERFLOW",  cls: "t-gate-skip" },
  SKIPPED_OPEN:            { label: "HELD",      cls: "t-gate-skip" },
  SKIPPED_COOLOFF:         { label: "COOLOFF",   cls: "t-gate-skip" },
};

const GATE_LEADING_LABELS = {
  relative_strength:   "RS",
  put_call_ratio:      "PC",
  unusual_calls:       "UC",
  volume_accumulation: "VA",
};

function getGateLockStates(decision) {
  if (decision === "FILTERED_ELIGIBILITY") return [false, null,  null,  null,  null];
  if (decision === "FILTERED_L1")          return [true,  false, null,  null,  null];
  if (decision === "FILTERED_L2")          return [true,  true,  false, null,  null];
  if (decision === "FILTERED_LEADING")     return [true,  true,  true,  false, null];
  if (decision === "FILTERED_L3")          return [true,  true,  true,  true,  false];
  return                                          [true,  true,  true,  true,  true ];
}

function GateFeed({ D, mode }) {
  const [open, setOpen] = useState(false);
  const [expanded, setExpanded] = useState({});
  const rows = mode === "live" ? (D.liveGateRows || []) : (D.gateRows || []);
  if (rows.length === 0) return null;

  const grouped = rows.reduce((acc, r) => {
    const prev = acc[acc.length - 1];
    if (prev && prev.sym === r.sym && prev.outcome === r.outcome) {
      prev._count = (prev._count || 1) + 1;
      prev._lastTs = r.ts;
    } else {
      acc.push({ ...r, _count: 1, _lastTs: r.ts });
    }
    return acc;
  }, []);

  const fmtTs = ts => new Date(ts).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", hour12: false });
  const lockColor = pass => pass === true ? "var(--t-accent)" : pass === false ? "var(--t-red)" : "var(--t-text-3)";
  const lockIcon  = pass => pass === true ? "✓" : pass === false ? "✗" : "·";

  return (
    <div className="t-card">
      <div className="t-card-head" style={{ cursor: "pointer", userSelect: "none" }} onClick={() => setOpen(v => !v)}>
        <div className="t-card-title">GATE ACTIVITY</div>
        <div className="t-row" style={{ gap: 10 }}>
          <span className="t-meta">LAST {grouped.length} · {mode === "live" ? "LIVE" : "DEMO"}</span>
          <span className="t-meta">{open ? "▲" : "▼"}</span>
        </div>
      </div>
      {open && (
        <div className="t-card-body" style={{ padding: 0, maxHeight: 400, overflow: "auto" }}>
          <table className="t-tbl t-tbl-pad t-tbl-feed">
            <thead>
              <tr>
                <th>TIME</th>
                <th>SYM</th>
                <th>SCORE</th>
                <th style={{ textAlign: "center" }}>L1</th>
                <th style={{ textAlign: "center" }}>L2</th>
                <th style={{ textAlign: "center" }}>L3</th>
                <th style={{ textAlign: "center" }}>L4</th>
                <th style={{ textAlign: "center" }}>L5</th>
                <th>OUTCOME</th>
              </tr>
            </thead>
            <tbody>
              {grouped.map((r, i) => {
                const outcome   = GATE_OUTCOME[r.outcome] ?? { label: r.outcome, cls: "t-gate-filt" };
                const isSkipped = r.outcome === "SKIPPED_OPEN" || r.outcome === "SKIPPED_COOLOFF";
                const locks     = getGateLockStates(r.outcome);
                const hasDetail = !isSkipped && !!(r.lock3_reasoning || r.lock_leading_checks || r.l2_summary || r.macro_reason);
                const isExpanded = expanded[i];
                const time = r._count > 1 ? `${fmtTs(r.ts)}–${fmtTs(r._lastTs)}` : fmtTs(r.ts);
                const scoreColor = r.l1_threshold == null ? "inherit"
                  : r.score >= r.l1_threshold + 0.05 ? "var(--t-accent)"
                  : r.score >= r.l1_threshold          ? "var(--t-amber)"
                  : "var(--t-red)";
                return [
                  <tr
                    key={i}
                    style={{ cursor: hasDetail ? "pointer" : "default" }}
                    onClick={() => hasDetail && setExpanded(e => ({ ...e, [i]: !e[i] }))}
                  >
                    <td className="t-meta" style={{ whiteSpace: "nowrap" }}>{time}</td>
                    <td>
                      <strong>{r.sym}</strong>
                      {r._count > 1 && <span className="t-meta" style={{ marginLeft: 4 }}>×{r._count}</span>}
                      {hasDetail && <span className="t-meta" style={{ marginLeft: 4 }}>{isExpanded ? "▲" : "▼"}</span>}
                    </td>
                    <td style={{ fontFamily: "var(--mono)" }}>
                      <span style={{ color: scoreColor, fontWeight: 600 }}>{r.score.toFixed(3)}</span>
                      {r.l1_threshold != null && (
                        <div style={{ fontSize: 9, color: "var(--t-text-3)" }}>/{r.l1_threshold.toFixed(3)}</div>
                      )}
                    </td>
                    {locks.map((s, li) => (
                      <td key={li} style={{ textAlign: "center", fontWeight: 700, fontSize: 13, color: isSkipped ? "var(--t-text-3)" : lockColor(s) }}>
                        {isSkipped ? "·" : lockIcon(s)}
                      </td>
                    ))}
                    <td>
                      <span className={cls("t-pill", outcome.cls)}>{outcome.label}</span>
                    </td>
                  </tr>,
                  isExpanded && hasDetail && (
                    <tr key={`${i}-d`}>
                      <td colSpan={9} style={{
                        padding: "8px 16px 12px",
                        background: "color-mix(in oklab, var(--t-bg) 60%, #000)",
                        borderTop: "1px solid var(--t-border)",
                        fontSize: 12,
                        lineHeight: 1.7,
                        fontFamily: "var(--mono)",
                      }}>
                        {r.macro_reason && (
                          <div style={{ marginBottom: 6 }}>
                            <span style={{ color: "var(--t-text-3)", marginRight: 8 }}>L1:</span>
                            <span style={{ color: "var(--t-text-2)" }}>{r.macro_reason}</span>
                          </div>
                        )}
                        {r.l2_summary && (
                          <div style={{ marginBottom: 6 }}>
                            <span style={{ color: "var(--t-text-3)", marginRight: 8 }}>L3:</span>
                            <span style={{ color: "var(--t-text-2)" }}>{r.l2_summary}</span>
                          </div>
                        )}
                        {r.lock_leading_checks && (
                          <div style={{ marginBottom: 6 }}>
                            <span style={{ color: "var(--t-text-3)", marginRight: 8 }}>L4:</span>
                            {Object.entries(GATE_LEADING_LABELS).map(([key, lbl]) => {
                              const c = r.lock_leading_checks[key];
                              if (!c) return null;
                              return (
                                <span key={key} style={{ marginRight: 12 }}>
                                  <span style={{ color: c.pass ? "var(--t-accent)" : "var(--t-red)", fontWeight: 600 }}>
                                    {c.pass ? "✓" : "✗"} {lbl}
                                  </span>
                                  {c.reason && <span style={{ color: "var(--t-text-3)", marginLeft: 4 }}>{c.reason}</span>}
                                </span>
                              );
                            })}
                          </div>
                        )}
                        {r.lock3_reasoning && (
                          <div>
                            <span style={{ color: "var(--t-text-3)", marginRight: 8 }}>L5:</span>
                            <span style={{ color: "var(--t-text-2)" }}>{r.lock3_reasoning}</span>
                          </div>
                        )}
                      </td>
                    </tr>
                  ),
                ];
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function Alerts({ D }) {
  const alerts = D.alerts || [];
  return (
    <div className="t-card">
      <div className="t-card-head">
        <div className="t-card-title">DRIFT MONITOR</div>
        <div className="t-meta">{alerts.length > 0 ? `${alerts.length} ACTIVE` : "CLEAR"}</div>
      </div>
      <div className="t-card-body" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {alerts.length === 0 ? (
          <div style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--t-text-3)" }}>No active alerts</div>
        ) : alerts.map(a => (
          <div key={a.id} className="t-alert">
            <span className={"t-pill t-sev-" + a.sev.toLowerCase()}>{a.sev}</span>
            <span style={{ fontFamily: "var(--mono)", color: "var(--t-text-1)", fontSize: 12 }}>{a.id}</span>
            <span className="t-meta" style={{ flex: 1 }}>{a.label}</span>
            <span className="t-meta">{Math.round((Date.now() - a.ts) / 3_600_000)}h</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function TestView({ D }) {
  const [backtestResult, setBacktestResult] = useState(null);
  return (
    <div className="t-grid" style={{ gridTemplateColumns: "1fr 520px" }}>
      <div className="t-col-main">
        <div className="t-card">
          <div className="t-card-head">
            <div className="t-card-title">NIGHTLY AUDIT</div>
            <div className="t-meta">{(D.auditReports || []).length} REPORTS</div>
          </div>
          <div className="t-card-body">
            <NightLog reports={D.auditReports || []} />
          </div>
        </div>
      </div>
      <div className="t-col-side">
        <div className="t-card">
          <div className="t-card-head">
            <div className="t-card-title">BACKTEST ENGINE</div>
            <div className="t-meta">SECTOR-FILTERED · KELLY SIZING</div>
          </div>
          <div className="t-card-body">
            <BacktestPanel onResult={setBacktestResult} />
          </div>
        </div>
        {backtestResult && (
          <div className="t-card">
            <div className="t-card-head">
              <div className="t-card-title">BACKTEST RESULTS</div>
            </div>
            <div className="t-card-body">
              <TradeLog result={backtestResult} />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function Header({ D, mode, setMode, onSettings, onPromote, marketOpen }) {
  const w = mode === "live" ? D.liveWallet : D.wallet;
  const pnl = w ? ((w.unrealized ?? 0) + (w.realized ?? 0)) : 0;
  const now = new Date();
  const timeStr = now.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
  return (
    <header className="t-header">
      <div className="t-row" style={{ gap: 18 }}>
        <div className="t-logo">APEX<span style={{ color: "var(--t-text-3)", marginLeft: 6, fontSize: 11 }}>v0.7</span></div>
        <div className="t-marketdot">
          <span className={cls("t-dot", marketOpen && "t-dot-open")} />
          <span style={{ color: marketOpen ? "var(--t-accent)" : "var(--t-text-3)" }}>
            {marketOpen ? "MARKET OPEN" : "MARKET CLOSED"}
          </span>
          <span className="t-meta" style={{ marginLeft: 8 }}>{timeStr} ET</span>
        </div>
        <div className="t-tabs">
          <button className={cls("t-tab", mode === "demo" && "t-tab-on")} onClick={() => setMode("demo")}>DEMO</button>
          <button className={cls("t-tab", mode === "live" && "t-tab-on")} onClick={() => setMode("live")}>
            <span className="t-dot t-dot-open" style={{ marginRight: 6, width: 6, height: 6 }} />
            LIVE
            <span className="t-pill t-pill-paper" style={{ marginLeft: 8 }}>PAPER</span>
          </button>
          <button className={cls("t-tab", mode === "test" && "t-tab-on")} onClick={() => setMode("test")}>TEST</button>
        </div>
      </div>
      <div className="t-row" style={{ gap: 12 }}>
        {w && (
          <div style={{ fontFamily: "var(--mono)", fontSize: 13 }}>
            {fmt$(w.balance)} <span className={pnl >= 0 ? "t-pos" : "t-neg"}>{pnl >= 0 ? "+" : "−"}{fmt$(Math.abs(pnl)).replace("$","")}</span>
          </div>
        )}
        {onSettings && <button className="t-btn" onClick={onSettings}>⚙ SETTINGS</button>}
        {onPromote  && <button className="t-btn t-btn-promote" onClick={onPromote}>↑ PROMOTE</button>}
      </div>
    </header>
  );
}

export default function ApexTerminal({ tweaks = {}, data: D, onSettings, onPromote, marketOpen, supplemental }) {
  const [mode, setMode] = useState(tweaks.mode || "demo");
  useEffect(() => { if (tweaks.mode) setMode(tweaks.mode); }, [tweaks.mode]);

  if (!D) return null;

  return (
    <div className="t-root" data-density={tweaks.density || "comfortable"} data-theme={tweaks.theme || "dark"} data-palette={tweaks.palette || "neon"} data-font={tweaks.font || "mono"}>
      <Header D={D} mode={mode} setMode={setMode} onSettings={onSettings} onPromote={onPromote} marketOpen={marketOpen} />
      {mode === "test" ? (
        <TestView D={D} />
      ) : (
        <>
          <div className="t-grid">
            <div className="t-col-main">
              <RegimeDial D={D} />
              <Positions D={D} mode={mode} />
              <SectorGrid D={D} />
              <GateFeed D={D} mode={mode} />
              <Watchlist />
              {mode === "live"
                ? <LiveTradeLog trades={D.liveTrades || []} />
                : <DemoTradeLog trades={D.demoTrades || []} />
              }
              {mode === "live" && <LiveOrderLog orders={D.liveOrders || []} />}
            </div>
            <div className="t-col-side">
              <Wallet D={D} mode={mode} />
              <EquityHero D={D} mode={mode} />
              <LockChain D={D} />
              <RegimeBayes />
              <Alerts D={D} />
            </div>
          </div>
          {supplemental && (
            <div style={{ padding: "0 24px 24px" }}>
              {supplemental}
            </div>
          )}
        </>
      )}
    </div>
  );
}
