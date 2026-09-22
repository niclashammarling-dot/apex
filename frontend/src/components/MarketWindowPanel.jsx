import React, { useEffect, useState } from "react";

// Dashboard surface for the scheduled market window (scripts/market_window.sh):
// per-session demo gate cycles vs expected and the launcher's own events, from
// /api/ops/window. Same numbers CHECK 80's coverage line reads.
const LAUNCHER = {
  running:     { label: "RUNNING",     cls: "t-pill-lock" },
  closed:      { label: "CLOSED",      cls: "t-pill-paper" },
  ended_early: { label: "ENDED EARLY", cls: "t-sev-high" },
  not_started: { label: "NO LAUNCHER", cls: "t-sev-medium" },
};

export default function MarketWindowPanel() {
  const [data, setData] = useState(null);
  useEffect(() => {
    const load = () => fetch("/api/ops/window?days=10").then(r => r.json()).then(setData).catch(() => {});
    load();
    const iv = setInterval(load, 60_000);
    return () => clearInterval(iv);
  }, []);

  if (!data) return <div className="t-meta">loading…</div>;
  const sessions = [...data.sessions].reverse();
  const trailing = data.sessions.slice(-3);
  const cov = trailing.length ? trailing.reduce((a, s) => a + s.cycles / s.expected, 0) / trailing.length : 0;
  const early = data.sessions.filter(s => s.launcher === "ended_early").length;

  return (
    <div>
      <div className="t-meta" style={{ marginBottom: 10 }}>
        TRAILING-3 COVERAGE <strong style={{ color: cov < 0.5 ? "var(--t-red)" : "var(--t-text-1)" }}>{Math.round(cov * 100)}%</strong>
        {" · "}EARLY ENDS {early}
        {" · "}CYCLE {data.gate_interval_min}M
      </div>
      <table className="t-tbl">
        <thead><tr><th>Session</th><th>Cycles</th><th></th><th>Launcher</th><th>EOD</th><th>Drift</th><th>Last event</th></tr></thead>
        <tbody>
          {sessions.map(s => {
            const pct = Math.min(1, s.cycles / s.expected);
            const last = s.events[s.events.length - 1];
            const st = LAUNCHER[s.launcher];
            return (
              <tr key={s.date}>
                <td><strong>{s.date.slice(5)}</strong>{s.early_close ? " ½" : ""}</td>
                <td>{s.cycles}/{s.expected}</td>
                <td style={{ width: 90 }}>
                  <div style={{ height: 6, background: "var(--t-grid)", borderRadius: 2 }}>
                    <div style={{ width: `${pct * 100}%`, height: "100%", borderRadius: 2,
                                  background: pct < 0.5 ? "var(--t-red)" : "var(--t-accent)" }} />
                  </div>
                </td>
                <td><span className={"t-pill " + st.cls}>{st.label}</span></td>
                <td title="regime (due 08:30 ET next session) · pcr rows · audit published" style={{ whiteSpace: "nowrap" }}>
                  {[["R", s.eod.regime], ["P", s.eod.pcr_rows > 0], ["A", s.eod.audit]].map(([k, ok]) => (
                    <span key={k} style={{ marginRight: 6, color: ok === null ? "var(--t-text-3)" : ok ? "var(--t-accent)" : "var(--t-red)" }}>
                      {k}{ok === null ? "·" : ok ? "✓" : "✗"}
                    </span>
                  ))}
                </td>
                <td>{s.clock_drift_s == null ? "—" : `${s.clock_drift_s}s`}</td>
                <td>{last ? `${last.at.slice(11)} ${last.event}` : "—"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
