import React, { useState, useEffect } from "react";
import {
  LineChart, Line, XAxis, YAxis, Tooltip,
  ResponsiveContainer, ReferenceLine,
} from "recharts";

const SECTOR_COLORS = {
  Financials:      "#ff3355",
  Communication:   "#ff7722",
  Energy:          "#ffaa00",
  ConsumerStaples: "#aacc11",
  Semiconductors:  "#77cc00",
  Utilities:       "#22bb55",
  Healthcare:      "#00ccaa",
  ConsumerDisc:    "#00aaee",
  Technology:      "#4477ff",
  Materials:       "#8844ff",
  Industrials:     "#cc44ee",
  Defense:         "#ee44cc",
  RealEstate:      "#ff44aa",
};

const SECTOR_DASH = {
  Communication: "5 3",
  ConsumerDisc:  "5 3",
  Materials:     "5 3",
  Defense:       "5 3",
};

const RANGES = [
  { label: "7D",  days: 7    },
  { label: "30D", days: 30   },
  { label: "90D", days: 90   },
  { label: "1Y",  days: 365  },
  { label: "2Y",  days: 730  },
  { label: "5Y",  days: 1825 },
  { label: "ALL", days: 0    },
];

function formatTick(ts, days) {
  const d = new Date(ts);
  if (days === 0 || days > 365)
    return d.toLocaleDateString("en-US", { month: "short", year: "2-digit" });
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

function ChartTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  const time = new Date(label).toLocaleString("en-US", {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  });
  const sorted = [...payload].sort((a, b) => b.value - a.value);
  return (
    <div style={{
      background: "var(--t-bg-3)", border: "1px solid var(--t-border)",
      borderRadius: 4, padding: "10px 14px", fontFamily: "var(--mono)", fontSize: 12,
    }}>
      <div className="t-meta" style={{ marginBottom: 6 }}>{time}</div>
      {sorted.map(p => (
        <div key={p.dataKey} style={{ color: p.color, marginBottom: 2 }}>
          {p.dataKey}: {p.value?.toFixed(3)}
        </div>
      ))}
    </div>
  );
}

