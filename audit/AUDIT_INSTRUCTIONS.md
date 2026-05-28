# APEX Nightly Audit Instructions

You are the APEX Audit + Fixer system. Complete Phase 1 fully before starting Phase 2.

---

## Phase 1 — Audit

Check the codebase for bugs, inconsistencies, and violations of patterns learned from past incidents. Report findings only — do not fix anything in this phase.

### Codebase layout
- Gate pipeline: `backend/gate/gate_runner.py` (demo), `backend/gate/gate_runner_live.py` (live)
- Broker: `backend/brokers/alpaca.py`
- DB layer: `backend/db.py`
- Config: `backend/config.py`, `data/demo_config.json`, `data/live_config.json`
- Tests: `tests/`
- Frontend: `frontend/src/`

### Severity definitions
- **CRITICAL** — can cause incorrect trades, data loss, or silent wrong state right now without any user action required.
- **WARNING** — will cause incorrect behavior under a plausible condition (API down, edge-case input, config drift). Needs fixing soon.
- **INFO** — brittle pattern, missing logging, or style issue. No current risk but makes future bugs harder to catch.

### CHECK 1 — Result-dict sync hazard
Look for any place where a result dict is built early (e.g. `gate_decision` derived from `outcome` in `_gate_result()`) and then `outcome` is mutated later without updating the derived field before a DB write.
- At every DB insert (`insert_live_gate_result`, `update_signal_gate`, etc.), verify the stored `gate_decision` comes from the final `outcome`, not an earlier snapshot.
- Flag any pattern: dict built, then `result["outcome"] = Y` without matching `result["gate_decision"] = Y`, followed by a DB write that reads `result["gate_decision"]`.

### CHECK 2 — Exception-catch coverage in tests
For every test that uses `side_effect=Exception(...)` to mock a broker or external call:
- Does it assert only the return value (e.g. `result["outcome"]`)?
- Does it also assert what was passed to the DB insert mock (e.g. `insert_live_gate_result.call_args`)?
Flag tests that check the return value but NOT the persistence call args — the bug hides in the persistence path.

### CHECK 3 — Fractional qty in broker order placement
- In `backend/brokers/alpaca.py`, verify that `place_bracket_order` uses integer qty (floored), not fractional. Alpaca rejects fractional shares for bracket orders (error 42210000).
- Check any other order placement functions for the same issue.

### CHECK 4 — Config parity
Every config key must exist in all three places: `backend/config.py` (as constants), `data/demo_config.json`, and `data/live_config.json`.
- Extract all keys from each source and report any key present in one but missing from another.
- Only flag keys that are clearly runtime config (not hardcoded constants unrelated to trading config).

### CHECK 5 — Sector name strings
Sector names are load-bearing — they must exactly match the SECTORS list in `backend/config.py`.
- Find all hardcoded sector name strings in `.py`, `.ts`, and `.tsx` files.
- Flag any that don't match the canonical list (case-sensitive).

### CHECK 6 — Test DB isolation
Tests must never touch the production DB (`data/apex.db` or `backend/apex.db`).
- Verify `tests/conftest.py` redirects to a temp DB via `pytest_configure`.
- Search for any test file that directly references or opens either DB path.

### CHECK 7 — Demo/live gate runner parity
Demo and live runners should be structurally identical except for thresholds and Alpaca calls.
- Compare `gate_runner.py` and `gate_runner_live.py` for logic divergence that isn't threshold-related.
- Flag any lock, skip guard, context field, or candidate-filtering logic present in one but missing from the other.

### CHECK 8 — General code health
- Bare `except: pass` or `except Exception: pass` that swallows errors silently without logging.
- TODO / FIXME / HACK comments.
- Functions that return inconsistent types (e.g. sometimes a dict, sometimes None) where callers don't check.

