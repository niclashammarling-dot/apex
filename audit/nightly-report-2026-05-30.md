# APEX Nightly Audit — 2026-05-30
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
| 37 Promote exclusion integrity | ✓ | — | — | — |
| 38 Live entry absence-of-activity | ✓ | — | — | — |
| 39 Live peak_price integrity | ✓ | — | — | — |
| 41 New-sector integrity | ✓ | — | — | — |
| 42 New-sector integrity | ✓ | — | — | — |
| 45 Static code analysis | ✓ | — | — | — |
| 40 Config coverage audit | ⚠ | WARNING | backend/demo_config.py:_defaults | "trailing_stop_pct" defaults to None (feature disabled) — acknowledged in _NONE_DEFAULTS_ALLOWED; set a non-None value to enable |
| 40 Config coverage audit | ⚠ | WARNING | backend/live_config.py:_defaults | "trailing_stop_pct" defaults to None (feature disabled) — acknowledged in _NONE_DEFAULTS_ALLOWED; set a non-None value to enable |

| check_2 exception-catch coverage in tests | ⚠ | WARNING | test_wallet.py:None | Tests using side_effect=Exception do not assert DB insert mock call arguments. |
| check_2 exception-catch coverage in tests | ⚠ | WARNING | test_gate_runners.py:None | Tests using side_effect=Exception do not assert DB insert mock call arguments. |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner.py:1 | `l2_summary` and `lock3_sentiment_score` fields in demo runner's context are not present in live runner's context. |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner.py:2 | `lock3_conviction` field in demo runner's context is not present in live runner's context. |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner.py:3 | `macro_reason` field in demo runner's context is not present in live runner's context. |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner_live.py:1 | `_run_l5_for_result` function in live runner has no equivalent in demo runner, affecting lock logic. |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner_live.py:1 | `build_base_context` in live runner uses `get_live_ticker_gate_fails`, which has no equivalent in demo runner. |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner_live.py:1 | `evaluate_chain` in live runner uses `stop_after_lock=4`, which is not present in demo runner. |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner_live.py:1 | `_chain_to_gate_result` in live runner stashes L5 inputs, which is not present in demo runner. |
| 7 Demo/live gate runner parity | ✓ | — | — | — |
| 8 General code health | ⚠ | WARNING | gate_runner.py:47 | Bare except block in risk path without logging in `_compute_bayesian_multipliers`. |
| 8 General code health | ⚠ | WARNING | gate_runner_live.py:97 | Bare except block in risk path without logging in `_record_live_trade`. |
| 8 General code health | ⚠ | INFO | gate_runner.py:75 | TODO/FIXME/HACK comment found. |
| 8 General code health | ⚠ | INFO | gate_runner_live.py:45 | Function `_evaluate` returns inconsistent types without caller guard. |
| 8 General code health | ✓ | — | — | — |

## Retirement Candidates
None
