# APEX Nightly Audit — 2026-04-02

## Summary
13 issues found: 0 critical, 5 warnings, 8 info

---

## Check 1 — Result-dict sync hazard

### [INFO] Live runner returns stale `gate_decision` in returned result dicts
**File:** `backend/gate/gate_runner_live.py:163–188, 207`
**Detail:** `_gate_result()` sets `gate_decision = "TRADE_EXECUTED"` eagerly for any `TRADE_QUEUED` outcome (line 347). When `run()` subsequently sets `result["outcome"] = "TRADE_REJECTED"` or `"TRADE_FAILED"`, it does **not** update `result["gate_decision"]`. The DB write at line 200 is correctly patched (`"gate_decision": result["outcome"]` with an explanatory comment), so persistence is safe. However, the dicts appended to `results` and returned to callers (scheduler, tests) still carry the stale `gate_decision = "TRADE_EXECUTED"` for any TRADE_REJECTED or TRADE_FAILED path. Any future caller that reads `result["gate_decision"]` instead of `result["outcome"]` will silently receive wrong data.
**Suggested fix:** After each outcome mutation in `run()`, also write `result["gate_decision"] = result["outcome"]` (mirrors the existing pattern in the demo runner at line 151).

Demo runner (`gate_runner.py`) correctly updates `gate_decision` in-place before the DB write and is clean.

---

## Check 2 — Exception-catch coverage in tests

### [WARNING] `test_evaluation_exception_does_not_crash_runner` does not assert persistence behaviour
**File:** `tests/test_gate_runners.py:256–266`
**Detail:** The test mocks `lock1_quant.evaluate` to raise `RuntimeError`, then asserts only `results == []`. It does not assert that `insert_demo_gate_result` (mock index 8 in `_demo_patches`) was **not** called. In the current implementation the exception causes the future to be dropped before it ever enters the serial execution loop, so no DB write occurs. But the test provides no regression guard: a future refactor that accidentally writes a partial record on evaluation failure would not be caught.
**Suggested fix:** Add `mocks[8].assert_not_called()` (insert_demo_gate_result) after `assert results == []`.

### [INFO] `test_alpaca_unreachable_returns_empty` does not assert no DB writes
**File:** `tests/test_gate_runners.py:353–363`
**Detail:** Same pattern — the test only checks `results == []` but does not confirm `insert_live_gate_result` was never called. Pre-flight failure correctly short-circuits before any DB writes, but there is no regression test for that invariant.
**Suggested fix:** Add `mocks[7].assert_not_called()` (insert_live_gate_result) after `assert results == []`.

---

## Check 3 — Fractional qty in broker order placement

✓ Clean — `place_bracket_order` uses `int(notional / current_price)` (line 122), which floors to a whole-share integer as required by Alpaca bracket orders. The recent commit (2a7877e) explicitly fixed a prior `round()` usage.

### [INFO] Log message uses float format spec on an integer qty
**File:** `backend/brokers/alpaca.py:141`
**Detail:** `f"...qty={qty:.4f}..."` prints a float format (e.g. `100.0000`) for `qty`, which is already an `int`. Not a runtime bug but misleading in logs — makes it look like fractional shares may be used.
**Suggested fix:** Change `{qty:.4f}` to `{qty}`.

---

## Check 4 — Config parity

### [WARNING] `live_config.json` has `lock2_sentiment_min=0.1`, less strict than demo's `0.2`
**File:** `data/live_config.json:3`, `data/demo_config.json:3`
**Detail:** Live trading should be at least as strict as demo on every gate threshold. `lock2_sentiment_min` is a floor — higher is stricter. Demo uses 0.2; live uses 0.1. This means live mode passes tickers through L2 that demo would reject. The `config.py` constant `LIVE_LOCK2_SENTIMENT_MIN` defaults to `0.2`, confirming the original intent for live to match or exceed demo strictness.
**Suggested fix:** Set `live_config.json` `lock2_sentiment_min` to at least `0.2`.

### [INFO] `trailing_stop_pct` key in both JSON configs has no corresponding constant in `config.py`
**File:** `backend/config.py` (missing), `data/demo_config.json:7`, `data/live_config.json:7`
**Detail:** Both JSON files include `trailing_stop_pct: 0.1`. `config.py` defines `TAKE_PROFIT_PCT`, `STOP_LOSS_PCT`, and `TIME_STOP_DAYS` but has no `TRAILING_STOP_PCT` constant. Code that reads `cfg["trailing_stop_pct"]` will fail if config is ever loaded from the module rather than the JSON files.
**Suggested fix:** Add `TRAILING_STOP_PCT = 0.10` to `config.py`.

