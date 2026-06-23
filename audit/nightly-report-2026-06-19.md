# APEX Nightly Audit — 2026-06-19
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
| 22 Yahoo data pipeline health | ⚠ | WARNING | data/regime_result_cache.json | regime_result_cache.json is 11 days old (last: 2026-06-08) — EOD regime may not have run successfully this week |
| 33 Bayesian multiplier health | ⚠ | WARNING | data/bayesian_multiplier_stats.json | Stats file is from 2026-06-08, last trading day was 2026-06-18 — gate runner did not complete a full cycle or _persist_multiplier_stats() was removed |
| 50 L4 group constraint (live data) | ⚠ | WARNING | /home/runner/work/apex/apex/data/apex.db | apex.db not found — cannot verify L4 group constraint on executed trades |

| 2 Exception-catch coverage in tests | ⚠ | WARNING | test_gate_runners.py:— | Tests using side_effect=Exception do not assert DB insert mock call arguments. |
| 2 Exception-catch coverage in tests | ⚠ | WARNING | test_wallet.py:— | Tests using side_effect=Exception do not assert DB insert mock call arguments. |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner.py:50 | `_persist_multiplier_stats` function exists in demo but has no equivalent in live, potentially missing logging of multiplier stats. |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner.py:100 | `_compute_bayesian_multipliers` function exists in demo but has no equivalent in live, potentially missing multiplier computation logic. |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner.py:150 | `_log_summary` function in demo logs "Gate" while in live it logs "Live gate", inconsistent logging message. |
| 7 Demo/live gate runner parity | ✓ | — | — | — |
| 8 General code health | ⚠ | WARNING | gate_runner.py:47 | Bare except block in risk path without logging. |
| 8 General code health | ⚠ | WARNING | gate_runner_live.py:97 | Bare except block in risk path without logging. |
| 8 General code health | ⚠ | INFO | gate_runner.py:75 | TODO/FIXME/HACK comment found. |
| 8 General code health | ⚠ | INFO | gate_runner_live.py:150 | Function returns inconsistent types without caller guard. |

## Retirement Candidates
None
