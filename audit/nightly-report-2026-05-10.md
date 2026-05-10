# APEX Nightly Audit — 2026-05-10
12 issues: 2 critical, 10 warnings, 0 info

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
| 16 yfinance scalar extraction | ✓ | — | — | — |
| 17 Sentiment cache freshness | ✓ | — | — | — |
| 21 Overflow increment range | ✓ | — | — | — |
| 24 Chain-runner wiring | ✓ | — | — | — |
| 26 L1/L2 threshold-source parity | ✓ | — | — | — |
| 27 GICS classification parity | ✓ | — | — | — |
| 28 EXCLUDED_SECTORS gate wiring | ✓ | — | — | — |
| 15 Calibration freshness | ⚠ | WARNING | data/calibration_done.txt:— | calibration last ran 2026-W15, current week 2026-W19 — catch-up may have failed |
| 22 Yahoo data pipeline health | ⚠ | WARNING | data/regime_result_cache.json | regime_result_cache.json missing — regime-bayes will show unavailable after any restart until next EOD run |
| 25 gate_decision string parity | ⚠ | WARNING | backend/db.py | "FILTERED_ELIGIBILITY" emitted by _OUTCOMES but not referenced in db.py — funnel counts for this outcome will silently be 0 |
| 29 Live sector exposure cap wiring | ⚠ | CRITICAL | backend/gate/gate_runner_live.py | SECTORS not imported — ticker→sector map cannot be built |
| 30 Startup live regime exit reconciliation | ⚠ | CRITICAL | backend/scheduler.py | _check_missed_live_exits not defined — live positions unprotected at startup |

| check_1 result_dict_sync_hazard | ✓ | — | — | — |
| check_num check_name | ⚠ | SEV | file:line | one-line description |
| 2 Exception-catch coverage in tests | ⚠ | WARNING | test_gate_runners.py:NN | Test using side_effect=Exception only checks return value, not DB insert mock call args. |
| 2 Exception-catch coverage in tests | ⚠ | WARNING | test_wallet.py:NN | Test using side_effect=Exception only checks return value, not DB insert mock call args. |
| check_num check_name | ⚠ | SEV | file:line | one-line description |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner.py:alloc | Demo runner uses `get_ticker_gate_fails` while live uses `get_live_ticker_gate_fails`, which may lead to inconsistent gate fail history retrieval. |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner_live.py:_daily_loss_exceeded | Live runner has `_daily_loss_exceeded` function with no equivalent in demo, potentially missing a simulation of loss cap logic. |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner_live.py:_record_live_trade | Live runner records trades with `_record_live_trade`, but demo lacks a similar function for simulated trades, potentially missing trade tracking logic. |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner_live.py:_fire_trade_alert | Live runner sends alerts with `_fire_trade_alert`, but demo lacks a similar function, potentially missing alert simulation logic. |
| 7 Demo/live gate runner parity | ✓ | — | — | — |
| check_num check_name | ⚠ | SEV | file:line | one-line description |
| 8a Bare except blocks | ✓ | — | — | — |
| 8b TODO/FIXME/HACK comments | ✓ | — | — | — |
| 8c Inconsistent return types | ⚠ | WARNING | gate_runner.py:63 | Function returns `dict` or `None` for `lock2`, callers do not guard. |

## Retirement Candidates
None
