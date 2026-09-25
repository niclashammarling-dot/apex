# Intraday stop parity — the rules the port commits to

Status: SPECIFICATION, 2026-09-25. Nothing below is implemented yet.

Both engines decide every exit on the daily CLOSE. Live stops rest at the
exchange as Alpaca bracket legs and trigger intraday. This file states the
rules a native intraday implementation will follow, *before* the port, so the
choices are made deliberately rather than inherited from
`intraday_stop_experiment.py` by accident.

Reference measurement, re-run 2026-09-25 and reproduced exactly:

    window 2025-12-22 → 2026-09-18, OHLC c45df0b1b512.parquet
    close-only   score 1.09335  sharpe 3.263  maxDD 2.6%  win 64.0%  75 trades  +31.3%
    intraday     score 0.56542  sharpe 1.792  maxDD 4.6%  win 43.5%  85 trades  +11.4%
    stop exits: 30 intraday + 11 gap = 41; 27 invisible to the close-only engine

## What live actually does

`wallet.py::check_exits` (demo) — TP → **SL** → profit-lock trail → TIME.
The docstring is explicit: SL is a "hard floor, always active".

`live_trades_tracker.py::check_live_exits` (live) — the stop is an Alpaca
bracket STOP leg resting at the exchange (GTC since 2026-05-08). The
profit-lock trail is **not** a separate polled exit: `_maybe_ratchet_bracket_sl`
PATCHes that same resting leg up to `peak × (1 − trail)`.

Two consequences that decide the rules below:

1. A live TSL exit and a live SL exit are the *same mechanism* — an exchange
   stop filling intraday at market. They differ only in where the stop sits.
2. The ratchet is **polled, not continuous**. `EXIT_CHECK_INTERVAL = 5`, and the
   peak comes from `_current_price(ticker)` at poll time. Live only ratchets off
   a high a poll happened to observe.

## R1 — Trigger detection

Fixed stop: trigger when `low <= stop_price`, where `stop_price =
entry_price × (1 − eff_sl)`. Unchanged from the reference.

Ratcheted stop: same test against the ratcheted stop price. See R4 for which
peak sets it.

## R2 — Fill price

- Gap-through (`open <= stop_price`): fill at `open`.
- Otherwise: fill at `stop_price`.
- No slippage or commission anywhere in `backend/backtest/`. Live fills at
  MARKET after trigger, so this is optimistic by construction. Measuring that
  bias is step (2) of the sequence, not this step.

## R3 — Same bar touches both the stop and the target

Daily OHLC cannot order two touches inside one bar. A choice is required.

**Rule: the stop wins.** Conservative, and it matches the reference, which
reaches the same outcome by construction — its pre-pass removes the trade
before the close-based TP logic ever sees it.

Note what this does: on the close path TP is checked *first* (`pnl_pct >=
eff_tp` is the opening branch of `_check_exits`), so the intraday rule
**inverts** the close-path priority. That inversion is deliberate and is the
single largest unmeasured assumption in the treatment.

**Required with the rule:** a counter of bars where both the stop and the
target were touched, reported alongside the result. An assumption whose
exposure is unmeasured is the failure mode this project keeps re-finding; the
counter makes its weight visible. The reference does not have this counter, so
its 27 "invisible" exits silently include an unknown number of bars that the
close-only engine would have booked as a TP — reclassifications with the
opposite sign, not additions.

## R4 — Same bar ratchets the trail and then hits it

The case: today's high lifts the peak, and the stop implied by that new peak is
above today's low.

**Rule: test first, ratchet second.** The trail is measured against the peak as
it stood at the *start* of the bar; the peak updates at bar end.

Justification is the live mechanism, not conservatism for its own sake. The
resting stop during today's session was priced off a peak observed by an
earlier poll. Ratcheting off the bar's high before testing would hand the
backtest a stop that live only gets if a poll landed exactly on the high —
and at a 5-minute cadence it usually does not.

The two orderings bracket the truth rather than one being correct:
test-then-ratchet is the floor, ratchet-then-test the ceiling. Implement the
floor; keep the ceiling available behind a flag so the spread is measurable.

## R5 — Fixed SL is unreachable once the ratchet is active, and live disagrees

In both engines the exit chain is an `elif` ladder:

    if   pnl_pct >= eff_tp              -> TP
    elif ratchet_active                 -> TSL / TIME / hold
    elif trailing_stop_pct is not None  -> TSL / TIME / hold   (legacy)
    elif pnl_pct <= -eff_sl             -> SL
    elif days_held >= time_stop_days    -> TIME

Once `ratchet_active` is true the `SL` branch **cannot be reached**. Live checks
SL *before* the trail and calls it a hard floor. Same `elif`-hides-the-branch
shape as CHECK 65's pin/divergence arms (2026-09-25).

Effect is a label divergence, not usually a P&L one: with live's 0.04/0.01 the
trail fires before the fixed stop is reached, so the exit lands on the same bar
at a similar price but is booked `TSL` where live books `SL`.

It is not cosmetic for `profit_lock_sweep.py`, which sweeps trigger 0.01–0.05
against SL 5% and reports `tsl_exits` as a headline metric. Every exit live
would have labelled `SL` is counted there as a profit-lock exit. That sweep set
live's 0.04/0.01 (`829bae5`).

