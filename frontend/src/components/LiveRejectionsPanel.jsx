import React, { useEffect, useState } from "react";

// Dashboard surface for the gate tables' outcome_reason (live and, from lock labels phase 2, demo) and live's
// cap_check, from /api/ops/rejections. Refusals per day by the refusing rule ("unknown": a FILTERED_UNKNOWN row,
// no rule covers it), and every execution-side sector cap test with the enforced cap vs the flat cap.
// Observe-only evidence for the two-caps decision (2026-09-27).
const REASON_CLS = {
  sector_cap:         "t-sev-high",
  max_positions:      "t-sev-medium",
  loss_cap:           "t-sev-high",
  notional_too_small: "t-sev-medium",
  price_missing:      "t-sev-medium",
  position_open:      "t-pill-paper",
  broker_error:       "t-sev-high",
  unknown:            "t-sev-high",
};

function ReasonDays({ days, book }) {
  if (days.length === 0) return <div className="t-meta" style={{ marginBottom: 10 }}>{book}: no refusals in 10 days</div>;
  return (
    <table className="t-tbl" style={{ marginBottom: 12 }}>
      <thead><tr><th>{book} day</th><th>Reasons</th></tr></thead>
      <tbody>
        {days.map(d => (
          <tr key={d.date}>
            <td><strong>{d.date.slice(5)}</strong></td>
            <td>{Object.entries(d.reasons).map(([k, n]) => (
              <span key={k} className={"t-pill " + (REASON_CLS[k] || "t-pill-paper")} style={{ marginRight: 6 }}>{k} ×{n}</span>
            ))}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

const pct = v => `${(v * 100).toFixed(1)}%`;
const sthlm = ts => new Date(ts).toLocaleString("sv-SE", { timeZone: "Europe/Stockholm", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });

export default function LiveRejectionsPanel() {
  const [data, setData] = useState(null);
  useEffect(() => {
    const load = () => fetch("/api/ops/rejections?days=10").then(r => r.json()).then(setData).catch(() => {});
    load();
    const iv = setInterval(load, 60_000);
    return () => clearInterval(iv);
  }, []);

  if (!data) return <div className="t-meta">loading…</div>;
  const s = data.summary;

  return (
    <div>
      <div className="t-meta" style={{ marginBottom: 10 }}>
        CAP TESTS <strong>{s.cap_tests}</strong>
        {" · "}DYNAMIC {s.dynamic || 0}
        {" · "}FLAT FALLBACK {s.flat_fallback || 0}
        {" · "}DISAGREE{" "}
        <strong style={{ color: (s.dynamic_rejects_flat_allows || s.flat_rejects_dynamic_allows) ? "var(--t-red)" : "var(--t-text-1)" }}>
          {(s.dynamic_rejects_flat_allows || 0) + (s.flat_rejects_dynamic_allows || 0)}
        </strong>
      </div>
      <ReasonDays days={data.days} book="LIVE" />
      <ReasonDays days={data.demo_days || []} book="DEMO" />
      {data.caps.length > 0 && (
        <table className="t-tbl">
          <thead><tr><th>Time (Sthlm)</th><th>Ticker</th><th>Sector</th><th>Projected</th><th>Cap</th><th>Flat</th><th>Decision</th></tr></thead>
          <tbody>
            {data.caps.slice(0, 30).map(c => (
              <tr key={c.timestamp + c.ticker}>
                <td>{sthlm(c.timestamp)}</td>
                <td><strong>{c.ticker}</strong></td>
                <td>{c.sector}</td>
                <td>{pct(c.before)} → {pct(c.projected)}</td>
                <td style={{ color: c.over_cap ? "var(--t-red)" : undefined }}>{pct(c.cap)}{c.source === "flat_fallback" ? " (fb)" : ""}</td>
                <td style={{ color: c.over_flat ? "var(--t-red)" : undefined }}>{pct(c.flat)}</td>
                <td>{c.decision}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
