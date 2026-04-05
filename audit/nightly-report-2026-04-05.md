# APEX Nightly Audit — 2026-04-05
24 issues: 1 critical, 21 warnings, 2 info

| Check | Status | Sev | File:line | Finding |
|-------|--------|-----|-----------|---------|
| 4 Config parity | ✓ | — | — | — |
| 5 Sector name strings | ✓ | — | — | — |
| 3 Fractional qty | ⚠ | WARNING | backend/brokers/alpaca.py:124 | qty assigned without int(): raise ValueError(f"Computed qty={qty} for {ticker} @ ${curre |
| 3 Fractional qty | ⚠ | WARNING | backend/brokers/alpaca.py:141 | qty assigned without int(): f"Alpaca bracket order placed [{ticker}]: qty={qty:.4f} @ ~$ |
| 6 Test DB isolation | ⚠ | CRITICAL | tests/conftest.py:7 | hardcoded reference to production DB path |
| 9 Config value drift | ⚠ | WARNING | data/demo_config.json:— | lock1_threshold < 0.65 — below effective floor (current: 0.6) |
| 9 Config value drift | ⚠ | INFO | data/*_config.json:— | config commit without per-value note: 2a7877e feat: Sharpe engine A/B, demo gate history, signal c |
| 9 Config value drift | ⚠ | INFO | data/*_config.json:— | config commit without per-value note: cc2aa23 feat: gate hardening, DB expansion, backtest optimiz |
| 10 Ticker data coverage | ⚠ | WARNING | backend/db.py:— | get_ticker_daily_scores default days=90, should be 180 |
| 11 NaN/null pipeline | ⚠ | WARNING | backend/gate/lock_macro.py:137 | cfg.get('vix_threshold', default) — default won't fire for stored None |

| check_1 result_dict_sync_hazard | ⚠ | WARNING | gate_runner.py:line | `gate_decision` is derived from `outcome` early in `_gate_result`, but `outcome` could be mutated later without updating `gate_decision`. |
| check_2 result_dict_sync_hazard | ✓ | — | — | — |
| check_num check_name | ⚠ | SEV | file:line | one-line description |
| 2 Exception-catch coverage in tests | ⚠ | WARNING | test_gate_runners.py:— | Some tests using side_effect=Exception only check return value, not DB insert mock call args. |
| 2 Exception-catch coverage in tests | ⚠ | WARNING | test_gate_locks.py:— | Some tests using side_effect=Exception only check return value, not DB insert mock call args. |
| check_num check_name | ⚠ | SEV | file:line | one-line description |
| 7 demo/live gate runner parity | ⚠ | WARNING | gate_runner_live.py:unknown | Missing sector_regime context update logic in live runner. |
| 7 demo/live gate runner parity | ⚠ | WARNING | gate_runner_live.py:unknown | Missing rotation_leader and rotation_predecessor context updates in live runner. |
| 7 demo/live gate runner parity | ⚠ | WARNING | gate_runner_live.py:unknown | Missing rotation_confirmed and rotation_transition_prob context updates in live runner. |
| 7 demo/live gate runner parity | ⚠ | WARNING | gate_runner_live.py:unknown | Missing rotation_regime_conditioned and rotation_regime_sample_size context updates in live runner. |
| 7 demo/live gate runner parity | ⚠ | WARNING | gate_runner_live.py:unknown | Missing exception handling for get_rotation_forecast in live runner. |
| 7 demo/live gate runner parity | ⚠ | WARNING | gate_runner_live.py:unknown | Missing exception handling for get_live_ticker_gate_fails in live runner. |
| 7 demo/live gate runner parity | ⚠ | WARNING | gate_runner_live.py:unknown | Missing _gate_result function's lock1_pass, lock2_pass, lock_leading_pass, lock3_pass, and gate_decision logic in live runner. |
| 7 demo/live gate runner parity | ⚠ | WARNING | gate_runner_live.py:unknown | Missing _log_summary function's logging logic in live runner. |
| 7 demo/live gate runner parity | ⚠ | WARNING | gate_runner_live.py:unknown | Missing _record_live_trade function in demo runner. |
| 7 demo/live gate runner parity | ⚠ | WARNING | gate_runner_live.py:unknown | Missing _fire_trade_alert function in demo runner. |
| 7 demo/live gate runner parity | ⚠ | WARNING | gate_runner_live.py:unknown | Missing _daily_loss_exceeded function in demo runner. |
| check_num check_name | ⚠ | SEV | file:line | one-line description |
| --- | --- | --- | --- | --- |
| 8a Bare except block | ⚠ | WARNING | gate_runner.py:12 | Bare except block in risk path without logging in `get_rotation_forecast`. |
| 8a Bare except block | ⚠ | WARNING | gate_runner.py:30 | Bare except block in risk path without logging in `get_ticker_gate_fails`. |
| 8b TODO/FIXME/HACK comments | ✓ | — | — | — |
| 8c Inconsistent return | ✓ | — | — | — |

## Retirement Candidates
None