**Rule: the port restores live's order** — TP → SL → trail → TIME — in both
engines, and the change is made *separately* from the intraday change, with its
own before/after, so the two effects do not land in one number.

### R5 measured, 2026-09-25 — a no-op on this window

Shipped in its own commit. Window 2025-12-22 → 2026-09-18, the 09-21 best
params with profit-lock forced to live's 0.04/0.01 (the optimizer path never
passes profit-lock, so this had to be driven directly):

    before  83 trades  47 wins  return 0.1076  sharpe 1.437
            TP 36  SL 29  TSL 11  TIME 4  END_OF_BACKTEST 3
    after   identical, and identical per-trade across all 83 trades
            (ticker, entry date, exit date, reason, outcome, price, pnl)

Verified by dumping the trade log on both sides of the change, not by comparing
aggregates. The two orderings coincide here because a 1% trail fires several
bars before a ratcheted position can fall to the 5% fixed stop, so the
unreachable branch was never reached.

**This does not clear `profit_lock_sweep`.** That sweep runs a different window
(2021-01-01 → 2026-05-01), on the slow engine, with triggers down to 0.01 where
the ratchet arms far earlier and the fixed stop is correspondingly more
reachable. Whether its `tsl_exits` headline was contaminated is open until the
re-run below.

## R6 — OHLC source

`_load_ohlc` picks `max(glob(...), key=os.path.getmtime)`. It happens to pick
`c45df0b1b512.parquet` (2025-08-14 → 2026-09-18), which does cover the window,
but any later sweep writing a cache file would silently swap the OHLC source.

**Rule: the port derives the parquet from the same hash `precompute` uses.**
High is already present in the cache (all five OHLCV fields are), so the R4
ratchet needs no new download.

## Scope the reference does NOT cover

- **TSL is absent from the reference entirely.** Its intraday pass reads only
  `trade.get("sl", stop_loss_pct)` — the fixed entry stop — and passes
  `trailing_stop_pct`, `profit_lock_trigger_pct` and `profit_lock_trail_pct`
  straight through to the close-based logic untouched. On top of that the
  09-21 best params carry `trailing_stop_pct: None` and `_evaluate` never
  passes profit-lock at all, so the reference arm ran with every trailing
  mechanism switched off.
- Therefore **41/27 certifies the fixed-stop path only**. Of the ~41 live stop
  exits, 10 `TSL` + 1 `TRAILING_STOP` are trailing exits with no reference
  behind them. R4 is specified from the live mechanism, not from the reference,
  and equivalence testing cannot cover it.

## Acceptance

**Test 1 — equivalence, fixed-stop path. Two levels (Niclas, 2026-09-25),
because a GitHub runner has no frozen parquet.**

*1a — exit-level equivalence, in CI.* Freeze the reference's entries; for each
trade, assert that its own bar window produces the right exit day, fill price
and reason, in BOTH engines. The fixture is only those trades' bars, small
enough to commit. R3's both-touched counter is asserted here directly.

*1b — full-run reproduction, local only.* 1.09335 / 0.56542 and the 41/27 split
are a one-time certification recorded in this file with the parquet hash, not a
repeatable CI artifact. Exit timing frees slots and therefore changes later
entries, so the full run is a compounding counterfactual and cannot be a unit
fixture.

Neither level covers R4 or R5, which change behaviour away from the reference
by design and ship with their own before/after.

**Test 2 — live agreement, two numbers reported separately.**
- *Detection:* for each live `SL`/`TSL` exit, did the engine's bar for that day
  trigger? Report the day-agreement rate both ways (live fired / engine did not,
  and engine fires / live did not). Pass-fail.
- *Price:* on the agreeing trades only, modelled fill vs Alpaca's actual exit
  price. Reported as a bias distribution, not pass-fail. Expect a persistent
  adverse bias — live fills at market after trigger and nothing here models
  slippage. That bias is the input to step (2), the cost term.

Live entry fills are never recorded (apex-moc Open Territory, 2026-09-23), so
the entry side of test 2 has no stored counterparty. Test 2 compares exits only.

## Consumers — the 09-16 amendment

Settled 2026-09-25 (Niclas).

**Test 1a → CI, on change, not on a schedule.** A deterministic test's result
can only change when its inputs change, so it runs when the engine files change.
The 09-16 amendment's wording moves from "a schedule the host keeps" to **"a
trigger the host keeps"**, so this reads as the amendment applied rather than
as a loophole around it. The wording change belongs in apex-moc with the
amendment itself.

**Verifying CI means one observed red run.** Push a deliberate break of the exit
rule on a branch, watch the workflow fail, revert. That observed failure is the
affirmative evidence — the same standard the audit loop failed three times in
one summer. Auditing the rest of `tests.yml` stays out of scope.

**Test 2 → recurring report.** Live exits keep accruing, so day-agreement and
the fill-bias distribution are a natural nightly/periodic check on the existing
audit surface.

## Re-runs this work owes

- `profit_lock_sweep` **once, after both R5 and the intraday change have
  landed** — not after R5 alone. Record (i) the corrected `tsl_exits` split and
  (ii) whether argmax moves off 0.04/0.01. **No parameter change here**;
  changing live's profit-lock is the modelling session's call.
- The stop-width grid, to see whether argmax still lands at 7% under the fixed
  engine. If it does, the 5% was an artifact of the blindness.
