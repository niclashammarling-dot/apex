# APEX Nightly Audit — 2026-04-14
12 issues: 0 critical, 9 warnings, 3 info

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
| 16 yfinance scalar extraction | ✓ | — | — | — |
| 17 Sentiment cache freshness | ✓ | — | — | — |
| 11 NaN/null pipeline | ⚠ | WARNING | backend/gate/lock1_eligibility.py:147 | cfg.get('vix_threshold', default) — default won't fire for stored None |
| 11 NaN/null pipeline | ⚠ | WARNING | backend/gate/lock1_eligibility.py:148 | cfg.get('macro_event_blackout_days', default) — default won't fire for stored None |
| 11 NaN/null pipeline | ⚠ | WARNING | backend/gate/lock1_eligibility.py:149 | cfg.get('macro_earnings_blackout_days', default) — default won't fire for stored None |
| 15 Calibration freshness | ⚠ | WARNING | data/calibration_done.txt:— | calibration last ran 2026-W15, current week 2026-W16 — catch-up may have failed |

| check_1 result_dict_sync_hazard | ✓ | — | — | — |
| check_num check_name | ✓ | — | — | — |
| 2 Exception-catch coverage in tests | ⚠ | WARNING | test_gate_runners.py: | Some tests using side_effect=Exception only check the return value and skip the persistence call args. |
| check_num check_name | ⚠ | SEV | file:line | one-line description |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner.py:line | Demo runner uses `get_ticker_gate_fails` while live uses `get_live_ticker_gate_fails`, which may lead to inconsistent fail history retrieval. |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner.py:line | Demo runner lacks `_daily_loss_exceeded`, `_record_live_trade`, and `_fire_trade_alert` functions, which are critical for live trading safety and record-keeping. |
| 7 Demo/live gate runner parity | ✓ | — | — | — |
| check_num check_name | ⚠ | SEV | file:line | one-line description |
| 8 General code health | ✓ | — | — | — |
| 8 General code health | ⚠ | INFO | gate_runner.py:29 | Bare except block missing logging in non-risk path. |
| 8 General code health | ⚠ | INFO | gate_runner.py:54 | Bare except block missing logging in non-risk path. |
| 8 General code health | ⚠ | WARNING | gate_runner_live.py:47 | Bare except block in risk path with logging at correct level. |
| 8 General code health | ⚠ | WARNING | gate_runner_live.py:74 | Bare except block in risk path with logging at correct level. |
| 8 General code health | ⚠ | INFO | gate_runner_live.py:92 | TODO/FIXME/HACK comment found. |
| 8 General code health | ✓ | — | — | — |

## Retirement Candidates
None
