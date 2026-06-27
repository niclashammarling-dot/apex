# Batman's Report — 2026-06-26
5 issues: 0 critical, 5 warnings, 0 info
*(LLM checks 1, 2, 7, 8 appended below by llm_checks.py)*

| Check | Status | Sev | File:line | Finding |
|-------|--------|-----|-----------|---------|
| 3 Fractional qty | ✓ | — | — | — |
| 4 Config parity | ✓ | — | — | — |
| 5 Sector name strings | ✓ | — | — | — |
| 6 Test DB isolation | ✓ | — | — | — |
| 9 Config value drift | ✓ | — | — | — |
| 10 Ticker data coverage | ✓ | — | — | — |
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
| 36 L4 sub-check pass rates | ✓ | — | — | — |
| 37 Promote exclusion integrity | ✓ | — | — | — |
| 38 Live entry absence-of-activity | ✓ | — | — | — |
| 39 Live peak_price integrity | ✓ | — | — | — |
| 40 Config coverage audit | ✓ | — | — | — |
| 41 New-sector integrity | ✓ | — | — | — |
| 42 New-sector integrity | ✓ | — | — | — |
| 45 Static code analysis | ✓ | — | — | — |
| 57 SIC_TO_SECTOR/SECTORS parity | ✓ | — | — | — |
| 11 NaN/null pipeline | ⚠ | WARNING | backend/gate/lock1_eligibility.py:201 | cfg.get('macro_event_blackout_days', default) — default won't fire for stored None |
| 32 Git sync divergence | ⚠ | WARNING | .git/ | 20 uncommitted file(s) in working tree — changes will be lost or skipped on next nightly run |
| 35 PCR collection freshness | ⚠ | WARNING | data/apex.db:lock4_pcr_history | last PCR observation is from 2026-06-24 (2d ago, last trading day was 2026-06-25) — collect_pcr cron job may have failed; per-ticker percentile calibration will be delayed if collection gap grows beyond 1 week |
| 54 gate funnel label/lock semantic parity | ⚠ | WARNING | frontend/src/App.jsx:adaptFunnel | exit_lock 2 (Quant) emits "FILTERED_L1", which adaptFunnel labels "L1 · Score" — funnel shows Quant rejections under a different lock's label |
| 54 gate funnel label/lock semantic parity | ⚠ | WARNING | frontend/src/App.jsx:adaptFunnel | exit_lock 3 (Sentiment) emits "FILTERED_L2", which adaptFunnel labels "L2 · Quant" — funnel shows Sentiment rejections under a different lock's label |

## Retirement Candidates
CHECK 1 (Result-dict sync hazard) — 93 days since last triggered
CHECK 2 (Exception-catch coverage in tests) — 93 days since last triggered
CHECK 5 (Sector name strings) — 90 days since last triggered
