# APEX Audit Check Registry

**Retirement policy:** a check becomes a candidate when `last_triggered` is more than 90 days ago AND the files it covers have had at least one commit in that window (proving it was actively exercised, not just untouched). Retired checks move to the Retired section — the lesson stays in Claude memory, the check stops running.

Columns: `last_triggered` = most recent date the check found an issue. `last_clean` = most recent date the check ran with no findings.

<!-- REGISTRY — column order and header format are parsed by the audit agent. Do not reorder. -->
| # | Name | Added | Prompted by | Files covered | last_triggered | last_clean |
|---|------|-------|-------------|---------------|----------------|------------|
| 1 | Result-dict sync hazard | 2026-03-25 | gate_decision stored from early snapshot; stale field persisted to DB | backend/gate/gate_runner.py,backend/gate/gate_runner_live.py,backend/db.py | 2026-03-25 | 2026-05-09 |
| 2 | Exception-catch coverage in tests | 2026-03-25 | mock tests asserted return value but not DB write args; bug hid in persistence path | tests/ | 2026-03-25 | 2026-05-09 |
| 3 | Fractional qty in broker | 2026-03-25 | Alpaca rejected bracket orders with fractional shares (error 42210000) | backend/brokers/alpaca.py | 2026-04-07 | 2026-05-09 |
| 4 | Config parity | 2026-03-25 | new keys added to config.py but not to demo_config/live_config | backend/config.py,data/demo_config.json,data/live_config.json | 2026-03-25 | 2026-05-09 |
| 5 | Sector name strings | 2026-03-28 | hardcoded sector name typo caused silent sector mismatch | backend/,frontend/src/ | 2026-03-28 | 2026-05-09 |
| 6 | Test DB isolation | 2026-03-25 | test accidentally opened production DB | tests/conftest.py,tests/ | 2026-04-06 | 2026-05-09 |
| 7 | Demo/live gate runner parity | 2026-03-25 | logic fix applied to demo runner but not live runner | backend/gate/gate_runner.py,backend/gate/gate_runner_live.py | 2026-03-25 | 2026-05-09 |
| 8 | General code health | 2026-03-25 | bare except swallowed errors silently | backend/ | 2026-04-13 | 2026-05-09 |
| 9 | Config value drift | 2026-03-28 | backtest-derived bounds for lock1_threshold/vix_threshold/take_profit_pct ignored after manual edits | data/demo_config.json,data/live_config.json | 2026-04-13 | 2026-05-09 |
| 10 | Ticker signal data coverage | 2026-03-29 | all tickers classified weak due to insufficient days in window; no error raised | backend/db.py,backend/sector_regime.py | 2026-04-06 | 2026-05-09 |
| 11 | NaN/null config pipeline | 2026-04-02 | parseFloat("") = NaN serialized to null; Python .get(key,default) returned None not default; delta<=None threw TypeError | frontend/src/App.jsx,backend/gate/lock1_eligibility.py | 2026-05-09 | 2026-04-07 |
| 12 | Lock3 context parity | 2026-04-06 | regime_bayes_* keys added to demo _build_claude_context but never synced to live _build_context; Lock 3 made live decisions without Bayesian sector data | backend/gate/gate_runner.py,backend/gate/gate_runner_live.py | 2026-04-06 | 2026-05-09 |
| 13 | Undisclosed config change | 2026-04-06 | lock1_threshold and macro_event_blackout_days changed silently inside feature commits; user unaware until audit caught stale value | data/demo_config.json,data/live_config.json | 2026-04-07 | 2026-05-09 |
| 14 | EOD regime freshness | 2026-04-06 | server downtime on April 3-4 meant EOD regime missed multiple days; Bayesian posteriors were stale going into Monday | data/apex.db | — | 2026-05-09 |
| 15 | Calibration freshness | 2026-04-06 | server not running at Sunday 3 AM meant per-sector thresholds missed two consecutive weekly recalibrations; post-crash distributions unrepresented | data/calibration_done.txt | 2026-05-09 | 2026-04-12 |
| 16 | yfinance scalar extraction | 2026-04-06 | newer yfinance returns multi-column DataFrame for single-ticker downloads; .iloc[-1] yields Series not scalar, silently breaking VIX gate | backend/ | 2026-04-06 | 2026-05-09 |
| 19 | Breakout volume floor integrity | 2026-04-07 | BREAKOUT assigned on streak logic alone; EQIX classified as breakout at 0.188 EOD volume (38% of 30d avg) | backend/sector_regime.py,backend/db.py | — | 2026-05-09 |
| 18 | Audit pipeline completeness | 2026-04-07 | verify_llm_findings.py existed for weeks without being called — dead tool caught nothing | audit/ | — | 2026-05-09 |
| 17 | Sentiment cache freshness | 2026-04-06 | rdt-cli output was raw YAML noise until parser was added; a prefetch before the fix would have stored garbage silently | backend/sentiment_prefetch.py,data/apex.db | — | 2026-05-09 |
| 20 | Import path integrity | 2026-04-13 | gate chain restructuring introduced bare `from gate.*` imports — no root-level gate/ package, entire chain failed at import | backend/gate/ | 2026-04-13 | 2026-05-09 |
| 21 | Overflow increment range | 2026-04-14 | new portfolio roof overflow filter uses overflow_quant_increment; misconfigured value (too small or too large) silently breaks the escalating threshold logic | data/demo_config.json,data/live_config.json | — | — |
| 22 | Yahoo data pipeline health | 2026-04-15 | Yahoo Finance 429 rate limit caused all downloads to fail silently; no snapshots written; regime-bayes unavailable after restart because _last_result was in-memory only | data/apex.db,data/regime_result_cache.json | 2026-04-15 | — |
| 23 | Gate chain wiring | 2026-04-18 | chain.py was written for the 2026-04-13 restructuring but never wired — gate_runner.py and gate_runner_live.py still called old lock modules for a week | backend/gate/gate_runner.py,backend/gate/gate_runner_live.py,backend/gate/chain.py | 2026-04-18 | — |
| 24 | Chain-runner wiring integrity | 2026-04-18 | mechanical guard: verify evaluate_chain defined in chain.py, imported in both runners, all 5 lock_evaluate calls present, no retired lock modules re-imported | backend/gate/chain.py,backend/gate/gate_runner.py,backend/gate/gate_runner_live.py | — | — |
| 25 | gate_decision string parity | 2026-04-20 | FILTERED_ELIGIBILITY renamed in _OUTCOMES but all consumers (frontend getLockStates, badge labels, db.py funnel SQL+dict) still checked old FILTERED_MACRO — tickers showed all-green on L1 fail; funnel macro_fail silently zeroed. Extended to cover ticker signal strings (sector_regime.py → frontend signal maps) after breakdown missing from RotationForecast.jsx | backend/gate/gate_runner.py,frontend/src/components/GateFeed.jsx,frontend/src/components/LiveGateFeed.jsx,backend/db.py,backend/sector_regime.py,frontend/src/components/SectorGrid.jsx,frontend/src/components/SectorRegime.jsx,frontend/src/components/Watchlist.jsx,frontend/src/components/RotationForecast.jsx | 2026-04-20 | 2026-05-03 |
| 26 | L1/L2 threshold-source parity | 2026-05-03 | L2 used static config 0.70 while L1 used calibrated ticker_thresholds; caused 100% FILTERED_L1 for a full week | backend/gate/gate_runner.py,backend/gate/gate_runner_live.py,backend/gate/lock2_quant.py | — | 2026-05-03 |
| 27 | GICS sector classification parity | 2026-05-06 | META/V/MA left in pre-2018 GICS assignments; V/MA benchmarked against wrong ETF in Lock 4; META added ad-cycle noise to Technology sector score | data/tickers.json | — | 2026-05-06 |
| 28 | EXCLUDED_SECTORS gate wiring | 2026-05-07 | 2021-2026 threshold sweep found Financials/Utilities/ConsumerStaples destroy capital (PF 0.46–0.89); exclusion wired at L1 and L2 floor; silent unwiring would resume entries with no visible signal | backend/config.py,backend/gate/lock1_eligibility.py,backend/gate/lock2_quant.py | — | 2026-05-07 |

---

## Retired Checks

None yet.

<!-- When retiring a check, move its row here and add a "Retired" column with the date. Keep the row so the history is preserved. -->
