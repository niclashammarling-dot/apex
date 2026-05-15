# APEX Nightly Audit — 2026-05-15
16 issues: 0 critical, 15 warnings, 1 info

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
| 33 Bayesian multiplier health | ⚠ | WARNING | data/bayesian_multiplier_stats.json | Stats file is from 2026-05-12, last trading day was 2026-05-14 — gate runner did not complete a full cycle or _persist_multiplier_stats() was removed |

| check_1 result_dict_sync_hazard | ✓ | — | — | — |
| check_num check_name | ⚠ | SEV | file:line | one-line description |
| 2 Exception-catch coverage in tests | ⚠ | WARNING | test_gate_runners.py:— | Tests using side_effect=Exception do not assert DB insert mock call args. |
| 2 Exception-catch coverage in tests | ⚠ | WARNING | test_wallet.py:— | Tests using side_effect=Exception do not assert DB insert mock call args. |
| check_num check_name | ⚠ | SEV | file:line | one-line description |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner.py:1 | Demo runner lacks `_daily_loss_exceeded` equivalent, missing loss cap logic. |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner.py:1 | Demo runner lacks `_record_live_trade` equivalent, missing trade record logic. |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner.py:1 | Demo runner lacks `_fire_trade_alert` equivalent, missing trade alert logic. |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner.py:1 | Demo runner lacks `get_live_ticker_gate_fails` equivalent, missing gate fail history logic. |
| 7 Demo/live gate runner parity | ✓ | — | — | — |
| check_num check_name | ⚠ | SEV | file:line | one-line description |
| 8a Bare except blocks | ✓ | — | — | — |
| 8b TODO/FIXME/HACK comments | ✓ | — | — | — |
| 8c Inconsistent return types | ⚠ | WARNING | gate_runner.py:101 | Function `_compute_bayesian_multipliers` returns `dict` or `{}` but not `None`, callers should guard against empty dict. |

## Retirement Candidates
None
