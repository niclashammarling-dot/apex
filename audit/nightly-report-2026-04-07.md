# APEX Nightly Audit — 2026-04-07
21 issues: 1 critical, 13 warnings, 7 info

| Check | Status | Sev | File:line | Finding |
|-------|--------|-----|-----------|---------|
| 4 Config parity | ✓ | — | — | — |
| 5 Sector name strings | ✓ | — | — | — |
| 6 Test DB isolation | ✓ | — | — | — |
| 10 Ticker data coverage | ✓ | — | — | — |
| 11 NaN/null pipeline | ✓ | — | — | — |
| 12 Lock3 context parity | ✓ | — | — | — |
| 14 EOD regime freshness | ✓ | — | — | — |
| 15 Calibration freshness | ✓ | — | — | — |
| 16 yfinance scalar extraction | ✓ | — | — | — |
| 3 Fractional qty | ⚠ | WARNING | backend/brokers/alpaca.py:124 | qty assigned without int(): raise ValueError(f"Computed qty={qty} for {ticker} @ ${curre |
| 3 Fractional qty | ⚠ | WARNING | backend/brokers/alpaca.py:141 | qty assigned without int(): f"Alpaca bracket order placed [{ticker}]: qty={qty} @ ~${cur |
| 9 Config value drift | ⚠ | INFO | data/*_config.json:— | config commit without per-value note: ad1b134 feat: Bayesian regime module, gate execution orderin |
| 9 Config value drift | ⚠ | INFO | data/*_config.json:— | config commit without per-value note: 2a7877e feat: Sharpe engine A/B, demo gate history, signal c |
| 9 Config value drift | ⚠ | INFO | data/*_config.json:— | config commit without per-value note: cc2aa23 feat: gate hardening, DB expansion, backtest optimiz |
| 13 Undisclosed config change | ⚠ | WARNING | data/demo_config.json:— | lock1_threshold: 0.6 → 0.65 (in ad1b1344: "feat: Bayesian regime module, gate execution order") |
| 13 Undisclosed config change | ⚠ | WARNING | data/demo_config.json:— | macro_event_blackout_days: 1 → 2 (in ad1b1344: "feat: Bayesian regime module, gate execution order") |

| check_1 result_dict_sync_hazard | ⚠ | CRITICAL | gate_runner.py:66 | `gate_decision` is derived from `outcome` early, but `outcome` can be mutated later without updating `gate_decision`. |
| check_2 result_dict_sync_hazard | ✓ | — | — | — |
| check_num check_name | ⚠ | SEV | file:line | one-line description |
| 2 Exception-catch coverage in tests | ⚠ | WARNING | test_gate_runners.py:— | Some tests using side_effect=Exception only check return value, not DB insert mock call args. |
| 2 Exception-catch coverage in tests | ⚠ | WARNING | test_gate_locks.py:— | Some tests using side_effect=Exception only check return value, not DB insert mock call args. |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner_live.py:missing | Missing transition probability awareness logic in live runner. |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner_live.py:missing | Missing Bayesian regime allocation logic in live runner. |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner_live.py:missing | Missing rotation score logic in live runner. |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner_live.py:missing | Missing _record_live_trade function in demo runner. |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner_live.py:missing | Missing _fire_trade_alert function in demo runner. |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner.py:missing | Missing _daily_loss_exceeded function in demo runner. |
| check_num check_name | ⚠ | SEV | file:line | one-line description |
| 8 General code health | ⚠ | INFO | gate_runner.py:15 | Bare except block without logging in a non-risk path. |
| 8 General code health | ⚠ | INFO | gate_runner.py:43 | Bare except block without logging in a non-risk path. |
| 8 General code health | ⚠ | WARNING | gate_runner_live.py:41 | Bare except block without logging in a risk path (_daily_loss_exceeded). |
| 8 General code health | ⚠ | INFO | gate_runner_live.py:66 | Bare except block without logging in a non-risk path. |
| 8 General code health | ⚠ | INFO | gate_runner_live.py:84 | Bare except block without logging in a non-risk path. |
| 8 General code health | ✓ | — | — | — |

## Retirement Candidates
None
