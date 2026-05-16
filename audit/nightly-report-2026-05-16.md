# APEX Nightly Audit — 2026-05-16
31 issues: 0 critical, 13 warnings, 18 info

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
| 9 Config value drift | ⚠ | INFO | data/*_config.json:— | config commit without per-value note: ace44a5 catchup: frontend redesign, Bayesian sizing, sector  |
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
| 37 Promote exclusion integrity | ⚠ | WARNING | backend/live_config.py | Could not import demo_thresholds for runtime check: No module named 'loguru' |

| check_1 result_dict_sync_hazard | ✓ | — | — | — |
| check_num check_name | ⚠ | SEV | file:line | one-line description |
| 2 Exception-catch coverage in tests | ⚠ | WARNING | test_gate_runners.py:— | Tests using side_effect=Exception do not assert DB insert mock call arguments. |
| 2 Exception-catch coverage in tests | ⚠ | WARNING | test_wallet.py:— | Tests using side_effect=Exception do not assert DB insert mock call arguments. |
| check_num check_name | ⚠ | SEV | file:line | one-line description |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner.py:1 | Demo runner lacks `_daily_loss_exceeded` equivalent, missing loss cap logic. |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner.py:1 | Demo runner lacks `_record_live_trade` equivalent, missing trade record logic. |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner.py:1 | Demo runner lacks `_fire_trade_alert` equivalent, missing trade alert logic. |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner.py:1 | Demo runner lacks `get_live_ticker_gate_fails` equivalent, missing gate fail history logic. |
| 7 Demo/live gate runner parity | ✓ | — | — | — |
| check_num check_name | ⚠ | SEV | file:line | one-line description |
| 8 General code health | ⚠ | INFO | gate_runner.py:47 | Bare except block missing logging in non-risk path. |
| 8 General code health | ⚠ | INFO | gate_runner_live.py:23 | Bare except block missing logging in non-risk path. |
| 8 General code health | ⚠ | WARNING | gate_runner_live.py:47 | Bare except block in risk path with logging at incorrect level. |
| 8 General code health | ⚠ | WARNING | gate_runner_live.py:66 | Bare except block in risk path with logging at incorrect level. |
| 8 General code health | ⚠ | INFO | gate_runner_live.py:82 | Bare except block missing logging in non-risk path. |
| 8 General code health | ⚠ | INFO | gate_runner_live.py:92 | Bare except block missing logging in non-risk path. |
| 8 General code health | ⚠ | INFO | gate_runner_live.py:102 | Bare except block missing logging in non-risk path. |
| 8 General code health | ⚠ | INFO | gate_runner_live.py:112 | Bare except block missing logging in non-risk path. |
| 8 General code health | ⚠ | INFO | gate_runner_live.py:122 | Bare except block missing logging in non-risk path. |
| 8 General code health | ⚠ | INFO | gate_runner_live.py:132 | Bare except block missing logging in non-risk path. |
| 8 General code health | ⚠ | INFO | gate_runner_live.py:142 | Bare except block missing logging in non-risk path. |
| 8 General code health | ⚠ | INFO | gate_runner_live.py:152 | Bare except block missing logging in non-risk path. |
| 8 General code health | ⚠ | INFO | gate_runner_live.py:162 | Bare except block missing logging in non-risk path. |
| 8 General code health | ⚠ | INFO | gate_runner_live.py:172 | Bare except block missing logging in non-risk path. |
| 8 General code health | ⚠ | INFO | gate_runner_live.py:182 | Bare except block missing logging in non-risk path. |
| 8 General code health | ⚠ | INFO | gate_runner_live.py:192 | Bare except block missing logging in non-risk path. |
| 8 General code health | ⚠ | INFO | gate_runner_live.py:202 | Bare except block missing logging in non-risk path. |
| 8 General code health | ⚠ | INFO | gate_runner_live.py:212 | Bare except block missing logging in non-risk path. |
| 8 General code health | ⚠ | INFO | gate_runner_live.py:222 | Bare except block missing logging in non-risk path. |
| 8 General code health |

## Retirement Candidates
None
