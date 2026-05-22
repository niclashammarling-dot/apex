# APEX Nightly Audit — 2026-05-18
8 issues: 0 critical, 6 warnings, 2 info
*(LLM checks 1, 2, 7, 8 appended below by llm_checks.py)*

| Check | Status | Sev | File:line | Finding |
|-------|--------|-----|-----------|---------|
| 3 Fractional qty | ✓ | — | — | — |
| 4 Config parity | ✓ | — | — | — |
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
| 22 Yahoo data pipeline health | ✓ | — | — | — |
| 24 Chain-runner wiring | ✓ | — | — | — |
| 25 gate_decision string parity | ✓ | — | — | — |
| 26 L1/L2 threshold-source parity | ✓ | — | — | — |
| 27 GICS classification parity | ✓ | — | — | — |
| 28 EXCLUDED_SECTORS gate wiring | ✓ | — | — | — |
| 29 Live sector exposure cap wiring | ✓ | — | — | — |
| 30 Startup live regime exit reconciliation | ✓ | — | — | — |
| 31 Live bracket TIF and exit reconciliation | ✓ | — | — | — |
| 36 L4 sub-check pass rates | ✓ | — | — | — |
| 37 Promote exclusion integrity | ✓ | — | — | — |
| 39 Live peak_price integrity | ✓ | — | — | — |
| 41 New-sector integrity | ✓ | — | — | — |
| 42 New-sector integrity | ✓ | — | — | — |
| 9 Config value drift | ⚠ | INFO | data/*_config.json:— | config commit without per-value note: 4609bb3 fix: SVG dial fixed positions, config reversion guar |
| 9 Config value drift | ⚠ | INFO | data/*_config.json:— | config commit without per-value note: ace44a5 catchup: frontend redesign, Bayesian sizing, sector  |
| 32 Git sync divergence | ⚠ | WARNING | .git/ | 7 uncommitted file(s) in working tree — changes will be lost or skipped on next nightly run |
| 33 Bayesian multiplier health | ⚠ | WARNING | data/bayesian_multiplier_stats.json | Stats file is from 2026-05-12, last trading day was 2026-05-15 — gate runner did not complete a full cycle or _persist_multiplier_stats() was removed |
| 35 PCR collection freshness | ⚠ | WARNING | data/apex.db:lock4_pcr_history | lock4_pcr_history is empty — collect_pcr cron job has never completed successfully |
| 38 Live entry absence-of-activity | ⚠ | WARNING | data/apex.db:live_gate_history | 19 active trading days with no TRADE_EXECUTED (last entry 2026-04-10); avg 23 assessments/day; dominant filters: FILTERED_ELIGIBILITY 82%, FILTERED_LEADING 12% |
| 40 Config coverage audit | ⚠ | WARNING | backend/demo_config.py:_defaults | "trailing_stop_pct" defaults to None (feature disabled) — acknowledged in _NONE_DEFAULTS_ALLOWED; set a non-None value to enable |
| 40 Config coverage audit | ⚠ | WARNING | backend/live_config.py:_defaults | "trailing_stop_pct" defaults to None (feature disabled) — acknowledged in _NONE_DEFAULTS_ALLOWED; set a non-None value to enable |

## Retirement Candidates
None
