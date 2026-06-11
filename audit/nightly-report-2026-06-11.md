# APEX Nightly Audit — 2026-06-11
16 issues: 0 critical, 13 warnings, 3 info

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
| 13 Undisclosed config change | ⚠ | WARNING | data/demo_config.json:— | macro_event_blackout_days: 2 → 1 (in f3ef09c5: "fix: EDGAR retry on transient 5xx + skip cache on ") |
| 13 Undisclosed config change | ⚠ | WARNING | data/demo_config.json:— | tp_cooloff_hours: <absent> → 168 (in f3ef09c5: "fix: EDGAR retry on transient 5xx + skip cache on ") |
| 13 Undisclosed config change | ⚠ | WARNING | data/live_config.json:— | trailing_stop_pct: 0.1 → <absent> (in f3ef09c5: "fix: EDGAR retry on transient 5xx + skip cache on ") |
| 33 Bayesian multiplier health | ⚠ | WARNING | data/bayesian_multiplier_stats.json | Stats file is from 2026-06-08, last trading day was 2026-06-10 — gate runner did not complete a full cycle or _persist_multiplier_stats() was removed |
| 50 L4 group constraint (live data) | ⚠ | WARNING | /home/runner/work/apex/apex/data/apex.db | apex.db not found — cannot verify L4 group constraint on executed trades |

| 2 Exception-catch coverage in tests | ⚠ | WARNING | test_wallet.py:None | Test does not assert DB insert mock call args when using side_effect=Exception. |
| 2 Exception-catch coverage in tests | ⚠ | WARNING | test_gate_runners.py:None | Test does not assert DB insert mock call args when using side_effect=Exception. |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner.py:50 | `_persist_multiplier_stats` function exists in demo but has no equivalent in live, potentially missing logic for tracking multiplier stats in live. |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner_live.py:50 | `_run_l5_for_result` function in live runner has no equivalent in demo, potentially missing logic for Lock 5 evaluation in demo. |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner_live.py:100 | `_evaluate` function in live runner uses `get_live_ticker_gate_fails` for `gate_history_fn`, no equivalent in demo, potentially missing historical gate failure logic in demo. |
| 7 Demo/live gate runner parity | ✓ | — | — | — |
| 8 General code health | ⚠ | WARNING | gate_runner.py:47 | Bare except block in risk path without logging in `_compute_bayesian_multipliers`. |
| 8 General code health | ⚠ | WARNING | gate_runner_live.py:97 | Bare except block in risk path without logging in `_record_live_trade`. |
| 8 General code health | ⚠ | INFO | gate_runner.py:75 | TODO/FIXME/HACK comment found. |
| 8 General code health | ⚠ | INFO | gate_runner_live.py:150 | TODO/FIXME/HACK comment found. |
| 8 General code health | ✓ | — | — | — |

## Retirement Candidates
None
