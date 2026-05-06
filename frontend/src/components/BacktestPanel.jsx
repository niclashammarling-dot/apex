import { useState } from "react";
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine, CartesianGrid, Legend } from "recharts";

function StatRow({ label, valueA, valueB, colorA, colorB, ab }) {
  if (!ab) {
    return (
      <div style={{ display: "flex", justifyContent: "space-between", padding: "6px 0", borderBottom: "1px solid #2a2a28" }}>
        <span style={{ fontFamily: "'Inconsolata', monospace", fontSize: 12, color: "var(--text-3)" }}>{label}</span>
        <span style={{ fontFamily: "'Inconsolata', monospace", fontSize: 13, color: colorA || "var(--text-1)", fontWeight: 500 }}>{valueA}</span>
      </div>
    );
  }
  return (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "5px 0", borderBottom: "1px solid #2a2a28" }}>
      <span style={{ fontFamily: "'Inconsolata', monospace", fontSize: 11, color: "var(--text-3)", minWidth: 90 }}>{label}</span>
      <div style={{ display: "flex", gap: 16 }}>
        <span style={{ fontFamily: "'Inconsolata', monospace", fontSize: 12, color: colorA || "var(--text-1)", fontWeight: 500, minWidth: 72, textAlign: "right" }}>{valueA}</span>
        <span style={{ fontFamily: "'Inconsolata', monospace", fontSize: 12, color: colorB || "var(--text-1)", fontWeight: 500, minWidth: 72, textAlign: "right" }}>{valueB}</span>
      </div>
    </div>
  );
}

function fmt(v, decimals = 2) {
  if (v === null || v === undefined) return "—";
  return v.toFixed(decimals);
}

function fmtPct(v) {
  if (v === null || v === undefined) return "—";
  const sign = v >= 0 ? "+" : "";
  return `${sign}${(v * 100).toFixed(2)}%`;
}

const COLOR_A = "#6aa0f0";
const COLOR_B = "#00d48c";

