import { useState } from "react";
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine, CartesianGrid } from "recharts";

function StatRow({ label, value, color }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", padding: "6px 0", borderBottom: "1px solid #1e2538" }}>
      <span style={{ fontFamily: "'DM Mono', monospace", fontSize: 12, color: "var(--text-3)" }}>{label}</span>
      <span style={{ fontFamily: "'DM Mono', monospace", fontSize: 13, color: color || "var(--text-1)", fontWeight: 500 }}>{value}</span>
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

export default function BacktestPanel({ onResult }) {
  const today = new Date().toISOString().slice(0, 10);
  const oneYearAgo = new Date(Date.now() - 365 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10);

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
  });
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  function handleChange(e) {
    setForm(f => ({ ...f, [e.target.name]: e.target.value }));
  }

  async function runBacktest(e) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const body = {
        start_date: form.start_date,
        end_date: form.end_date,
        initial_balance: parseFloat(form.initial_balance) || 10000,
      };
      if (form.take_profit_pct)   body.take_profit_pct   = parseFloat(form.take_profit_pct);
      if (form.stop_loss_pct)     body.stop_loss_pct     = parseFloat(form.stop_loss_pct);
      if (form.trailing_stop_pct) body.trailing_stop_pct = parseFloat(form.trailing_stop_pct);
      if (form.time_stop_days)    body.time_stop_days    = parseInt(form.time_stop_days);
      if (form.lock1_threshold)   body.lock1_threshold   = parseFloat(form.lock1_threshold);
      if (form.atr_exits)          body.atr_exits          = true;
      if (form.earnings_filter)    body.earnings_filter_days = parseInt(form.earnings_filter_days) || 7;

      const res = await fetch("/api/backtest/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      setResult(data);
      onResult?.(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  const returnPositive = result && result.total_return_pct >= 0;
  const stroke = returnPositive ? "#2a9d5c" : "#e05555";
  const starting = result?.initial_balance ?? 10000;

  return (
    <div className="card">
      <div className="card-header">
        <div className="card-title">Backtest</div>
        {result && (
          <div className="card-meta" style={{ color: returnPositive ? "var(--green)" : "var(--red)" }}>
            {fmtPct(result.total_return_pct)} · Sharpe {fmt(result.sharpe)}
          </div>
        )}
      </div>
      <div className="card-body" style={{ padding: "14px 16px" }}>
        <form onSubmit={runBacktest} style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 14, alignItems: "flex-end" }}>
          {[
            { name: "start_date", label: "Start", type: "date" },
            { name: "end_date",   label: "End",   type: "date" },
            { name: "initial_balance", label: "Balance ($)", type: "number", placeholder: "10000" },
            { name: "take_profit_pct",   label: "TP (e.g. 0.15)",  type: "number", placeholder: "default" },
            { name: "stop_loss_pct",     label: "SL (e.g. 0.05)",  type: "number", placeholder: "default" },
            { name: "trailing_stop_pct", label: "Trail (e.g. 0.05)", type: "number", placeholder: "off" },
            { name: "time_stop_days",    label: "Time stop (days)", type: "number", placeholder: "5" },
            { name: "lock1_threshold",   label: "L1 threshold",     type: "number", placeholder: "default" },
          ].map(({ name, label, type, placeholder }) => (
            <div key={name} style={{ display: "flex", flexDirection: "column", gap: 3 }}>
              <label style={{ fontFamily: "'DM Mono', monospace", fontSize: 11, color: "var(--text-3)", letterSpacing: "0.06em" }}>
                {label.toUpperCase()}
              </label>
              <input
                name={name}
                type={type}
                value={form[name]}
                onChange={handleChange}
                placeholder={placeholder}
                step={type === "number" ? "any" : undefined}
                style={{
                  background: "#111828", border: "1px solid var(--border)", borderRadius: 5,
                  color: "var(--text-1)", fontFamily: "'DM Mono', monospace", fontSize: 13,
                  padding: "6px 10px", width: name.includes("date") ? 140 : 116, outline: "none",
                }}
              />
            </div>
          ))}
          <div style={{ display: "flex", flexDirection: "column", gap: 8, justifyContent: "flex-end" }}>
            <label style={{ display: "flex", alignItems: "center", gap: 6, cursor: "pointer" }}>
              <input
                type="checkbox"
                checked={form.atr_exits}
                onChange={e => setForm(f => ({ ...f, atr_exits: e.target.checked }))}
                style={{ accentColor: "#7ab8f5", width: 14, height: 14 }}
              />
              <span style={{ fontFamily: "'DM Mono', monospace", fontSize: 12, color: "var(--text-2)" }}>ATR exits</span>
            </label>
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <input
                type="checkbox"
                checked={form.earnings_filter}
                onChange={e => setForm(f => ({ ...f, earnings_filter: e.target.checked }))}
                style={{ accentColor: "#7ab8f5", width: 14, height: 14, cursor: "pointer" }}
              />
              <span style={{ fontFamily: "'DM Mono', monospace", fontSize: 12, color: "var(--text-2)" }}>Skip earnings</span>
              <input
                type="number"
                value={form.earnings_filter_days}
                onChange={e => setForm(f => ({ ...f, earnings_filter_days: e.target.value }))}
                disabled={!form.earnings_filter}
                min="1" max="30"
                style={{
                  background: "#111828", border: "1px solid var(--border)", borderRadius: 4,
                  color: form.earnings_filter ? "var(--text-1)" : "var(--text-3)",
                  fontFamily: "'DM Mono', monospace", fontSize: 12,
                  padding: "3px 6px", width: 40, outline: "none",
                }}
              />
              <span style={{ fontFamily: "'DM Mono', monospace", fontSize: 11, color: "var(--text-3)" }}>days</span>
            </div>
          </div>
          <button
            type="submit"
            disabled={loading}
            style={{
              background: loading ? "#1a2035" : "#1e3a5f", border: "1px solid #2a5080",
              borderRadius: 5, color: loading ? "var(--text-3)" : "#7ab8f5",
              fontFamily: "'DM Mono', monospace", fontSize: 13, fontWeight: 500,
              padding: "7px 18px", cursor: loading ? "not-allowed" : "pointer",
            }}
          >
            {loading ? "Running…" : "Run"}
          </button>
        </form>

        {error && (
          <div style={{ color: "var(--red)", fontFamily: "'DM Mono', monospace", fontSize: 13, marginBottom: 12 }}>
            {error}
          </div>
        )}

        {result && (
          <>
            <ResponsiveContainer width="100%" height={150}>
              <LineChart data={result.equity_curve} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#2a3350" vertical={false} />
                <XAxis dataKey="date" tick={{ fontFamily: "'DM Mono', monospace", fontSize: 11, fill: "#5c6b8a" }}
                  axisLine={false} tickLine={false} interval="preserveStartEnd" />
                <YAxis domain={["auto", "auto"]}
                  tick={{ fontFamily: "'DM Mono', monospace", fontSize: 11, fill: "#5c6b8a" }}
                  axisLine={false} tickLine={false} width={60}
                  tickFormatter={v => `$${(v / 1000).toFixed(1)}k`} />
                <Tooltip
                  contentStyle={{ background: "var(--bg-header)", border: "1px solid var(--border)", fontFamily: "'DM Mono', monospace", fontSize: 13 }}
                  labelStyle={{ color: "var(--text-3)" }}
                  formatter={v => [`$${v.toLocaleString("en-US", { minimumFractionDigits: 2 })}`, "Balance"]}
                />
                <ReferenceLine y={starting} stroke="#2a3350" strokeDasharray="4 4" />
                <Line type="monotone" dataKey="balance" stroke={stroke} strokeWidth={2} dot={false} />
              </LineChart>
            </ResponsiveContainer>

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0 24px", marginTop: 12 }}>
              <div>
                <StatRow label="Total Return"    value={fmtPct(result.total_return_pct)} color={returnPositive ? "var(--green)" : "var(--red)"} />
                <StatRow label="SPY (buy&hold)" value={result.spy_return_pct != null ? fmtPct(result.spy_return_pct) : "—"} color={result.spy_return_pct != null && result.spy_return_pct >= 0 ? "var(--text-2)" : "var(--red)"} />
                <StatRow label="CAGR"            value={fmtPct(result.cagr)}             color={result.cagr >= 0 ? "var(--green)" : "var(--red)"} />
                <StatRow label="Sharpe"          value={fmt(result.sharpe)}              color={result.sharpe >= 1 ? "var(--green)" : result.sharpe >= 0 ? "#e8a020" : "var(--red)"} />
                <StatRow label="Max Drawdown"    value={fmtPct(-result.max_drawdown)}    color="var(--red)" />
              </div>
              <div>
                <StatRow label="Total Trades"    value={result.total_trades} />
                <StatRow label="Win Rate"        value={result.win_rate !== null ? `${(result.win_rate * 100).toFixed(1)}%` : "—"} />
                <StatRow label="Avg Win"         value={fmtPct(result.avg_win_pct)}  color="var(--green)" />
                <StatRow label="Profit Factor"   value={fmt(result.profit_factor)}   color={result.profit_factor > 1 ? "var(--green)" : "var(--red)"} />
              </div>
            </div>

          </>
        )}
      </div>
    </div>
  );
}
