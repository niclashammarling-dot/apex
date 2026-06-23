# APEX Nightly Audit — 2026-06-18
13 issues: 0 critical, 13 warnings, 0 info

| Check | Status | Sev | File:line | Finding |
| 3 Fractional qty | ✓ | — | — | — |
| 5 Sector name strings | ✓ | — | — | — |
| 6 Test DB isolation | ✓ | — | — | — |
| 9 Config value drift | ✓ | — | — | — |
| 10 Ticker data coverage | ✓ | — | — | — |
| 11 NaN/null pipeline | ✓ | — | — | — |
| 12 Lock3 context parity | ✓ | — | — | — |
| 13 Undisclosed config change | ✓ | — | — | — |
| 14 EOD regime freshness | ✓ | — | — | — |
| 16 yfinance scalar extraction | ✓ | — | — | — |
| 17 Sentiment cache freshness | ✓ | — | — | — |
| 21 Overflow increment range | ✓ | — | — | — |
| 24 Chain-runner wiring | ✓ | — | — | — |
| 25 gate_decision string parity | ✓ | — | — | — |
| 26 L1/L2 threshold-source parity | ✓ | — | — | — |
| 27 GICS classification parity | ✓ | — | — | — |
| 28 EXCLUDED_SECTORS gate wiring | ✓ | — | — | — |
| 29 Live sector exposure cap wiring | ✓ | — | — | — |
| 30 Startup live regime exit reconciliation | ✓ | — | — | — |
| 31 Live bracket TIF and exit reconciliation | ✓ | — | — | — |
| 32 Git sync divergence | ✓ | — | — | — |
| 35 PCR collection freshness | ✓ | — | — | — |
| 36 L4 sub-check pass rates | ✓ | — | — | — |
| 37 Promote exclusion integrity | ✓ | — | — | — |
| 38 Live entry absence-of-activity | ✓ | — | — | — |
| 39 Live peak_price integrity | ✓ | — | — | — |
| 40 Config coverage audit | ✓ | — | — | — |
| 41 New-sector integrity | ✓ | — | — | — |
| 42 New-sector integrity | ✓ | — | — | — |
| 45 Static code analysis | ✓ | — | — | — |
| 4 Config parity | ⚠ | WARNING | data/live_config.json:— | key 'tp_cooloff_hours' in demo but missing from live |
| 15 Calibration freshness | ⚠ | WARNING | data/calibration_done.txt:— | calibration last ran 2026-W24, current week 2026-W25 — catch-up may have failed |
| 22 Yahoo data pipeline health | ⚠ | WARNING | data/regime_result_cache.json | regime_result_cache.json is 10 days old (last: 2026-06-08) — EOD regime may not have run successfully this week |
| 33 Bayesian multiplier health | ⚠ | WARNING | data/bayesian_multiplier_stats.json | Stats file is from 2026-06-08, last trading day was 2026-06-17 — gate runner did not complete a full cycle or _persist_multiplier_stats() was removed |
| 50 L4 group constraint (live data) | ⚠ | WARNING | /home/runner/work/apex/apex/data/apex.db | apex.db not found — cannot verify L4 group constraint on executed trades |

| 2 Exception-catch coverage in tests | ⚠ | WARNING | test_gate_runners.py:— | Tests using side_effect=Exception do not assert DB insert mock call arguments. |
| 2 Exception-catch coverage in tests | ⚠ | WARNING | test_wallet.py:— | Tests using side_effect=Exception do not assert DB insert mock call arguments. |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner.py:1 | Demo runner lacks `_run_l5_for_result` logic present in live runner, potentially affecting lock evaluation. [UNVERIFIED: IDENTIFIER NOT FOUND: '_run_l5_for_result' not in gate_runner.py] |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner.py:1 | Demo runner does not stash L5 inputs for serial execution, unlike live runner. |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner.py:1 | Demo runner lacks `_record_live_trade` and `_fire_trade_alert` functions, which are expected in live runner for real trade execution. [UNVERIFIED: IDENTIFIER NOT FOUND: '_record_live_trade' not in gate_runner.py; IDENTIFIER NOT FOUND: '_fire_trade_alert' not in gate_runner.py] |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner.py:1 | Demo runner does not handle `lock3_pass` and `claude_reasoning` fields, which are present in live runner. |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner.py:1 | Demo runner lacks `_evaluate` function's detailed context building and evaluation chain logic present in live runner. |
| 8 Bare except block | ⚠ | WARNING | gate_runner.py:47 | Bare except block in risk path without logging. |
| 8 TODO/FIXME/HACK comments | ✓ | — | — | — |
| 8 Inconsistent return types | ✓ | — | — | — |

## Retirement Candidates
None
