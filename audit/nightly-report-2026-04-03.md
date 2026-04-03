# APEX Nightly Audit — 2026-04-03
27 issues: 1 critical, 16 warnings, 10 info

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
| 9 Config value drift | ⚠ | INFO | data/*_config.json:— | config commit without per-value note: df2d706 feat: leading lock, backtest lock integration, trail |
| 9 Config value drift | ⚠ | INFO | data/*_config.json:— | config commit without per-value note: 089b267 feat: TRADE_REJECTED visibility, sector exposure in  |
| 10 Ticker data coverage | ⚠ | WARNING | backend/db.py:— | get_ticker_daily_scores default days=90, should be 180 |
| 11 NaN/null pipeline | ⚠ | WARNING | backend/gate/lock_macro.py:137 | cfg.get('vix_threshold', default) — default won't fire for stored None |

| check_1 result_dict_sync_hazard | ⚠ | WARNING | gate_runner.py: line where _gate_result is defined | `gate_decision` is derived from `outcome` early, but `outcome` could be mutated later without updating `gate_decision`. |
| check_2 result_dict_sync_hazard | ✓ | — | — | — |
| check_num check_name | ⚠ | SEV | file:line | one-line description |
| 2 Exception-catch coverage in tests | ⚠ | WARNING | test_gate_runners.py:NN | Test uses side_effect=Exception but only checks return value, not DB insert mock call args. |
| 2 Exception-catch coverage in tests | ⚠ | WARNING | test_gate_locks.py:NN | Test uses side_effect=Exception but only checks return value, not DB insert mock call args. |
| check_num check_name | ⚠ | SEV | file:line | one-line description |
| 7 demo/live gate runner parity | ⚠ | WARNING | gate_runner_live.py:unknown | Missing sector_regime context update logic in live runner. |
| 7 demo/live gate runner parity | ⚠ | WARNING | gate_runner_live.py:unknown | Missing rotation_leader and rotation_predecessor context update in live runner. |
| 7 demo/live gate runner parity | ⚠ | WARNING | gate_runner_live.py:unknown | Missing rotation_confirmed and rotation_transition_prob context update in live runner. |
| 7 demo/live gate runner parity | ⚠ | WARNING | gate_runner_live.py:unknown | Missing rotation_regime_conditioned and rotation_regime_sample_size context update in live runner. |
| 7 demo/live gate runner parity | ⚠ | WARNING | gate_runner_live.py:unknown | Different function used for fetching gate fail history in live runner. |
| 7 demo/live gate runner parity | ⚠ | WARNING | gate_runner_live.py:unknown | Live runner includes _daily_loss_exceeded function not present in demo runner. |
| 7 demo/live gate runner parity | ⚠ | WARNING | gate_runner_live.py:unknown | Live runner includes _record_live_trade function not present in demo runner. |
| 7 demo/live gate runner parity | ⚠ | WARNING | gate_runner_live.py:unknown | Live runner includes _fire_trade_alert function not present in demo runner. |
| check_num check_name | ⚠ | SEV | file:line | one-line description |
| --- | --- | --- | --- | --- |
| CHECK 8 General code health | ⚠ | INFO | gate_runner.py:12 | Bare except block in non-risk path without logging. |
| CHECK 8 General code health | ⚠ | INFO | gate_runner.py:28 | Bare except block in non-risk path without logging. |
| CHECK 8 General code health | ⚠ | INFO | gate_runner.py:1 | Missing return type check for function returning dict or None. |
| CHECK 8 General code health | ⚠ | INFO | gate_runner_live.py:26 | Bare except block in non-risk path without logging. |
| CHECK 8 General code health | ⚠ | INFO | gate_runner_live.py:47 | Bare except block in non-risk path without logging. |
| CHECK 8 General code health | ⚠ | INFO | gate_runner_live.py:63 | Bare except block in non-risk path without logging. |
| CHECK 8 General code health | ✓ | — | — | — |

## Retirement Candidates
None
