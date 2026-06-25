import React, { useState, useEffect, useMemo } from "react";
import "../apex-dashboard.css";

const fmt$ = (n, d = 2) => "$" + Math.abs(n).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
const fmtPct = (n, d = 2) => (n >= 0 ? "+" : "−") + Math.abs(n * 100).toFixed(d) + "%";
const cls = (...a) => a.filter(Boolean).join(" ");

function Stat({ label, value, tone }) {
  return (
    <div className="a-stat">
      <div className="a-stat-label">{label}</div>
      <div className={cls("a-stat-value", tone && `a-${tone}`)}>{value}</div>
    </div>
  );
}

function EquityBanded({ D, mode }) {
  const eq = mode === "live" ? D.liveEq : D.equity;
  if (!eq || eq.length < 2) {
    return (
      <section className="a-section a-hero">
        <header className="a-section-head">
          <div><div className="a-eyebrow">Fig. 01 · Cumulative equity</div><h2 className="a-title">Equity curve</h2></div>
        </header>
        <div className="a-meta">No equity data</div>
      </section>
    );
  }
  const w = 820, h = 220;
  const vals = eq.map(p => p.value);
  const min = Math.min(...vals), max = Math.max(...vals);
  const pad = (max - min) * 0.12 || 10;
  const yMin = min - pad, yMax = max + pad;
  const x = i => (i / (eq.length - 1)) * w;
  const y = v => h - ((v - yMin) / (yMax - yMin)) * h;
  const start = eq[0].value, end = eq[eq.length - 1].value;
  const pnl = end - start, pct = pnl / start;
  const path = eq.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(p.value).toFixed(1)}`).join(" ");

  let peak = -Infinity;
  const peakPath = eq.map((p, i) => {
    peak = Math.max(peak, p.value);
    return `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(peak).toFixed(1)}`;
  }).join(" ");

  const wallet = mode === "live" ? D.liveWallet : D.wallet;

  const closedPts = eq.filter(p => p.ts && /^\d{4}-\d{2}-\d{2}/.test(p.ts));
  const firstTs = closedPts[0]?.ts;
  const calDays = firstTs ? Math.round((Date.now() - new Date(firstTs).getTime()) / 86400000) : null;
  const title = calDays != null ? `Equity, ${calDays} days` : "Equity curve";

  return (
    <section className="a-section a-hero">
      <header className="a-section-head">
        <div>
          <div className="a-eyebrow">Fig. 01 · Cumulative equity</div>
          <h2 className="a-title">{title}</h2>
        </div>
        <div className="a-row" style={{ gap: 32 }}>
          <Stat label="Balance" value={fmt$(end)} />
          <Stat label="P/L" value={fmtPct(pct)} tone={pnl >= 0 ? "pos" : "neg"} />
          {wallet?.sharpe > 0 && <Stat label="Sharpe" value={wallet.sharpe.toFixed(2)} />}
          {wallet?.drawdown > 0 && <Stat label="Max DD" value={fmtPct(-wallet.drawdown)} tone="neg" />}
        </div>
      </header>
      <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" style={{ width: "100%", height: h, display: "block" }}>
        <defs>
          <pattern id="a-hatch" patternUnits="userSpaceOnUse" width="6" height="6" patternTransform="rotate(45)">
            <line x1="0" y1="0" x2="0" y2="6" stroke="var(--a-ink-2)" strokeOpacity="0.08" strokeWidth="1" />
          </pattern>
        </defs>
        <rect x="0" y="0" width={w} height={h} fill="url(#a-hatch)" />
        {[0.2, 0.4, 0.6, 0.8].map(g => (
          <line key={g} x1="0" x2={w} y1={h*g} y2={h*g} stroke="var(--a-rule)" />
        ))}
        <path d={`${peakPath} L${w} ${h} L0 ${h} Z`} fill="var(--a-accent)" opacity="0.06" />
        <path d={`${path} L${w} ${h} L0 ${h} Z`} fill="var(--a-accent)" opacity="0.14" />
        <path d={peakPath} stroke="var(--a-ink-2)" strokeDasharray="3 3" strokeWidth="1" fill="none" opacity="0.5" />
        <path d={path} stroke="var(--a-accent)" strokeWidth="2" fill="none" />
        {[yMin, (yMin+yMax)/2, yMax].map((v, i) => (
          <text key={i} x="6" y={h - ((v - yMin)/(yMax - yMin))*h - 4} fill="var(--a-ink-3)" fontFamily="var(--a-mono)" fontSize="10">
            ${(v/1000).toFixed(1)}k
          </text>
        ))}
      </svg>
    </section>
  );
}

function Leaderboard({ D }) {
  const [span, setSpan] = useState("1d");
  if (!D.regime || D.regime.length === 0) return null;
  const max = Math.max(...D.regime.map(r => r.posterior), 0.01);
  const deltaKey = span === "7d" ? "delta7d" : "delta";
  return (
    <section className="a-section">
      <header className="a-section-head">
        <div>
          <div className="a-eyebrow">Fig. 02 · Bayesian posterior</div>
          <h2 className="a-title">Sector leaderboard</h2>
        </div>
        <span className="a-meta" style={{ display: "flex", alignItems: "center", gap: 8 }}>
          EOD 16:15 · prior × signal
          <span style={{ display: "flex", gap: 4 }}>
            <button className={cls("a-toggle-btn", span === "1d" && "a-toggle-on")} onClick={() => setSpan("1d")}>1D</button>
            <button className={cls("a-toggle-btn", span === "7d" && "a-toggle-on")} onClick={() => setSpan("7d")}>7D</button>
          </span>
        </span>
      </header>
      <ol className="a-ranks">
        {D.regime.map((s, i) => {
          const d = s[deltaKey];
          return (
            <li key={s.code} className={cls("a-rank-row", s.excluded && "a-rank-out")}>
              <span className="a-rank-num">{(i + 1).toString().padStart(2, "0")}</span>
              <span className="a-rank-name">{s.name}</span>
              <span className="a-rank-etf">{s.etf}</span>
              <span className="a-rank-bar">
                <span className="a-rank-fill" style={{ width: `${(s.posterior / max) * 100}%` }} />
                <span className="a-rank-floor" />
              </span>
              <span className="a-rank-val">{s.posterior.toFixed(3)}</span>
              <span className={cls("a-rank-delta", d == null ? "" : d >= 0 ? "a-pos" : "a-neg")}>
                {d == null ? "—" : `${d >= 0 ? "▲" : "▼"} ${Math.abs(d).toFixed(3)}`}
              </span>
              <span className="a-rank-tag">{s.excluded ? "EXCLUDED" : i < 5 ? "LEADER" : i < 8 ? "ELIGIBLE" : "WATCH"}</span>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

function Funnel({ D }) {
  if (!D.funnel || D.funnel.length === 0) return null;
  const total = D.funnel[0].count || 1;
  return (
    <section className="a-section">
      <header className="a-section-head">
        <div>
          <div className="a-eyebrow">Fig. 03 · Gate chain attrition</div>
          <h2 className="a-title">Today's funnel</h2>
        </div>
        <span className="a-meta">{D.funnel[0].count} → {D.funnel[D.funnel.length - 1].count}</span>
      </header>
      <div className="a-funnel">
        {D.funnel.map((f, i) => {
          const pct = f.count / total;
          const last = i === D.funnel.length - 1;
          return (
            <div key={f.stage} className={cls("a-funnel-row", last && "a-funnel-final")}>
              <span className="a-funnel-stage">{f.stage}</span>
              <span className="a-funnel-label">{f.label}</span>
              <span className="a-funnel-bar">
                <span className="a-funnel-fill" style={{
                  width: `${Math.max(2, pct * 100)}%`,
                  background: last ? "var(--a-accent)" : f.stage.startsWith("SKIPPED") ? "var(--a-ink-3)" : "var(--a-amber)",
                }} />
              </span>
              <span className="a-funnel-count">{f.count.toString().padStart(2, "0")}</span>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function Cell({ t, v, tone }) {
  return (
    <div className="a-cell">
      <dt>{t}</dt>
      <dd className={tone && `a-${tone}`}>{v}</dd>
    </div>
  );
}

function Wallet({ D, mode }) {
  const w = mode === "live" ? D.liveWallet : D.wallet;
  if (!w) return null;
  const pnl = (w.unrealized ?? 0) + (w.realized ?? 0);
  return (
    <aside className="a-section a-wallet">
      <div className="a-eyebrow">Wallet · {mode === "live" ? "live · alpaca paper" : "demo · simulated"}</div>
      <div className="a-wallet-balance">{fmt$(w.balance)}</div>
      <div style={{ marginTop: 4 }}>
        <span className={cls("a-mono", pnl >= 0 ? "a-pos" : "a-neg")}>
          {pnl >= 0 ? "+" : "−"}{fmt$(Math.abs(pnl)).replace("$","")}
        </span>
        <span className="a-meta" style={{ marginLeft: 8 }}>{fmtPct((w.balance - w.starting)/w.starting)} since inception</span>
      </div>
      <dl className="a-wallet-dl">
        <Cell t="Cash"       v={fmt$(w.cash ?? 0)} />
        <Cell t="Invested"   v={fmt$(w.invested ?? 0)} />
        <Cell t="Realized"   v={fmt$(w.realized ?? 0)} tone={(w.realized ?? 0) >= 0 ? "pos" : "neg"} />
        <Cell t="Unrealized" v={fmt$(w.unrealized ?? 0)} tone={(w.unrealized ?? 0) >= 0 ? "pos" : "neg"} />
        <Cell t="Win rate"   v={((w.winRate ?? 0) * 100).toFixed(0) + "%"} />
        <Cell t="Trades"     v={w.trades ?? 0} />
        {w.avgWin > 0 && <Cell t="Avg win"  v={fmtPct(w.avgWin)} />}
        {w.avgLoss > 0 && <Cell t="Avg loss" v={fmtPct(-w.avgLoss)} />}
      </dl>
    </aside>
  );
}

function Positions({ D, mode }) {
  const positions = mode === "live" ? (D.livePositions || []) : (D.positions || []);
  const maxPos = mode === "live" ? (D.settings?.live?.max_positions ?? 6) : (D.settings?.demo?.max_positions ?? 6);
  return (
    <section className="a-section">
      <header className="a-section-head">
        <div>
          <div className="a-eyebrow">Open positions · {positions.length} of {maxPos}</div>
          <h2 className="a-title">Holdings</h2>
        </div>
        <span className="a-meta">25% sector cap · 15% per position</span>
      </header>
      {positions.length === 0 ? (
        <div style={{ padding: "12px 0", fontFamily: "var(--a-mono)", fontSize: 12, color: "var(--a-ink-3)" }}>
          No open positions
        </div>
      ) : (
        <table className="a-table">
          <thead>
            <tr>
              <th>Ticker</th><th>Sector</th><th className="a-r">Qty</th>
              <th className="a-r">Entry</th><th className="a-r">Market</th>
              <th className="a-r">P/L</th><th className="a-r">Return</th>
              <th>Held</th><th>Stop</th>
            </tr>
          </thead>
          <tbody>
            {positions.map(p => (
              <tr key={p.sym}>
                <td><span className="a-ticker">{p.sym}</span></td>
                <td className="a-meta">{p.sector}</td>
                <td className="a-r a-mono">{p.qty}</td>
                <td className="a-r a-mono">{(p.entry ?? 0).toFixed(2)}</td>
                <td className="a-r a-mono">{(p.mkt ?? 0).toFixed(2)}</td>
                <td className={cls("a-r a-mono", (p.pnl ?? 0) >= 0 ? "a-pos" : "a-neg")}>
                  {(p.pnl ?? 0) >= 0 ? "+" : ""}{(p.pnl ?? 0).toFixed(2)}
                </td>
                <td className={cls("a-r a-mono", (p.pct ?? 0) >= 0 ? "a-pos" : "a-neg")}>
                  {(p.pct ?? 0) >= 0 ? "+" : ""}{(p.pct ?? 0).toFixed(2)}%
                </td>
                <td className="a-mono">{p.held}d</td>
                <td>
                  {p.trail
                    ? <span className="a-tag a-tag-lock">Profit-lock</span>
                    : <span className="a-meta a-mono">{p.sl ? `SL ${p.sl.toFixed(2)}` : "—"}</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function SectorTable({ D }) {
  const bySector = useMemo(() => {
    const m = {};
    (D.tickers || []).forEach(t => (m[t.sector] = m[t.sector] || []).push(t));
    return m;
  }, [D.tickers]);

  const sectors = D.SECTORS || [];
  const excluded = D.EXCLUDED || new Set();

  return (
    <section className="a-section">
      <header className="a-section-head">
        <div>
          <div className="a-eyebrow">Fig. 04 · Signal grid · {(D.tickers || []).length} instruments</div>
          <h2 className="a-title">Periodic view</h2>
        </div>
        <div className="a-row" style={{ gap: 16 }}>
          <span className="a-legend"><span className="a-sw" style={{ background: "var(--a-accent)" }} /> ≥ 0.70</span>
          <span className="a-legend"><span className="a-sw" style={{ background: "var(--a-amber)" }} /> 0.55–0.70</span>
          <span className="a-legend"><span className="a-sw" style={{ background: "var(--a-ink-3)" }} /> &lt; 0.55</span>
        </div>
      </header>
      <div className="a-periodic">
        {sectors.map(s => (
          <div key={s.code} className={cls("a-pgroup", excluded.has(s.code) && "a-pgroup-out")}>
            <div className="a-pgroup-head">
              <span className="a-pgroup-name">{s.name}</span>
              <span className="a-pgroup-etf">{s.etf}</span>
            </div>
            <div className="a-pgroup-tiles">
              {(bySector[s.code] || []).map(t => {
                const tone = t.score >= 0.7 ? "accent" : t.score >= 0.55 ? "amber" : "dim";
                return (
                  <div key={t.sym} className={cls("a-pel", `a-pel-${tone}`, t.watchlist && "a-pel-watch")}>
                    <div className="a-pel-sym">{t.sym}</div>
                    <div className="a-pel-score">{t.score.toFixed(2)}</div>
                    <div className={cls("a-pel-chg", (t.chg ?? 0) >= 0 ? "a-pos" : "a-neg")}>
                      {(t.chg ?? 0) >= 0 ? "+" : ""}{(t.chg ?? 0).toFixed(1)}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

function GateLog({ D, mode }) {
  const rows = mode === "live" ? (D.liveGateRows || []) : (D.gateRows || []);
  if (rows.length === 0) {
    if (mode === "live" && D.liveGateBlocked) {
      return (
        <section className="a-section">
          <header className="a-section-head">
            <div>
              <div className="a-eyebrow">Gate ledger</div>
              <h2 className="a-title">Activity</h2>
            </div>
            <span className="a-meta">live · blocked</span>
          </header>
          <p className="a-meta" style={{ padding: "12px 0", color: "var(--a-red, #c0392b)" }}>{D.liveGateBlocked}</p>
        </section>
      );
    }
    return null;
  }
  return (
    <section className="a-section">
      <header className="a-section-head">
        <div>
          <div className="a-eyebrow">Gate ledger · last {Math.min(rows.length, 18)} evaluations</div>
          <h2 className="a-title">Activity</h2>
        </div>
        <span className="a-meta">{mode === "live" ? "live · paper" : "demo · simulated"}</span>
      </header>
      <div className="a-log">
        {rows.slice(0, 18).map((r, i) => {
          const time = new Date(r.ts).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
          const cls2 = r.outcome === "TRADE_EXECUTED" ? "a-log-x"
            : r.outcome.startsWith("SKIPPED") ? "a-log-skip"
            : "a-log-filt";
          const sent = r.sent != null ? ` · sent ${r.sent >= 0 ? "+" : ""}${r.sent.toFixed(2)}` : "";
          const conf = r.conf != null ? ` · conf ${r.conf.toFixed(2)}` : "";
          return (
            <div key={i} className={cls("a-log-row", cls2)}>
              <span className="a-log-time">{time}</span>
              <span className="a-log-sym">{r.sym}</span>
              <span className="a-log-meta">{r.sector} · score {(r.score ?? 0).toFixed(2)}{sent}{conf}</span>
              <span className="a-log-outcome">{r.outcome.replace("FILTERED_","").replace("SKIPPED_","skip·")}</span>
              <span className="a-log-reason">— {r.reason}</span>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function PollCountdown() {
  const [secsLeft, setSecsLeft] = useState(null);

  useEffect(() => {
    let ticker = null;

    function startTick(targetMs) {
      if (ticker) clearInterval(ticker);
      ticker = setInterval(() => {
        const diff = Math.max(0, Math.round((targetMs - Date.now()) / 1000));
        setSecsLeft(diff);
        if (diff <= 0) {
          clearInterval(ticker);
          ticker = null;
          fetchNext();
        }
      }, 1000);
    }

    function fetchNext() {
      fetch("/health")
        .then(r => r.json())
        .then(d => {
          const job = (d.scheduler_jobs || []).find(j => j.id === "poll_sectors");
          if (!job?.next_run) return;
          const targetMs = new Date(job.next_run).getTime();
          const diff = Math.max(0, Math.round((targetMs - Date.now()) / 1000));
          setSecsLeft(diff);
          startTick(targetMs);
        })
        .catch(() => {});
    }

    fetchNext();
    return () => { if (ticker) clearInterval(ticker); };
  }, []);

  if (secsLeft === null) return null;
  const mm = String(Math.floor(secsLeft / 60)).padStart(2, "0");
  const ss = String(secsLeft % 60).padStart(2, "0");
  return (
    <span style={{ fontFamily: "var(--a-mono)", fontSize: 11, color: "var(--a-ink-3)", marginLeft: 10 }}>
      poll {mm}:{ss}
    </span>
  );
}

function AtlasHeader({ D, mode, setMode, onSettings, onPromote, marketOpen }) {
  const w = mode === "live" ? D.liveWallet : D.wallet;
  const pnl = w ? ((w.unrealized ?? 0) + (w.realized ?? 0)) : 0;
  return (
    <header className="a-masthead">
      <div className="a-row">
        <div className="a-brand">
          <span className="a-brand-mark">A</span>
          <div>
            <div className="a-brand-name">APEX</div>
            <div className="a-brand-sub">Signal · Filter · Size · Execute</div>
          </div>
        </div>
        <div className="a-marketstate">
          <span className="a-dot a-dot-live" style={{ opacity: marketOpen ? 1 : 0.3 }} />
          {marketOpen ? "Market open" : "Market closed"}
          {" · "}
          {new Date().toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", hour12: false })} ET
          <PollCountdown />
        </div>
      </div>
      <div className="a-row" style={{ gap: 18 }}>
        <div className="a-toggle">
          <button className={cls("a-toggle-btn", mode === "demo" && "a-toggle-on")} onClick={() => setMode("demo")}>Demo</button>
          <button className={cls("a-toggle-btn", mode === "live" && "a-toggle-on")} onClick={() => setMode("live")}>Live · Paper</button>
        </div>
        {w && (
          <div className="a-balance">
            <span className="a-balance-num">{fmt$(w.balance)}</span>
            <span className={cls("a-mono", pnl >= 0 ? "a-pos" : "a-neg")} style={{ marginLeft: 8, fontSize: 13 }}>
              {pnl >= 0 ? "+" : "−"}{fmt$(Math.abs(pnl)).replace("$","")}
            </span>
          </div>
        )}
        {onSettings && (
          <button onClick={onSettings} style={{ background: "transparent", border: "1px solid var(--a-border)", borderRadius: 6, padding: "6px 12px", cursor: "pointer", fontFamily: "var(--a-mono)", fontSize: 11, color: "var(--a-ink-2)", letterSpacing: "0.06em" }}>
            ⚙ Settings
          </button>
        )}
        {onPromote && (
          <button onClick={onPromote} style={{ background: "transparent", border: "1px solid var(--a-border)", borderRadius: 6, padding: "6px 12px", cursor: "pointer", fontFamily: "var(--a-mono)", fontSize: 11, color: "var(--a-accent)", letterSpacing: "0.06em" }}>
            ↑ Promote
          </button>
        )}
      </div>
    </header>
  );
}

function AtlasAlerts({ D }) {
  const alerts = D.alerts || [];
  return (
    <aside className="a-section a-alerts">
      <div className="a-eyebrow">Drift monitor · {alerts.length > 0 ? `${alerts.length} active` : "clear"}</div>
      {alerts.length === 0 ? (
        <div style={{ fontFamily: "var(--a-mono)", fontSize: 12, color: "var(--a-ink-3)", padding: "8px 0" }}>
          No active alerts
        </div>
      ) : alerts.map(a => (
        <div key={a.id} className={cls("a-alert", `a-sev-${a.sev.toLowerCase()}`)}>
          <span className="a-alert-sev">{a.sev}</span>
          <div>
            <div className="a-alert-id">{a.id}</div>
            <div className="a-alert-text">{a.label}</div>
          </div>
          <span className="a-meta">{Math.round((Date.now() - a.ts) / 3_600_000)}h ago</span>
        </div>
      ))}
    </aside>
  );
}

export default function ApexAtlas({ tweaks = {}, data: D, onSettings, onPromote, marketOpen, supplemental }) {
  const [mode, setMode] = useState(tweaks.mode || "demo");
  useEffect(() => { if (tweaks.mode) setMode(tweaks.mode); }, [tweaks.mode]);

  if (!D) return null;

  return (
    <div className="a-root" data-density={tweaks.density || "comfortable"} data-theme={tweaks.theme || "light"} data-palette={tweaks.palette || "ink"} data-font={tweaks.font || "serif"}>
      <AtlasHeader D={D} mode={mode} setMode={setMode} onSettings={onSettings} onPromote={onPromote} marketOpen={marketOpen} />
      <main className="a-main">
        <div className="a-grid-2">
          <div className="a-col-major">
            <Leaderboard D={D} />
            <Positions D={D} mode={mode} />
            <SectorTable D={D} />
            <GateLog D={D} mode={mode} />
          </div>
          <div className="a-col-minor">
            <Wallet D={D} mode={mode} />
            <EquityBanded D={D} mode={mode} />
            <Funnel D={D} />
            <AtlasAlerts D={D} />
          </div>
        </div>
        {supplemental && (
          <div style={{ borderTop: "1px solid var(--a-rule)", padding: "40px 0 8px", colorScheme: tweaks.theme === "dark" ? "dark" : "light" }}>
            <div className="a-eyebrow" style={{ marginBottom: 24 }}>Analysis &amp; Tools</div>
            {supplemental}
          </div>
        )}
        <footer className="a-foot">
          <span>APEX · personal algorithmic trading signal intelligence</span>
          <span className="a-meta">
            {new Date().toLocaleDateString("en-US", { dateStyle: "long" })}
          </span>
        </footer>
      </main>
    </div>
  );
}
