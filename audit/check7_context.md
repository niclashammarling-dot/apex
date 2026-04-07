# CHECK 7 — Demo/Live Runner Design Intent

Use this document to reason about whether a divergence between `gate_runner.py` (demo)
and `gate_runner_live.py` (live) is a bug or intentional design. Do not treat this as
an exhaustive allowlist — use the examples to understand the *principle* behind each
category, then apply that reasoning to any divergence you find.

---

## Principle 1 — Live-only enforcement functions

Live trades have consequences that demo does not. Functions that enforce real-money
safety or broker-side record-keeping exist only in the live runner by design.

**Examples:**
- `_daily_loss_exceeded(equity, cap)` — checks real account equity against a daily loss
  cap before the candidate loop runs. Demo uses wallet simulation with no real capital at
  risk, so this guard is meaningless there.
- `_record_live_trade(signal, notional, order_id, cfg)` — writes an executed trade to the
  DB after a real Alpaca order is placed. Demo never places real orders; wallet simulation
  handles position tracking internally.
- `_fire_trade_alert(ticker, signal, notional, order_id, cfg)` — sends a real-time alert
  after a confirmed live execution. Alerting on simulated demo trades would be noise.

**How to reason:** If a function in the live runner deals with real-money enforcement,
broker confirmation, or post-execution record-keeping that has no equivalent in simulation,
its absence from the demo runner is expected. Flag only if the *logic it enforces* (e.g.
a loss cap or a block guard) has no equivalent mechanism in demo at all.

---

## Principle 2 — Threshold and configuration differences

Demo and live configs intentionally differ on thresholds (lock1_threshold, notional floors,
etc.). Do not flag threshold value differences — they are documented in config files and
tested separately.

---

## Principle 3 — Context builder naming

Demo uses `_build_claude_context`, live uses `_build_context`. Same function, different
name — a cosmetic inconsistency, not a logic divergence. Do NOT flag name differences
unless the *fields passed to Lock 3* differ between the two functions.

---

## What you SHOULD flag

- Lock logic (L1/L2/L3/leading lock) present in one `_evaluate` but absent or different in the other
- A context field passed to Lock 3 in demo that is missing from the live context builder
- A filtering step (skip guard, sector block, cooldown check) applied in one runner but not the other
- `_gate_result` or `_log_summary` logic that diverges in a way that affects stored outcomes
- Any new function in demo that encodes trading logic and has no live counterpart (the reverse
  of Principle 1 — if demo gains a logic function, live should too unless it's simulation-specific)
