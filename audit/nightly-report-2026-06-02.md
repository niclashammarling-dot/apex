# APEX Nightly Audit — 2026-06-02
12 issues: 0 critical, 12 warnings, 0 info

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
| 35 PCR collection freshness | ✓ | — | — | — |
| 36 L4 sub-check pass rates | ✓ | — | — | — |
| 37 Promote exclusion integrity | ✓ | — | — | — |
| 38 Live entry absence-of-activity | ✓ | — | — | — |
| 39 Live peak_price integrity | ✓ | — | — | — |
| 41 New-sector integrity | ✓ | — | — | — |
| 42 New-sector integrity | ✓ | — | — | — |
| 45 Static code analysis | ✓ | — | — | — |
| 15 Calibration freshness | ⚠ | WARNING | data/calibration_done.txt:— | calibration last ran 2026-W22, current week 2026-W23 — catch-up may have failed |
| 33 Bayesian multiplier health | ⚠ | WARNING | data/bayesian_multiplier_stats.json | Stats file is from 2026-05-28, last trading day was 2026-06-01 — gate runner did not complete a full cycle or _persist_multiplier_stats() was removed |
| 40 Config coverage audit | ⚠ | WARNING | backend/demo_config.py:_defaults | "trailing_stop_pct" defaults to None (feature disabled) — acknowledged in _NONE_DEFAULTS_ALLOWED; set a non-None value to enable |
| 40 Config coverage audit | ⚠ | WARNING | backend/live_config.py:_defaults | "trailing_stop_pct" defaults to None (feature disabled) — acknowledged in _NONE_DEFAULTS_ALLOWED; set a non-None value to enable |

| 2 Exception-catch coverage in tests | ⚠ | WARNING | test_wallet.py:None | Test does not assert DB insert mock call arguments when using side_effect=Exception. |
| 2 Exception-catch coverage in tests | ⚠ | WARNING | test_gate_runners.py:None | Test does not assert DB insert mock call arguments when using side_effect=Exception. |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner.py:50 | Demo runner lacks `_run_l5_for_result` logic present in live runner, potentially missing lock 5 evaluation. [UNVERIFIED: IDENTIFIER NOT FOUND: '_run_l5_for_result' not in gate_runner.py] |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner.py:100 | Demo runner does not stash L5 inputs for serial execution, unlike live runner. |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner.py:150 | Demo runner lacks `_record_live_trade` and `_fire_trade_alert` functions, which are not covered by Principle 1 as they affect trade alerting and recording logic. [UNVERIFIED: IDENTIFIER NOT FOUND: '_record_live_trade' not in gate_runner.py; IDENTIFIER NOT FOUND: '_fire_trade_alert' not in gate_runner.py] |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner.py:200 | Demo runner's `_log_summary` function does not include lock 5 results, unlike live runner. |
| 7 Demo/live gate runner parity | ✓ | — | — | — |
| 8 General code health | ⚠ | WARNING | gate_runner.py:47 | Bare except block missing logging in risk path for multiplier stats persistence. |
| 8 General code health | ⚠ | WARNING | gate_runner_live.py:75 | Bare except block missing logging in risk path for live trade recording. |
| 8 General code health | ✓ | — | — | — |
| 8 General code health | ✓ | — | — | — |

## Retirement Candidates
None
