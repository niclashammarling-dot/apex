# APEX Nightly Audit — 2026-05-28
5 issues: 1 critical, 4 warnings, 0 info
*(LLM checks 1, 2, 7, 8 appended below by llm_checks.py)*

| Check | Status | Sev | File:line | Finding |
|-------|--------|-----|-----------|---------|
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
| 33 Bayesian multiplier health | ✓ | — | — | — |
| 35 PCR collection freshness | ✓ | — | — | — |
| 37 Promote exclusion integrity | ✓ | — | — | — |
| 38 Live entry absence-of-activity | ✓ | — | — | — |
| 39 Live peak_price integrity | ✓ | — | — | — |
| 41 New-sector integrity | ✓ | — | — | — |
| 42 New-sector integrity | ✓ | — | — | — |
| 32 Git sync divergence | ⚠ | WARNING | .git/ | 2 uncommitted file(s) in working tree — changes will be lost or skipped on next nightly run |
| 36 L4 sub-check pass rates | ⚠ | WARNING | data/apex.db:signals | L4 sub-check 'unusual_calls' passed 2/60 times (3.3%) over 30d — likely dead weight; review base-rate assumption for the APEX universe |
| 40 Config coverage audit | ⚠ | WARNING | backend/demo_config.py:_defaults | "trailing_stop_pct" defaults to None (feature disabled) — acknowledged in _NONE_DEFAULTS_ALLOWED; set a non-None value to enable |
| 40 Config coverage audit | ⚠ | WARNING | backend/live_config.py:_defaults | "trailing_stop_pct" defaults to None (feature disabled) — acknowledged in _NONE_DEFAULTS_ALLOWED; set a non-None value to enable |
| 44 Regime-conditioned aggregator weight validation | ⚠ | CRITICAL | data/apex.db:sector_posterior_history | sector_posterior_history table missing — insert_sector_posterior_history never ran; regime-conditioned weights have no validation data accumulating |

## Retirement Candidates
None
