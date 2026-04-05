import React, { useState, useEffect } from "react";
import {
  LineChart, Line, XAxis, YAxis, Tooltip,
  ResponsiveContainer, ReferenceLine,
} from "recharts";

// Hues spread ~33° apart around the full wheel, all vivid on dark background.
// strokeDash adds a second differentiator for sectors that share a hue quadrant.
const SECTOR_COLORS = {
  Financials:      "#ff3355",   //   0° red
  Communication:   "#ff7722",   //  22° orange-red
  Energy:          "#ffaa00",   //  40° golden amber
  ConsumerStaples: "#aacc11",   //  75° chartreuse
  Utilities:       "#22bb55",   // 145° green
  Healthcare:      "#00ccaa",   // 168° teal
  ConsumerDisc:    "#00aaee",   // 200° sky blue
  Technology:      "#4477ff",   // 225° blue
  Materials:       "#8844ff",   // 265° indigo
  Industrials:     "#cc44ee",   // 290° violet
  RealEstate:      "#ff44aa",   // 330° pink
};

const SECTOR_DASH = {
  Communication:   "5 3",    // orange-red close to Energy amber — dash it
  ConsumerDisc:    "5 3",    // sky blue close to Technology blue — dash it
  Materials:       "5 3",    // indigo close to Industrials violet — dash it
};

const RANGES = [
  { label: "7d",  days: 7    },
  { label: "30d", days: 30   },
  { label: "90d", days: 90   },
  { label: "1Y",  days: 365  },
  { label: "2Y",  days: 730  },
  { label: "5Y",  days: 1825 },
  { label: "All", days: 0    },
];

