# APEX Nightly Audit — 2026-06-21
14 issues: 0 critical, 12 warnings, 2 info

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
| 33 Bayesian multiplier health | ✓ | — | — | — |
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
| 22 Yahoo data pipeline health | ⚠ | WARNING | data/regime_result_cache.json | regime_result_cache.json is 13 days old (last: 2026-06-08) — EOD regime may not have run successfully this week |
| 50 L4 group constraint (live data) | ⚠ | WARNING | /home/runner/work/apex/apex/data/apex.db | apex.db not found — cannot verify L4 group constraint on executed trades |

| 2 Exception-catch coverage in tests | ⚠ | WARNING | test_gate_runners.py:NN | Test using side_effect=Exception only checks return value, not DB insert mock call args. |
| 2 Exception-catch coverage in tests | ⚠ | WARNING | test_wallet.py:NN | Test using side_effect=Exception only checks return value, not DB insert mock call args. |
| 7 demo_live_gate_runner_parity | ⚠ | WARNING | gate_runner.py:50 | Demo runner lacks `_run_l5_for_result` logic present in live runner, potentially affecting lock evaluation consistency. [UNVERIFIED: IDENTIFIER NOT FOUND: '_run_l5_for_result' not in gate_runner.py] |
| 7 demo_live_gate_runner_parity | ⚠ | WARNING | gate_runner.py:100 | Demo runner does not stash L5 inputs for serial execution, unlike live runner, which may lead to inconsistent lock processing. |
| 7 demo_live_gate_runner_parity | ⚠ | WARNING | gate_runner.py:150 | Demo runner lacks `_record_live_trade` and `_fire_trade_alert` functions, which are expected in live runner for real trade execution and alerting. [UNVERIFIED: IDENTIFIER NOT FOUND: '_record_live_trade' not in gate_runner.py; IDENTIFIER NOT FOUND: '_fire_trade_alert' not in gate_runner.py] |
| 7 demo_live_gate_runner_parity | ⚠ | WARNING | gate_runner.py:200 | Demo runner's `_log_summary` function does not include L5 lock results, unlike live runner, potentially leading to incomplete logging. |
| 8 General code health | ⚠ | WARNING | gate_runner.py:47 | Bare except block missing logging in risk-enforcement path. |
| 8 General code health | ⚠ | WARNING | gate_runner_live.py:97 | Bare except block missing logging in risk-enforcement path. |
| 8 General code health | ⚠ | INFO | gate_runner.py:75 | TODO/FIXME/HACK comment found. |
| 8 General code health | ⚠ | INFO | gate_runner_live.py:150 | Function returns inconsistent types without caller guard. |

## Retirement Candidates
None
