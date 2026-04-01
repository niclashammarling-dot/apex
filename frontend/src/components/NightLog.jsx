import React, { useState } from "react";

const styles = `
.night-log { display: flex; flex-direction: column; gap: 0; }

.night-log-header {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: .08em;
  text-transform: uppercase;
  color: var(--text-3);
  padding: 0 0 10px 0;
}

.night-log-empty {
  color: var(--text-3);
  font-size: 13px;
  padding: 24px 0;
  text-align: center;
}

.audit-entry {
  border: 1px solid var(--border);
  border-radius: 8px;
  margin-bottom: 8px;
  overflow: hidden;
}

.audit-entry-header {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 14px;
  cursor: pointer;
  background: var(--card);
  user-select: none;
}
.audit-entry-header:hover { background: #1a2035; }

.audit-date {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-1);
  min-width: 90px;
}

.audit-badge {
  font-size: 11px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 10px;
  white-space: nowrap;
}
.audit-badge.critical { background: rgba(239,68,68,.15); color: #f87171; }
.audit-badge.warning  { background: rgba(234,179,8,.15);  color: #facc15; }
.audit-badge.clean    { background: rgba(34,197,94,.15);  color: #4ade80; }
.audit-badge.info     { background: rgba(99,102,241,.15); color: #818cf8; }

.audit-summary {
  font-size: 12px;
  color: var(--text-3);
  flex: 1;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.audit-chevron {
  font-size: 10px;
  color: var(--text-3);
  transition: transform .15s;
}
.audit-chevron.open { transform: rotate(90deg); }

.audit-body {
  padding: 14px 16px;
  border-top: 1px solid var(--border);
  background: #0d1117;
}

.audit-markdown {
  font-family: 'Courier New', monospace;
  font-size: 12px;
  line-height: 1.65;
  color: var(--text-2);
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 480px;
  overflow-y: auto;
}

/* Minimal markdown colouring */
.audit-markdown .md-h1  { color: var(--text-1); font-size: 14px; font-weight: 700; }
.audit-markdown .md-h2  { color: var(--text-1); font-size: 13px; font-weight: 600; margin-top: 8px; }
.audit-markdown .md-h3  { color: #818cf8; font-size: 12px; font-weight: 600; }
.audit-markdown .md-critical { color: #f87171; font-weight: 600; }
.audit-markdown .md-warning  { color: #facc15; font-weight: 600; }
.audit-markdown .md-clean    { color: #4ade80; }
`;

function badge(summary) {
  if (!summary) return { label: "—", cls: "info" };
  const low = summary.toLowerCase();
  if (/0 issues|✓ clean/i.test(low)) return { label: "Clean", cls: "clean" };
  const crit = low.match(/(\d+)\s+critical/);
  if (crit && parseInt(crit[1]) > 0) return { label: `${crit[1]} critical`, cls: "critical" };
  const warn = low.match(/(\d+)\s+warn/);
  if (warn && parseInt(warn[1]) > 0) return { label: `${warn[1]} warnings`, cls: "warning" };
  return { label: "Info", cls: "info" };
}

function AuditEntry({ report }) {
  const [open, setOpen] = useState(false);
  const { label, cls } = badge(report.summary);

  return (
    <div className="audit-entry">
      <div className="audit-entry-header" onClick={() => setOpen(o => !o)}>
        <span className="audit-date">{report.date}</span>
        <span className={`audit-badge ${cls}`}>{label}</span>
        <span className="audit-summary">{report.summary || "No summary"}</span>
        <span className={`audit-chevron ${open ? "open" : ""}`}>▶</span>
      </div>
      {open && (
        <div className="audit-body">
          <pre className="audit-markdown">{report.content}</pre>
        </div>
      )}
    </div>
  );
}

export default function NightLog({ reports }) {
  return (
    <>
      <style>{styles}</style>
      <div className="night-log">
        <div className="night-log-header">Night Log</div>
        {(!reports || reports.length === 0)
          ? <div className="night-log-empty">No audit reports yet — first run tonight at 2am.</div>
          : reports.map(r => <AuditEntry key={r.date} report={r} />)
        }
      </div>
    </>
  );
}
