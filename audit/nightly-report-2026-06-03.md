# APEX Nightly Audit — 2026-06-03
15 issues: 0 critical, 13 warnings, 2 info

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
| 22 Yahoo data pipeline health | ⚠ | WARNING | data/regime_result_cache.json | regime_result_cache.json is 6 days old (last: 2026-05-28) — EOD regime may not have run successfully this week |
| 33 Bayesian multiplier health | ⚠ | WARNING | data/bayesian_multiplier_stats.json | Stats file is from 2026-05-28, last trading day was 2026-06-02 — gate runner did not complete a full cycle or _persist_multiplier_stats() was removed |
| 40 Config coverage audit | ⚠ | WARNING | backend/demo_config.py:_defaults | "trailing_stop_pct" defaults to None (feature disabled) — acknowledged in _NONE_DEFAULTS_ALLOWED; set a non-None value to enable |
| 40 Config coverage audit | ⚠ | WARNING | backend/live_config.py:_defaults | "trailing_stop_pct" defaults to None (feature disabled) — acknowledged in _NONE_DEFAULTS_ALLOWED; set a non-None value to enable |

| check_2 exception-catch coverage in tests | ⚠ | WARNING | test_wallet.py:None | Tests using side_effect=Exception do not assert DB insert mock call arguments. |
| check_2 exception-catch coverage in tests | ⚠ | WARNING | test_gate_runners.py:None | Tests using side_effect=Exception do not assert DB insert mock call arguments. |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner.py: _compute_bayesian_multipliers | Demo runner lacks equivalent logic for handling non-qualifying sectors with allocation=0, which is present in live runner. |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner.py: _persist_multiplier_stats | Demo runner has logic for persisting multiplier stats, which is not present in live runner, potentially missing important logging or auditing information. |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner_live.py: _run_l5_for_result | Live runner includes Lock 5 evaluation logic, which is absent in demo runner, leading to potential discrepancies in trade evaluation. |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner_live.py: _evaluate | Live runner uses `get_live_ticker_gate_fails` for gate history, while demo runner lacks equivalent logic, potentially affecting trade evaluation context. |
| 7 Demo/live gate runner parity | ✓ | — | — | — |
| 8 General code health | ⚠ | WARNING | gate_runner.py:47 | Bare except block in risk path without logging in `_compute_bayesian_multipliers`. |
| 8 General code health | ⚠ | WARNING | gate_runner_live.py:75 | Bare except block in risk path without logging in `_record_live_trade`. |
| 8 General code health | ⚠ | INFO | gate_runner.py:63 | TODO/FIXME/HACK comment found. |
| 8 General code health | ⚠ | INFO | gate_runner_live.py:102 | Function `_evaluate` returns inconsistent types without caller guard. |
| 8 General code health | ✓ | — | — | — |

## Retirement Candidates
None
