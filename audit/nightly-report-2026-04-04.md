# APEX Nightly Audit — 2026-04-04
31 issues: 1 critical, 21 warnings, 9 info

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
| 10 Ticker data coverage | ⚠ | WARNING | backend/db.py:— | get_ticker_daily_scores default days=90, should be 180 |
| 11 NaN/null pipeline | ⚠ | WARNING | backend/gate/lock_macro.py:137 | cfg.get('vix_threshold', default) — default won't fire for stored None |

| check_1 result_dict_sync_hazard | ⚠ | WARNING | gate_runner.py:line | `gate_decision` is derived from `outcome` early in `_gate_result`, but `outcome` could be mutated later without updating `gate_decision`. |
| check_2 result_dict_sync_hazard | ✓ | — | — | — |
| check_num check_name | ⚠ | SEV | file:line | one-line description |
| 2 Exception-catch coverage in tests | ⚠ | WARNING | test_gate_runners.py:— | Some tests using side_effect=Exception only check return value, not DB insert mock call args. |
| 2 Exception-catch coverage in tests | ⚠ | WARNING | test_gate_locks.py:— | Some tests using side_effect=Exception only check return value, not DB insert mock call args. |
| check_num check_name | ⚠ | SEV | file:line | one-line description |
| 7 demo/live gate runner parity | ⚠ | WARNING | gate_runner_live.py:missing | Live runner missing sector_regime context update logic present in demo. |
| 7 demo/live gate runner parity | ⚠ | WARNING | gate_runner_live.py:missing | Live runner missing rotation_leader context update logic present in demo. |
| 7 demo/live gate runner parity | ⚠ | WARNING | gate_runner_live.py:missing | Live runner missing rotation_predecessor context update logic present in demo. |
| 7 demo/live gate runner parity | ⚠ | WARNING | gate_runner_live.py:missing | Live runner missing sector_next_probability context update logic present in demo. |
| 7 demo/live gate runner parity | ⚠ | WARNING | gate_runner_live.py:missing | Live runner missing rotation_confirmed context update logic present in demo. |
| 7 demo/live gate runner parity | ⚠ | WARNING | gate_runner_live.py:missing | Live runner missing rotation_transition_prob context update logic present in demo. |
| 7 demo/live gate runner parity | ⚠ | WARNING | gate_runner_live.py:missing | Live runner missing rotation_regime_conditioned context update logic present in demo. |
| 7 demo/live gate runner parity | ⚠ | WARNING | gate_runner_live.py:missing | Live runner missing rotation_regime_sample_size context update logic present in demo. |
| 7 demo/live gate runner parity | ⚠ | WARNING | gate_runner_live.py:missing | Live runner missing exception handling for rotation forecast logic present in demo. |
| 7 demo/live gate runner parity | ⚠ | WARNING | gate_runner_live.py:missing | Live runner missing exception handling for ticker gate fails logic present in demo. |
| 7 demo/live gate runner parity | ⚠ | WARNING | gate_runner_live.py:present | Live runner has _daily_loss_exceeded function not present in demo. |
| 7 demo/live gate runner parity | ⚠ | WARNING | gate_runner_live.py:present | Live runner has _record_live_trade function not present in demo. |
| 7 demo/live gate runner parity | ⚠ | WARNING | gate_runner_live.py:present | Live runner has _fire_trade_alert function not present in demo. |
| check_num check_name | ⚠ | SEV | file:line | one-line description |
| --- | --- | --- | --- | --- |
| CHECK 8 General code health | ⚠ | INFO | gate_runner.py:12 | Bare except block in non-risk path without logging. |
| CHECK 8 General code health | ⚠ | INFO | gate_runner.py:28 | Bare except block in non-risk path without logging. |
| CHECK 8 General code health | ⚠ | INFO | gate_runner.py:3 | Function sometimes returns a dict and sometimes None without caller checks. |
| CHECK 8 General code health | ⚠ | INFO | gate_runner_live.py:36 | Bare except block in non-risk path without logging. |
| CHECK 8 General code health | ⚠ | INFO | gate_runner_live.py:54 | Bare except block in non-risk path without logging. |
| CHECK 8 General code health | ⚠ | INFO | gate_runner_live.py:71 | Bare except block in non-risk path without logging. |
| CHECK 8 General code health | ✓ | — | — | — |

## Retirement Candidates
None