export default function SectorRotation() {
  const [days,          setDays]       = useState(30);
  const [rawData,       setRawData]    = useState([]);
  const [loading,       setLoading]    = useState(true);
  const [error,         setError]      = useState(null);
  const [hiddenSectors, setHidden]     = useState(() => {
    try {
      const saved = localStorage.getItem("apex_hidden_sectors");
      return saved ? new Set(JSON.parse(saved)) : new Set();
    } catch { return new Set(); }
  });
  const [normalized,    setNormalized] = useState(false);
  const [thresholds,    setThresholds] = useState({});

  const [showBackfill, setShowBackfill] = useState(false);
  const [bfStart,      setBfStart]      = useState("2020-01-01");
  const [bfEnd,        setBfEnd]        = useState(new Date().toISOString().slice(0, 10));
  const [bfStatus,     setBfStatus]     = useState(null);
  const [bfInserted,   setBfInserted]   = useState(0);
  const [bfError,      setBfError]      = useState(null);
  const bfPollRef = React.useRef(null);

  useEffect(() => {
    fetch("/api/calibrate/ticker-thresholds")
      .then(r => r.ok ? r.json() : {})
      .then(d => setThresholds(d.thresholds ?? {}))
      .catch(() => {});
  }, []);

  useEffect(() => {
    setLoading(true);
    setError(null);
    fetch(`/api/sectors/history?days=${days}`)
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then(rows => { setRawData(rows); setLoading(false); })
      .catch(e => { setError(`Failed to load sector history (${e})`); setLoading(false); });
  }, [days]);

  const FLAT_THRESHOLD = 0.55;
  const sectors = [...new Set(rawData.map(r => r.sector))].sort();
  const tsMap = {};
  for (const row of rawData) {
    if (!tsMap[row.timestamp]) tsMap[row.timestamp] = { timestamp: row.timestamp };
    const score = row.avg_score;
    if (normalized) {
      const thr = thresholds[row.sector] ?? FLAT_THRESHOLD;
      tsMap[row.timestamp][row.sector] = thr > 0 ? Math.round((score / thr) * 1000) / 1000 : score;
    } else {
      tsMap[row.timestamp][row.sector] = score;
    }
  }
  const chartData = Object.values(tsMap).sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp));
  const stride    = Math.max(1, Math.floor(chartData.length / 120));
  const thinned   = chartData.filter((_, i) => i % stride === 0);

  function toggleSector(s) {
    setHidden(prev => {
      const next = new Set(prev);
      next.has(s) ? next.delete(s) : next.add(s);
      try { localStorage.setItem("apex_hidden_sectors", JSON.stringify([...next])); } catch {}
      return next;
    });
  }

  function startBackfill() {
    setBfStatus("running"); setBfError(null); setBfInserted(0);
    fetch("/api/sectors/backfill", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ start_date: bfStart, end_date: bfEnd }),
    })
      .then(r => r.ok ? r.json() : r.json().then(e => Promise.reject(e.detail)))
      .then(({ job_id }) => {
        bfPollRef.current = setInterval(() => {
          fetch(`/api/sectors/backfill/${job_id}`)
            .then(r => r.json())
            .then(job => {
              if (job.status === "done") {
                clearInterval(bfPollRef.current);
                setBfStatus("done"); setBfInserted(job.inserted);
                setLoading(true);
                fetch(`/api/sectors/history?days=${days}`)
                  .then(r => r.json()).then(setRawData).finally(() => setLoading(false));
              } else if (job.status === "error") {
                clearInterval(bfPollRef.current);
                setBfStatus("error"); setBfError(job.error);
              }
            });
        }, 3000);
      })
      .catch(e => { setBfStatus("error"); setBfError(String(e)); });
  }

  return (
    <div className="t-card">
      <div className="t-card-head">
        <div className="t-card-title">SECTOR ROTATION</div>
        <div className="t-row" style={{ gap: 4 }}>
          <button
            className="t-btn"
            onClick={() => setNormalized(v => !v)}
            title={normalized ? "Show raw scores" : "Normalize by L1 threshold — makes sectors comparable"}
            style={normalized ? { color: "var(--t-accent)", borderColor: "var(--t-accent)", marginRight: 8 } : { marginRight: 8 }}
          >NORM</button>
          {RANGES.map(r => (
            <button
              key={r.days}
              className={`t-tab${days === r.days ? " t-tab-on" : ""}`}
              onClick={() => setDays(r.days)}
            >{r.label}</button>
          ))}
          <button
            className="t-btn"
            onClick={() => setShowBackfill(v => !v)}
            style={{ marginLeft: 8, ...(showBackfill ? { color: "var(--t-accent)", borderColor: "var(--t-accent)" } : {}) }}
          >↺</button>
        </div>
      </div>

      <div className="t-card-body">
        {showBackfill && (
          <div style={{
            background: "var(--t-bg-3)", border: "1px solid var(--t-border)", borderRadius: 3,
            padding: "10px 14px", marginBottom: 14,
            display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap",
          }}>
            <span className="t-meta">Backfill from</span>
            <input
              type="date" value={bfStart} onChange={e => setBfStart(e.target.value)}
              disabled={bfStatus === "running"}
              style={{
                fontFamily: "var(--mono)", fontSize: 11, padding: "3px 6px",
                background: "var(--t-bg)", border: "1px solid var(--t-border)", borderRadius: 3,
                color: "var(--t-text-1)", colorScheme: "dark",
              }}
            />
            <span className="t-meta">to</span>
            <input
              type="date" value={bfEnd} onChange={e => setBfEnd(e.target.value)}
              disabled={bfStatus === "running"}
              style={{
                fontFamily: "var(--mono)", fontSize: 11, padding: "3px 6px",
                background: "var(--t-bg)", border: "1px solid var(--t-border)", borderRadius: 3,
                color: "var(--t-text-1)", colorScheme: "dark",
              }}
            />
            <button
              className="t-btn"
              onClick={startBackfill}
              disabled={bfStatus === "running"}
              style={{ color: "var(--t-accent)", borderColor: "var(--t-accent)", opacity: bfStatus === "running" ? 0.6 : 1 }}
            >{bfStatus === "running" ? "RUNNING…" : "RUN"}</button>
            {bfStatus === "running" && <span className="t-meta">Computing scores…</span>}
            {bfStatus === "done"    && <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--t-accent)" }}>✓ {bfInserted} rows inserted</span>}
            {bfStatus === "error"   && <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--t-red)" }}>Error: {bfError}</span>}
          </div>
        )}

        <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 12 }}>
          {sectors.map(s => {
            const color  = SECTOR_COLORS[s] ?? "var(--t-text-3)";
            const dashed = !!SECTOR_DASH[s];
            const hidden = hiddenSectors.has(s);
            return (
              <button
                key={s}
                onClick={() => toggleSector(s)}
                style={{
                  fontFamily: "var(--mono)", fontSize: 10,
                  padding: "2px 8px", borderRadius: 3, cursor: "pointer",
                  border: `1px ${dashed ? "dashed" : "solid"} ${color}`,
                  background: hidden ? "transparent" : `${color}22`,
                  color: hidden ? "var(--t-text-3)" : color,
                  opacity: hidden ? 0.45 : 1,
                }}
              >{s}</button>
            );
          })}
        </div>

        {loading && (
          <div className="t-meta" style={{ padding: "24px 0", textAlign: "center" }}>Loading…</div>
        )}
        {error && (
          <div style={{ fontFamily: "var(--mono)", fontSize: 12, color: "var(--t-red)", padding: "12px 0" }}>{error}</div>
        )}
        {!loading && !error && thinned.length === 0 && (
          <div className="t-meta" style={{ padding: "24px 0", textAlign: "center" }}>
            No data yet — builds up after first poll cycle.
          </div>
        )}

        {!loading && !error && thinned.length > 0 && (
          <ResponsiveContainer width="100%" height={260}>
            <LineChart data={thinned} margin={{ top: 4, right: 8, left: -20, bottom: 0 }}>
              <XAxis
                dataKey="timestamp"
                tickFormatter={ts => formatTick(ts, days)}
                tick={{ fontFamily: "var(--mono)", fontSize: 10, fill: "var(--t-text-3)" }}
                axisLine={false} tickLine={false}
                interval="preserveStartEnd"
              />
              <YAxis
                domain={normalized ? [0.5, 1.5] : [0, 1]}
                tick={{ fontFamily: "var(--mono)", fontSize: 10, fill: "var(--t-text-3)" }}
                axisLine={false} tickLine={false}
                tickFormatter={v => normalized ? `${v.toFixed(1)}×` : v.toFixed(1)}
              />
              <Tooltip content={<ChartTooltip />} />
              <ReferenceLine y={normalized ? 1.0 : 0.55} stroke="var(--t-border)" strokeDasharray="3 3" />
              {sectors
                .filter(s => !hiddenSectors.has(s))
                .map(s => (
                  <Line
                    key={s} type="monotone" dataKey={s}
                    stroke={SECTOR_COLORS[s] ?? "var(--t-text-3)"}
                    strokeWidth={1.5}
                    strokeDasharray={SECTOR_DASH[s] ?? undefined}
                    dot={false} connectNulls
                  />
                ))
              }
            </LineChart>
          </ResponsiveContainer>
        )}

        <div className="t-meta" style={{ marginTop: 8 }}>
          {normalized
            ? "Normalized: score ÷ per-sector L1 threshold. 1.0× = at threshold."
            : "Raw scores. Dashed line = 0.55. Click sectors to toggle."}
        </div>
      </div>
    </div>
  );
}
