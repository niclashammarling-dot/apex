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
  },
  {
    key: "verify", name: "VERIFICATION", q: "Do we know it works?",
    parts: ["Test suite", "Audit CHECKs", "Battery cells (pre-registered)", "Weekend bundle"],
    source: "audit/CHECKS.md · tests/ · battery notes",
  },
  {
    key: "exec", name: "EXECUTION & RISK", q: "Is the order placed and sized right?",
    parts: ["Sizing (F15)", "Brackets: stop / target", "Slots and overflow", "Sector caps", "Broker guard"],
    source: "audit/PARAMETERS.json · live_config.json",
  },
  {
    key: "parity", name: "ENGINE ↔ LIVE", q: "Does the backtest model live?",
    parts: ["engine_fast / engine.py", "Regime port (port-regime)", "Exit parity", "CHECK 76"],
    source: "PRODUCTION_GAP.md (numbered rows)",
  },
  {
    key: "signals", name: "SIGNALS & LOCKS", q: "What does the model see?",
    parts: ["L1–L5 lock chain", "Regime Bayes", "Signal register S1–S10"],
    source: "PRODUCTION_GAP.md (S-rows)",
  },
  {
    key: "data", name: "DATA & INPUTS", q: "Is the input there and true?",
    parts: ["Prices (Yahoo / Alpaca)", "Sector snapshots", "PCR", "Sentiment", "Macro calendar"],
    source: "data/ · input CHECKs",
  },
];

const FUNNEL = ["BUILT", "TESTED", "IN FLIGHT"];

function Band({ layer, i, n }) {
  // Width grows toward the base: top 40 %, base 100 %.
  const w = 40 + (60 * i) / (n - 1);
  const inset = (100 - w) / 2;
  return (
    <div style={{ position: "relative", height: 92, marginBottom: 6 }}>
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
            {layer.name}
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
  );
}

export default function OverviewPyramid() {
  return (
    <div className="t-grid" style={{ gridTemplateColumns: "1fr 300px" }}>
      <div className="t-col-main">
        <div className="t-card">
          <div className="t-card-head">
            <div className="t-card-title">APEX PYRAMID</div>
            <div className="t-meta">SKETCH · COUNTS NOT DERIVED YET</div>
          </div>
          <div className="t-card-body">
            {LAYERS.map((l, i) => <Band key={l.key} layer={l} i={i} n={LAYERS.length} />)}
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
