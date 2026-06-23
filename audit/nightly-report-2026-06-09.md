# APEX Nightly Audit — 2026-06-09
18 issues: 0 critical, 14 warnings, 4 info

| Check | Status | Sev | File:line | Finding |
| 3 Fractional qty | ✓ | — | — | — |
| 5 Sector name strings | ✓ | — | — | — |
| 6 Test DB isolation | ✓ | — | — | — |
| 10 Ticker data coverage | ✓ | — | — | — |
| 11 NaN/null pipeline | ✓ | — | — | — |
| 12 Lock3 context parity | ✓ | — | — | — |
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
| 40 Config coverage audit | ✓ | — | — | — |
| 41 New-sector integrity | ✓ | — | — | — |
| 42 New-sector integrity | ✓ | — | — | — |
| 45 Static code analysis | ✓ | — | — | — |
| 4 Config parity | ⚠ | WARNING | data/live_config.json:— | key 'tp_cooloff_hours' in demo but missing from live |
| 9 Config value drift | ⚠ | INFO | data/*_config.json:— | config commit without per-value note: f3ef09c fix: EDGAR retry on transient 5xx + skip cache on fe |
| 9 Config value drift | ⚠ | INFO | data/*_config.json:— | config commit without per-value note: a99cfe6 fix: remove trailing stop — SL floor was silently by |
| 13 Undisclosed config change | ⚠ | WARNING | data/demo_config.json:— | macro_event_blackout_days: 2 → 1 (in f3ef09c5: "fix: EDGAR retry on transient 5xx + skip cache on ") |
| 13 Undisclosed config change | ⚠ | WARNING | data/demo_config.json:— | tp_cooloff_hours: <absent> → 168 (in f3ef09c5: "fix: EDGAR retry on transient 5xx + skip cache on ") |
| 13 Undisclosed config change | ⚠ | WARNING | data/live_config.json:— | trailing_stop_pct: 0.1 → <absent> (in f3ef09c5: "fix: EDGAR retry on transient 5xx + skip cache on ") |
| 50 L4 group constraint (live data) | ⚠ | WARNING | /home/runner/work/apex/apex/data/apex.db | apex.db not found — cannot verify L4 group constraint on executed trades |

| check_2_exception_catch_coverage_in_tests | ⚠ | WARNING | test_wallet.py:— | Test does not assert DB insert mock call arguments when using side_effect=Exception. |
| check_2_exception_catch_coverage_in_tests | ⚠ | WARNING | test_gate_runners.py:— | Test does not assert DB insert mock call arguments when using side_effect=Exception. |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner.py: _compute_bayesian_multipliers | Demo runner lacks a mechanism to handle non-qualifying sectors, which may lead to incorrect multiplier calculations. |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner_live.py: _evaluate | Live runner uses `get_live_ticker_gate_fails` for gate history, which is absent in demo, potentially affecting evaluation consistency. |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner_live.py: _run_l5_for_result | Live runner includes Lock 5 evaluation, which is not present in demo, leading to potential logic divergence. |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner_live.py: _record_live_trade | Live runner records trades in a database, which is absent in demo, potentially affecting trade tracking. |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner_live.py: _fire_trade_alert | Live runner sends trade alerts, which are absent in demo, potentially affecting alerting consistency. |
| 7 Demo/live gate runner parity | ✓ | — | — | — |
| 8 General code health | ⚠ | WARNING | gate_runner.py:47 | Bare except block in risk path without logging in `_compute_bayesian_multipliers`. |
| 8 General code health | ⚠ | WARNING | gate_runner_live.py:97 | Bare except block in risk path without logging in `_record_live_trade`. |
| 8 General code health | ⚠ | INFO | gate_runner.py:75 | TODO/FIXME/HACK comment found. |
| 8 General code health | ⚠ | INFO | gate_runner_live.py:120 | TODO/FIXME/HACK comment found. |
| 8 General code health | ✓ | — | — | — |

## Retirement Candidates
None