function formatTick(ts, days) {
  const d = new Date(ts);
  if (days === 0 || days > 365)
    return d.toLocaleDateString("en-US", { month: "short", year: "2-digit" });
  if (days > 90)
    return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

function CustomTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  const d = new Date(label);
  const time = d.toLocaleString("en-US", {
    month: "short", day: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
  const sorted = [...payload].sort((a, b) => b.value - a.value);
  return (
    <div style={{
      background: "#141926", border: "1px solid #2a3450",
      borderRadius: 6, padding: "10px 14px", fontSize: 12,
      fontFamily: "'DM Mono', monospace",
    }}>
      <div style={{ color: "var(--text-3)", marginBottom: 6 }}>{time}</div>
      {sorted.map(p => (
        <div key={p.dataKey} style={{ color: p.color, marginBottom: 2 }}>
          {p.dataKey}: {p.value?.toFixed(3)}
        </div>
      ))}
    </div>
  );
}

export default function SectorRotation() {
  const [days,          setDays]      = useState(30);
  const [rawData,       setRawData]   = useState([]);
  const [loading,       setLoading]   = useState(true);
  const [error,         setError]     = useState(null);
  const [hiddenSectors, setHidden]    = useState(() => {
    try {
      const saved = localStorage.getItem("apex_hidden_sectors");
      return saved ? new Set(JSON.parse(saved)) : new Set();
    } catch { return new Set(); }
  });
  const [normalized,    setNormalized] = useState(false);
  const [thresholds,    setThresholds] = useState({});

  // Backfill state
  const [showBackfill, setShowBackfill] = useState(false);
  const [bfStart,      setBfStart]      = useState("2020-01-01");
  const [bfEnd,        setBfEnd]        = useState(new Date().toISOString().slice(0, 10));
  const [bfJobId,      setBfJobId]      = useState(null);
  const [bfStatus,     setBfStatus]     = useState(null);  // null | "running" | "done" | "error"
  const [bfInserted,   setBfInserted]   = useState(0);
  const [bfError,      setBfError]      = useState(null);
  const bfPollRef = React.useRef(null);

  useEffect(() => {
    fetch("/api/calibrate/ticker-thresholds")
      .then(r => r.ok ? r.json() : {})
      .then(data => setThresholds(data.thresholds ?? {}))
      .catch(() => {});
  }, []);

  useEffect(() => {
    setLoading(true);
    setError(null);
    fetch(`/api/sectors/history?days=${days}`)
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then(rows => {
        setRawData(rows);
        setLoading(false);
      })
      .catch(e => {
        setError(`Failed to load sector history (${e})`);
        setLoading(false);
      });
  }, [days]);

  // Pivot flat rows into [{timestamp, Sector1: score, Sector2: score, ...}]
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
  const chartData = Object.values(tsMap).sort((a, b) =>
    new Date(a.timestamp) - new Date(b.timestamp)
  );

  // Thin out to max ~120 points for readability
  const stride = Math.max(1, Math.floor(chartData.length / 120));
  const thinned = chartData.filter((_, i) => i % stride === 0);

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
        setBfJobId(job_id);
        bfPollRef.current = setInterval(() => {
          fetch(`/api/sectors/backfill/${job_id}`)
            .then(r => r.json())
            .then(job => {
              if (job.status === "done") {
                clearInterval(bfPollRef.current);
                setBfStatus("done");
                setBfInserted(job.inserted);
                // Reload chart data
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
    <div style={{
      background: "var(--bg-card)", border: "1px solid var(--border)",
      borderRadius: 8, padding: "20px 22px", marginTop: 16,
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div style={{ fontFamily: "'DM Mono', monospace", fontSize: 11, color: "var(--text-3)", letterSpacing: "0.08em", textTransform: "uppercase", fontWeight: 500 }}>
            Sector Rotation
          </div>
          <button
            onClick={() => setShowBackfill(v => !v)}
            title="Backfill historical data"
            style={{
              fontFamily: "'DM Mono', monospace", fontSize: 10,
              padding: "2px 8px", borderRadius: 4, cursor: "pointer",
              border: "1px solid var(--border)",
              background: showBackfill ? "var(--accent)" : "transparent",
              color: showBackfill ? "#fff" : "var(--text-3)",
            }}
          >⟳ backfill</button>
        </div>
        <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
          <button
            onClick={() => setNormalized(v => !v)}
            title={normalized ? "Show raw scores" : "Normalize by L1 threshold — makes sectors comparable"}
            style={{
              fontFamily: "'DM Mono', monospace", fontSize: 10,
              padding: "3px 10px", borderRadius: 4, cursor: "pointer",
              border: `1px solid ${normalized ? "var(--accent)" : "var(--border)"}`,
              background: normalized ? "var(--accent)" : "transparent",
              color: normalized ? "#fff" : "var(--text-3)",
              marginRight: 4,
            }}
          >normalized</button>
          {RANGES.map(r => (
            <button
              key={r.days}
              onClick={() => setDays(r.days)}
              style={{
                fontFamily: "'DM Mono', monospace", fontSize: 11,
                padding: "3px 10px", borderRadius: 4, cursor: "pointer",
                border: "1px solid var(--border)",
                background: days === r.days ? "var(--accent)" : "transparent",
                color: days === r.days ? "#fff" : "var(--text-3)",
              }}
            >{r.label}</button>
          ))}
        </div>
      </div>

      {showBackfill && (
        <div style={{
          background: "#1a2035", border: "1px solid var(--border)", borderRadius: 6,
          padding: "12px 16px", marginBottom: 16,
          display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap",
        }}>
          <span style={{ fontFamily: "'DM Mono', monospace", fontSize: 11, color: "var(--text-3)" }}>
            Backfill from
          </span>
          <input
            type="date" value={bfStart} onChange={e => setBfStart(e.target.value)}
            disabled={bfStatus === "running"}
            style={{ fontFamily: "'DM Mono', monospace", fontSize: 11, padding: "3px 6px",
              background: "#0e1525", border: "1px solid var(--border)", borderRadius: 3,
              color: "var(--text-1)", colorScheme: "dark" }}
          />
          <span style={{ fontFamily: "'DM Mono', monospace", fontSize: 11, color: "var(--text-3)" }}>to</span>
          <input
            type="date" value={bfEnd} onChange={e => setBfEnd(e.target.value)}
            disabled={bfStatus === "running"}
            style={{ fontFamily: "'DM Mono', monospace", fontSize: 11, padding: "3px 6px",
              background: "#0e1525", border: "1px solid var(--border)", borderRadius: 3,
              color: "var(--text-1)", colorScheme: "dark" }}
          />
          <button
            onClick={startBackfill}
            disabled={bfStatus === "running"}
            style={{
              fontFamily: "'DM Mono', monospace", fontSize: 11,
              padding: "3px 12px", borderRadius: 4, cursor: bfStatus === "running" ? "default" : "pointer",
              border: "1px solid var(--accent)", background: "var(--accent)", color: "#fff",
              opacity: bfStatus === "running" ? 0.6 : 1,
            }}
          >{bfStatus === "running" ? "running…" : "Run"}</button>

          {bfStatus === "running" && (
            <span style={{ fontFamily: "'DM Mono', monospace", fontSize: 11, color: "var(--text-3)" }}>
              Computing signal scores — this may take a few minutes for long ranges…
            </span>
          )}
          {bfStatus === "done" && (
            <span style={{ fontFamily: "'DM Mono', monospace", fontSize: 11, color: "var(--green)" }}>
              ✓ {bfInserted} rows inserted
            </span>
          )}
          {bfStatus === "error" && (
            <span style={{ fontFamily: "'DM Mono', monospace", fontSize: 11, color: "var(--red)" }}>
              Error: {bfError}
            </span>
          )}
        </div>
      )}

      {/* Sector legend / toggles */}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 14 }}>
        {sectors.map(s => {
          const color  = SECTOR_COLORS[s] ?? "#888";
          const dashed = !!SECTOR_DASH[s];
          const hidden = hiddenSectors.has(s);
          return (
            <button
              key={s}
              onClick={() => toggleSector(s)}
              style={{
                fontFamily: "'DM Mono', monospace", fontSize: 10,
                padding: "2px 8px", borderRadius: 3, cursor: "pointer",
                border: `1px ${dashed ? "dashed" : "solid"} ${color}`,
                background: hidden ? "transparent" : color + "22",
                color: hidden ? "var(--text-3)" : color,
                opacity: hidden ? 0.45 : 1,
              }}
            >{s}</button>
          );
        })}
      </div>

      {loading && (
        <div style={{ fontFamily: "'DM Mono', monospace", fontSize: 12, color: "var(--text-3)", padding: "32px 0", textAlign: "center" }}>
          Loading…
        </div>
      )}

      {error && (
        <div style={{ fontFamily: "'DM Mono', monospace", fontSize: 12, color: "var(--red)", padding: "16px 0" }}>
          {error}
        </div>
      )}

      {!loading && !error && thinned.length === 0 && (
        <div style={{ fontFamily: "'DM Mono', monospace", fontSize: 12, color: "var(--text-3)", padding: "32px 0", textAlign: "center" }}>
          No rotation data yet — snapshots build up after the first poll cycle.
        </div>
      )}

      {!loading && !error && thinned.length > 0 && (
        <ResponsiveContainer width="100%" height={260}>
          <LineChart data={thinned} margin={{ top: 4, right: 8, left: -20, bottom: 0 }}>
            <XAxis
              dataKey="timestamp"
              tickFormatter={ts => formatTick(ts, days)}
              tick={{ fontFamily: "'DM Mono', monospace", fontSize: 10, fill: "var(--text-3)" }}
              axisLine={false} tickLine={false}
              interval="preserveStartEnd"
            />
            <YAxis
              domain={normalized ? [0.5, 1.5] : [0, 1]}
              tick={{ fontFamily: "'DM Mono', monospace", fontSize: 10, fill: "var(--text-3)" }}
              axisLine={false} tickLine={false}
              tickFormatter={v => normalized ? `${v.toFixed(1)}×` : v.toFixed(1)}
            />
            <Tooltip content={<CustomTooltip />} />
            <ReferenceLine
              y={normalized ? 1.0 : 0.55}
              stroke="#2a3450" strokeDasharray="3 3"
            />
            {sectors
              .filter(s => !hiddenSectors.has(s))
              .map(s => (
                <Line
                  key={s}
                  type="monotone"
                  dataKey={s}
                  stroke={SECTOR_COLORS[s] ?? "#888"}
                  strokeWidth={1.5}
                  strokeDasharray={SECTOR_DASH[s] ?? undefined}
                  dot={false}
                  connectNulls
                />
              ))
            }
          </LineChart>
        </ResponsiveContainer>
      )}

      <div style={{ fontFamily: "'DM Mono', monospace", fontSize: 10, color: "var(--text-3)", marginTop: 8 }}>
        {normalized
          ? "Normalized: score ÷ per-sector L1 threshold. 1.0× = exactly at threshold. Sectors are directly comparable."
          : "Raw scores. Dashed line = 0.55 (approximate). Use Normalized view for cross-sector comparison."
        } Click sectors to toggle visibility.
      </div>
    </div>
  );
}
