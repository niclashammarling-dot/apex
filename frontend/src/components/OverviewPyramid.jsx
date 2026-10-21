import { useState } from "react";

// APEX overview pyramid (Niclas 2026-10-19 idea; reshaped 2026-10-21: "Meningen är att pyramiden ska
// hjälpa med en överblick. Den ska inte absorbera någon tid och energi, bara klart och tydligt visa
// hur vårt arbete hänger ihop mot målet på toppen").
// Each layer states the one question it answers for the layer above, whether that is answered, and
// what is open. No numbers by design (ruling 2026-10-21, option b): numbers live in the registers and
// notes, and hand-typed numbers here drifted within two days. A line changes only when a state does.

const GOAL = {
  name: "GO LIVE · O3", q: "Is APEX trustworthy enough for real money?",
  source: "Go criterion (vault raw/notes/2026-10/2026-10-16-apex-real-money-go-criterion-frame.md, group 1 ruled 10-19) · OUTCOMES O3",
  groups: [
    ["1 Edge", "not met", "Ruled. Needs an input with a shown direction edge (SIGNALS), then live months by the formula after the freeze"],
    ["2 Risk", "open", "Thresholds not set; measured against the ported backtest (ENGINE ↔ LIVE)"],
    ["3 Robustness", "open", "Plateau threshold not set; the 2022 fold and the plateau need the ported backtest"],
    ["4 Live matches model", "open", "Scored only after the regime port is merged"],
    ["5 Operations", "open", "Threshold not set; depends on nothing below, can be set now"],
    ["6 Discipline", "open", "Evaluation window start not set (the freeze starts then)"],
    ["7 Staging", "open", "Size step and stop rule not set; the stop rule depends on nothing below, can be set now"],
  ],
};

const LAYERS = [
  {
    key: "verify", name: "VERIFICATION", state: "partial",
    up: "Can each verdict below be trusted: rule written before the run, controlled, enough power?",
    now: "Test ladder with pre-registered cards; identical-arms guard; audit CHECKs; parameter registry",
    open: "The live chain after the score (T7) lacks the power to answer yet",
  },
  {
    key: "exec", name: "EXECUTION & RISK", state: "open",
    up: "Do exits and sizing deliver the expected value per trade that group 1 rests on?",
    now: "Exits card T8: entry × exit pairs, IS decides, OOS must hold. Runs Thursday, read Saturday",
    open: "Exit pair undecided; no size rule has passed (F15)",
  },
  {
    key: "parity", name: "ENGINE ↔ LIVE", state: "partial",
    up: "Can a backtest verdict be trusted for live?",
    now: "Exit parity R1–R9; the engine tracks live's own trades closely",
    open: "The regime port is not merged: blocks groups 2, 3 and 4",
  },
  {
    key: "signals", name: "SIGNALS & LOCKS", state: "open",
    up: "Which inputs carry a measured direction edge?",
    now: "Signal register S-rows, one pre-registered cell each: per-component IC, VIX and macro cards, factor attribution",
    open: "The score alone selects volatility, not direction (S11). No input has a shown edge yet: blocks group 1",
  },
  {
    key: "data", name: "DATA & INPUTS", state: "partial",
    up: "Are the inputs there and true, live and in the tested period?",
    now: "Input CHECKs in live; a missing input is a finding",
    open: "Backtest universe is today's tickers (survivorship); no historical earnings dates",
  },
];

const STATE_COLOR = { answered: "var(--t-accent)", partial: "var(--t-text-2)", open: "var(--t-text-3)", "not met": "var(--t-text-3)" };

function Chip({ state }) {
  return (
    <span style={{ fontFamily: "var(--mono)", fontSize: 10, letterSpacing: "0.06em", padding: "1px 6px",
      border: "1px solid var(--t-border)", color: STATE_COLOR[state] || "var(--t-text-3)", whiteSpace: "nowrap" }}>
      {state.toUpperCase()}
    </span>
  );
}

function Row({ label, children }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "110px 1fr", gap: 12, padding: "6px 0", borderTop: "1px solid var(--t-border)" }}>
      <div className="t-meta" style={{ color: "var(--t-text-3)" }}>{label}</div>
      <div className="t-meta" style={{ color: "var(--t-text-1)", lineHeight: 1.5 }}>{children}</div>
    </div>
  );
}

function Band({ i, n, open, onToggle, title, sub, state, children }) {
  // Width grows toward the base: top 40 %, base 100 %.
  const w = 40 + (60 * i) / (n - 1);
  const inset = (100 - w) / 2;
  return (
    <>
      <div onClick={onToggle} title={open ? "close" : "open"} style={{ position: "relative", height: 76, marginBottom: 6, cursor: "pointer" }}>
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
            <div className="t-meta" style={{ marginTop: 3, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{sub}</div>
          </div>
          {state && <Chip state={state} />}
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
  // ?layer=<key> opens one layer on load (deep link); "goal" opens the top.
  const [open, setOpen] = useState(() => new URLSearchParams(window.location.search).get("layer"));
  const toggle = (k) => setOpen(open === k ? null : k);
  const n = LAYERS.length + 1;
  return (
    <div className="t-grid" style={{ gridTemplateColumns: "1fr 300px" }}>
      <div className="t-col-main">
        <div className="t-card">
          <div className="t-card-head">
            <div className="t-card-title">APEX PYRAMID</div>
            <div className="t-meta">EACH LAYER ANSWERS ONE QUESTION FOR THE LAYER ABOVE</div>
          </div>
          <div className="t-card-body">
            <Band i={0} n={n} open={open === "goal"} onToggle={() => toggle("goal")} title={GOAL.name} sub={GOAL.q}>
              {GOAL.groups.map(([g, s, t]) => (
                <Row key={g} label={g}><Chip state={s} /> <span style={{ marginLeft: 6 }}>{t}</span></Row>
              ))}
              <Row label="SOURCE">{GOAL.source}</Row>
            </Band>
            {LAYERS.map((l, k) => (
              <Band key={l.key} i={k + 1} n={n} open={open === l.key} onToggle={() => toggle(l.key)}
                title={l.name} sub={l.up} state={l.state}>
                <Row label="ANSWERS UP">{l.up}</Row>
                <Row label="WORK ON IT">{l.now}</Row>
                <Row label="OPEN">{l.open}</Row>
              </Band>
            ))}
            <div className="t-meta" style={{ textAlign: "center", marginTop: 8 }}>
              a layer whose question is open blocks everything above it · numbers live in the registers, not here
            </div>
          </div>
        </div>
      </div>
      <div className="t-col-side">
        <div className="t-card">
          <div className="t-card-head">
            <div className="t-card-title">WHERE THE CHAIN BREAKS</div>
            <div className="t-meta">IN BLOCKING ORDER</div>
          </div>
          <div className="t-card-body" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {[
              ["Top", "Go criterion groups 2–7 cannot be scored yet; thresholds for 5 and 7 can be set now"],
              ["Engine ↔ live", "Regime port not merged: groups 2, 3, 4"],
              ["Signals", "No input with a shown direction edge: group 1"],
              ["Data", "Survivorship in the backtest universe: absolute baselines"],
            ].map(([w, t]) => (
              <div key={w} className="t-meta"><span style={{ color: "var(--t-text-1)", marginRight: 6 }}>{w}</span>{t}</div>
            ))}
            <div className="t-meta" style={{ color: "var(--t-text-3)", marginTop: 6 }}>
              Work counts toward the top when it closes one of these.
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