### CHECK 9 — Config value drift against documented constraints
Flag any value outside these bounds:
- `lock1_threshold` demo: >= 0.65. Warn if < 0.65, CRITICAL if < 0.60.
- `lock1_threshold` live: >= 0.65.
- `vix_threshold`: >= 30.
- `take_profit_pct`: <= 0.08.
- `max_positions` demo: <= 15. This is user-controlled; do not revert it.
- SKIP CONDITION (2026-05-27): When compressed signals hold ≥ 50% of total aggregator weight, spread signal ratio sweeps will be flat regardless of individual signal IQR. Mechanism: the compressed block attenuates spread signal score deltas to below the reordering threshold; individual signal IQR measures spread in isolation, not after attenuation through the aggregator weight structure. Do not run a spread signal weight ratio sweep if the compressed block exceeds 50% — it will be invariant by construction. The correct prior sweep is the budget sweep (Q2), which varies the compressed/spread allocation directly.
- PATTERN (2026-05-27, updated with full series close): Parameter review series complete. Full evidence base: RSI discount (inversion, closed 2026-05-24); momentum split (invariant, component co-movement post-L1); volume divisor (invariant, source compression 89% normal-volume); ev_norm (invariant, formula truncation at 0.27, IQR 1.6%); aggregator trend/rs split Q1 (invariant, compressed block at 0.65 weight suppresses spread signal delta); aggregator budget Q2 (regime-dependent — training 2023-2026 peaked at b=0.25-0.30, held-out 2021-2023 peaked at b=0.15-0.25 and degraded at training optimum; peaks move in opposite directions across regimes; no weight change warranted). Architectural finding: the aggregator's optimal weight distribution is regime-dependent. Regime-conditioned weights implemented 2026-05-28: bull (mean top-3 posterior ≥ 0.75) uses higher spread budget (trend=0.35, rs=0.30); bear (< 0.60) uses lower budget (trend=0.25, rs=0.20); neutral = current calibrated weights. Thresholds constructed from single-snapshot analysis — empirical validation requires ≥ 4 weeks of sector_posterior_history data (see CHECK 44). Do not change weight vectors until CHECK 44 validation gate is cleared.
- `backend/signals/momentum.py` RSI discount: the 70-80 linear discount was removed 2026-05-24. The absence of `RSI_DISCOUNT_START` and the discount branch is intentional — 2023-2026 backtest showed 70-80 band PF 1.57 vs 60-70 PF 1.36; the discount was inverting the signal. Do not restore it. The hard cap at RSI >= 80 (`RSI_HARD_CAP`) is intentional and should remain.
- `backend/signals/volume.py` volume normalising divisor (2.0): survivor-set invariant (2026-05-27, N=184, volume_divisor_analysis.py) — volume_ratio IQR [0.74, 1.14] is 9.9% of theoretical range; post-L1 universe is 89% normal-volume days; effective score IQR [0.37, 0.57]; all divisors 1.0–4.0 produce identical trade selection; divisor is structurally inert — not a calibration target. Root cause: gate selects momentum names with steady institutional volume, not volume-spike candidates.
- `backend/signals/momentum.py` momentum vs RSI sub-weight split (0.6/0.4): survivor-set invariant (2026-05-27, N=184, momentum_weight_analysis.py) — momentum_clipped and rsi_score co-move post-L1 (L1 selects momentum names; both components are positive in the survivor set); 0.25 aggregator weight buries any divergence; max signal_score delta ~0.015 across the full 0.3–0.7 split range; parameter has no detectable effect on trade selection. Do not treat the split as a calibration target — it is structurally inert in this configuration. Architectural question (whether the sub-weight split is worth maintaining) deferred to future session.
- COOLDOWN_SWEEP (2026-05-28, N=355, 2021–2026, cooldown_sweep.py, post-AMZN-removal post-profit-lock): SL=5d TP=0d confirmed backtest-optimal (PF baseline 1.889). TP cooldown universally degrades PF across full 5×5 grid (SL: 0/3/5/7/10d × TP: 0/2/3/5/7d); best TP>0 result ~160bp below baseline. SL=3d ties baseline exactly — parameter insensitive in [3,5] range. Current defaults (sl_cooldown_days=5, tp_cooldown_days=0 in engine.run()) are correct. Do not add TP cooldown without re-running sweep against current universe.
- Check git log: `git log --oneline -10 -- data/demo_config.json data/live_config.json`. Flag any config change in the last 7 days without a per-value justification in the commit message.

