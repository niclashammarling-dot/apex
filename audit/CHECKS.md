# APEX Audit Check Registry

**Retirement policy:** a check becomes a candidate when `last_triggered` is more than 90 days ago AND the files it covers have had at least one commit in that window (proving it was actively exercised, not just untouched). Retired checks move to the Retired section — the lesson stays in Claude memory, the check stops running.

Columns: `last_triggered` = most recent date the check found an issue. `last_clean` = most recent date the check ran with no findings.

<!-- REGISTRY — column order and header format are parsed by the audit agent. Do not reorder. -->
| # | Name | Added | Prompted by | Files covered | last_triggered | last_clean |
|---|------|-------|-------------|---------------|----------------|------------|
| 1 | Result-dict sync hazard | 2026-03-25 | gate_decision stored from early snapshot; stale field persisted to DB | backend/gate/gate_runner.py,backend/gate/gate_runner_live.py,backend/db.py | 2026-03-25 | 2026-04-06 |
| 2 | Exception-catch coverage in tests | 2026-03-25 | mock tests asserted return value but not DB write args; bug hid in persistence path | tests/ | 2026-03-25 | 2026-04-06 |
| 3 | Fractional qty in broker | 2026-03-25 | Alpaca rejected bracket orders with fractional shares (error 42210000) | backend/brokers/alpaca.py | 2026-04-06 | — |
| 4 | Config parity | 2026-03-25 | new keys added to config.py but not to demo_config/live_config | backend/config.py,data/demo_config.json,data/live_config.json | 2026-03-25 | 2026-04-06 |
| 5 | Sector name strings | 2026-03-28 | hardcoded sector name typo caused silent sector mismatch | backend/,frontend/src/ | 2026-03-28 | 2026-04-06 |
| 6 | Test DB isolation | 2026-03-25 | test accidentally opened production DB | tests/conftest.py,tests/ | 2026-04-06 | 2026-04-06 |
| 7 | Demo/live gate runner parity | 2026-03-25 | logic fix applied to demo runner but not live runner | backend/gate/gate_runner.py,backend/gate/gate_runner_live.py | 2026-03-25 | 2026-04-06 |
| 8 | General code health | 2026-03-25 | bare except swallowed errors silently | backend/ | 2026-04-06 | 2026-04-06 |
| 9 | Config value drift | 2026-03-28 | backtest-derived bounds for lock1_threshold/vix_threshold/take_profit_pct ignored after manual edits | data/demo_config.json,data/live_config.json | 2026-04-06 | — |
| 10 | Ticker signal data coverage | 2026-03-29 | all tickers classified weak due to insufficient days in window; no error raised | backend/db.py,backend/sector_regime.py | 2026-04-06 | 2026-04-06 |
| 11 | NaN/null config pipeline | 2026-04-02 | parseFloat("") = NaN serialized to null; Python .get(key,default) returned None not default; delta<=None threw TypeError | frontend/src/App.jsx,backend/gate/lock_macro.py | 2026-04-06 | 2026-04-06 |
| 12 | Lock3 context parity | 2026-04-06 | regime_bayes_* keys added to demo _build_claude_context but never synced to live _build_context; Lock 3 made live decisions without Bayesian sector data | backend/gate/gate_runner.py,backend/gate/gate_runner_live.py | 2026-04-06 | 2026-04-06 |
| 13 | Undisclosed config change | 2026-04-06 | lock1_threshold and macro_event_blackout_days changed silently inside feature commits; user unaware until audit caught stale value | data/demo_config.json,data/live_config.json | 2026-04-06 | — |
| 14 | EOD regime freshness | 2026-04-06 | server downtime on April 3-4 meant EOD regime missed multiple days; Bayesian posteriors were stale going into Monday | data/apex.db | — | 2026-04-06 |
| 15 | Calibration freshness | 2026-04-06 | server not running at Sunday 3 AM meant per-sector thresholds missed two consecutive weekly recalibrations; post-crash distributions unrepresented | data/calibration_done.txt | — | 2026-04-06 |
| 16 | yfinance scalar extraction | 2026-04-06 | newer yfinance returns multi-column DataFrame for single-ticker downloads; .iloc[-1] yields Series not scalar, silently breaking VIX gate | backend/ | 2026-04-06 | 2026-04-06 |

---

## Retired Checks

None yet.

<!-- When retiring a check, move its row here and add a "Retired" column with the date. Keep the row so the history is preserved. -->
