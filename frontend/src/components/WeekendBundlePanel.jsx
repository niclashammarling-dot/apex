import React, { useEffect, useState } from "react";

// The weekend bundle (audit/bundle.py, /api/ops/bundle): every APEX fault that
// needs a decision — non-INFO findings, TODO markers, signals with no reader,
// config changes since the last bundle — each with how long it has waited.
// Built in the week's last publish and mailed; read here at weekend maintenance.
// Nothing is removed automatically (Niclas 2026-10-10). Shows its build time and
// an explicit offline state, never an empty card.
const SECTIONS = [["findings", "Findings"], ["todos", "TODO"], ["parity", "No reader"], ["config", "Config changed"]];
const SEV = { CRITICAL: "t-sev-high", WARNING: "t-sev-medium" };
const sthlm = ts => new Date(ts).toLocaleString("sv-SE", { timeZone: "Europe/Stockholm", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
const ageDays = d => Math.max(0, Math.round((Date.now() - new Date(d + "T12:00:00Z").getTime()) / 86400000));

export default function WeekendBundlePanel() {
  const [data, setData]   = useState(null);
  const [error, setError] = useState(null);
  const [open, setOpen]   = useState("findings");
  useEffect(() => {
    const load = () => fetch("/api/ops/bundle")
      .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then(d => { setData(d); setError(null); })
      .catch(e => setError(e.message || "unreachable"));
    load();
    const iv = setInterval(load, 300_000);
    return () => clearInterval(iv);
  }, []);

  const banner = error && (
    <div className="t-meta" style={{ color: "var(--t-red)", marginBottom: 8 }}>
      BACKEND OFFLINE ({error}){data?.present ? ` — showing the bundle built ${sthlm(data.generated_at)}` : " — no bundle loaded yet"}
    </div>
  );
  if (!data) return banner || <div className="t-meta">loading…</div>;
  if (!data.present) return <div>{banner}<div className="t-meta">No bundle built yet — the first is due at the end of the week's last session (publish 22:33 CEST).</div></div>;

  const rows = data.items.filter(i => i.section === open).sort((a, b) => a.first_seen.localeCompare(b.first_seen));
  return (
    <div>
      {banner}
      <div className="t-meta" style={{ marginBottom: 10 }}>
        BUILT {sthlm(data.generated_at)} @ {data.host_commit}
        {SECTIONS.map(([k, label]) => (
          <span key={k} onClick={() => setOpen(k)} style={{ cursor: "pointer", marginLeft: 12, fontWeight: open === k ? 700 : 400 }}>
            {label.toUpperCase()} {data.counts[k] ?? 0}
          </span>
        ))}
        <span style={{ marginLeft: 12 }}>EXCEPTED {data.excepted.length}</span>
        {data.counts.no_scope ? <span style={{ marginLeft: 12, color: "var(--t-red)" }}>NO SCOPE {data.counts.no_scope}</span> : null}
      </div>
      {rows.length === 0 ? <div className="t-meta">nothing in this section</div> : (
        <table className="t-tbl">
          <thead><tr><th>Waiting</th><th>Sev</th><th>Where</th><th>Item</th></tr></thead>
          <tbody>
            {rows.map(i => (
              <tr key={i.key}>
                <td>{ageDays(i.first_seen)}d</td>
                <td><span className={"t-pill " + (SEV[i.severity] || "t-pill-paper")}>{i.severity}</span></td>
                <td>{i.where}</td>
                <td>{i.text}
                  <div className="t-meta" style={{ marginTop: 4, color: i.scope ? undefined : "var(--t-red)" }}>
                    {i.scope ? `Scope: ${i.scope}` : "NO PROPOSED SCOPE — write one before the bundle day"}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