### CHECK 10 — Ticker signal data coverage (silent degradation canary)
- In `backend/db.py`, verify `get_ticker_daily_scores` uses `days=180` (not 90).
- Run SQLite query if `data/apex.db` is accessible:
  `SELECT ticker, COUNT(DISTINCT day) as day_count FROM (SELECT ticker, day FROM ticker_history WHERE day >= DATE('now', '-180 days') UNION SELECT ticker, DATE(timestamp) AS day FROM signals WHERE timestamp >= DATE('now', '-180 days')) GROUP BY ticker ORDER BY day_count ASC LIMIT 10;`
- WARNING if median ticker < 15 days (CONFIRMED_MIN). WARNING if < 21 days (EXTENDED_MIN).
- Check `backend/sector_regime.py` `compute_ticker_signals()` calls `get_ticker_daily_scores(days=180)`.

### CHECK 11 — NaN/null config pipeline
- Frontend `frontend/src/App.jsx`: find any `parseFloat(val)` without empty-string guard. `parseFloat("")` = NaN; `NaN ?? ""` does not fall back. Correct: `val === "" ? null : parseFloat(val)`.
- Backend `backend/gate/lock1_eligibility.py`: find `cfg.get(key, default)` for numeric fields used in comparisons. Stored None bypasses the default. Check: `macro_event_blackout_days`, `macro_earnings_blackout_days`, `vix_threshold`, `gate_cooloff_hours`.

### Output format

Get today's date with `date +%Y-%m-%d`. Write findings to `audit/nightly-report-YYYY-MM-DD.md` (create `audit/` if needed).

Short summary only — no prose, no code blocks. Every finding is ONE row. Total length under 50 lines.

```
# APEX Nightly Audit — YYYY-MM-DD
N issues: X critical, Y warnings, Z info

| Check | Status | Sev | File:line | Finding |
|-------|--------|-----|-----------|---------|
| 1 Result-dict sync | ✓ or ⚠ | INFO | gate_runner.py:149 | one-line description |

## Retirement Candidates
None
```

Status: `✓` = clean, `⚠` = has findings. Sev: CRITICAL / WARNING / INFO / —. Multiple findings = multiple rows. File:line mandatory for every non-clean row.

### CHECK 19 — Breakout volume floor integrity
Verify that breakout signal classification enforces minimum volume backing.
- Confirm `BREAKOUT_MIN_VOLUME` is defined in `backend/sector_regime.py` and set to ≥ 0.40.
- Confirm `get_latest_ticker_volume_scores()` exists in `backend/db.py` and its query anchors to `DATE(timestamp) < DATE('now')` (EOD, not intraday).
- Confirm `compute_ticker_signals()` calls `get_latest_ticker_volume_scores()` and applies the floor before assigning `signal = "breakout"`.
- Flag any breakout classification path that bypasses the volume check as CRITICAL.

### CHECK 25 — gate_decision string parity
Every string in `_OUTCOMES` in `gate_runner.py` must be recognized by all consumers.
- Extract string values from `_OUTCOMES` dict (`FILTERED_*` strings).
- Verify each string appears in `getLockStates` (or equivalent) in both `GateFeed.jsx` and `LiveGateFeed.jsx`.
- Verify each string appears in the badge/label map in both frontend components.
- Verify each string appears in the funnel SQL queries and dict lookups in `db.py`.
- Flag any mismatch as CRITICAL — an unrecognized decision string falls through to the all-green default, showing passing lock icons for a failed ticker.
- Also extract ticker `signal = "..."` values from `sector_regime.py` (excluding `vel_signal` lines) and verify each appears in all frontend signal maps (`SectorGrid.jsx`, `SectorRegime.jsx`, `Watchlist.jsx`, `RotationForecast.jsx`). Flag any missing entry as WARNING — renders as fallback label/color.

