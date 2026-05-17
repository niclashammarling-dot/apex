# APEX Nightly Audit — 2026-05-17
12 issues: 0 critical, 11 warnings, 1 info

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
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner.py:50 | `_persist_multiplier_stats` function exists in demo but has no equivalent in live, potentially missing logic for live multiplier stats persistence. |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner.py:100 | `_compute_bayesian_multipliers` function exists in demo but has no equivalent in live, potentially missing logic for live Bayesian multiplier computation. |
| 7 Demo/live gate runner parity | ⚠ | WARNING | gate_runner.py:150 | `_log_summary` function in demo and live have identical logic, but potential differences in logging context or format are not covered by principles. |
| 7 Demo/live gate runner parity | ✓ | — | — | — |
| check_num check_name | ⚠ | SEV | file:line | one-line description |
| 8a Bare except blocks | ✓ | — | — | — |
| 8b TODO/FIXME/HACK comments | ✓ | — | — | — |
| 8c Inconsistent return types | ⚠ | WARNING | gate_runner.py:65 | Function `_compute_bayesian_multipliers` returns `dict` or `{}` but not `None`, callers should guard against empty dict. |

## Retirement Candidates
None
