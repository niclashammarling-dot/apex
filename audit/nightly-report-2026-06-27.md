# Batman's Report — 2026-06-27
14 issues: 0 critical, 13 warnings, 1 info
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
| 38 Live entry absence-of-activity | ✓ | — | — | — |
| 39 Live peak_price integrity | ✓ | — | — | — |
| 40 Config coverage audit | ✓ | — | — | — |
| 41 New-sector integrity | ✓ | — | — | — |
| 42 New-sector integrity | ✓ | — | — | — |
| 45 Static code analysis | ✓ | — | — | — |
| 57 SIC_TO_SECTOR/SECTORS parity | ✓ | — | — | — |
| 9 Config value drift | ⚠ | INFO | data/*_config.json:— | config commit without per-value note: 95a92bd feat: ETF negative penalty, SL/win-rate wiring, demo |
| 13 Undisclosed config change | ⚠ | WARNING | data/demo_config.json:— | etf_negative_floor: <absent> → -1.0 (in 95a92bd0: "feat: ETF negative penalty, SL/win-rate wiring, de") |
| 13 Undisclosed config change | ⚠ | WARNING | data/demo_config.json:— | etf_negative_penalty: <absent> → 0.0 (in 95a92bd0: "feat: ETF negative penalty, SL/win-rate wiring, de") |
| 13 Undisclosed config change | ⚠ | WARNING | data/demo_config.json:— | macro_earnings_near_days: 14 → <absent> (in 95a92bd0: "feat: ETF negative penalty, SL/win-rate wiring, de") |
| 13 Undisclosed config change | ⚠ | WARNING | data/demo_config.json:— | macro_earnings_near_penalty: 0.15 → <absent> (in 95a92bd0: "feat: ETF negative penalty, SL/win-rate wiring, de") |
| 13 Undisclosed config change | ⚠ | WARNING | data/demo_config.json:— | macro_pre_event_penalty: 0.05 → <absent> (in 95a92bd0: "feat: ETF negative penalty, SL/win-rate wiring, de") |
| 13 Undisclosed config change | ⚠ | WARNING | data/demo_config.json:— | stop_loss_pct: 0.06 → 0.07 (in 95a92bd0: "feat: ETF negative penalty, SL/win-rate wiring, de") |
| 13 Undisclosed config change | ⚠ | WARNING | data/live_config.json:— | etf_negative_floor: <absent> → -1.0 (in 95a92bd0: "feat: ETF negative penalty, SL/win-rate wiring, de") |
| 13 Undisclosed config change | ⚠ | WARNING | data/live_config.json:— | etf_negative_penalty: <absent> → 0.1 (in 95a92bd0: "feat: ETF negative penalty, SL/win-rate wiring, de") |
| 13 Undisclosed config change | ⚠ | WARNING | data/live_config.json:— | stop_loss_pct: 0.05 → 0.06 (in 95a92bd0: "feat: ETF negative penalty, SL/win-rate wiring, de") |
| 13 Undisclosed config change | ⚠ | WARNING | data/live_config.json:— | tp_cooloff_hours: <absent> → 168 (in 95a92bd0: "feat: ETF negative penalty, SL/win-rate wiring, de") |
| 32 Git sync divergence | ⚠ | WARNING | .git/ | 15 uncommitted file(s) in working tree — changes will be lost or skipped on next nightly run |
| 54 gate funnel label/lock semantic parity | ⚠ | WARNING | frontend/src/App.jsx:adaptFunnel | exit_lock 2 (Quant) emits "FILTERED_L1", which adaptFunnel labels "L1 · Score" — funnel shows Quant rejections under a different lock's label |
| 54 gate funnel label/lock semantic parity | ⚠ | WARNING | frontend/src/App.jsx:adaptFunnel | exit_lock 3 (Sentiment) emits "FILTERED_L2", which adaptFunnel labels "L2 · Quant" — funnel shows Sentiment rejections under a different lock's label |

## Retirement Candidates
CHECK 1 (Result-dict sync hazard) — 94 days since last triggered
CHECK 2 (Exception-catch coverage in tests) — 94 days since last triggered
CHECK 5 (Sector name strings) — 91 days since last triggered
