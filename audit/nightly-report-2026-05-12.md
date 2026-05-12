# APEX Nightly Audit — 2026-05-12
7 issues: 0 critical, 5 warnings, 2 info (LLM checks 1, 2, 7, 8 appended below by llm_checks.py)

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
| 21 Overflow increment range | ✓ | — | — | — |
| 24 Chain-runner wiring | ✓ | — | — | — |
| 26 L1/L2 threshold-source parity | ✓ | — | — | — |
| 27 GICS classification parity | ✓ | — | — | — |
| 28 EXCLUDED_SECTORS gate wiring | ✓ | — | — | — |
| 29 Live sector exposure cap wiring | ✓ | — | — | — |
| 30 Startup live regime exit reconciliation | ✓ | — | — | — |
| 9 Config value drift | ⚠ | WARNING | data/demo_config.json:— | lock1_threshold < 0.65 — below effective floor (current: 0.6) |
| 17 Sentiment cache freshness | ⚠ | WARNING | data/apex.db:sentiment_cache | sentiment_cache last populated 96.7h ago — pre-fetch may have failed |
| 22 Yahoo data pipeline health | ⚠ | WARNING | data/apex.db:sector_snapshots | No sector snapshots written today (2026-05-12) — polling may have failed due to Yahoo rate limit or API outage |
| 25 gate_decision string parity | ⚠ | WARNING | backend/db.py | "FILTERED_ELIGIBILITY" emitted by _OUTCOMES but not referenced in db.py — funnel counts for this outcome will silently be 0 |
| 32 Git sync divergence | ⚠ | WARNING | .git/ | 22 uncommitted file(s) in working tree — changes will be lost or skipped on next nightly run |
| 33 Bayesian multiplier health | ⚠ | WARNING | data/bayesian_multiplier_stats.json | Stats file is from 2026-05-11, not today (2026-05-12) — gate runner did not complete a full cycle or _persist_multiplier_stats() was removed |

| check_1 result_dict_sync_hazard | ✓ | — | — | — |
| check_2 exception-catch coverage | ⚠ | WARNING | test_gate_runners.py:— | Some tests using side_effect=Exception only check return value, not DB insert mock call args. |
| check_2 exception-catch coverage | ⚠ | WARNING | test_wallet.py:— | Some tests using side_effect=Exception only check return value, not DB insert mock call args. |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner.py:alloc | Demo runner uses `get_ticker_gate_fails` while live uses `get_live_ticker_gate_fails`, which may lead to inconsistent gate fail history retrieval. |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner_live.py:_daily_loss_exceeded | Live runner has `_daily_loss_exceeded` function with no equivalent in demo, potentially missing a simulation of loss cap logic. |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner_live.py:_record_live_trade | Live runner has `_record_live_trade` function with no equivalent in demo, potentially missing a simulation of trade recording logic. |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner_live.py:_fire_trade_alert | Live runner has `_fire_trade_alert` function with no equivalent in demo, potentially missing a simulation of trade alert logic. |
| 7 Demo/live gate runner parity | ✓ | — | — | — |
| 8a Bare except blocks | ✓ | — | — | — |
| 8b TODO/FIXME/HACK comments | ✓ | — | — | — |
| 8c Inconsistent return types | ⚠ | WARNING | gate_runner.py:63 | Function returns `None` for `lock2` without caller guard. |

## Retirement Candidates
None
