import { useEffect, useState } from "react";

// APEX pyramid, derived (Niclas 2026-10-21: "underhållas automatiskt utifrån det arbete vi gör …
// en visuell representation av maskineriet i apex … siffrorna ska komma ur vårt arbete med att
// kartlägga hur vi får svar på alla våra frågor").
// Everything shown comes from /api/ops/pyramid: audit/QUESTIONS.md maps each question to the register
// rows that answer it, and audit/question_map.py reads each row's state from its register. The only
// thing written here is the order and the short name of the layers.

const LAYERS = [
  ["Q-VER", "VERIFICATION"],
  ["Q-EXE", "EXECUTION & RISK"],
  ["Q-PAR", "ENGINE ↔ LIVE"],
  ["Q-SIG", "SIGNALS & LOCKS"],
  ["Q-DAT", "DATA & INPUTS"],
  ["Q-OPS", "OPERATIONS"],
];
const GROUPS = ["G1", "G2", "G3", "G4", "G5", "G6", "G7"];

const STATE_COLOR = { answered: "var(--t-accent)", partial: "var(--t-text-2)", open: "var(--t-text-3)" };

function Chip({ text, state }) {
  return (
    <span style={{ fontFamily: "var(--mono)", fontSize: 10, letterSpacing: "0.06em", padding: "1px 6px",
      border: "1px solid var(--t-border)", color: STATE_COLOR[state] || "var(--t-text-3)", whiteSpace: "nowrap" }}>
      {String(text).toUpperCase()}
    </span>
  );
}

// {"S:open": 13, "S:discarded": 1, "GAP:yes": 6} -> "S open 13 · discarded 1 | GAP yes 6"
function countsLine(counts) {
  const by = {};
  Object.entries(counts || {}).forEach(([k, n]) => {
    const [kind, st] = k.split(/:(.*)/s);
    (by[kind] = by[kind] || []).push(`${st} ${n}`);
  });
  return Object.entries(by).map(([kind, xs]) => `${kind} ${xs.join(" · ")}`).join("  |  ");
}

function Row({ label, children }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "110px 1fr", gap: 12, padding: "5px 0", borderTop: "1px solid var(--t-border)" }}>
      <div className="t-meta" style={{ color: "var(--t-text-3)" }}>{label}</div>
      <div className="t-meta" style={{ color: "var(--t-text-1)", lineHeight: 1.5 }}>{children}</div>
    </div>
  );
}

function Rows({ q }) {
  if (!q.rows.length) return null;
  return (
    <Row label="ROWS">
      <div style={{ display: "flex", flexWrap: "wrap", gap: "4px 10px" }}>
        {q.rows.map((r) => (
          <span key={r.ref} style={{ color: r.answered ? "var(--t-accent)" : "var(--t-text-2)" }}>
            {r.ref.replace(/^PARAM:.*::/, "PARAM ")} <span style={{ color: "var(--t-text-3)" }}>{r.state}</span>
          </span>
        ))}
      </div>
    </Row>
  );
}

// Test cards that answer this question (2026-10-31, ruling A of 2026-10-24): read from each card's frontmatter
// (answers, delivers, verdict, next) by audit/question_map.card_index, newest first. Nothing typed here.
function Cards({ q }) {
  if (!q.cards || !q.cards.length) return <Row label="CARDS">none answer this question yet</Row>;
  return (
    <Row label={q.card_break ? "CARDS · BREAK" : "CARDS"}>
      {q.cards.map((c) => (
        <div key={c.slug} style={{ marginBottom: 4 }}>
          <Chip text={c.verdict} state={c.verdict === "supported" ? "answered" : c.verdict === "unanswered" || c.verdict === "pending" ? "open" : "partial"} />
          <span style={{ marginLeft: 6, color: "var(--t-text-2)" }}>{c.slug}</span>
          <div style={{ color: "var(--t-text-1)" }}>delivers: {c.delivers}</div>
          {c.next && <div style={{ color: "var(--t-text-3)" }}>next: {c.next}</div>}
        </div>
      ))}
    </Row>
  );
}

