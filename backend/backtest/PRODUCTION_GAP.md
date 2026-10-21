# Backtest ↔ production gap inventory

**Written 2026-09-16.** One-time, bounded. Every row cites the line that
establishes it.

Both `optimizer.py` (line 281-282) and `objective_walkforward.py` (line 45)
run on `engine_fast.py`. `engine.py` is the slow original with the same
imports (`engine.py:98-114`) and one extra option the fast engine lacks
(row 11). So every conclusion drawn from the sweep, the optimizer's
200 experiments, the 2026-09-15 walk-forward (C1/C2, SPY comparison, "does the
search add value over the incumbent") was measured on the system described
in the MODELLED column below, not the live one.

Found because the entry-floor investigation (CHECK 71, 2026-09-15) went
looking for one lever and it wasn't there. Nobody knew where the boundary
sat; this file is the boundary.

## Live gate path (what production runs per candidate)

`gate/chain.py` order: L1 eligibility → penalties → L2 quant → L3 sentiment →
L4 leading → L5 Claude → Bayesian size scale → sector cap → execute.
Signal score is computed upstream in `data/fetcher_yahoo.py:127-136`.

| # | Production component | Where (live) | In `engine_fast.py`? | Note |
|---|---|---|---|---|
| 1 | Signal aggregator, 4 signals | `fetcher_yahoo.py:127`, `signals/aggregator.py:17` | **yes** `:642` | same function |
| 2 | Regime-conditioned aggregator weights (`regime_state` bull/neutral/bear from `RegimeBayes.market_regime_state`) | `fetcher_yahoo.py:132`, `aggregator.py:10-14` | **closed under `regime_layer=True` only** (2026-10-17) — cache stores the components (`:738`), `_regime_score` re-weights them with bucket(t−1) (`:762`, `:250`); flag off = neutral weights, as before | trend weight 0.35 (bull) / 0.30 (neutral) / 0.25 (bear); RS 0.30/0.25/0.20 |
| 3 | Sector ETF regime multiplier on score | `fetcher_yahoo.py:135-136` | **yes** `:629,:646` | same formula |
| 4 | Position size | live: `position_size_pct` from Lock 5, told to use `risk_limits.max_position_size` "unless a specific portfolio risk factor warrants reduction" (`lock5_claude.py:66`), then the row 20 Bayesian scale, then `available_cash × min(pct, max_position_size)` (`gate_runner_live.py:630-632`) | **no** (corrected 2026-10-18) — the engines size by `kelly_size` (`fetcher_yahoo.py:137` formula, `:695`); live computes Kelly but `kelly_size` has 0 hits in `backend/gate/`, the tracker and the broker (control: present in `fetcher_yahoo.py`) | Was "yes, same formula": the formula is the same, but live never sizes by it. They land near each other only because Kelly's B is the 05-07 backtest constant 0.077/0.057 (close-only exits; R8 removed that basis) and its p reads the demo `trades` table, which puts Kelly near the cap. Live notional measured: Sep mean $851, Oct $863 (about 9 % of an account near $10,000, estimated). Size cell in the battery (ruled 10-18): live L5-max, Kelly constant B, Kelly realised B, fixed fraction |
| 5 | L1-A: sector must have Bayesian `allocation > 0` (hysteresis enter ≥ 0.37 / exit < 0.33 on `aggregate_score × posterior`, posterior clamped [0.05, 0.95]) | `lock1_eligibility.py` docstring, `regime_bayes.py:47-48, 68-69, 466-470` | **closed under `regime_layer=True` only** (2026-10-17) — `allocation(t−1) > 0` filter in `_get_candidates_from_cache` (`:888`); flag off = only `EXCLUDED_SECTORS` | the CHECK 71 finding: today 8 of 11 sectors cannot enter live; the backtest lets all 11 in |
| 6 | L1-B1: VIX threshold | `lock1_eligibility.py` | **yes** `vix_threshold` (`:229`) | |
| 7 | L1-B2: macro event blackout (FOMC/CPI/NFP window) | `lock1_eligibility.py` | **yes** in both engines since 2026-10-17 — `backend/macro_calendar.py` (official FOMC/BLS dates 2015–2027, shared rule `macro_status`); engine_fast `:290` | the divergence that first tripped CHECK 76 layer 3 |
| 8 | L1-B3: earnings within N days | `lock1_eligibility.py` | **off-both** (2026-10-18) — live's `_get_earnings_date` tests `cal.empty` on yfinance 0.2.66's dict `.calendar`, raises AttributeError, logs at debug, returns None: 0 earnings fails in `live_gate_history` against 1,278 macro fails (control). The engines' `earnings_filter_days` reads `.earnings_dates`, which needs `lxml` (not installed); `_fetch_earnings_dates` swallows it and returns empty sets, so the option was a no-op | Live fix in shadow mode first (ruled 10-18, INDEX). Before `lxml` is installed the signal cache key must hash the fetched earnings sets, or every result changes with no code change. Row 9's test must fail on an empty set |
| 9 | Earnings near-term *penalty* tiers on score | `chain.py:116-124` | **off-both** (2026-10-18) — same dead source as row 8: 0 of ~20,000 live and 0 of 17,574 demo gate rows since June carry `days_to_earnings` (about 15 % expected at 14 days) | engine is skip-or-pass, no penalty band. Built only after the live fix and a historical date source; battery cell on vs off pre-registered (ruled 10-18) |
| 10 | Macro pre-event penalty on score | `chain.py:126-134` | **yes** in both engines — `macro_pre_event_penalty` (live 0.05, passed by the profit read since 2026-10-17); off when `None` | |
| 11 | ETF negative penalty (sector ETF 5d return below floor) | `chain.py:156-182` | yes in both engines since 2026-10-18 — `live_shape.etf_penalty_fails` / `etf_return_as_live`, `etf_negative_penalty` (None = off, as `engine.py` had it; the profit-read incumbent and CHECK 76 layer 3 pass live's 0.15 / -1.0) | Ported as live runs it, not as the 07-17 sweep modelled it: the return spans 7 intervals (live downloads `period="8d"`, 8 rows measured 10-18; `engine.py` used 5), the percent is rounded to 2 decimals before the floor test, and the penalty gates only (live's final_score is L4/L5's; `engine.py` carried the penalised score on). Live drops rows where the ticker or the ETF is missing jointly; the engines read the ETF alone. Before/after on the CHECK 76 window at live values: 3 entries blocked, 44 trades either way. The 0.15 rests on the 07-17 sweep run on the NaN-poisoned `_etf_regime_on` (CHECK 76 finding c): its own pre-registered cell in the rerun battery (ruled 10-18) |
| 12 | L2 threshold: calibrated per-sector `ticker_thresholds` from DB (weekly `recalibrate`), floored by `SECTOR_THRESHOLD_FLOORS`, fallback `LOCK1_THRESHOLD` | `lock2_quant.py:91-110`, `ticker_threshold_calibration.calibrate` | **partial** — single `lock1_threshold` floored by `SECTOR_THRESHOLD_FLOORS` (`:690`) until the as-of build (ruled 10-18, option b) | Premise corrected 10-18: `ticker_thresholds` keeps no history (sector is the primary key, overwritten weekly), but `ticker_history` reaches 2000-01-03 (292,764 rows) and live's `calibrate()` takes max(p80, outcome threshold) over all of it, so the weekly threshold is reconstructible as-of. Its rows mix two scorers: the `sector_backfill` rows from `engine._score_all_tickers` (version unknown, likely before the 09-16 fixes) and live's signals since go-live; live's 0.54–0.76 rest on that mix today. Before the as-of build (INDEX, 10-24): can `ticker_history` be recomputed once with today's scorer, so the calibration reads clean history? Fallback inconsistency noted: `_sector_threshold` falls back to config 0.70, the overflow path to live config 0.65 |
| 13 | L2 pre-rotation discount (named `on_watchlist` / `WATCHLIST_DISCOUNT`) | `gate_runner_live.py:510-535, 561, 759`; `lock2_quant.py:26` | **partial** — reconstructible from 2021-10-18 via `sector_snapshots` (1,259 days), not before; not built | Corrected 2026-10-18: the gate's `on_watchlist` is `signal["pre_rotation"]`, not the watchlist table (read only by `GET /watchlist` and `sentiment_prefetch`). Pre-rotation: sectors in `get_rotation_forecast()["watching"]` (transition matrix learnt from `sector_snapshots`, as-of needed against look-ahead) add candidates at threshold x 0.85 (`PRE_ROTATION_FLOOR`), Lock 2 discounts their threshold 15 %, and execution sorts them after confirmed candidates. The sort is deliberate (`ad1b134` 2026-04-06: "Regular (confirmed) candidates always execute before pre-rotation candidates"); floor and discount from `a6de8cf` 2026-03-29. Rename after class 1 |
| 14 | Overflow slots above `max_positions` at raised threshold (`overflow_quant_increment`) | `gate_runner_live.py:566-627` | yes in both engines since 2026-10-18 — `live_shape.overflow_admits`, `overflow_quant_increment` (None = live 0.05; `math.inf` = the old hard stop) | Ported as the code does it, not as the name `max_positions` promises: live has no hard cap (`config.py:115-118`); slot n over the roof needs the raw signal score (before the macro-eve penalty, as live reads `signal["signal_score"]`) to clear the sector threshold x (1 + n x 0.05), and a rejection moves on to the next candidate. The engines' hard stop was the more restrictive variant, so every result before this is directionless on this axis until the rerun (ruled 10-18). Before/after on the CHECK 76 window at the engine default roof: 44 → 66 trades, max concurrent 4 → 7. Base threshold is the engine's own L2 threshold (row 12 partial) |
| 15 | L3 Grok sentiment, fail-closed, circuit breaker | `lock3_sentiment.py` | **no** | external, not reconstructible historically |
| 16 | L4 leading: RS vs sector ETF | `lock4_leading.py` | **optional** `use_leading_rs` (default False, `:250`) | off in optimizer/walk-forward unless passed Window differs (found 2026-10-18): live's RS compares last / first of an 8-row download (7 intervals), `_build_rs_cache` uses `pct_change(5)` |
| 17 | L4 leading: put/call ratio, unusual calls, volume accumulation (2 of 4 must pass) | `lock4_leading.py` | **no** | options data not reconstructible; volume accumulation could be |
| 18 | L5 Claude synthesis (fail-closed, sonnet only) | `lock5_claude.py` | **no** | not reconstructible |
| 19 | L5 context: rotation forecast (`sector_transitions.get_rotation_forecast`) | `chain.py:325-341`, `gate_runner_live.py:403` | **no** | |
| 20 | Bayesian sector size scale on `position_size_pct` post-L5 | `gate_runner_live.py:596-604`, arithmetic `RegimeBayes.size_multipliers` (shared since 2026-10-17) | **closed under `regime_layer=True` only** (2026-10-17) — same function over the day's queued set (`:284`), applied as live (`:309`) | n·s/Σs: the allocation level cancels (2026-10-16 pre-work finding 2); queued set = one decision a day vs live's per-cycle set |
| 21 | Dynamic sector caps (`compute_dynamic_caps`: score_w × duration_w × bayes_w, clamped [MIN_MULT, MAX_MULT]) | `sector_caps.py:51-93`, `gate_runner_live.py:399,526,566` | **closed under `regime_layer=True` only** (2026-10-17) — `sector_caps.dynamic_caps_from` (the live wrapper's pure core) per day (`:845`, `:314`); flag off = flat `MAX_SECTOR_EXPOSURE` | streaks from the engine's own close snapshots over 365 days vs live's daily average of intraday snapshots |
| 22 | Daily loss cap | live cfg `daily_loss_cap` ($500 on $10,000) | **yes since 2026-10-17** — 5 % of the starting balance (`live_shape.DAILY_LOSS_CAP_PCT`), both engines, overridable by `daily_loss_cap` | **Corrected 2026-10-17.** Read "yes" since 09-16 on the presence of a cap, not its value: config's $100 (the demo's 5 % of $2,000) on the engine's $10,000 = 1 %, five times stricter than live. Equity-based cap: Saturday 10-24 question |
| 22b | Sector exposure cap base | live cfg `max_sector_exposure` 0.30 (`gate_runner_live.py:504`; set by hand, not tested — Niclas 10-17) | **different default** — config `MAX_SECTOR_EXPOSURE` 0.25; both engines take `max_sector_exposure`, the profit read passes live's 0.30 | a sector-cap sweep is filed: the value should follow the profit evidence |
| 23 | SL / TP re-entry cooloff per ticker | `gate_runner_live.py:397-400` (`get_recently_exited_live_tickers`, `exit_cooloff_hours` 24 after SL, `tp_cooloff_hours` 168 after TP, `data/live_config.json:21-22`; since `7fff25e` 2026-05-29, split `109e7be` 2026-06-03) | **yes, live values by default since 2026-10-17** — both engines resolve `None` through `live_shape.live_cooloff_days()` (hours rounded up to days: 1 / 7; `engine_fast.py:202`, `engine.py:283`). Until then: 5 / 0 defaults, and `optimizer.py` / `objective_walkforward.py` passed neither. **Window per exit as live since 2026-10-18:** `live_shape.reentry_cooloff_days` mirrors `db.get_recently_exited_live_tickers` (TP/TSL wins → TP window, every other exit incl. TIME/EXPIRED → SL window), read by both engines; until then both set no cooloff after a TIME exit and gave a TSL win the SL window when the TP window was 0. No-op on the CHECK 76 window (no TIME exits); test `tests/test_reentry_cooloff.py` fails on `8177535` | **Corrected 2026-10-16.** Written 09-16 as "engine-only, 0 hits for cooldown/reentry": the search missed live's term (*cooloff*) and had no positive control. Real divergence: SL 5 d vs 1 d, TP none vs 7 d — sign on profit unknown. Every optimizer and walk-forward run since June, including the 09-16 Sharpe-cap case 1 (`ba1cd49`), ran with the engine values. Fix: the regime port reads both values from live config. |
| 24 | Exits: TP / SL / time stop / TSL / profit-lock ratchet | `gate_runner_live.py`, `_check_missed_live_exits` | **yes** `_check_exits_fast` (`:706`) | exit-semantics defect fixed 2026-08-17/18 in both. Live-side evidence for the stop model (master, 2026-10-21): CHECK 93 measures live stop fills on R7's own definition (a gap is a fill within 5 min of the open with the open at or below the stop, measured against the open) and reproduces R7 on its own fills (intraday n 37, +0.02 %; gap n 7, +0.49 % against 0.50 %); only fills from 2026-10-21 13:30 UTC count as forward evidence, and the INFO line counts them per class. The append-only `stop_ratchet_log` records every confirmed live stop move from the first market window after `7f9db01`, so the ratchet's path is no longer only in the broker's leg history |
| 24b | Take-profit leg intraday (bracket limit fills when price trades through it; gap-up fills at the open) | `brokers/alpaca.py:262-264` (`TakeProfitRequest`) | yes since 2026-10-18 — R8 in `exit_rules.check_intraday_bracket`, both engines (`intraday_tp=True` default; `tp_first` is the both-legs ceiling) | added to this list 2026-10-17; Niclas 09-25: symmetric TP before the cost term. Before/after in EXIT_PARITY.md § R8 |
| 25 | ATR-based exits | — | **engine-only** option `atr_exits` (`:269`) | 0 hits for an ATR exit/stop in `gate_runner_live.py` or `alpaca.py`; off by default, so inert unless a sweep passes it. `atr_exit_mult` (engine_fast only, 2026-10-21, T8's ATRSYM): the (TP, SL) multiples, default (2, 1) = engine.py's rule; CHECK 76 accepted set |
| 26 | Corroboration gate on exit cancellation, OCO re-protection | `alpaca.py`, `gate_runner_live.py` | n/a | execution layer, no backtest analogue by design |
| 27 | Sector universe | `ticker_config.get_sectors()` | **yes** (`:126`, fixed 2026-08-18) | parity since the stale-universe fix |
| 28 | Regime layer IPO input (EDGAR IPO share per sector) | `regime_bayes.py` (live EDGAR feed) | **partial** — uniform `REGIME_IPO_NEUTRAL_SHARE` (LR 1.0); no historical feed | Healthcare the main fidelity gap (14/43 identical allocation sets, 2026-10-17) |
| 29 | Sector snapshots from intraday polls | `sector_snapshots` (daily average of intraday polls) | **partial** — the engine's own close snapshots | feeds row 21 streaks and the regime scores |
| 30 | Decision cadence: many gate cycles per session | `gate_runner_live.py` scheduler | **partial** — one decision a day at the close | the row 20 queued set and intraday entry prices differ |
| 31 | Stop fill slippage | Alpaca market fill after the stop triggers | **yes** since 2026-10-17 — R7 (`exit_rules.py`, measured on 44 fills) | commission/fees ignored (Niclas 10-17) |
| 32 | REGIME exit: sector off the Bayesian leaderboard AND the ticker down `TICKER_RECOVERY_DAYS` (5) closes in a row → close at market | `live_trades_tracker.py:668` (`check_live_regime_exits`, every 5-min exit cycle) | yes by construction (2026-10-18) — cannot fire in the engines: the leaderboard keeps the top `LEADERBOARD_SIZE` (12) of 11 active sectors, so a held sector is off it only when moved to `EXCLUDED_SECTORS` while held, and the engines never enter an excluded sector | Live's one REGIME exit (−$1,524.39) was SO (Utilities): entered 2026-04-10, Utilities excluded 05-07 (`2bf88b1`), closed 05-08, a config transition and not a ranking drop. Not ported, so no dead code. `tests/test_regime_exit_noop.py` fails the day the active universe outgrows the board (control: board 10 fires); then port it, evaluated at the close on raw closes (live evaluates intraday) |

## The regime layer in the engine (`regime_layer=True`, 2026-10-17)

`build_regime_timeline` (`engine_fast.py:769`) computes it once per precompute
with `RegimeBayes.step` (the live code, `persisted=False`): state(t−1) →
bucket(t−1) → scores(t) → snapshot(t) → step(t). Decisions on t read the regime
from t−1 and the snapshot from t. The flag is off by default, so every existing
result stays reproducible and CHECK 76 layer 3 runs with it off. Rows 2, 5, 20, 21
are closed **under the flag only**; a default run still has no regime layer.
Known differences from live, none of them one-signed:

- IPO uniform: share `REGIME_IPO_NEUTRAL_SHARE` = LR 1.0 (no historical EDGAR feed;
  a guard test fails if `backend/backtest/` touches `IpoSentiment`).
- Snapshots from the close (mean of the day's cached scores per sector); live uses
  the last intraday poll before 16:15 ET. RSI-blocked tickers have no cache entry,
  so they are not in the engine's snapshot mean.
- One decision a day against many live cycles (the row 20 queued set).
- Warm-up: cold start (live's uniform fallback) on the precompute lookback, at least
  `REGIME_WARMUP_MIN_SESSIONS` scored sessions before the first day, else it refuses.
- OHLCV per step: 90 calendar days ending t (the `as_of` path); live's same-day
  path downloads `period="60d"`.
- Transition priors are not passed (inert live, 2026-10-16 pre-work finding 1).
- The timeline is not persisted, so no cache key covers the regime files; the signal
  cache key already changes with `engine_fast.py`.

## What class of question the backtest can answer

**Can answer:** signal-formula questions under neutral weights; exit-rule
questions (24); position-count and per-position sizing questions *given*
that every non-excluded sector is always enterable; universe questions.

**Cannot answer (flag off):** anything that runs through the Bayesian regime layer
(2, 5, 20, 21) — which is the layer that decides *which sectors* are open on
a given day. Live, that layer closed 8 of 11 sectors on 2026-09-15; the
backtest never closes any. Any "too few positions" question is therefore
outside the backtest's scope by construction, and any tunable whose effect
is mediated by sector breadth (`max_positions`, `max_sector_exposure`) is
measured here on a breadth the live system does not have.

**Every absolute figure is an upper bound:** the universe is today's tickers (survivorship and look-ahead in one choice; 2026-10-21 overview, Hou, Xue & Zhang). Relative questions on the same universe (arm against arm, R2 against R1) largely cancel it; a point-in-time universe is the fix (scope note 2026-10-31).

**Answers with a caveat:** L2 threshold questions (12) — the backtest's
static threshold is a stand-in for a weekly-moving one; the sweep that set
`SECTOR_THRESHOLD_FLOORS` is a floor on the live value, not the live value.

## Read on the 2026-09-15 walk-forward

The hold decision stands — it was reached on the harness's own terms and
nothing here would have flipped a hold to a ship. But the scope caveat
applies to every conclusion from it: C1's noise floor, C2's fold margins,
the SPY comparison and the "top-3 collapse is adjacency, tunables bind"
reading are all statements about a system with rows 2, 5, 7, 9-11, 13-15,
17-21 absent and row 23 on different values (corrected 2026-10-16). The "tunables have low leverage over the
trade path" finding in particular was measured with the highest-leverage
upstream gate (row 5) removed.

## Engine vs engine — asserted by CHECK 76

The two engines are documented as drop-in equivalents (`engine_fast.py:9`)
and were not. `audit/checks_code.py::check76` holds `raw_data` constant,
neutralises the accepted slow-only divergences, and requires identical trade
logs. Accepted divergences (edit the check's lists and this file together):

- `run()` slow-only: `etf_negative_floor`, `etf_negative_penalty`,
  `macro_hard_block`, `macro_pre_event_penalty`; fast-only: `precomputed`,
  `regime_layer`, `random_entries` (2026-10-17; both off by default, layer 3 runs with them off;
  `random_entries` is the profit read's RND placebo, not a production component).
- `engine.py:330` macro block is unconditional on event days; `engine_fast`
  has no macro calendar.

Fixed 2026-09-16 on the check's first run (all undocumented until then):
SPY `return_20d` off-by-one in the fast engine (aligned to production's
19-period definition, not to the docstring's 20); NaN-poisoned ETF MA20 in
the slow engine; signal-cache key with no code version (stale `sig_*.pkl`
served across a signal edit). Every optimizer and walk-forward run before
that date used the first and third; the 2026-07-17 ETF-penalty sweep, on the
slow engine, ran with the second (see apex-moc Open Territory).

## Not a defect list

Rows 15, 17 (options), 18 are unreconstructible and their absence is a
design boundary, not a gap to close. Rows 2, 5, 20, 21 are one component
(`RegimeBayes`) and closing them is one build: wire `RegimeBayes` into the
engine's day loop with OHLCV-derivable signals — `floor_reconstruction.py`
and `regime_conditioned_cs.py` already do that replay standalone. That build
is the prerequisite for putting the entry floor through the harness
(CHECK 71 follow-up); until it lands, the harness returns identical trade
paths for any floor value, trivially. **Built 2026-10-17** as `regime_layer=True`
on `RegimeBayes.step` — the live code, not a third replay copy. The two standalone
replays above still carry their own copies of the loop (untested; repoint or retire
is a bundle item, not done here).

Row 25 runs the other way: the backtest enforces a rule production does
not. Row 23 was listed here too until 2026-10-16. Production does have a
re-entry cooloff, with different values (SL 1 d / TP 7 d against the
engine's 5 d / none). The 09-16 search for "cooldown/reentry" never ran
on live's word, "cooloff", and no known-present term was searched as a
control. An absence claim on this file needs a positive control on the
identical search.

## Signal gaps — hypotheses (Niclas 2026-10-19)

**Frozen 2026-10-21: moved to master `backend/backtest/SIGNAL_REGISTER.md` (Niclas: option a). Edit S-rows there, never here; this copy is removed at the merge.**

"Alla glapp måste in i production gap, testas och antingen slängas eller antas."
These are not live components the engine lacks (the numbered rows above); they are
information APEX does not use, or uses without a measured contribution. Ids are
`S`-prefixed so `gap_bounds` never reads them as unmodelled rows. Each is closed by
a battery cell with a rule written before its run: **adopted** (into live and the
engine together) or **discarded** (with the cell's verdict). Data availability is
the first gate: a hypothesis without history cannot be backtested and goes to a
shadow book instead. Facts as of 2026-10-19, read from code.

**Class rule for every S card (ruling 2026-10-21; vault `2026-10-21-apex-preregistration-class-by-effect-window.md`):** a class is defined by the window its hypothesised effect occupies, stated before the class is coded, together with what else the measured return contains (card template item 2b; defect D7 "mixed class"). Three mixed classes were found in two days: CHECK 93's gap class (10-20), S13's FOMC_PRE (10-21), and S13's EVE (10-21, caught before code). Results carry their labels into every citation: a pre-registered "unanswered" stays "unanswered", and a look taken after the run is "exploratory" with its producer commit.

| Id | Hypothesis | Information | Data for a backtest | Status |
|---|---|---|---|---|
| S1 | The aggregator's four components are one signal, not four: momentum, trend, RS and volume are all price/volume (`aggregator.py:13-16`) | redundancy measurement | in the signal cache (have) | open — correlation of the components, then each one's marginal contribution. **Ruled 2026-10-21** (proposal (a) from Niclas's overview, vault `2026-10-21-apex-from-theory-to-application-mapped-to-the-ladder.md`): per-component rank IC at L0 (T1 design with its injection control), momentum, trend, RS and volume each alone, h 1/5/10/25; card committed before the run, slot 2026-10-23. It tests whether any part of L1 ranks direction when the combined score does not (S11), and answers Niclas's 10-21 question "how can we exclude even more losers and let through more winners" at the first lock |
| S2 | Each backtestable lock adds to the chain more than the breadth it removes (fundamental law: IR ≈ IC × √breadth) | ablation per lock: L1 parts, ETF penalty, regime layer, L4 RS (row 16), L4 volume accumulation (row 17) | engine (have; row 17's volume part to build) | open |
| S3 | Earnings information predicts the entry: surprise drift after the report, the pre-report premium | earnings dates and EPS surprise | historical dates/EPS 2015–2026 not in the repo (row 9) | open — data inventory first |
| S4 | Options-implied information predicts the entry: IV skew/smirk, put/call, unusual calls | options history | none in the repo; L4 options parts live-only (row 17) | open — data inventory first |
| S5 | Short interest and positioning predict the entry | short interest history | not in the repo | open — data inventory first |
| S6 | Profitability/quality separates winners within a sector | fundamentals history | not in the repo | open — data inventory first |
| S7 | Momentum-crash conditioning (after market drawdowns, high volatility) protects the sector rotation | market state from prices | have | open |
| S8 | Breadth: more open sectors/positions raise IR more than the locks that close them cost (CHECK 71: 8 of 11 sectors closed 2026-09-15) | breadth measurement | engine (have) | open |
| S9 | L3 sentiment (analyst consensus, short interest, news, X via Grok) adds to the chain | LLM over text | not backtestable (model look-ahead) | open — shadow book: forward returns of candidates L3 rejected vs passed. **T7 2026-10-20** (vault `2026-10-20-apex-rejected-candidate-card.md`): unanswered (D4). Entered minus sentiment-rejected was −7.2 pts (p 0.88), but 19 of the 32 rejected ticker-days are MRNA, rejected through its vaccine-approval news before it more than doubled. Without MRNA (post hoc) 12 of 13 rejections lost, p 0.01: a hypothesis only. The next version clusters by ticker-event |
| S10 | L5 (Claude) adds to the chain | LLM | not backtestable | open — shadow book as S9. **T7 2026-10-20:** unanswered (D4; MDE about 2.7 pts against 1 pt relevant). Entered minus L5-rejected was −0.36 pts (p 0.47); rejected candidates won 70 % against entered 61 %. No sign that L5 selects; n 57 vs 44 cannot tell |
| S11 | The score alone ranks direction: the engine's aggregator score, which the battery and the ladder test, picks tickers that go up | L0 rank IC and same-day volatility-matched deciles | signal cache 2015–2026 (have) | discarded 2026-10-20 (ladder T1/T2, vault `7f89f49f` / `fe859dfa`, ruling 10-20): rank IC −0.003 to −0.035 at 1–25 days, IS and OOS; picks earn what same-ATR peers earn and move 12–15 % more (t 13.5 IS / 9.2 OOS). The score alone is a volatility selector, and a go-live case cannot rest on it as a direction predictor. The live chain (score plus sentiment, leading, L5, freshness, caps) is untested until T7 reports (vault `2026-10-20-apex-rejected-candidate-card.md`; S9/S10 shadow book). **Peaks (T4 archived MFE, read 2026-10-21):** picks reach a 6 % peak within 25 days as often as same-day vol-matched non-picks (IS 56.7 vs 55.5 %, OOS 41.7 vs 44.0 %). The live chain does select: on its own days live won 64.9 % against 54 % for same-exit random entries (R9, p 0.04, 1,000 draws), so the selecting part sits after the score or in timing (S12) |
| S12 | Entries decided pre-open on the prior close and filled at the open select direction (live class `same_day_pre_open`) | the score at close d−1 against the return from the open of d | engine L0 over 2015–2026 (have: closes and opens in the caches); live: 15 trades in `db.LIVE_SAMPLE` | open — hypothesis for an L0 card. Post hoc, one of about eight looks (2026-10-20): live 67 % wins against 37 % for same-day vol-matched random draws, mean +0.98 % against −1.70 % (p 0.02 / 0.025). Finding 1 (`gate/freshness.py`) now excludes this class from live. If supported, the attached design is to decide pre-open and execute after the open, with live anchors |
| S13 | The macro blackout (live Lock 1 B2; engine rows 7/10) blocks entries placed for a documented calendar return: the pre-FOMC drift (Lucca & Moench 2015) and the announcement-day premium (Savor & Wilson 2013); read 2026-10-21 (vault `2026-10-21-apex-sources-lucca-moench-savor-wilson-kurov.md`): Kurov, Wolfe & Gilbert (2021) find the drift disappeared after 2015, so the IS window lies entirely after it; S&W's inflation days are PPI, not CPI | L0: the pool's forward return from the entry close of FOMC_PRE / FOMC_DAY / RELEASE / EVE sessions against CLEAR, Newey-West, Holm; then the R1 card at L2: blocked-day candidates against the same-day vol-matched set, isolated-trade outcomes as if a slot were free (the 10-11 candidate ledger) | engine caches 2015–2026 (have); macro_calendar 2015–2027 (have) | open, ruled 2026-10-21. Trigger: the 7C ablation (reverting rows 7/10 lifted IS R1 start 0 to p 0.007), which repeats the 10-19 gate's IS reading; OOS went the other way there (R2 on better 20/20 starts, by one trade). Two channels kept apart: macro timing is S13, the slot cascade (1,065 of 2,220 entries differ) is CHECK 71's capacity frame. L0 pre-registered (vault `2026-10-21-apex-s13-macro-blackout-l0-preregistration.md`); no live change rests on L0. **L0 result 2026-10-21** (`f3d34bb`, controls passed): every class unanswered at this power; all four IS h=1 estimates negative (−0.03 to −0.13 %), the reading that a Lucca & Moench-sized drift is excluded was withdrawn the same day: FOMC_PRE mixes the session before the FOMC day with the one before that, and only the first spans the pre-announcement window; the window is read from the paper before the R1 card. **Reading (ruling 2026-10-21):** the IS window probably holds no pre-FOMC drift (8.9 bp, not significant, 2011–2018, Hu et al.), so the blackout question moves onto R1's picks and the slot cascade. Next: the R1 card (L2, candidate ledger), after the exits card, with classes PRE_NEXT_IS_FOMC (h 1), PRE_EARLIER (h 2), FOMC_DAY, RELEASE, EVE_CPI, EVE_NFP (EVE split: CPI eve close +0.02 bp, NFP +12.9 bp, 1994–2018), a 2015 split for FOMC classes, and drawdown and dispersion on blocked days beside the mean; no policy-uncertainty split (power) |
| S14 | The VIX gate (live Lock 1, `vix_threshold` 30; engine `vix_ge` `7cfbb8b`) blocks entries placed for the heightened-VIX premium: after VIX closes ≥ 3 points above its recent average, the S&P earned 48 bp the next day (Hu, Pan, Wang & Zhu 2019; 1986–2018) | L0: the pool's forward return (h 1 / 5 / 10) from JUMP_ONLY / LEVEL_ONLY / BOTH closes against NEITHER, Newey-West, Holm per horizon, with SD, 5th percentile and drawdown share beside the mean; h 1 is the literature's quantity, h 5/10 APEX's | engine VIX closes and price caches 2015–2026 (have) | open, ruled 2026-10-21; L0 pre-registered (vault `2026-10-21-apex-s14-heightened-vix-l0-preregistration.md`), run date not ruled. IS counts: 88 jump, 149 level, 36 both (jumps cluster in 2020 and 2022); OOS level class about 4 closes, so its sign check is uninformative. Expected verdict at h 1: unanswered (MDE ≈ 0.65–1.2 % against 48 bp). No live change rests on L0. **L0 result 2026-10-21** (`0eaf75d`, controls passed; vault `cca4fff0`): unanswered; Hu et al.'s one-day premium not detected after jumps below 30 (CI tops at +0.41 %). Reading: after level closes, which the gate blocks, the pool earned +1.6 % over 5 days and +2.4 % over 10 days more than after ordinary closes (Holm 0.13), with two to three times the spread and more drawdowns; without the 2020 crash episode, +1.8 / +2.8 % with CIs clear of 0, and SPY agrees (exploratory `b294a11`, 19 episodes). Labels travel with every citation: "unanswered at the pre-registered power"; the ex-2020 figures are "exploratory, `b294a11`". **Ruled 2026-10-21:** the candidate ledger is built once, and S14's R1 card on VIX ≥ 30 days is read before S13's. Design: alpha primary with Calmar beside it; n = 19 episodes with episode resampling; live exits, not fixed holds; the 10-16 COVID fold (2020-02-03 → 06-30) as its own row. No live change |
| S15 | The exit is half the strategy: live's static bracket (TP 6 %, SL 6 %, lock 4/1 %, 25 days) is not at the win-rate/return sweet spot (Niclas 2026-10-21: "one of a pair that decides win rate and win size. Here exists a sweet spot, whether it's static or flexible is to be determined") | the exit rule applied to the same entries | engine exit function, T4 grid of 45 exits (have); live's 57 canonical entries (have) | open — **T8, amended and ruled 2026-10-21, code 10-22, read 10-24** (vault `2026-10-20-apex-exits-card.md` § Amendment). Facts read from T4's archive (polled peak, vol-matched non-picks): the bracket's win rate decomposes as 50 % (symmetric, no drift) → ~56 % (universe drift) → ~61 % (lock geometry, +4 % before −6 %); the 6 % take-profit almost never binds under the lock; the lock keeps 4.5 % of a 7 % median peak; the 6 % stop is the costly leg: TP 4 % with no stop wins 73 / 69 % (IS / OOS) at 0.069 / 0.055 %/capital-day against LIVE's 60.5 / 61.6 % at 0.044 / 0.038 (best of 45 after reading; tail and Calmar unmeasured). T8 judges entry × exit pairs on four populations incl. live's 57, net of 10 bp per round trip, Holm over nine arms. Static vs flexible (e.g. ATR-scaled, regime-conditional) is the next card's question, not T8's |
| S16 | APEX's return is mostly cheap factor exposure: momentum (UMD, 12–1 months) or, at APEX's ~20-day horizon, short-term reversal, rather than alpha (overview, Carhart 1997) | daily factor returns MKT, SMB, HML, UMD, ST_Rev (Kenneth French data library, free) | to fetch; engine R1 daily returns 2019–2026 (have); live daily returns since 07-07 (have, ~75 days, descriptive only) | open — **adopted 2026-10-21** (Niclas: "Very good conclusions on all points"): an attribution card on the R1 backtest and on live; also reported beside the SPY alpha in T8 § D. If the intercept is about 0 with a high momentum loading, APEX is an expensive momentum fund, which is a go/no-go fact for O3. Benchmark: SPY decides for now (Niclas 2026-10-21: "spy for now, change or keep after the factor card") |