### [INFO] `max_hold_days` in JSON vs `TIME_STOP_DAYS` in `config.py` — naming divergence
**File:** `backend/config.py:41`, `data/demo_config.json:11`, `data/live_config.json:11`
**Detail:** `config.py` uses `TIME_STOP_DAYS = 40`; both JSON files use the key `max_hold_days` (30 in demo, 40 in live). These are the same concept with different names. A code path that looks up `cfg["time_stop_days"]` would fail silently; one that looks up `cfg["max_hold_days"]` has no constant fallback.
**Suggested fix:** Standardise on one name (`max_hold_days`) and add `MAX_HOLD_DAYS` to `config.py`.

---

## Check 5 — Sector name strings

✓ Clean — No frontend TypeScript/TSX files are present in the repo (`frontend/src/` glob returned no `.ts` or `.tsx` files). All backend Python files use sector strings that match the canonical `SECTORS` dict in `config.py`: Technology, Healthcare, Energy, Industrials, Financials, ConsumerDisc, ConsumerStaples, Communication, Utilities, Materials, RealEstate. No mismatches found.

---

## Check 6 — Test DB isolation

✓ Clean — `tests/conftest.py` redirects `backend.db.DB_PATH` to a temp directory via `pytest_configure` (before any test module imports). No test file contains a direct reference to `data/apex.db` or `backend/apex.db` (only a comment in `conftest.py` itself).

---

## Check 7 — Demo/live gate runner parity

### [WARNING] Live runner never computes real `sector_exposure` — Lock 3 always sees `{}`
**File:** `backend/gate/gate_runner_live.py:96–101`
**Detail:** The live `wallet_ctx` hardcodes `"sector_exposure": {}` with the comment "not computed for live — Lock 3 prompt uses open_positions count". Demo uses `get_wallet_context()` which returns actual sector-level exposure percentages. Lock 3's risk-limit check in live mode therefore never sees sector concentration, meaning Claude may approve trades that would breach `max_sector_exposure`. The divergence is present in the context dict (`_build_context` line 267) and the risk_limits block (line 268), but the sector_exposure field is always `{}`.
**Suggested fix:** In `run()`, call `broker.get_positions()` once, compute per-sector exposure from positions, and populate `wallet_ctx["sector_exposure"]`.

### [INFO] `_daily_loss_exceeded` makes a redundant second Alpaca API call
**File:** `backend/gate/gate_runner_live.py:356–370`
**Detail:** `run()` fetches `acct = broker.get_account()` at line 47 and passes `acct["equity"]` to `_daily_loss_exceeded(current_equity, cap)`. Inside `_daily_loss_exceeded`, however, the function ignores its `current_equity` argument and calls `broker.get_account()` again (line 362). This is an unnecessary duplicate network round-trip and the outer `except Exception: pass` means failures in this inner call are silently ignored, potentially returning `False` (loss not exceeded) when Alpaca is flaky.
**Suggested fix:** Remove the inner `broker.get_account()` call and use the passed-in parameters directly.

### [INFO] Skipped-ticker DB insert in live runner omits `lock_leading_pass`, `lock_leading_checks`, `l2_summary`, `macro_reason`
**File:** `backend/gate/gate_runner_live.py:78–83`
**Detail:** The demo skip insert (lines 67–74) explicitly sets `lock_leading_pass=0`, `lock_leading_checks=None`, `l2_summary=None`, and `macro_reason=None`. The live skip insert does not include these fields. If the `live_gate_history` schema has `NOT NULL` on any of these columns or if query code always selects them, the missing fields could cause errors or NULL gaps in funnel reports.
**Suggested fix:** Add the missing fields to the live skip insert (matching the demo pattern).

---

## Check 8 — General code health

### [INFO] Five `except Exception: pass` blocks swallow errors silently without logging
**Files and lines:**
- `backend/gate/gate_runner.py:273` — rotation forecast context build
- `backend/gate/gate_runner.py:287` — gate fail history context build
- `backend/gate/gate_runner_live.py:309` — rotation forecast context build (live)
- `backend/gate/gate_runner_live.py:323` — gate fail history context build (live)
- `backend/gate/gate_runner_live.py:369` — inner `_daily_loss_exceeded` account re-fetch

