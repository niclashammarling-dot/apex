# APEX Nightly Audit — 2026-06-08
16 issues: 9 critical, 6 warnings, 1 info
*(LLM checks 1, 2, 7, 8 appended below by llm_checks.py)*

| Check | Status | Sev | File:line | Finding |
|-------|--------|-----|-----------|---------|
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
| 40 Config coverage audit | ✓ | — | — | — |
| 41 New-sector integrity | ✓ | — | — | — |
| 42 New-sector integrity | ✓ | — | — | — |
| 4 Config parity | ⚠ | WARNING | data/demo_config.json:— | key 'trailing_stop_pct' in live but missing from demo |
| 9 Config value drift | ⚠ | INFO | data/*_config.json:— | config commit without per-value note: a99cfe6 fix: remove trailing stop — SL floor was silently by |
| 32 Git sync divergence | ⚠ | WARNING | .git/ | 7 uncommitted file(s) in working tree — changes will be lost or skipped on next nightly run |
| 36 L4 sub-check pass rates | ⚠ | WARNING | data/apex.db:signals | L4 sub-check 'unusual_calls' passed 10/224 times (4.5%) over 30d — likely dead weight; review base-rate assumption for the APEX universe |
| 44 Regime-conditioned aggregator weight validation | ⚠ | CRITICAL | data/apex.db:sector_posterior_history | only 6 distinct date(s) in last 14 calendar days — persistence not accumulating; validation window cannot be built; check insert_sector_posterior_history call in EOD regime runner |
| 45 Static code analysis | ⚠ | WARNING | backend/gate/lock4_leading.py:198 | ruff: F541 [*] f-string without any placeholders |
| 45 Static code analysis | ⚠ | WARNING | backend/gate/lock4_leading.py:200 | ruff: F541 [*] f-string without any placeholders |
| 50 L4 group constraint (live data) | ⚠ | CRITICAL | data/apex.db:live_gate_history | FDX @ 2026-06-02: TRADE_EXECUTED with both options sub-checks failing (PCR=False, UC=False) — group constraint bypassed or code regressed |
| 50 L4 group constraint (live data) | ⚠ | CRITICAL | data/apex.db:live_gate_history | MU @ 2026-06-02: TRADE_EXECUTED with both options sub-checks failing (PCR=False, UC=False) — group constraint bypassed or code regressed |
| 50 L4 group constraint (live data) | ⚠ | CRITICAL | data/apex.db:live_gate_history | MSFT @ 2026-06-02: TRADE_EXECUTED with both price sub-checks failing (RS=False, VA=False) — group constraint bypassed or code regressed |
| 50 L4 group constraint (live data) | ⚠ | CRITICAL | data/apex.db:live_gate_history | NVDA @ 2026-06-02: TRADE_EXECUTED with both price sub-checks failing (RS=False, VA=False) — group constraint bypassed or code regressed |
| 50 L4 group constraint (live data) | ⚠ | CRITICAL | data/apex.db:live_gate_history | MU @ 2026-05-29: TRADE_EXECUTED with both options sub-checks failing (PCR=False, UC=False) — group constraint bypassed or code regressed |
| 50 L4 group constraint (live data) | ⚠ | CRITICAL | data/apex.db:live_gate_history | MU @ 2026-05-27: TRADE_EXECUTED with both options sub-checks failing (PCR=False, UC=False) — group constraint bypassed or code regressed |
| 50 L4 group constraint (live data) | ⚠ | CRITICAL | data/apex.db:live_gate_history | LLY @ 2026-05-26: TRADE_EXECUTED with both options sub-checks failing (PCR=False, UC=False) — group constraint bypassed or code regressed |
| 50 L4 group constraint (live data) | ⚠ | CRITICAL | data/apex.db:live_gate_history | LRCX @ 2026-05-25: TRADE_EXECUTED with both options sub-checks failing (PCR=False, UC=False) — group constraint bypassed or code regressed |
| 51 Lock 5 Bayesian field exclusion parity | ⚠ | WARNING | backend/gate/lock5_claude.py:SYSTEM_PROMPT | regime_bayes_* field(s) ['regime_bayes_adjusted_score', 'regime_bayes_leader', 'regime_bayes_qualified', 'regime_bayes_rank'] present in Lock 5 context (build_base_context) but not named in SYSTEM_PROMPT exclusion text — silent permission risk; update the prompt's Do NOT list |

## Retirement Candidates
None
