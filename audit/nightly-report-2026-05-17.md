# APEX Nightly Audit — 2026-05-17
23 issues: 0 critical, 10 warnings, 2 info
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
| 28 EXCLUDED_SECTORS gate wiring | ✓ | — | — | — |
| 37 Promote exclusion integrity | ✓ | — | — | — |
| 9 Config value drift | ⚠ | INFO | data/*_config.json:— | config commit without per-value note: 4609bb3 fix: SVG dial fixed positions, config reversion guar |
| 9 Config value drift | ⚠ | INFO | data/*_config.json:— | config commit without per-value note: ace44a5 catchup: frontend redesign, Bayesian sizing, sector  |
| 13 Undisclosed config change | ⚠ | WARNING | data/demo_config.json:— | max_positions: 5 → 10 (in 4609bb3a: "fix: SVG dial fixed positions, config reversion gu") |
| 13 Undisclosed config change | ⚠ | WARNING | data/live_config.json:— | max_hold_days: 40 → 25 (in ca6eb606: "fix(config): exclude account-size constants from P") |
| 13 Undisclosed config change | ⚠ | WARNING | data/live_config.json:— | max_positions: 4 → 8 (in ca6eb606: "fix(config): exclude account-size constants from P") |
| 13 Undisclosed config change | ⚠ | WARNING | data/live_config.json:— | max_sector_exposure: 0.15 → 0.2 (in ca6eb606: "fix(config): exclude account-size constants from P") |
| 13 Undisclosed config change | ⚠ | WARNING | data/live_config.json:— | starting_balance: 2000 → 100000 (in ca6eb606: "fix(config): exclude account-size constants from P") |
| 27 GICS sector classification parity | ⚠ | WARN | data/tickers.json | AVGO has no GICS entry in the audit map — add it when onboarding new tickers |
| 27 GICS sector classification parity | ⚠ | WARN | data/tickers.json | CRM has no GICS entry in the audit map — add it when onboarding new tickers |
| 27 GICS sector classification parity | ⚠ | WARN | data/tickers.json | ORCL has no GICS entry in the audit map — add it when onboarding new tickers |
| 27 GICS sector classification parity | ⚠ | WARN | data/tickers.json | ADBE has no GICS entry in the audit map — add it when onboarding new tickers |
| 27 GICS sector classification parity | ⚠ | WARN | data/tickers.json | NOW has no GICS entry in the audit map — add it when onboarding new tickers |
| 27 GICS sector classification parity | ⚠ | WARN | data/tickers.json | QCOM has no GICS entry in the audit map — add it when onboarding new tickers |
| 27 GICS sector classification parity | ⚠ | WARN | data/tickers.json | EOG has no GICS entry in the audit map — add it when onboarding new tickers |
| 27 GICS sector classification parity | ⚠ | WARN | data/tickers.json | DLR has no GICS entry in the audit map — add it when onboarding new tickers |
| 27 GICS sector classification parity | ⚠ | WARN | data/tickers.json | O has no GICS entry in the audit map — add it when onboarding new tickers |
| 27 GICS sector classification parity | ⚠ | WARN | data/tickers.json | VTR has no GICS entry in the audit map — add it when onboarding new tickers |
| 27 GICS sector classification parity | ⚠ | WARN | data/tickers.json | PSA has no GICS entry in the audit map — add it when onboarding new tickers |
| 32 Git sync divergence | ⚠ | WARNING | .git/ | 1 uncommitted file(s) in working tree — changes will be lost or skipped on next nightly run |
| 36 L4 sub-check pass rates | ⚠ | WARNING | data/apex.db:signals | L4 sub-check 'insider_cluster' passed 0/38 times (0.0%) over 30d — likely dead weight; review base-rate assumption for the APEX universe |
| 38 Live entry absence-of-activity | ⚠ | WARNING | data/apex.db:live_gate_history | 19 active trading days with no TRADE_EXECUTED (last entry 2026-04-10); avg 23 assessments/day; dominant filters: FILTERED_ELIGIBILITY 82%, FILTERED_LEADING 12% |
| 40 Config coverage audit | ⚠ | WARNING | backend/demo_config.py:_defaults | "trailing_stop_pct" defaults to None (feature disabled) — acknowledged in _NONE_DEFAULTS_ALLOWED; set a non-None value to enable |
| 40 Config coverage audit | ⚠ | WARNING | backend/live_config.py:_defaults | "trailing_stop_pct" defaults to None (feature disabled) — acknowledged in _NONE_DEFAULTS_ALLOWED; set a non-None value to enable |

## Retirement Candidates
None
