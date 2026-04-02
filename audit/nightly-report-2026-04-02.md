# APEX Nightly Audit — 2026-04-02

## Summary
8 issues found: 0 critical, 3 warnings, 5 info

---

## Check 1 — Result-dict sync hazard

Partially clean — one structural fragility in the demo runner (no current bug, but brittle pattern).

### [INFO] Demo gate: gate_decision not explicitly updated for TRADE_EXECUTED path
**File:** `backend/gate/gate_runner.py:149–151, 165`
**Detail:** `_gate_result()` (line 312) initialises `gate_decision = "TRADE_EXECUTED"` when `outcome == "TRADE_QUEUED"`. After execution (line 149), if the trade succeeds `result["outcome"]` becomes `"TRADE_EXECUTED"` but `result["gate_decision"]` is never explicitly re-set — it merely happens to already hold the correct value from the early initialisation. Only the `TRADE_REJECTED` path is explicitly corrected (line 151). Line 165 then persists `result["gate_decision"]`.

This is not a current bug, but it is fragile: any new outcome type (e.g. `"TRADE_TIMEOUT"`, `"TRADE_THROTTLED"`) added to the demo execution block without a matching `result["gate_decision"] = ...` update would silently persist a stale `gate_decision`. The live runner avoids this entirely — its DB-insert dict (line 200) writes `"gate_decision": result["outcome"]` with an explicit comment explaining the pattern.
**Suggested fix:** Mirror the live runner pattern — at the demo DB-insert dict (line 165), use `result["outcome"]` instead of `result["gate_decision"]`.

---

## Check 2 — Exception-catch coverage in tests

✓ Clean

All tests using `side_effect=Exception(...)` were verified:
- `test_gate_runners.py` — `test_alpaca_unreachable_returns_empty`: asserts early-return empty list; no DB-call path reached.
- `test_gate_runners.py` — `test_broker_exception_sets_trade_failed`: asserts both `result["outcome"] == "TRADE_FAILED"` and the saved `gate_decision` via `insert_live_gate_result.call_args`.
- `test_gate_locks.py` — `test_insufficient_data_fails`: checks lock failure outcome.

No test found that checks the return value only while skipping persistence-call args.

---

## Check 3 — Fractional qty in broker order placement

✓ Clean

`backend/brokers/alpaca.py:122` explicitly casts to `int()`:
```python
qty = int(notional / current_price)  # floor to whole shares
```
A guard `if qty <= 0: raise ValueError(...)` follows immediately. The Alpaca `MarketOrderRequest` receives the integer value. A separate fractional qty (6 decimal places) is stored in the DB trade record only — it is never sent to the Alpaca API.

---

## Check 4 — Config parity

✓ Clean

All 18 runtime config keys are present in both `data/demo_config.json` and `data/live_config.json`:
```
lock1_threshold, lock2_sentiment_min, lock3_confidence_min,
take_profit_pct, stop_loss_pct, trailing_stop_pct, max_positions,
max_position_size, daily_loss_cap, max_hold_days, vix_threshold,
macro_event_blackout_days, macro_earnings_blackout_days,
gate_cooloff_hours, max_sector_exposure, lock_leading_min_pass,
starting_balance, max_drawdown_pct
```
No keys missing from either file. `backend/config.py` provides compile-time defaults; runtime configs override them.

---

## Check 5 — Sector name strings

✓ Clean

Canonical list from `backend/config.py`:
```
Technology, Healthcare, Energy, Industrials, Financials, ConsumerDisc,
ConsumerStaples, Communication, Utilities, Materials, RealEstate
```
All hardcoded sector strings found in `.py`, `.ts`, and `.tsx` files match this list case-sensitively. Test fixtures and the sector regime CYCLICAL/DEFENSIVE classification arrays all use canonical names.

---

## Check 6 — Test DB isolation

✓ Clean

`tests/conftest.py:13–17` uses the `pytest_configure` hook (runs before any module is imported during collection):
```python
def pytest_configure(config):
    import backend.db as db_module
    tmp = tempfile.mkdtemp(prefix="apex_test_")
    db_module.DB_PATH = Path(tmp) / "apex_test.db"
```
No test file contains a hardcoded reference to `data/apex.db` or `backend/apex.db`. All `init_db()` calls in tests resolve through the redirected `DB_PATH`.

---

## Check 7 — Demo/live gate runner parity

✓ Clean — divergences confirmed intentional

Both runners share identical logic for:
- Lock evaluation order: L1 → Macro → L2 → Leading → L3
- Candidate filtering and pre-rotation floor
- Skip-guard recording for failed/already-open tickers
- Context field names (sector regime, rotation forecast, ticker gate history)

Intentional live-only additions (all expected):
- Pre-flight Alpaca account validation and block check (lines 44–53)
- Daily loss cap guard (lines 56–61)
- Notional floor guard (line 170, $10 minimum)
- Real broker call (`place_bracket_order`) vs wallet simulation
- `mode: "LIVE — real money"` in context dict (line 252)

No logic present in one runner but absent from the other without clear justification.

---

## Check 8 — General code health

### [WARNING] Silent fail-open in daily loss cap check
**File:** `backend/gate/gate_runner_live.py:362–370`
**Detail:** `_daily_loss_exceeded()` wraps `broker.get_account()` in a bare `except Exception: pass`, then unconditionally returns `False`. If the Alpaca API is unreachable or returns a malformed response, the exception is swallowed with no log entry and the function signals "cap not exceeded" — allowing trading to continue regardless of actual P&L. This is a fail-open in the primary live risk guardrail.
```python
except Exception:
    pass
return False   # ← trading continues even if cap check errors
```
**Suggested fix:** Add `logger.warning("daily loss cap check failed — skipping enforcement: %s", e)` at minimum. Evaluate whether fail-closed (`return True`) is the safer default when the check cannot be completed.