export default function BacktestPanel({ onResult }) {
  const today = new Date().toISOString().slice(0, 10);
  const oneYearAgo = new Date(Date.now() - 365 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10);

  const [mode, setMode] = useState("standard");   // "standard" | "ab"
  const [form, setForm] = useState({
    start_date: oneYearAgo,
    end_date: today,
    initial_balance: "10000",
    take_profit_pct: "",
    stop_loss_pct: "",
    trailing_stop_pct: "",
    time_stop_days: "",
    lock1_threshold: "",
    atr_exits: false,
    earnings_filter: false,
    earnings_filter_days: "7",
    // Sharpe engine params
    vol_target: "0.15",
    vol_lookback: "20",
    max_leverage: "2.0",
  });
  const [result, setResult]   = useState(null);   // standard mode
  const [abResult, setAbResult] = useState(null); // ab mode: {a, b}
  const [loading, setLoading] = useState(false);
  const [error, setError]     = useState(null);

  function handleChange(e) {
    setForm(f => ({ ...f, [e.target.name]: e.target.value }));
  }

  const normPct = v => { const n = parseFloat(v); return n > 1 ? n / 100 : n; };

  function buildSharedBody() {
    const body = {
      start_date: form.start_date,
      end_date: form.end_date,
      initial_balance: parseFloat(form.initial_balance) || 10000,
    };
    if (form.take_profit_pct)   body.take_profit_pct   = normPct(form.take_profit_pct);
    if (form.stop_loss_pct)     body.stop_loss_pct     = normPct(form.stop_loss_pct);
    if (form.trailing_stop_pct) body.trailing_stop_pct = normPct(form.trailing_stop_pct);
    if (form.time_stop_days)    body.time_stop_days    = parseInt(form.time_stop_days);
    if (form.lock1_threshold)   body.lock1_threshold   = parseFloat(form.lock1_threshold);
    if (form.atr_exits)         body.atr_exits         = true;
    if (form.earnings_filter)   body.earnings_filter_days = parseInt(form.earnings_filter_days) || 7;
    return body;
  }

  async function runBacktest(e) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    setResult(null);
    setAbResult(null);

    function extractError(body, status) {
      const d = body.detail;
      if (Array.isArray(d)) return d.map(e => e.msg || JSON.stringify(e)).join("; ");
      return d || `HTTP ${status}`;
    }

    try {
      if (mode === "standard") {
        const res = await fetch("/api/backtest/run", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(buildSharedBody()),
        });
        if (!res.ok) { const b = await res.json().catch(() => ({})); throw new Error(extractError(b, res.status)); }
        const data = await res.json();
        setResult(data);
        onResult?.(data);
      } else {
        const body = {
          ...buildSharedBody(),
          vol_target:   normPct(form.vol_target || "0.15") || 0.15,
          vol_lookback: parseInt(form.vol_lookback)   || 20,
          max_leverage: parseFloat(form.max_leverage) || 2.0,
        };
        const res = await fetch("/api/backtest/run-ab", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        if (!res.ok) { const b = await res.json().catch(() => ({})); throw new Error(extractError(b, res.status)); }
        const data = await res.json();
        setAbResult(data);
        onResult?.(data.a);
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  // Merge two equity curves for the chart: {date, a, b}
  function mergeEquityCurves(curveA, curveB) {
    const mapB = Object.fromEntries((curveB || []).map(p => [p.date, p.balance]));
    return (curveA || []).map(p => ({ date: p.date, a: p.balance, b: mapB[p.date] ?? null }));
  }

  const activeResult = mode === "standard" ? result : abResult?.a;
  const starting = activeResult?.initial_balance ?? 10000;

  return (
    <div className="card">
      <div className="card-header">
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <div className="card-title">Backtest</div>
          {/* Mode toggle */}
          <div style={{ display: "flex", background: "#181816", border: "1px solid var(--border)", borderRadius: 5, overflow: "hidden" }}>
            {["standard", "ab"].map(m => (
              <button
                key={m}
                onClick={() => { setMode(m); setResult(null); setAbResult(null); setError(null); }}
                style={{
                  fontFamily: "'Inconsolata', monospace", fontSize: 11, fontWeight: 600,
                  padding: "4px 12px", border: "none", cursor: "pointer",
                  background: mode === m ? "#1e1e00" : "transparent",
                  color: mode === m ? "#7ab8f5" : "var(--text-3)",
                  letterSpacing: "0.06em",
                }}
              >
                {m === "standard" ? "WINRATE" : "WR/SHARPE"}
              </button>
            ))}
          </div>
        </div>

        {/* Summary badge */}
        {result && mode === "standard" && (
          <div className="card-meta" style={{ color: result.total_return_pct >= 0 ? "var(--green)" : "var(--red)" }}>
            {fmtPct(result.total_return_pct)} · Sharpe {fmt(result.sharpe)}
          </div>
        )}
        {abResult && mode === "ab" && (
          <div style={{ display: "flex", gap: 12, fontSize: 12, fontFamily: "'Inconsolata', monospace" }}>
            <span style={{ color: COLOR_A }}>WR {fmtPct(abResult.a.total_return_pct)} · {fmt(abResult.a.sharpe)}</span>
            <span style={{ color: "var(--text-3)" }}>vs</span>
            <span style={{ color: COLOR_B }}>SR {fmtPct(abResult.b.total_return_pct)} · {fmt(abResult.b.sharpe)}</span>
          </div>
        )}
      </div>

      <div className="card-body" style={{ padding: "14px 16px" }}>
        <form onSubmit={runBacktest} style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 14, alignItems: "flex-end" }}>
          {[
            { name: "start_date",        label: "Start",             type: "date" },
            { name: "end_date",          label: "End",               type: "date" },
            { name: "initial_balance",   label: "Balance ($)",       type: "number", placeholder: "10000" },
            { name: "take_profit_pct",   label: "Take profit",       type: "number", placeholder: "default", title: "Exit when price rises this % above entry. 0.06 = +6%. Backtest research: 6–8% hits target 55%+ of trades. Above 8% drops to ~28%." },
            { name: "stop_loss_pct",     label: "Stop loss",         type: "number", placeholder: "default", title: "Hard exit when price falls this % below entry. 0.05 = -5%. Tighter stops reduce loss size but increase whipsaw exits." },
            { name: "trailing_stop_pct", label: "Trailing stop",     type: "number", placeholder: "off",     title: "Trails the stop up as price rises, locking in gains. 0.05 = stop trails 5% below peak. Leave blank to disable." },
            { name: "time_stop_days",    label: "Time stop (days)",  type: "number", placeholder: "5",       title: "Force-exit after this many days regardless of P&L. Prevents capital being stuck in stalled trades." },
            { name: "lock1_threshold",   label: "L1 threshold",      type: "number", placeholder: "default", title: "Minimum quantitative signal score to enter a trade. Uses per-sector calibrated thresholds by default. Dead zone below 0.65 — the filter has no effect under that value." },
          ].map(({ name, label, type, placeholder, title }) => (
            <div key={name} style={{ display: "flex", flexDirection: "column", gap: 3 }}>
              <label title={title} style={{ fontFamily: "'Inconsolata', monospace", fontSize: 11, color: "var(--text-3)", letterSpacing: "0.06em", cursor: title ? "help" : "default", borderBottom: title ? "1px dotted #404040" : "none" }}>
                {label.toUpperCase()}
              </label>
              <input
                name={name}
                type={type}
                value={form[name]}
                onChange={handleChange}
                placeholder={placeholder}
                step={type === "number" ? "any" : undefined}
                title={title}
                style={{
                  background: "#181816", border: "1px solid var(--border)", borderRadius: 5,
                  color: "var(--text-1)", fontFamily: "'Inconsolata', monospace", fontSize: 13,
                  padding: "6px 10px", width: name.includes("date") ? 140 : 116, outline: "none",
                }}
              />
            </div>
          ))}

          {/* Sharpe params — only in A/B mode */}
          {mode === "ab" && (
            <div style={{ display: "flex", gap: 8, alignItems: "flex-end", borderLeft: `2px solid ${COLOR_B}33`, paddingLeft: 12, marginLeft: 4 }}>
              {[
                { name: "vol_target",   label: "Vol target",  placeholder: "0.15", title: "Annualized portfolio vol target. Positions shrink when realized vol exceeds this, grow when below. 0.15 = target 15% annual vol." },
                { name: "vol_lookback", label: "Vol lookback", placeholder: "20",   title: "Trading days used to estimate realized vol from the equity curve. Shorter = more reactive, longer = smoother." },
                { name: "max_leverage", label: "Max lev",      placeholder: "2.0",  title: "Cap on the vol-targeting multiplier. Prevents over-sizing when realized vol is very low. 2.0 = max 2× normal position size." },
              ].map(({ name, label, placeholder, title }) => (
                <div key={name} style={{ display: "flex", flexDirection: "column", gap: 3 }}>
                  <label title={title} style={{ fontFamily: "'Inconsolata', monospace", fontSize: 11, color: COLOR_B, letterSpacing: "0.06em", opacity: 0.8, cursor: "help", borderBottom: `1px dotted ${COLOR_B}66` }}>
                    {label.toUpperCase()}
                  </label>
                  <input
                    name={name}
                    type="number"
                    value={form[name]}
                    onChange={handleChange}
                    placeholder={placeholder}
                    step="any"
                    title={title}
                    style={{
                      background: "#181816", border: `1px solid ${COLOR_B}44`, borderRadius: 5,
                      color: "var(--text-1)", fontFamily: "'Inconsolata', monospace", fontSize: 13,
                      padding: "6px 10px", width: 90, outline: "none",
                    }}
                  />
                </div>
              ))}
            </div>
          )}

          <div style={{ display: "flex", flexDirection: "column", gap: 8, justifyContent: "flex-end" }}>
            <label style={{ display: "flex", alignItems: "center", gap: 6, cursor: "pointer" }}>
              <input
                type="checkbox"
                checked={form.atr_exits}
                onChange={e => setForm(f => ({ ...f, atr_exits: e.target.checked }))}
                style={{ accentColor: "#7ab8f5", width: 14, height: 14 }}
              />
              <span style={{ fontFamily: "'Inconsolata', monospace", fontSize: 12, color: "var(--text-2)" }}>ATR exits</span>
            </label>
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <input
                type="checkbox"
                checked={form.earnings_filter}
                onChange={e => setForm(f => ({ ...f, earnings_filter: e.target.checked }))}
                style={{ accentColor: "#7ab8f5", width: 14, height: 14, cursor: "pointer" }}
              />
              <span style={{ fontFamily: "'Inconsolata', monospace", fontSize: 12, color: "var(--text-2)" }}>Skip earnings</span>
              <input
                type="number"
                value={form.earnings_filter_days}
                onChange={e => setForm(f => ({ ...f, earnings_filter_days: e.target.value }))}
                disabled={!form.earnings_filter}
                min="1" max="30"
                style={{
                  background: "#181816", border: "1px solid var(--border)", borderRadius: 4,
                  color: form.earnings_filter ? "var(--text-1)" : "var(--text-3)",
                  fontFamily: "'Inconsolata', monospace", fontSize: 12,
                  padding: "3px 6px", width: 40, outline: "none",
                }}
              />
              <span style={{ fontFamily: "'Inconsolata', monospace", fontSize: 11, color: "var(--text-3)" }}>days</span>
            </div>
          </div>

          <button
            type="submit"
            disabled={loading}
            style={{
              background: loading ? "#282826" : "#1e1e00", border: "1px solid #3a3a00",
              borderRadius: 5, color: loading ? "var(--text-3)" : "#7ab8f5",
              fontFamily: "'Inconsolata', monospace", fontSize: 13, fontWeight: 500,
              padding: "7px 18px", cursor: loading ? "not-allowed" : "pointer",
            }}
          >
            {loading ? "Running…" : mode === "ab" ? "Run WR/Sharpe" : "Run"}
          </button>
        </form>

        {error && (
          <div style={{ color: "var(--red)", fontFamily: "'Inconsolata', monospace", fontSize: 13, marginBottom: 12 }}>
            {error}
          </div>
        )}

        {/* ── Standard result ── */}
        {result && mode === "standard" && (() => {
          const returnPositive = result.total_return_pct >= 0;
          const stroke = returnPositive ? "#faff69" : "#e05555";
          return (
            <>
              <ResponsiveContainer width="100%" height={150}>
                <LineChart data={result.equity_curve} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#343434" vertical={false} />
                  <XAxis dataKey="date" tick={{ fontFamily: "'Inconsolata', monospace", fontSize: 11, fill: "#a0a0a0" }}
                    axisLine={false} tickLine={false} interval="preserveStartEnd" />
                  <YAxis domain={["auto", "auto"]}
                    tick={{ fontFamily: "'Inconsolata', monospace", fontSize: 11, fill: "#a0a0a0" }}
                    axisLine={false} tickLine={false} width={60}
                    tickFormatter={v => `$${(v / 1000).toFixed(1)}k`} />
                  <Tooltip
                    contentStyle={{ background: "var(--bg-header)", border: "1px solid var(--border)", fontFamily: "'Inconsolata', monospace", fontSize: 13 }}
                    labelStyle={{ color: "var(--text-3)" }}
                    formatter={v => [`$${v.toLocaleString("en-US", { minimumFractionDigits: 2 })}`, "Balance"]}
                  />
                  <ReferenceLine y={starting} stroke="#343434" strokeDashboard="4 4" />
                  <Line type="monotone" dataKey="balance" stroke={stroke} strokeWidth={2} dot={false} />
                </LineChart>
              </ResponsiveContainer>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0 24px", marginTop: 12 }}>
                <div>
                  <StatRow label="Total Return"   valueA={fmtPct(result.total_return_pct)} colorA={returnPositive ? "var(--green)" : "var(--red)"} />
                  <StatRow label="SPY (buy&hold)" valueA={result.spy_return_pct != null ? fmtPct(result.spy_return_pct) : "—"} colorA={result.spy_return_pct != null && result.spy_return_pct >= 0 ? "var(--text-2)" : "var(--red)"} />
                  <StatRow label="CAGR"           valueA={fmtPct(result.cagr)} colorA={result.cagr >= 0 ? "var(--green)" : "var(--red)"} />
                  <StatRow label="Sharpe"         valueA={fmt(result.sharpe)} colorA={result.sharpe >= 1 ? "var(--green)" : result.sharpe >= 0 ? "#e8a020" : "var(--red)"} />
                  <StatRow label="Max Drawdown"   valueA={fmtPct(-result.max_drawdown)} colorA="var(--red)" />
                </div>
                <div>
                  <StatRow label="Total Trades" valueA={result.total_trades} />
                  <StatRow label="Win Rate"     valueA={result.win_rate !== null ? `${(result.win_rate * 100).toFixed(1)}%` : "—"} />
                  <StatRow label="Avg Win"      valueA={fmtPct(result.avg_win_pct)} colorA="var(--green)" />
                  <StatRow label="Profit Factor" valueA={fmt(result.profit_factor)} colorA={result.profit_factor > 1 ? "var(--green)" : "var(--red)"} />
                </div>
              </div>
            </>
          );
        })()}

        {/* ── A/B result ── */}
        {abResult && mode === "ab" && (() => {
          const a = abResult.a;
          const b = abResult.b;
          const merged = mergeEquityCurves(a.equity_curve, b.equity_curve);
          const sharpeColor = (v) => v >= 1 ? "var(--green)" : v >= 0 ? "#e8a020" : "var(--red)";
          const retColor    = (v) => v >= 0 ? "var(--green)" : "var(--red)";
          return (
            <>
              {/* Legend */}
              <div style={{ display: "flex", gap: 16, marginBottom: 8 }}>
                <span style={{ fontFamily: "'Inconsolata', monospace", fontSize: 11, color: COLOR_A, fontWeight: 600 }}>
                  — WR: Return-optimized
                </span>
                <span style={{ fontFamily: "'Inconsolata', monospace", fontSize: 11, color: COLOR_B, fontWeight: 600 }}>
                  — SHARPE: Vol-targeted
                </span>
              </div>

              <ResponsiveContainer width="100%" height={160}>
                <LineChart data={merged} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#343434" vertical={false} />
                  <XAxis dataKey="date" tick={{ fontFamily: "'Inconsolata', monospace", fontSize: 11, fill: "#a0a0a0" }}
                    axisLine={false} tickLine={false} interval="preserveStartEnd" />
                  <YAxis domain={["auto", "auto"]}
                    tick={{ fontFamily: "'Inconsolata', monospace", fontSize: 11, fill: "#a0a0a0" }}
                    axisLine={false} tickLine={false} width={60}
                    tickFormatter={v => `$${(v / 1000).toFixed(1)}k`} />
                  <Tooltip
                    contentStyle={{ background: "var(--bg-header)", border: "1px solid var(--border)", fontFamily: "'Inconsolata', monospace", fontSize: 12 }}
                    labelStyle={{ color: "var(--text-3)" }}
                    formatter={(v, name) => [`$${v?.toLocaleString("en-US", { minimumFractionDigits: 2 })}`, name === "a" ? "WR" : "SHARPE"]}
                  />
                  <ReferenceLine y={starting} stroke="#343434" strokeDasharray="4 4" />
                  <Line type="monotone" dataKey="a" stroke={COLOR_A} strokeWidth={2} dot={false} connectNulls />
                  <Line type="monotone" dataKey="b" stroke={COLOR_B} strokeWidth={2} dot={false} connectNulls />
                </LineChart>
              </ResponsiveContainer>

              {/* Side-by-side stats */}
              <div style={{ marginTop: 12 }}>
                {/* Column headers */}
                <div style={{ display: "flex", justifyContent: "flex-end", gap: 16, marginBottom: 4, paddingRight: 0 }}>
                  <span style={{ fontFamily: "'Inconsolata', monospace", fontSize: 11, color: COLOR_A, minWidth: 72, textAlign: "right", fontWeight: 600 }}>WR</span>
                  <span style={{ fontFamily: "'Inconsolata', monospace", fontSize: 11, color: COLOR_B, minWidth: 72, textAlign: "right", fontWeight: 600 }}>SHARPE</span>
                </div>
                <StatRow ab label="Total Return"   valueA={fmtPct(a.total_return_pct)}   colorA={retColor(a.total_return_pct)}    valueB={fmtPct(b.total_return_pct)}   colorB={retColor(b.total_return_pct)} />
                <StatRow ab label="CAGR"           valueA={fmtPct(a.cagr)}               colorA={retColor(a.cagr)}                valueB={fmtPct(b.cagr)}               colorB={retColor(b.cagr)} />
                <StatRow ab label="Sharpe"         valueA={fmt(a.sharpe)}                colorA={sharpeColor(a.sharpe)}           valueB={fmt(b.sharpe)}                colorB={sharpeColor(b.sharpe)} />
                <StatRow ab label="Max Drawdown"   valueA={fmtPct(-a.max_drawdown)}      colorA="var(--red)"                     valueB={fmtPct(-b.max_drawdown)}      colorB="var(--red)" />
                <StatRow ab label="Win Rate"       valueA={a.win_rate != null ? `${(a.win_rate*100).toFixed(1)}%` : "—"}         valueB={b.win_rate != null ? `${(b.win_rate*100).toFixed(1)}%` : "—"} />
                <StatRow ab label="Total Trades"   valueA={a.total_trades}                                                        valueB={b.total_trades} />
                <StatRow ab label="Profit Factor"  valueA={fmt(a.profit_factor)}         colorA={a.profit_factor > 1 ? "var(--green)" : "var(--red)"}  valueB={fmt(b.profit_factor)}  colorB={b.profit_factor > 1 ? "var(--green)" : "var(--red)"} />
                <StatRow ab label="SPY buy&hold"   valueA={a.spy_return_pct != null ? fmtPct(a.spy_return_pct) : "—"}            valueB="—" />
              </div>
            </>
          );
        })()}
      </div>
    </div>
  );
}