### CHECK 26 — L1/L2 threshold-source parity
Both L1 candidate selection and L2 quant gate must read sector thresholds from `ticker_thresholds` table, not static config.
- Verify `gate_runner.py` and `gate_runner_live.py` both call `get_ticker_thresholds()` and pass the result as `sector_thresholds` to `get_lock1_candidates()`.
- Verify `lock2_quant._sector_threshold()` calls `get_ticker_thresholds()`.
- Flag any file that omits the call as CRITICAL — reversion to static config silently applies a single global threshold (0.70) instead of calibrated per-sector values (0.54–0.62), blocking all valid candidates.

### CHECK 20 — Import path integrity after restructuring
Verify all Python files in `backend/gate/` use fully-qualified `backend.*` import paths, not bare package names.
- Grep `backend/gate/` for `^from gate\.` and `^import gate\.` (bare `gate` without `backend.` prefix).
- Flag any match as CRITICAL — bare imports fail at runtime with ModuleNotFoundError when the app runs from the project root.
- Also check `backend/gate/chain.py` specifically: all lock imports must be `from backend.gate.lockN_* import`.

### CHECK 33 — Bayesian multiplier health
- Read `data/bayesian_multiplier_stats.json`.
- On weekdays: flag WARNING if the file is missing or has a date prior to today — the gate runner did not complete or the stats writer was removed.
- Flag CRITICAL if `suspicious_cycles > 0`: a cycle ran with `regime_present=True`, `queued_count≥3`, and `all_unity=True`. This means `ticker_allocations()` returned zeros or the sector allocation lookup failed silently — Bayesian sizing had no effect despite regime data being available.
- Distinguish from legitimate all-1.0: `multiplier_count=0` (no queued tickers) and `regime_present=False` (no regime data) are not suspicious. Only the combination of regime-present + tickers-queued + all-unity is the failure pattern.

### CHECK 18 — Audit pipeline completeness
Every `.py` script in `audit/` must be explicitly called somewhere in these instructions.
- List all `.py` files in `audit/` (excluding `__pycache__`).
- Verify each one appears in a `python3 audit/...` command in this file.
- Flag any script present in `audit/` but not referenced here as WARNING — it is dead code.

### CHECK 37 — Promote exclusion integrity
Account-size-specific config keys must not appear in the promotable set.
- Run `python3 -c "from backend.maintenance import check_promote_exclusions; r = check_promote_exclusions(); print(r)"`.
- Flag any non-empty result as CRITICAL — a leaked key means the next Promote will overwrite live account-specific constants with demo-scale values, silently corrupting sector exposure calculations or daily loss limits.
- Specifically: `starting_balance` (sector exposure denominator; demo=$2k, live=$100k) and `daily_loss_cap` (absolute dollars; demo=$100 → near-zero daily limit on $100k account) must be in `_PROMOTE_EXCLUDE` and absent from `demo_thresholds()`.

### CHECK 38 — Live entry absence-of-activity

Run as part of `python3 audit/mechanical_checks.py` (no separate invocation needed).

Queries `data/apex.db:live_gate_history`. Trigger condition: ≥ N=5 consecutive active trading days (≥ M=5 gate assessments, excluding SKIPPED_*) with zero TRADE_EXECUTED since the last entry.

**Severity: WARNING** — diagnostic only. Do not treat as an audit failure. Legitimate compression periods will trigger this; the filter breakdown is what distinguishes compression from breakage:
- FILTERED_ELIGIBILITY dominant → eligibility logic suspect (sector exposure, promote corruption, config error)
- FILTERED_L1 dominant → threshold calibration too tight or market genuinely compressed
- FILTERED_LEADING dominant → leading indicators blocking broadly

Output row includes: days since last entry, average daily assessment count, top 2 filter reasons with percentages.

### CHECK 39 — Live peak_price integrity

Run as part of `python3 audit/mechanical_checks.py` (no separate invocation needed).

Queries `data/apex.db:live_trades`. Trigger condition: any open live position where `peak_price IS NULL OR peak_price = entry_price` and the position has been open for more than 2 trading days.

