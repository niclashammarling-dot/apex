# APEX Nightly Audit — 2026-05-15
23 issues: 0 critical, 11 warnings, 1 info
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
| 9 Config value drift | ⚠ | WARNING | data/demo_config.json:— | max_positions > 6 — dilutes signal quality (current: 10) |
| 9 Config value drift | ⚠ | INFO | data/*_config.json:— | config commit without per-value note: ace44a5 catchup: frontend redesign, Bayesian sizing, sector  |
| 13 Undisclosed config change | ⚠ | WARNING | data/demo_config.json:— | lock1_threshold: 0.65 → 0.6 (in ace44a5c: "catchup: frontend redesign, Bayesian sizing, secto") |
| 13 Undisclosed config change | ⚠ | WARNING | data/demo_config.json:— | profit_lock_trail_pct: <absent> → 0.015 (in ace44a5c: "catchup: frontend redesign, Bayesian sizing, secto") |
| 13 Undisclosed config change | ⚠ | WARNING | data/demo_config.json:— | profit_lock_trigger_pct: <absent> → 0.02 (in ace44a5c: "catchup: frontend redesign, Bayesian sizing, secto") |
| 13 Undisclosed config change | ⚠ | WARNING | data/demo_config.json:— | take_profit_pct: 0.07 → 0.06 (in ace44a5c: "catchup: frontend redesign, Bayesian sizing, secto") |
| 13 Undisclosed config change | ⚠ | WARNING | data/live_config.json:— | lock1_threshold: 0.7 → 0.65 (in ace44a5c: "catchup: frontend redesign, Bayesian sizing, secto") |
| 13 Undisclosed config change | ⚠ | WARNING | data/live_config.json:— | profit_lock_trail_pct: <absent> → 0.015 (in ace44a5c: "catchup: frontend redesign, Bayesian sizing, secto") |
| 13 Undisclosed config change | ⚠ | WARNING | data/live_config.json:— | profit_lock_trigger_pct: <absent> → 0.02 (in ace44a5c: "catchup: frontend redesign, Bayesian sizing, secto") |
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
| 32 Git sync divergence | ⚠ | WARNING | .git/ | 19 uncommitted file(s) in working tree — changes will be lost or skipped on next nightly run |
| 35 PCR collection freshness | ⚠ | WARNING | data/apex.db:lock4_pcr_history | lock4_pcr_history is empty — collect_pcr cron job has never completed successfully |
| 36 L4 sub-check pass rates | ⚠ | WARNING | data/apex.db:signals | L4 sub-check 'insider_cluster' passed 0/55 times (0.0%) over 30d — likely dead weight; review base-rate assumption for the APEX universe |

## Retirement Candidates
None