### [INFO] Bare `except Exception: pass` in context enrichment — demo runner
**File:** `backend/gate/gate_runner.py:273–274, 286–287`
**Detail:** Both blocks enrich the Claude context with rotation forecast data and ticker gate history. Exceptions are silenced with no log entry, so the gate proceeds without those context fields. Fail-open is appropriate (enrichment is optional), but debugging is harder when context is unexpectedly absent.
**Suggested fix:** Replace `pass` with `logger.debug("Context enrichment failed: %s", e)`.

### [INFO] Bare `except Exception: pass` in context enrichment — live runner
**File:** `backend/gate/gate_runner_live.py:309–310, 322–323`
**Detail:** Same pattern as demo runner on optional context enrichment. Same recommendation applies.

### [INFO] Bare `except Exception: return []` in sentiment headline fetch
**File:** `backend/gate/lock2_sentiment.py:77–78`
**Detail:** `_fetch_headlines()` returns an empty list on any exception without logging the failure. An empty headline list causes L2 to fail closed (no bullish signal), which is the safe direction for trading decisions, but makes it impossible to distinguish "no headlines found" from "fetch errored". No current bug.
**Suggested fix:** Add `logger.debug` before the return.

### [INFO] No TODO/FIXME/HACK comments found
Codebase is clean of deferred-work markers.

---

## Check 9 — Config value drift against documented constraints

### [WARNING] demo `lock1_threshold` = 0.60 — below documented effective floor
**File:** `data/demo_config.json`
**Detail:** `lock1_threshold` is `0.60`. The documented constraint is ≥ 0.65; the 0.40–0.60 range is the "dead zone" where the filter has no practical effect (~21 candidates/day, `max_positions` fills regardless of threshold). At exactly 0.60 the threshold sits at the dead-zone boundary. The live config correctly uses `0.70`. The demo value appears intentionally lenient (more signal volume for backtesting), but there is no comment in the config file documenting this intent.
**Suggested fix:** Add an inline comment or companion note explaining `0.60` is deliberate for demo-mode signal volume.

### [WARNING] Five config-file commits in the last 7 days without per-value justification
**File:** `data/demo_config.json`, `data/live_config.json`
**Detail:** `git log --oneline -- data/demo_config.json data/live_config.json` shows five commits within the last 7 days:
```
2a7877e 2026-04-01  feat: Sharpe engine A/B, demo gate history, signal coverage, tooltips
cc2aa23 2026-04-01  feat: gate hardening, DB expansion, backtest optimizer, and dashboard UX
df2d706 2026-03-28  feat: leading lock, backtest lock integration, trailing stop, and config calibration
818e0b4 2026-03-28  feat: sector rotation intelligence, per-sector thresholds, and system hardening
089b267 2026-03-27  feat: TRADE_REJECTED visibility, sector exposure in settings
```
None of the commit messages name which specific config values changed or why. It is impossible to tell from history alone whether `lock1_threshold`, `vix_threshold`, or `take_profit_pct` were quietly adjusted as part of a larger feature commit.
**Suggested fix:** When modifying config values, include a note like `config: lock1_threshold 0.65→0.60 (demo volume)` in the commit message, or use a separate config-only commit.

All other config values are within documented safe ranges:

| Parameter | Demo | Live | Constraint | Status |
|---|---|---|---|---|
| `lock1_threshold` | 0.60 | 0.70 | ≥ 0.65 | ⚠️ demo below floor |
| `vix_threshold` | 30 | 30 | ≥ 30 | ✓ |
| `take_profit_pct` | 0.07 | 0.06 | ≤ 0.08 | ✓ |
| `max_positions` (demo) | 5 | 4 | ≤ 6 | ✓ |

---

## Check 10 — Ticker signal data coverage

✓ Clean (code path correct; live DB not available in this environment for row-count verification)

### Lookback configuration
`backend/db.py`: `get_ticker_daily_scores(days: int = 90)` — default is 90 days.
`backend/sector_regime.py:127` (inside `compute_ticker_signals()`): calls `get_ticker_daily_scores(days=180)` — explicit override with comment:
> "Uses 180 days of daily averages … so the streak algo has enough history even during the Jan–Mar 2026 ticker_history gap"

The explicit `days=180` override is correct and well-documented. No signal-production code path calls `get_ticker_daily_scores()` without specifying `days=180`.

### Live DB row-count query
`data/apex.db` was not accessible in the audit environment — the query could not be run. The code path is sound; a manual operator check is recommended after the DB is confirmed present:

```sql
SELECT ticker, COUNT(DISTINCT day) AS day_count
FROM (
  SELECT ticker, day FROM ticker_history WHERE day >= DATE('now', '-180 days')
  UNION
  SELECT ticker, DATE(timestamp) AS day FROM signals WHERE timestamp >= DATE('now', '-180 days')
)
GROUP BY ticker ORDER BY day_count ASC LIMIT 10;
```

Flag WARNING if median ticker returns fewer than 15 distinct days (CONFIRMED_MIN — signals collapse to "weak").
Flag WARNING if fewer than 21 days (EXTENDED_MIN — "extended" classifications impossible).
