# APEX Nightly Audit — 2026-04-13
5 issues: 1 critical, 2 warnings, 2 info

| Check | Status | Sev | File:line | Finding |
|-------|--------|-----|-----------|---------|
| 1 Result-dict sync | ✓ | — | — | gate_decision synced correctly in both runners |
| 2 Exception-catch in tests | ✓ | — | — | n/a (test suite broken — see CHECK 8) |
| 3 Fractional qty | ✓ | — | — | int(notional/price) at alpaca.py:122 |
| 4 Config parity | ✓ | — | — | demo_keys == live_keys (18 keys each) |
| 5 Sector name strings | ✓ | — | — | no freehand sector strings in new modules |
| 6 Test DB isolation | ✓ | — | — | conftest.py present; no direct DB path refs in new tests |
| 7 Demo/live parity | ⚠ | INFO | gate_runner.py:98 | Live runner skips dynamic_caps/sector exposure enforcement — demo uses compute_dynamic_caps + wallet.execute_trade; live doesn't. By design but structural divergence. |
| 8 General code health | ⚠ | CRITICAL | backend/gate/chain.py:31 | All new gate modules use `from gate.*` imports — no root-level gate/ package, no sys.path hack. chain.py and lock1-5 all fail at import. Entire gate chain dead on first cycle. |
| 8 General code health | ⚠ | CRITICAL | tests/test_gate_locks.py:98 | Tests import deleted `backend.gate.lock_macro` — ModuleNotFoundError at collection. test_gate_runners.py:126,318 patch same dead path. |
| 9 Config value drift | ⚠ | WARNING | data/demo_config.json | Commit ad1b134 (2026-04-06) changed config; message "feat: Bayesian regime module..." has no per-value justification for config edits. |
| 10 Ticker signal data coverage | ✓ | — | — | sector_regime.py:127 calls get_ticker_daily_scores(days=180) |
| 11 NaN/null config pipeline | ⚠ | WARNING | backend/gate/lock1_eligibility.py:148 | cfg.get('vix_threshold', 25.0) returns None not default when stored None; same for macro_event_blackout_days:149, macro_earnings_blackout_days:150. AUDIT_INSTRUCTIONS and CHECKS.md still reference dead lock_macro.py. |
| 18 Audit pipeline completeness | ⚠ | INFO | audit/mechanical_checks.py:350 | mechanical_checks.py, llm_checks.py, alert_criticals.py not referenced in AUDIT_INSTRUCTIONS.md. mechanical_checks.py targets dead path backend/gate/lock_macro.py. |
| 19 Breakout volume floor | ✓ | — | — | BREAKOUT_MIN_VOLUME=0.50 ≥ 0.40; volume floor applied before breakout assignment at sector_regime.py:181 |

## Retirement Candidates
None
