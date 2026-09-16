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
| 2 | Regime-conditioned aggregator weights (`regime_state` bull/neutral/bear from `RegimeBayes.market_regime_state`) | `fetcher_yahoo.py:132`, `aggregator.py:10-14` | **no** — `:642` omits `regime_state`, always neutral weights | trend weight 0.35 (bull) / 0.30 (neutral) / 0.25 (bear); RS 0.30/0.25/0.20 |
| 3 | Sector ETF regime multiplier on score | `fetcher_yahoo.py:135-136` | **yes** `:629,:646` | same formula |
| 4 | Kelly size × score scaling | `fetcher_yahoo.py:141` | **yes** `:695` | same formula |
| 5 | L1-A: sector must have Bayesian `allocation > 0` (hysteresis enter ≥ 0.37 / exit < 0.33 on `aggregate_score × posterior`, posterior clamped [0.05, 0.95]) | `lock1_eligibility.py` docstring, `regime_bayes.py:47-48, 68-69, 466-470` | **no** — only `EXCLUDED_SECTORS` (`:684`) | the CHECK 71 finding: today 8 of 11 sectors cannot enter live; the backtest lets all 11 in |
| 6 | L1-B1: VIX threshold | `lock1_eligibility.py` | **yes** `vix_threshold` (`:229`) | |
| 7 | L1-B2: macro event blackout (FOMC/CPI/NFP window) | `lock1_eligibility.py` | **no** in `engine_fast`; **yes, always on** in `engine.py` (`:330` — event day and FOMC −2 days block unconditionally; `macro_hard_block` only widens to pre-event days) | the divergence that first tripped CHECK 76 layer 3 |
| 8 | L1-B3: earnings within N days | `lock1_eligibility.py` | **yes** `earnings_filter_days` (`:245`) | binary skip in both |
| 9 | Earnings near-term *penalty* tiers on score | `chain.py:116-124` | **no** | engine is skip-or-pass, no penalty band |
| 10 | Macro pre-event penalty on score | `chain.py:126-134` | **no** | |
| 11 | ETF negative penalty (sector ETF 5d return below floor) | `chain.py:156-182` | **no** in `engine_fast` (0 hits); **yes** in `engine.py` (`:187`, `etf_negative_penalty` param) | `etf_penalty_sweep.py:75` imports `engine.run` — the 07-17 sweep did apply it; the optimizer and walk-forward, on `engine_fast`, do not. The two engines are not drop-in equivalents despite `engine_fast.py:9` saying so |
| 12 | L2 threshold: calibrated per-sector `ticker_thresholds` from DB (weekly `recalibrate`), floored by `SECTOR_THRESHOLD_FLOORS`, fallback `LOCK1_THRESHOLD` | `lock2_quant.py:91-110` | **partial** — single `lock1_threshold` floored by `SECTOR_THRESHOLD_FLOORS` (`:690`) | live threshold moves weekly; backtest's is one number for the whole window |
| 13 | L2 watchlist 15% threshold discount | `lock2_quant.py:26` | **no** (0 hits for "watchlist") | |
| 14 | Overflow slots above `max_positions` at raised threshold (`overflow_quant_increment`) | `gate_runner_live.py:453-514` | **no** — hard `break` at `max_pos` (`:234`) | |
| 15 | L3 Grok sentiment, fail-closed, circuit breaker | `lock3_sentiment.py` | **no** | external, not reconstructible historically |
| 16 | L4 leading: RS vs sector ETF | `lock4_leading.py` | **optional** `use_leading_rs` (default False, `:250`) | off in optimizer/walk-forward unless passed |
| 17 | L4 leading: put/call ratio, unusual calls, volume accumulation (2 of 4 must pass) | `lock4_leading.py` | **no** | options data not reconstructible; volume accumulation could be |
| 18 | L5 Claude synthesis (fail-closed, sonnet only) | `lock5_claude.py` | **no** | not reconstructible |
| 19 | L5 context: rotation forecast (`sector_transitions.get_rotation_forecast`) | `chain.py:325-341`, `gate_runner_live.py:403` | **no** | |
| 20 | Bayesian sector size scale on `position_size_pct` post-L5 | `gate_runner_live.py:486-497` | **no** | |
| 21 | Dynamic sector caps (`compute_dynamic_caps`: score_w × duration_w × bayes_w, clamped [MIN_MULT, MAX_MULT]) | `sector_caps.py:51-93`, `gate_runner_live.py:399,526,566` | **no** — flat `MAX_SECTOR_EXPOSURE` (`:257`) | |
| 22 | Daily loss cap | live cfg | **yes** `DAILY_LOSS_CAP` (`:242`) | |
| 23 | SL / TP cooldown per ticker | — | **engine-only** (`:199-209`, `sl_cooldown_days=5` default) | 0 hits for cooldown/reentry anywhere in `backend/` outside `backtest/` and L3's circuit breaker; the backtest applies a rule production does not have |
| 24 | Exits: TP / SL / time stop / TSL / profit-lock ratchet | `gate_runner_live.py`, `_check_missed_live_exits` | **yes** `_check_exits_fast` (`:706`) | exit-semantics defect fixed 2026-08-17/18 in both |
| 25 | ATR-based exits | — | **engine-only** option `atr_exits` (`:269`) | 0 hits for an ATR exit/stop in `gate_runner_live.py` or `alpaca.py`; off by default, so inert unless a sweep passes it |
| 26 | Corroboration gate on exit cancellation, OCO re-protection | `alpaca.py`, `gate_runner_live.py` | n/a | execution layer, no backtest analogue by design |
| 27 | Sector universe | `ticker_config.get_sectors()` | **yes** (`:126`, fixed 2026-08-18) | parity since the stale-universe fix |

