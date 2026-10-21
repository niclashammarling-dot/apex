import { useState } from "react";

// APEX overview pyramid — SKETCH (Niclas 2026-10-19: "en kombination"; idea
// raw/inbox/ideas/2026-10-19-apex-overview-parts-and-process.md in the vault).
// Layers by dependency form the pyramid: each stands on the one below. Each
// layer carries its own evidence funnel (built / tested / in flight). No count
// is shown yet: every number must be derived from the register named on its
// layer, never typed here. Until that derivation exists the funnel says so.

const LAYERS = [
  {
    key: "profit", name: "PROFIT", q: "Does it earn, in reality?",
    parts: ["Go criterion group 1 (90 % power, floor 3 mo)", "OUTCOMES O3 (hit rate > 72 %)", "Live months"],
    source: "OUTCOMES.md · go-criterion note",
    register: "Vault wiki/_index/OUTCOMES.md (O3) and the go-criterion note (raw/notes/2026-10/2026-10-19-apex-go-criterion-group1-and-signal-register-ruled.md)",
    writes: "Niclas's rulings in session: target, power, floor, population",
    reads: "Every INDEX commitment carries an outcome tag [→ O3]; the weekly report and its weekly read (bundle section weekly)",
    together: "The layer asks whether APEX earns. The register says what 'earns' means before anyone looks: hit rate above 72 % on in-session entries, and live months by the formula max(3, n/rate) at 90 % power. Live failing means the model is wrong.",
    status: [
      ["hypothesis", "Live beats same-day volatility-matched random entries on wins (p 0.01–0.04, 1,000 draws), not yet on the mean (p 0.07–0.08); 57 trades, one period, several looks. Population: the canonical live sample, db.live_sample_rows (07-07 reset, unverified and annotated rows out; master 62041b9). Vault 2026-10-20-apex-live-vs-engine-canonical-sample.md"],
      ["hypothesis", "Pre-open class (n 15) is the strongest contrast (p 0.003–0.03) and is excluded from live by Finding 1: signal-register S12"],
    ]
  },
  {
    key: "verify", name: "VERIFICATION", q: "Do we know it works?",
    parts: ["Test ladder (L0–L5)", "Identical-arms guard", "Test suite", "Audit CHECKs", "Battery cells (pre-registered)", "Weekend bundle"],
    source: "audit/CHECKS.md · tests/ · battery notes",
    register: "audit/CHECKS.md (one row per CHECK), tests/, battery pre-registration notes in the vault",
    writes: "A finding or a bug adds a CHECK row plus a positive-control test; a battery cell gets its rule written before the run",
    reads: "publish_audit_state runs the CHECKs nightly; non-INFO findings reach the weekend bundle (TEST tab); the pre-push hook runs the parameter ratchet",
    together: "This layer is the one that tells the others whether they hold. Its register is the list of what is checked; a part with no CHECK or test row is unverified, however well it runs.",
    status: [
      ["measured", "Test ladder L0–L5: each card names its question, null, baseline, validity gates and defects D1–D6 before it runs (vault 2026-10-20-apex-test-ladder-and-cards.md)"],
      ["measured", "Identical-arms guard: differently configured arms with identical results are refused, naming the parameter (port-regime archive_guard.py, battery reader). Commit hook: no git commit after a piped test run without pipefail (~/.claude/hooks)"],
      ["unanswered", "T7 rejected-candidate shadow book: L5, sentiment and leading unanswered at this n (MDE 3.5–5.2 pts against 1 pt); read monthly, first 2026-11-20"],
    ]
  },
  {
    key: "exec", name: "EXECUTION & RISK", q: "Is the order placed and sized right?",
    parts: ["Exit rules (T4/T8)", "CHECK 93 stop slippage", "Sizing (F15)", "Brackets: stop / target", "Slots and overflow", "Sector caps", "Broker guard"],
    source: "audit/PARAMETERS.json · live_config.json",
    register: "audit/PARAMETERS.json (every parameter with provenance), data/live_config.json and data/demo_config.json",
    writes: "A parameter change registers its value, source and engine/commit; Settings writes the configs",
    reads: "tests/test_parameter_registry.py (ratchet, also run before every push); CHECK 9 config bounds; CHECK 90 reads Lock 5 against the enforced cap",
    together: "The layer places and sizes orders. The register records why each number is what it is, so a size or stop can be traced to the test that set it (F15 stands: no size rule passed the 10-19 gate).",
    status: [
      ["measured", "Exits: fixed 5-day exit 0.103 %/capital-day against the live bracket 0.051–0.064 (IS), robust across three peak models and both windows; live loses to a random exit of the same length (t −2.1 to −3.1). The profit lock buys about 2 win-rate points with per-day return. Vault ladder note, T4 on R9"],
      ["hypothesis", "Exits card T8 (pre-registered 2026-10-20; code Thursday 10-22): does a fixed-time exit beat the bracket at L3, in both windows and in 2022 alone?"],
      ["measured", "CHECK 93 stop-fill slippage on R7's own definition (gap = open at or below the stop, measured against the open; 2026-10-21): intraday n 37, +0.02 % (model 0.02 %); gap n 7, +0.49 % (model 0.50 %). These are largely the fills R7 measured, so the definitions match. Only fills from 2026-10-21 13:30 UTC test the model; each INFO line counts them per class"],
      ["measured", "Stop ratchet log: every live stop move appended (stop_ratchet_log, UPDATE and DELETE refused); CHECK 93 warns when a TSL exit's moves are missing. Starts with the scheduled market window 2026-10-21 14:20 Stockholm"],
    ]
  },
  {
    key: "parity", name: "ENGINE ↔ LIVE", q: "Does the backtest model live?",
    parts: ["engine_fast / engine.py", "Regime port (port-regime)", "Exit parity", "CHECK 76"],
    source: "PRODUCTION_GAP.md (numbered rows)",
    register: "backend/backtest/PRODUCTION_GAP.md, numbered rows: every known difference between the engines and live; EXIT_PARITY.md R1–R9 for the exits",
    writes: "A found difference becomes a row; a fix closes it with its commit",
    reads: "CHECK 76 (backtest engine parity); the battery's flip attribution reverts named fixes to explain a changed verdict",
    together: "A backtest only says something about live where the engine models live. The register is the list of where it does not yet, so a battery result is read with its open rows beside it.",
    status: [
      ["measured", "R9: the engine's profit-lock peak carries the day's high; the close model armed the lock on 17.5 % of trades against live's 60 %. Three settings (close / high / polled) bracket live (EXIT_PARITY.md R9, port-regime e753565, 1c159b1)"],
      ["measured", "Engine on live's own entries: sign agreement 0.91, correlation 0.86–0.87 on the canonical sample"],
      ["measured", "7C ablation (2026-10-21): no single engine fix explains IS R1's move from rank 198/200 to p 0.076. Three fidelity reverts each restore p ≤ 0.05 (macro blackout 0.007, ETF penalty 0.020, overflow 0.027); the stop-slip model lowers expectancy from +6.16 to +5.47 USD a trade without changing a trade. R1's start-0 rank sits on the decision line, which is why 1C reads five starts (vault edge-probe note, ablation section)"],
    ]
  },
  {
    key: "signals", name: "SIGNALS & LOCKS", q: "What does the model see?",
    parts: ["L1–L5 lock chain", "Regime Bayes", "Signal register S1–S13"],
    source: "PRODUCTION_GAP.md (S-rows)",
    register: "backend/backtest/PRODUCTION_GAP.md, S-rows S1–S13: signal hypotheses (on port-regime until the merge)",
    writes: "Every signal gap enters as an S-row (Niclas 2026-10-19)",
    reads: "The weekend bundle's SIGNAL REGISTER section lists every open S-row (apex 2a066af, live after the merge)",
    together: "S-row → battery cell with its rule written before the run → adopted into engine and live together, or discarded, with the verdict written into the row. The layer grows only through that path.",
    status: [
      ["measured", "S11 discarded: the score alone is a volatility selector, not a direction predictor (rank IC −0.003 to −0.035, picks earn what same-ATR peers earn; ladder T1/T2). A go-live case cannot rest on the score alone"],
      ["unanswered", "The live chain after the score (sentiment, leading, L5) is untested until T7 reaches power; leading is the only layer with a consistent sign"],
      ["unanswered", "S13 macro blackout, L0 (2026-10-21): no calendar premium from the entry close at this power. All four IS estimates are negative. The pre-FOMC drift reading (excluded) was withdrawn: half of FOMC_PRE closes end before the pre-announcement window. Sources read 10-21: the pre-FOMC drift disappeared after 2015 (Kurov, Wolfe & Gilbert 2021), before the IS window starts. Next: the R1 card (L2 candidate ledger) for selection; the slot cascade stays with CHECK 71"],
    ]
  },
  {
    key: "data", name: "DATA & INPUTS", q: "Is the input there and true?",
    parts: ["Prices (Yahoo / Alpaca)", "Sector snapshots", "PCR", "Sentiment", "Macro calendar"],
    source: "data/ · input CHECKs",
    register: "The input CHECK rows in audit/CHECKS.md (e.g. 75 prior-close fallback rate, 77–79 EOD regime) and the DB tables the jobs write",
    writes: "Scheduler jobs: sector poll, EOD regime, PCR collection",
    reads: "The nightly CHECKs; a missing input is a WARNING, never a silent skip",
    together: "Everything above stands on this. A missing or wrong input shows here first, as a finding, before it can show up as a strange signal or a bad trade.",
    status: [
      ["measured", "Survivorship: the universe is today's tickers, so absolute-return baselines are inflated (equal-weight pool +502 % IS); a survivorship-free source (delisted prices from 2015) is Niclas's search"],
      ["measured", "gate_decision codes run one behind the lock numbers (FILTERED_L3 = Lock 5); fix Friday 10-23"],
    ]
  },
];

