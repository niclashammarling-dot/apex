# APEX Nightly Audit — 2026-04-09
6 issues: 0 critical, 5 warnings, 1 info

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
| 17 Sentiment cache freshness | ✓ | — | — | — |
| 9 Config value drift | ⚠ | INFO | data/*_config.json:— | config commit without per-value note: ad1b134 feat: Bayesian regime module, gate execution orderin |

| check_1 result_dict_sync_hazard | ✓ | — | — | — |
| check_num check_name | ⚠ | SEV | file:line | one-line description |
| 2 Exception-catch coverage in tests | ⚠ | WARNING | test_gate_runners.py:NN | Test using side_effect=Exception only checks return value, not DB insert mock call args. |
| 2 Exception-catch coverage in tests | ⚠ | WARNING | test_gate_locks.py:NN | Test using side_effect=Exception only checks return value, not DB insert mock call args. |
| check_num check_name | ⚠ | SEV | file:line | one-line description |
|----------------------|---|-----|------------|----------------------|
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner.py:3 | Demo runner uses `get_ticker_gate_fails` while live uses `get_live_ticker_gate_fails`, which may lead to inconsistent fail history handling. |
| 7 Demo/live gate runner parity | ✓ | — | — | — |
| check_num check_name | ⚠ | SEV | file:line | one-line description |
|----------------------|---|-----|------------|-----------------------|
| 8a Bare except blocks | ✓ | — | — | — |
| 8b TODO/FIXME/HACK comments | ✓ | — | — | — |
| 8c Inconsistent return types | ⚠ | WARNING | gate_runner.py:15 | Function returns `None` for `rotation_regime_conditioned` and `rotation_regime_sample_size` without caller guards. |
| 8c Inconsistent return types | ⚠ | WARNING | gate_runner.py:43 | Function returns `None` for `ticker_gate_history` without caller guards. |

## Retirement Candidates
None
