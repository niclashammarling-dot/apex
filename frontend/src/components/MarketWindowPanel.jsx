import React, { useEffect, useState } from "react";

// Dashboard surface for the scheduled market window (scripts/market_window.sh):
// per-session demo gate cycles vs expected and the launcher's own events, from
// /api/ops/window. Same numbers CHECK 80's coverage line reads (audit/gate_cycles.py:
// one cycle per cycle_started_at, expected = grid slots at the scheduler's phase).
// Live cycles shown beside demo (L); the bar and coverage stay demo, as CHECK 80's do.
// From 2026-10-04 each cycle's exit reason (gate_cycles): quiet (Q), halted (H),
// every evaluation raised (R), manual /gate/run cycles (M, not in the counts).
const LAUNCHER = {
  running:     { label: "RUNNING",     cls: "t-pill-lock" },
  closed:      { label: "CLOSED",      cls: "t-pill-paper" },
  ended_early: { label: "ENDED EARLY", cls: "t-sev-high" },
  not_started: { label: "NO LAUNCHER", cls: "t-sev-medium" },
};

const QUIET = ["no_candidates", "all_skipped", "all_excluded"];
const HALT = ["halt_unreconciled", "halt_data_quality", "broker_unreachable", "account_blocked", "loss_cap"];

function CycleReasons({ r }) {
  if (!r) return null;
  const sum = (book, keys) => keys.reduce((a, k) => a + (r[book][k] || 0), 0);
  const q = sum("demo", QUIET) + sum("live", QUIET);
  const h = sum("live", HALT);
  const x = sum("demo", ["all_raised"]) + sum("live", ["all_raised"]);
  const m = r.manual.demo + r.manual.live;
  const detail = ["demo", "live"].map(b =>
    `${b}: ` + (Object.entries(r[b]).map(([k, n]) => `${k} ${n}`).join(", ") || "none")).join("\n")
    + `\nmanual: demo ${r.manual.demo}, live ${r.manual.live}`;
  return (
    <span className="t-meta" style={{ marginLeft: 6 }} title={detail}>
      Q {q}
      {h > 0 && <span style={{ color: "var(--t-red)", marginLeft: 4 }}>H {h}</span>}
      {x > 0 && <span style={{ color: "var(--t-red)", marginLeft: 4 }}>R {x}</span>}
      {m > 0 && <span style={{ marginLeft: 4 }}>M {m}</span>}
    </span>
  );
}

// Stamped scheduler jobs from /api/ops/jobs (CHECK 86): last finish, outcome,
// age. prune_signals ran nowhere for three weeks (09-16 → 10-07) and nothing
// showed it; a stale job is red here.
function JobRuns({ jobs }) {
  if (!jobs || !jobs.length) return null;
  return (
    <div className="t-meta" style={{ marginBottom: 10 }}>
      JOBS{" "}
      {jobs.map(j => (
        <span key={j.job} style={{ marginRight: 10, color: j.stale ? "var(--t-red)" : undefined }}
              title={`started ${j.started_at}\nfinished ${j.finished_at || "—"}\noutcome ${j.outcome || "—"}`
                     + (j.max_age_h ? `\nstale after ${j.max_age_h} h` : "")}>
          {j.job} {j.outcome === "ok" ? `${j.age_h} h ago` : (j.outcome || "running")}
        </span>
      ))}
    </div>
  );
}