const FUNNEL = ["BUILT", "TESTED", "IN FLIGHT"];

function Detail({ layer }) {
  const row = (label, text) => (
    <div style={{ display: "grid", gridTemplateColumns: "150px 1fr", gap: 12, padding: "6px 0", borderTop: "1px solid var(--t-border)" }}>
      <div className="t-meta" style={{ color: "var(--t-text-3)" }}>{label}</div>
      <div className="t-meta" style={{ color: "var(--t-text-1)", lineHeight: 1.5 }}>{text}</div>
    </div>
  );
  return (
    <div style={{ margin: "0 2% 10px", padding: "10px 14px", border: "1px solid var(--t-accent)", background: "var(--t-bg-2)" }}>
      {row("REGISTER", layer.register)}
      {row("WRITES IT", layer.writes)}
      {row("READS IT", layer.reads)}
      {row("HOW THEY WORK", layer.together)}
      {layer.status && row("LATEST", (
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          {layer.status.map(([level, text]) => (
            <div key={text}><span style={{ fontFamily: "var(--mono)", fontSize: 10, marginRight: 6,
              color: level === "measured" ? "var(--t-accent)" : "var(--t-text-3)" }}>{level.toUpperCase()}</span>{text}</div>
          ))}
        </div>
      ))}
      {row("PARTS", (
        <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
          {layer.parts.map((pt) => (
            <div key={pt} style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
              <span>{pt}</span>
              <span style={{ color: "var(--t-text-3)" }}>built — · tested — · in flight —</span>
            </div>
          ))}
        </div>
      ))}
      <div className="t-meta" style={{ color: "var(--t-text-3)", marginTop: 6 }}>Sketch text as of 2026-10-20 (LATEST added 10-20; ablation, CHECK 93 with forward count, ratchet log and S13 10-21: each entry says measured, hypothesis or unanswered); to be derived from the register once the mapping is decided.</div>
    </div>
  );
}