**Severity: WARNING** — the trailing stop is silently disabled for the flagged position. Two failure modes:
- `peak_price IS NULL` → migration didn't run or DB write failed at open time
- `peak_price = entry_price` after >2 days → `update_live_trade_peak_price()` is not being called (scheduler gap, price feed returning None continuously, or `check_live_exits()` not running)

A new position on its first or second trading day will legitimately show `peak = entry` if price hasn't yet exceeded entry — the 2-day grace window prevents false positives on fresh entries.

### CHECK 44 — Regime-conditioned aggregator weight validation

Run as part of `python3 audit/mechanical_checks.py` (no separate invocation needed).

**Sub-check A — Posterior history persistence:** Query `sector_posterior_history` for the count of distinct dates in the last 10 trading days. If < 7, flag CRITICAL — persistence is broken and the validation window cannot accumulate.

**Sub-check B — Bucket switching:** Query `sector_posterior_history` for the last 20 trading days. Compute mean top-3 posterior per date (top-3 by rank within that day's leaderboard). If all 20 days fall in the same bucket (all ≥ 0.75 or all < 0.60 or all in [0.60, 0.75)), flag WARNING — thresholds may be misplaced relative to actual posterior distribution. Monotone bucket state means the regime-conditioned weights never switch, which defeats the architecture.

**Severity:** Sub-check A: CRITICAL (silently accumulating no data). Sub-check B: WARNING once 20+ days of history exist.

**Validation gate:** Once ≥ 4 weeks of data exist, run a retrospective analysis: for each bucket, compute conditional PF against the held-out 2021–23 period by backfilling posteriors from the history table. If bull-bucket PF < neutral-bucket PF, the thresholds are misplaced and need upward adjustment (the "bull" label is applying at too low a posterior).

### Update the registry

After writing the report, run this Python script. Set `triggered` to check numbers that had at least one finding.

```bash
python3 - <<'PYEOF'
from datetime import date, timedelta
import subprocess
today = date.today().isoformat()
CHECKS_PATH = "audit/CHECKS.md"
RETIREMENT_DAYS = 90
triggered = []  # replace with actual triggered check numbers
with open(CHECKS_PATH) as f:
    lines = f.read().splitlines()
new_lines = []
retirement_candidates = []
for line in lines:
    parts = [p.strip() for p in line.split('|')]
    if len(parts) >= 9 and parts[1].isdigit():
        num = int(parts[1])
        name, added, prompted, files = parts[2], parts[3], parts[4], parts[5]
        lt = today if num in triggered else parts[6]
        lc = parts[7] if num in triggered else today
        try:
            days_since = (date.today() - date.fromisoformat(lt)).days
            if days_since >= RETIREMENT_DAYS:
                since = (date.today() - timedelta(days=RETIREMENT_DAYS)).isoformat()
                if any(subprocess.run(['git','log','--oneline',f'--since={since}','--',f],
                       capture_output=True, text=True).stdout.strip() for f in files.split(',')):
                    retirement_candidates.append((num, name, days_since))
        except Exception:
            pass
        new_lines.append(f'| {num} | {name} | {added} | {prompted} | {files} | {lt} | {lc} |')
    else:
        new_lines.append(line)
with open(CHECKS_PATH, 'w') as f:
    f.write('\n'.join(new_lines) + '\n')
for num, name, days in retirement_candidates:
    print(f'RETIRE CHECK {num} ({name}) - {days} days since last triggered')
PYEOF
```

Run the LLM finding verifier — strips template artifacts and tags unverified findings in place:
```
python3 audit/verify_llm_findings.py
```

Commit all changes — report, registry, and any code fixes written during the audit run:
```
git add -A
git commit -m "audit: nightly report YYYY-MM-DD"
git push
```

---

## Phase 2 — Fixer Agent

After committing the audit report, run:
```
python3 audit/fixer.py
```

This reads today's report, applies Pattern A fixes (bare except with no logging) to INFO findings, and opens a PR if anything was fixed. It will not push to master. Let it run to completion without intervention.
