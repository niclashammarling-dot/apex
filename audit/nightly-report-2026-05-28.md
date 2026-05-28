# APEX Nightly Audit — 2026-05-28
13 issues: 0 critical, 11 warnings, 2 info

| Check | Status | Sev | File:line | Finding |
| 3 Fractional qty | ✓ | — | — | — |
| 4 Config parity | ✓ | — | — | — |
| 5 Sector name strings | ✓ | — | — | — |
| 6 Test DB isolation | ✓ | — | — | — |
| 9 Config value drift | ✓ | — | — | — |
| 10 Ticker data coverage | ✓ | — | — | — |
| 11 NaN/null pipeline | ✓ | — | — | — |
| 12 Lock3 context parity | ✓ | — | — | — |
| 13 Undisclosed config change | ✓ | — | — | — |
| 14 EOD regime freshness | ✓ | — | — | — |
| 15 Calibration freshness | ✓ | — | — | — |
| 16 yfinance scalar extraction | ✓ | — | — | — |
| 17 Sentiment cache freshness | ✓ | — | — | — |
| 21 Overflow increment range | ✓ | — | — | — |
| 22 Yahoo data pipeline health | ✓ | — | — | — |
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
| 38 Live entry absence-of-activity | ✓ | — | — | — |
| 39 Live peak_price integrity | ✓ | — | — | — |
| 41 New-sector integrity | ✓ | — | — | — |
| 42 New-sector integrity | ✓ | — | — | — |
| 37 Promote exclusion integrity | ⚠ | WARNING | backend/live_config.py | Could not import demo_thresholds for runtime check: No module named 'dotenv' |
| 40 Config coverage audit | ⚠ | WARNING | backend/demo_config.py | _defaults() could not be imported for demo config — skipping None-default check |
| 40 Config coverage audit | ⚠ | WARNING | backend/live_config.py | _defaults() could not be imported for live config — skipping None-default check |

| check_2_exception_catch_coverage_in_tests | ⚠ | WARNING | test_wallet.py:TEST 1 | Test only checks return value, not DB insert mock call args. |
| check_2_exception_catch_coverage_in_tests | ✓ | — | — | — |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner.py:50 | Demo runner lacks `_daily_loss_exceeded` equivalent, risking unbounded losses in simulation. [UNVERIFIED: IDENTIFIER NOT FOUND: '_daily_loss_exceeded' not in gate_runner.py] |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner.py:50 | Demo runner lacks `_record_live_trade` equivalent, missing trade record simulation. [UNVERIFIED: IDENTIFIER NOT FOUND: '_record_live_trade' not in gate_runner.py] |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner.py:50 | Demo runner lacks `_fire_trade_alert` equivalent, missing alert simulation. [UNVERIFIED: IDENTIFIER NOT FOUND: '_fire_trade_alert' not in gate_runner.py] |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner.py:50 | Demo runner's `_evaluate` function lacks L5 lock logic present in live runner. |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner.py:50 | Demo runner's `_log_summary` function does not log L5 lock results, unlike live runner. |
| 8 General code health | ⚠ | WARNING | gate_runner.py:47 | Bare except block in risk path without logging in `_compute_bayesian_multipliers`. |
| 8 General code health | ⚠ | WARNING | gate_runner_live.py:63 | Bare except block in risk path without logging in `_record_live_trade`. |
| 8 General code health | ⚠ | INFO | gate_runner.py:47 | Inconsistent return types in `_compute_bayesian_multipliers`, returns `dict` or `{}`. |
| 8 General code health | ⚠ | INFO | gate_runner_live.py:63 | Inconsistent return types in `_record_live_trade`, returns `None` or logs warning. |
| 8 General code health | ✓ | — | — | — |

## Retirement Candidates
None