function Band({ i, n, open, onToggle, title, q, children }) {
  // Width grows toward the base: top 40 %, base 100 %.
  const w = 40 + (60 * i) / (n - 1);
  const inset = (100 - w) / 2;
  return (
    <>
      <div onClick={onToggle} title={open ? "close" : "open"} style={{ position: "relative", height: 84, marginBottom: 6, cursor: "pointer" }}>
        <div style={{
          position: "absolute", inset: 0,
          clipPath: `polygon(${inset + 3}% 0, ${100 - inset - 3}% 0, ${100 - inset}% 100%, ${inset}% 100%)`,
          background: `color-mix(in oklab, var(--t-accent) ${i === 0 ? 22 : 6 + i * 3}%, var(--t-bg-2))`,
          border: "1px solid var(--t-border)",
        }} />
        <div style={{ position: "absolute", top: 0, bottom: 0, left: `${inset + 5}%`, right: `${inset + 5}%`,
          display: "flex", alignItems: "center", gap: 10, minWidth: 0 }}>
          <div style={{ minWidth: 0, flex: 1 }}>
            <div style={{ fontFamily: "var(--mono)", fontSize: 12, color: "var(--t-text-1)", letterSpacing: "0.08em" }}>
              <span style={{ color: "var(--t-accent)", marginRight: 6 }}>{open ? "▾" : "▸"}</span>{title}
            </div>
            <div className="t-meta" style={{ marginTop: 2, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{q.question}</div>
            <div className="t-meta" style={{ marginTop: 2, color: "var(--t-text-3)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
              {countsLine(q.counts)}
            </div>
          </div>
          <Chip text={q.state} state={q.state} />
        </div>
      </div>
      {open && (
        <div style={{ margin: "0 2% 10px", padding: "8px 14px", border: "1px solid var(--t-accent)", background: "var(--t-bg-2)" }}>
          {children}
        </div>
      )}
    </>
  );
}

export default function OverviewPyramid() {
  // ?layer=<id> opens one layer on load (deep link), e.g. ?layer=Q-SIG or ?layer=O3.
  const [open, setOpen] = useState(() => new URLSearchParams(window.location.search).get("layer"));
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => {
    fetch("/api/ops/pyramid")
      .then(async (r) => (r.ok ? r.json() : Promise.reject((await r.json().catch(() => ({}))).detail || `HTTP ${r.status}`)))
      .then(setData).catch((e) => setErr(String(e)));
  }, []);
  const toggle = (k) => setOpen(open === k ? null : k);

  if (err || !data) {
    return (
      <div className="t-card"><div className="t-card-head"><div className="t-card-title">APEX PYRAMID</div></div>
        <div className="t-card-body t-meta">{err ? `not derived: ${err}` : "deriving…"}</div></div>
    );
  }
  const Q = data.questions;
  const n = LAYERS.length + 1;
  const openQs = LAYERS.map(([id, name]) => [name, Q[id]]).filter(([, q]) => q && q.state !== "answered");
  return (
    <div className="t-grid" style={{ gridTemplateColumns: "1fr 300px" }}>
      <div className="t-col-main">
        <div className="t-card">
          <div className="t-card-head">
            <div className="t-card-title">APEX PYRAMID</div>
            <div className="t-meta">DERIVED FROM audit/QUESTIONS.md · CHECKS RUN {data.as_of.checks_run || "—"}</div>
          </div>
          <div className="t-card-body">
            <Band i={0} n={n} open={open === "O3"} onToggle={() => toggle("O3")} title="GO LIVE · O3" q={Q.O3}>
              {GROUPS.filter((g) => Q[g]).map((g) => (
                <Row key={g} label={g}>
                  <Chip text={Q[g].scored ? Q[g].state : "unscored"} state={Q[g].scored ? Q[g].state : "open"} />
                  <span style={{ marginLeft: 6 }}>{Q[g].question}</span>
                  <div style={{ color: "var(--t-text-3)" }}>threshold: {Q[g].threshold} · {countsLine(Q[g].counts) || `via ${Q[g].children.join(", ")}`}</div>
                </Row>
              ))}
            </Band>
            {LAYERS.map(([id, name], k) => Q[id] && (
              <Band key={id} i={k + 1} n={n} open={open === id} onToggle={() => toggle(id)} title={name} q={Q[id]}>
                <Row label="ANSWERS UP">{Q[id].answers.join(", ")}</Row>
                {Q[id].children.length > 0 && <Row label="VIA">{Q[id].children.join(", ")}</Row>}
                <Rows q={Q[id]} />
                <Cards q={Q[id]} />
              </Band>
            ))}
            <div className="t-meta" style={{ textAlign: "center", marginTop: 8 }}>
              a layer whose question is open blocks everything above it · every number is a count of register rows
            </div>
          </div>
        </div>
      </div>
      <div className="t-col-side">
        <div className="t-card">
          <div className="t-card-head">
            <div className="t-card-title">WHERE THE CHAIN BREAKS</div>
            <div className="t-meta">ROWS NO REGISTER HOLDS YET</div>
          </div>
          <div className="t-card-body" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {data.breaks.map((b) => (
              <div key={b.id} className="t-meta"><span style={{ color: "var(--t-text-1)", marginRight: 6 }}>{b.id}</span>{b.what}
                <span style={{ color: "var(--t-text-3)" }}> · {b.needed_by}</span></div>
            ))}
            <div className="t-meta" style={{ color: "var(--t-text-3)", marginTop: 6 }}>
              Open layers: {openQs.map(([name, q]) => `${name} (${q.state})`).join(", ") || "none"}
            </div>
            <div className="t-meta" style={{ color: "var(--t-text-3)" }}>
              Layers whose cards are all unanswered: {LAYERS.filter(([id]) => Q[id] && Q[id].card_break).map(([, name]) => name).join(", ") || "none"}
            </div>
            {data.cards_unread && <div className="t-meta" style={{ color: "var(--t-sev-medium, var(--t-text-1))" }}>cards unread: {data.cards_unread}</div>}
            {data.cards_invalid && data.cards_invalid.length > 0 && (
              <div className="t-meta">cards with a malformed frontmatter: {data.cards_invalid.map((c) => c.slug).join(", ")}</div>)}
          </div>
        </div>
      </div>
    </div>
  );
}
