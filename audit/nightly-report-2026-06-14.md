# APEX Nightly Audit — 2026-06-14
14 issues: 0 critical, 11 warnings, 3 info

| Check | Status | Sev | File:line | Finding |
| 3 Fractional qty | ✓ | — | — | — |
| 5 Sector name strings | ✓ | — | — | — |
| 6 Test DB isolation | ✓ | — | — | — |
| 10 Ticker data coverage | ✓ | — | — | — |
| 11 NaN/null pipeline | ✓ | — | — | — |
| 12 Lock3 context parity | ✓ | — | — | — |
| 13 Undisclosed config change | ✓ | — | — | — |
| 14 EOD regime freshness | ✓ | — | — | — |
| 15 Calibration freshness | ✓ | — | — | — |
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
| 9 Config value drift | ⚠ | INFO | data/*_config.json:— | config commit without per-value note: f3ef09c fix: EDGAR retry on transient 5xx + skip cache on fe |
| 22 Yahoo data pipeline health | ⚠ | WARNING | data/regime_result_cache.json | regime_result_cache.json is 6 days old (last: 2026-06-08) — EOD regime may not have run successfully this week |
| 50 L4 group constraint (live data) | ⚠ | WARNING | /home/runner/work/apex/apex/data/apex.db | apex.db not found — cannot verify L4 group constraint on executed trades |

| 2 Exception-catch coverage in tests | ⚠ | WARNING | test_gate_runners.py:— | Tests using side_effect=Exception do not assert DB insert mock call arguments. |
| 2 Exception-catch coverage in tests | ⚠ | WARNING | test_wallet.py:— | Tests using side_effect=Exception do not assert DB insert mock call arguments. |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner.py: _compute_bayesian_multipliers | Function exists in demo but not in live, potentially affecting trade sizing logic. |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner.py: _persist_multiplier_stats | Function exists in demo but not in live, potentially affecting multiplier stats persistence. |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner_live.py: _run_l5_for_result | Lock 5 logic present in live but not in demo, affecting trade evaluation. |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner_live.py: _evaluate | Context building and lock evaluation logic differ between demo and live, potentially affecting trade outcomes. |
| 7 Demo/live gate runner parity | ✓ | — | — | — |
| 8 General code health | ⚠ | WARNING | gate_runner.py:47 | Bare except block in risk path without logging in `_compute_bayesian_multipliers`. |
| 8 General code health | ⚠ | WARNING | gate_runner_live.py:97 | Bare except block in risk path without logging in `_record_live_trade`. |
| 8 General code health | ⚠ | INFO | gate_runner.py:75 | TODO/FIXME/HACK comment found. |
| 8 General code health | ⚠ | INFO | gate_runner_live.py:45 | Function `_evaluate` returns inconsistent types without caller guard. |
| 8 General code health | ✓ | — | — | — |

## Retirement Candidates
None