function Band({ layer, i, n, open, onToggle }) {
  // Width grows toward the base: top 40 %, base 100 %.
  const w = 40 + (60 * i) / (n - 1);
  const inset = (100 - w) / 2;
  return (
    <>
    <div onClick={onToggle} title={open ? "close" : "open the layer"}
      style={{ position: "relative", height: 92, marginBottom: 6, cursor: "pointer" }}>
      <div
        style={{
          position: "absolute", inset: 0,
          clipPath: `polygon(${inset + 3}% 0, ${100 - inset - 3}% 0, ${100 - inset}% 100%, ${inset}% 100%)`,
          background: `color-mix(in oklab, var(--t-accent) ${6 + i * 3}%, var(--t-bg-2))`,
          border: "1px solid var(--t-border)",
        }}
      />
      <div
        style={{
          position: "absolute", top: 0, bottom: 0, left: `${inset + 4}%`, right: `${inset + 4}%`,
          display: "flex", alignItems: "center", gap: 14, padding: "0 8px", minWidth: 0,
        }}
      >
        <div style={{ minWidth: 0, flex: 1 }}>
          <div style={{ fontFamily: "var(--mono)", fontSize: 12, color: "var(--t-text-1)", letterSpacing: "0.08em" }}>
            <span style={{ color: "var(--t-accent)", marginRight: 6 }}>{open ? "▾" : "▸"}</span>{layer.name}
          </div>
          <div className="t-meta" style={{ marginTop: 2 }}>{layer.q}</div>
          <div className="t-meta" style={{ marginTop: 4, color: "var(--t-text-3)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
            {layer.parts.join(" · ")}
          </div>
        </div>
        <div style={{ display: "flex", gap: 6, flexShrink: 0 }}>
          {FUNNEL.map((f) => (
            <div key={f} title={`not counted yet — source: ${layer.source}`}
              style={{ width: 62, textAlign: "center", border: "1px dashed var(--t-border)", padding: "4px 0" }}>
              <div style={{ fontFamily: "var(--mono)", fontSize: 15, color: "var(--t-text-3)" }}>—</div>
              <div className="t-meta" style={{ fontSize: 9 }}>{f}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
    {open && <Detail layer={layer} />}
    </>
  );
}

export default function OverviewPyramid() {
  // ?layer=<key> opens one layer on load (deep link).
  const [open, setOpen] = useState(() => new URLSearchParams(window.location.search).get("layer"));
  return (
    <div className="t-grid" style={{ gridTemplateColumns: "1fr 300px" }}>
      <div className="t-col-main">
        <div className="t-card">
          <div className="t-card-head">
            <div className="t-card-title">APEX PYRAMID</div>
            <div className="t-meta">SKETCH · COUNTS NOT DERIVED YET</div>
          </div>
          <div className="t-card-body">
            {LAYERS.map((l, i) => (
              <Band key={l.key} layer={l} i={i} n={LAYERS.length}
                open={open === l.key} onToggle={() => setOpen(open === l.key ? null : l.key)} />
            ))}
            <div className="t-meta" style={{ textAlign: "center", marginTop: 8 }}>
              each layer stands on the one below · a layer is never more trustworthy than its base
            </div>
          </div>
        </div>
      </div>
      <div className="t-col-side">
        <div className="t-card">
          <div className="t-card-head">
            <div className="t-card-title">EVIDENCE FUNNEL</div>
            <div className="t-meta">PER LAYER</div>
          </div>
          <div className="t-card-body">
            {["Hypothesis (S-row, gap row)", "Rule written before the run", "Battery / test", "Live check", "Adopted or discarded"].map((s, i, a) => (
              <div key={s} style={{ margin: "0 auto 6px", width: `${100 - i * 12}%`, textAlign: "center",
                border: "1px solid var(--t-border)", padding: "6px 4px",
                background: `color-mix(in oklab, var(--t-accent) ${4 + i * 4}%, transparent)` }}>
                <span className="t-meta" style={{ color: "var(--t-text-1)" }}>{s}</span>
                {i < a.length - 1 && <div className="t-meta" style={{ color: "var(--t-text-3)" }}>↓</div>}
              </div>
            ))}
            <div className="t-meta" style={{ marginTop: 12 }}>
              Proposed count sources, one register per layer (hover a box). Open: confirm which register counts which layer, then derive every number from it.
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