**Detail:** All five blocks suppress exceptions completely. The gate-context blocks (lines 273, 287, 309, 323) are providing optional enrichment, so some failure tolerance is appropriate — but without even a `logger.debug()` call there is no way to distinguish "feature unavailable" from "silent regression". The `_daily_loss_exceeded` block (line 369) is more concerning: if the inner API call fails, the function returns `False` (loss cap not exceeded), allowing trading to continue even if Alpaca was temporarily unreachable during that sub-call.
**Suggested fix:** Replace bare `pass` with at least `logger.debug(f"…{e}")` in all five blocks.

No TODO/FIXME/HACK comments found in the backend codebase. ✓

No functions with inconsistent return types found. ✓

---

## Check 9 — Config value drift against documented constraints

### [WARNING] `demo_config.json` `lock1_threshold=0.60` is in the documented dead zone (< 0.65)
**File:** `data/demo_config.json:2`
**Detail:** The valid range floor is 0.65. At 0.60, the threshold is in the dead zone where the filter has minimal effect (~21 candidates/day, `max_positions` always fills regardless of threshold). The threshold was lowered from 0.70 → 0.58 in commit `cc2aa23` (2026-04-01, "gate hardening") and then partially raised 0.58 → 0.60 in commit `2a7877e` (2026-04-01, "Sharpe engine A/B"). Neither commit provided per-change rationale for why the dead-zone threshold was acceptable. The value remains 0.05 below the documented minimum.
**Note:** 0.60 is exactly at the CRITICAL boundary (< 0.60 = CRITICAL, < 0.65 = WARNING). Current value is WARNING, not CRITICAL.
**Suggested fix:** Raise `lock1_threshold` to at least `0.65`. If lower candidate volume is desired, reduce `max_positions` instead.

### [INFO] Multiple `demo_config.json` changes in commit `cc2aa23` (2026-04-01) lack per-change justification
**File:** `data/demo_config.json` (commit `cc2aa23`)
**Detail:** Commit `cc2aa23` changed at least 7 demo config values simultaneously (lock1_threshold 0.70→0.58, lock2_sentiment_min 0.1→0.2, take_profit_pct 0.06→0.07, max_positions 4→5, max_position_size 0.15→0.20, daily_loss_cap 500→100, gate_cooloff_hours implicit change). The commit message says only "Config calibration across demo/live/backend" with no per-value rationale. This makes it impossible to audit whether each change was intentional and evidence-backed.
**Suggested fix:** Future config commits should include a per-key justification line (e.g. `lock1_threshold 0.70 → 0.58: testing lower-threshold candidate pool`).

### [INFO] `live_config.json` `lock3_confidence_min=0.6` matches demo — consider tightening for live
**File:** `data/live_config.json:4`
**Detail:** Not a constraint violation, but a risk observation. `config.py` sets `LIVE_LOCK3_CONFIDENCE_MIN = 0.75` as the default, yet `live_config.json` uses 0.6 (identical to demo). This means live trading uses a 25% lower Claude confidence threshold than the code's own documented live default.
**Suggested fix:** Review whether `lock3_confidence_min` in `live_config.json` should be raised to match `LIVE_LOCK3_CONFIDENCE_MIN = 0.75`.

---

## Check 10 — Ticker signal data coverage

### [WARNING] `get_ticker_daily_scores()` default parameter is `days=90` — footgun for future callers
**File:** `backend/db.py:337`
**Detail:** The function signature is `def get_ticker_daily_scores(days: int = 90)`. The commit message for `2a7877e` explicitly notes the fix was to widen the look-back to 180 days to bridge a Jan–Mar 2026 data gap in `ticker_history`. `compute_ticker_signals()` in `sector_regime.py` correctly passes `days=180` (line 127), so the live call site is safe. However, the default of `90` means any future caller that omits `days=` will get 90 days of history — potentially below `CONFIRMED_MIN=15` trading days for some tickers depending on data gaps — causing silent degradation to "weak" signals with no error raised.
**Suggested fix:** Update the default: `def get_ticker_daily_scores(days: int = 180)`.

### [INFO] Production DB `data/apex.db` does not exist in this environment — SQLite data coverage query could not be run
**Detail:** The prescribed audit query (`SELECT ticker, COUNT(DISTINCT day)...`) requires the production database. It was not present at audit time (`data/apex.db` missing). Median ticker day-count and CONFIRMED_MIN coverage cannot be verified from this audit run.
**Suggested fix:** Ensure `data/apex.db` is present on the audit host, or run the coverage query as part of a separate production health check.
