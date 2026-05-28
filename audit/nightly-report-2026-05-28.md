# APEX Nightly Audit — 2026-05-28
14 issues: 0 critical, 12 warnings, 2 info

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
| 37 Promote exclusion integrity | ⚠ | WARNING | backend/live_config.py | Could not import demo_thresholds for runtime check: No module named 'loguru' |
| 40 Config coverage audit | ⚠ | WARNING | backend/demo_config.py | _defaults() could not be imported for demo config — skipping None-default check |
| 40 Config coverage audit | ⚠ | WARNING | backend/live_config.py | _defaults() could not be imported for live config — skipping None-default check |

| check_2 exception-catch coverage in tests | ⚠ | WARNING | test_wallet.py:100 | Test does not assert DB insert mock call arguments when using side_effect=Exception. |
| check_2 exception-catch coverage in tests | ⚠ | WARNING | test_gate_runners.py:100 | Test does not assert DB insert mock call arguments when using side_effect=Exception. |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner.py:50 | Demo runner lacks `_daily_loss_exceeded` equivalent, missing loss cap logic. [UNVERIFIED: IDENTIFIER NOT FOUND: '_daily_loss_exceeded' not in gate_runner.py] |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner.py:50 | Demo runner lacks `_record_live_trade` equivalent, missing trade record logic. [UNVERIFIED: IDENTIFIER NOT FOUND: '_record_live_trade' not in gate_runner.py] |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner.py:50 | Demo runner lacks `_fire_trade_alert` equivalent, missing trade alert logic. [UNVERIFIED: IDENTIFIER NOT FOUND: '_fire_trade_alert' not in gate_runner.py] |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner.py:50 | Demo runner lacks `_evaluate` function's L5 logic, missing lock logic. |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner.py:50 | Demo runner lacks `_log_summary` equivalent for L5, missing logging logic. |
| 8 General code health | ⚠ | WARNING | gate_runner.py:43 | Bare except block in risk path without logging in `_compute_bayesian_multipliers`. |
| 8 General code health | ⚠ | WARNING | gate_runner_live.py:63 | Bare except block in risk path without logging in `_record_live_trade`. |
| 8 General code health | ⚠ | INFO | gate_runner.py:72 | TODO/FIXME/HACK comment found. |
| 8 General code health | ⚠ | INFO | gate_runner_live.py:102 | Function `_daily_loss_exceeded` returns inconsistent types without caller guard. |
| 8 General code health | ✓ | — | — | — |

## Retirement Candidates
None