export default function MarketWindowPanel() {
  const [data, setData] = useState(null);
  const [jobs, setJobs] = useState(null);
  useEffect(() => {
    const load = () => {
      fetch("/api/ops/window?days=10").then(r => r.json()).then(setData).catch(() => {});
      fetch("/api/ops/jobs").then(r => r.json()).then(d => setJobs(d.jobs)).catch(() => {});
    };
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
      <JobRuns jobs={jobs} />
      <table className="t-tbl">
        <thead><tr><th>Session</th><th>Cycles</th><th></th><th>Launcher</th><th>EOD</th><th>Watch</th><th>Commit</th><th>Drift</th><th>Last event</th></tr></thead>
        <tbody>
          {sessions.map(s => {
            const pct = Math.min(1, s.cycles / s.expected);
            const last = s.events[s.events.length - 1];
            const st = LAUNCHER[s.launcher];
            return (
              <tr key={s.date}>
                <td><strong>{s.date.slice(5)}</strong>{s.early_close ? " ½" : ""}
                  {s.partial && (
                    <span className="t-pill t-sev-high" style={{ marginLeft: 4 }}
                          title={`flagged partial (${s.partial.status}) by the cycle watch: ${s.partial.cause}`}>
                      PARTIAL
                    </span>
                  )}
                </td>
                <td style={{ whiteSpace: "nowrap" }}>
                  {s.cycles}/{s.expected}
                  {s.live_expected != null && (
                    <span className="t-meta" style={{ marginLeft: 6 }} title="live gate cycles / grid slots">
                      L {s.live_cycles}/{s.live_expected}
                    </span>
                  )}
                  <CycleReasons r={s.cycle_reasons} />
                </td>
                <td style={{ width: 90 }}>
                  <div style={{ height: 6, background: "var(--t-grid)", borderRadius: 2 }}>
                    <div style={{ width: `${pct * 100}%`, height: "100%", borderRadius: 2,
                                  background: pct < 0.5 ? "var(--t-red)" : "var(--t-accent)" }} />
                  </div>
                </td>
                <td>
                  <span className={"t-pill " + st.cls}>{st.label}</span>
                  {s.startup_catchups_failed?.length > 0 && (
                    <span className="t-pill t-sev-high" style={{ marginLeft: 4 }}
                          title={"startup catch-up raised, startup continued: " + s.startup_catchups_failed.join(", ")}>
                      CATCH-UP ✗{s.startup_catchups_failed.length}
                    </span>
                  )}
                </td>
                <td title={"heartbeat pushed by the launcher · regime (due 08:30 ET next session) · pcr rows · audit published · off-host heartbeat watcher recorded the session"
                           + (s.eod.watcher_detail ? " — " + s.eod.watcher_detail : "")} style={{ whiteSpace: "nowrap" }}>
                  {[["H", s.heartbeat_pushed ?? null], ["R", s.eod.regime], ["P", s.eod.pcr_rows > 0], ["A", s.eod.audit]].map(([k, ok]) => (
                    <span key={k} style={{ marginRight: 6, color: ok === null ? "var(--t-text-3)" : ok ? "var(--t-accent)" : "var(--t-red)" }}>
                      {k}{ok === null ? "·" : ok ? "✓" : "✗"}
                    </span>
                  ))}
                  {data.watcher_alert_enabled === false ? (
                    <span style={{ color: "var(--t-text-3)" }}
                          title="counter-watch mail off by decision until real money — see apex-moc go-live checklist; result still recorded">W off</span>
                  ) : (
                    <span style={{ color: s.eod.watcher == null ? "var(--t-text-3)" : s.eod.watcher ? "var(--t-accent)" : "var(--t-red)" }}>
                      W{s.eod.watcher == null ? "·" : s.eod.watcher ? "✓" : "✗"}
                    </span>
                  )}
                </td>
                <td title={s.cycle_watch
                             ? `cycle watch (every 15 min, market hours): ${s.cycle_watch.runs} runs, ${s.cycle_watch.stale_runs} stale` +
                               `${s.cycle_watch.alerted ? ", alert mailed" : ""} — last ${s.cycle_watch.last_at}: ${s.cycle_watch.last}`
                             : "cycle watch: no run logged this session (task not firing, or before 2026-10-05)"}
                    style={{ whiteSpace: "nowrap",
                             color: s.cycle_watch == null ? "var(--t-text-3)"
                                  : s.cycle_watch.stale_runs > 0 ? "var(--t-red)" : "var(--t-accent)" }}>
                  {s.cycle_watch == null ? "—"
                    : s.cycle_watch.stale_runs > 0 ? `✗ ${s.cycle_watch.stale_runs}/${s.cycle_watch.runs}`
                    : `✓ ${s.cycle_watch.runs}`}
                </td>
                <td title="commit the serving process loaded at startup (dirty = tracked code modified outside data/)"
                    style={{ whiteSpace: "nowrap", color: s.serving_commit?.dirty ? "var(--t-red)" : undefined }}>
                  {s.serving_commit == null ? "—"
                    : s.serving_commit.commit == null ? "unknown"
                    : s.serving_commit.commit.slice(0, 7) + (s.serving_commit.dirty ? " dirty" : "")}
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
