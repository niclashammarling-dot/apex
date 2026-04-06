# APEX Nightly Audit — 2026-04-06
7 issues: 0 critical, 4 warnings, 3 info
*(LLM checks 1, 2, 7, 8 appended below by llm_checks.py)*

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

## Retirement Candidates
None