## What class of question the backtest can answer

**Can answer:** signal-formula questions under neutral weights; exit-rule
questions (24); position-count and per-position sizing questions *given*
that every non-excluded sector is always enterable; universe questions.

**Cannot answer:** anything that runs through the Bayesian regime layer
(2, 5, 20, 21) — which is the layer that decides *which sectors* are open on
a given day. Live, that layer closed 8 of 11 sectors on 2026-09-15; the
backtest never closes any. Any "too few positions" question is therefore
outside the backtest's scope by construction, and any tunable whose effect
is mediated by sector breadth (`max_positions`, `max_sector_exposure`) is
measured here on a breadth the live system does not have.

**Answers with a caveat:** L2 threshold questions (12) — the backtest's
static threshold is a stand-in for a weekly-moving one; the sweep that set
`SECTOR_THRESHOLD_FLOORS` is a floor on the live value, not the live value.

## Read on the 2026-09-15 walk-forward

The hold decision stands — it was reached on the harness's own terms and
nothing here would have flipped a hold to a ship. But the scope caveat
applies to every conclusion from it: C1's noise floor, C2's fold margins,
the SPY comparison and the "top-3 collapse is adjacency, tunables bind"
reading are all statements about a system with rows 2, 5, 7, 9-11, 13-15,
17-21 absent and row 23 present. The "tunables have low leverage over the
trade path" finding in particular was measured with the highest-leverage
upstream gate (row 5) removed.

## Engine vs engine — asserted by CHECK 76

The two engines are documented as drop-in equivalents (`engine_fast.py:9`)
and were not. `audit/checks_code.py::check76` holds `raw_data` constant,
neutralises the accepted slow-only divergences, and requires identical trade
logs. Accepted divergences (edit the check's lists and this file together):

- `run()` slow-only: `etf_negative_floor`, `etf_negative_penalty`,
  `macro_hard_block`, `macro_pre_event_penalty`; fast-only: `precomputed`.
- `engine.py:330` macro block is unconditional on event days; `engine_fast`
  has no macro calendar.

Fixed 2026-09-16 on the check's first run (all undocumented until then):
SPY `return_20d` off-by-one in the fast engine (aligned to production's
19-period definition, not to the docstring's 20); NaN-poisoned ETF MA20 in
the slow engine; signal-cache key with no code version (stale `sig_*.pkl`
served across a signal edit). Every optimizer and walk-forward run before
that date used the first and third.

## Not a defect list

Rows 15, 17 (options), 18 are unreconstructible and their absence is a
design boundary, not a gap to close. Rows 2, 5, 20, 21 are one component
(`RegimeBayes`) and closing them is one build: wire `RegimeBayes` into the
engine's day loop with OHLCV-derivable signals — `floor_reconstruction.py`
and `regime_conditioned_cs.py` already do that replay standalone. That build
is the prerequisite for putting the entry floor through the harness
(CHECK 71 follow-up); until it lands, the harness returns identical trade
paths for any floor value, trivially.

Rows 23 and 25 run the other way: the backtest enforces rules production
does not. Row 23 (5-day SL cooldown) affects every backtest trade count and
should either be added to production deliberately or removed from the
engine's defaults — it is a silent divergence in the direction nobody
checks.
