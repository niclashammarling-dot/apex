# APEX Nightly Audit — 2026-05-22
9 issues: 0 critical, 8 warnings, 1 info
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
| 36 L4 sub-check pass rates | ✓ | — | — | — |
| 37 Promote exclusion integrity | ✓ | — | — | — |
| 39 Live peak_price integrity | ✓ | — | — | — |
| 41 New-sector integrity | ✓ | — | — | — |
| 42 New-sector integrity | ✓ | — | — | — |
| 9 Config value drift | ⚠ | INFO | data/*_config.json:— | config commit without per-value note: 4609bb3 fix: SVG dial fixed positions, config reversion guar |
| 13 Undisclosed config change | ⚠ | WARNING | data/demo_config.json:— | macro_earnings_blackout_days: 3 → 1 (in 8935d97b: "config: commit intentional runtime config changes ") |
| 13 Undisclosed config change | ⚠ | WARNING | data/demo_config.json:— | max_sector_exposure: 0.2 → 0.4 (in 8935d97b: "config: commit intentional runtime config changes ") |
| 13 Undisclosed config change | ⚠ | WARNING | data/live_config.json:— | macro_earnings_blackout_days: 3 → 2 (in 8935d97b: "config: commit intentional runtime config changes ") |
| 13 Undisclosed config change | ⚠ | WARNING | data/live_config.json:— | max_sector_exposure: 0.2 → 0.3 (in 8935d97b: "config: commit intentional runtime config changes ") |
| 32 Git sync divergence | ⚠ | WARNING | .git/ | 6 uncommitted file(s) in working tree — changes will be lost or skipped on next nightly run |
| 38 Live entry absence-of-activity | ⚠ | WARNING | data/apex.db:live_gate_history | 22 active trading days with no TRADE_EXECUTED (last entry 2026-04-10); avg 22 assessments/day; dominant filters: FILTERED_ELIGIBILITY 83%, FILTERED_LEADING 11% |
| 40 Config coverage audit | ⚠ | WARNING | backend/demo_config.py:_defaults | "trailing_stop_pct" defaults to None (feature disabled) — acknowledged in _NONE_DEFAULTS_ALLOWED; set a non-None value to enable |
| 40 Config coverage audit | ⚠ | WARNING | backend/live_config.py:_defaults | "trailing_stop_pct" defaults to None (feature disabled) — acknowledged in _NONE_DEFAULTS_ALLOWED; set a non-None value to enable |

## Retirement Candidates
None
