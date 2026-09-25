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
aggregates.

**The no-op is structural at these parameters, not luck** (Niclas, 2026-09-25).
At 0.04/0.01 the ratchet arms at +4% and rests the stop at peak × 0.99, i.e. at
least +2.96% over entry. Once armed, any close below that level fires the trail.
So the fixed SL can only become the binding branch if a single close-to-close
move carries the price from above the trail to below −5% — a drop of about 8% in
one day. Below that, the trail has already closed the trade on an earlier bar.
None of the 83 trades had such a move, so the reorder could only ever have
changed labels on bars that did not occur.

**Provenance of live's 0.04/0.01, stated at the width the evidence supports.**
Not "selected on miscounted data". The correct claim is: *unaffected on this
window, untested at wider one-day moves.* The miscount risk lives at the tighter
grid points, where the gap between the trail and the fixed stop is smaller — and
even there it still needs a large single-day drop. `profit_lock_sweep` runs a
different window (2021-01-01 → 2026-05-01) on the slow engine with triggers down
to 0.01, so whether any of its `tsl_exits` were mislabelled is open until the
re-run below. That is a question about the sweep's tighter cells, not about the
shipped parameter.

**Why the intraday path cannot regress this way.** `resting_stop` returns
`max(fixed, trailed)` and the reason is simply whichever term binds. That mirrors
live, which has one resting STOP leg that `_maybe_ratchet_bracket_sl` only
PATCHes upward (its gate 4 skips when the new stop would not move up) — there is
never a separate SL competing with a TSL. With one effective level there is no
branch order to get wrong, and a gap-through is just `open <= effective stop`,
filled at the open. R5 therefore protects the CLOSE path only, which still
matters for bars absent from the OHLC cache and for TP/TIME exits.

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

### 1b certified, 2026-09-25 — PASS

Native implementation vs the monkeypatched reference, same parquet
(`c45df0b1b512.parquet`, 2025-08-14 → 2026-09-18), same 09-21 best params,
window 2025-12-22 → 2026-09-18:

    arm          score    sharpe   maxDD   win     trades  return
    reference    0.56542  1.792    0.0456  0.435   85      0.1139
    native       0.56542  1.792    0.0456  0.435   85      0.1139

    reference    intraday 30  gap 11  total 41  invisible 27
    native       intraday 30  gap 11  total 41  invisible 27

Missed-set keys identical; zero fill/stop/close mismatches on shared keys.

**R3's exposure is zero on this window.** `both_touched = 0` at every stop width
in the grid, so no bar reached both the stop and the target and the stop-wins
rule never had to break a tie. The concern that the reference's 27 silently
contained sign-flipped reclassifications is therefore retired *for this window* —
measured, not argued. The counter stays, because the next window is not this one.

### Stop-width grid under the native engine, 2026-09-25

    SL     close    intraday   delta     stops  invisible  both-touched
    0.04   0.612    0.07325    +0.53875   58     34         0
    0.05   1.09335  0.56542    +0.52793   41     27         0
    0.06   1.02105  0.32863    +0.69242   33     19         0
    0.07   0.6469   1.07905    -0.43215   16      9         0

    argmax close-only: SL 5%      argmax intraday: SL 7%

Reproduces the reference grid exactly. **The argmax still moves 5% → 7% under
the fixed engine, so the 5% recommendation was an artifact of the blindness.**
The caveat the experiment recorded still stands and is not discharged by the
port: the 7% cell *improves* under the treatment, which can only be trade-path
effects, so no single cell is a clean read and the shift is suggestive. One
window, four widths.

Full suite after both changes: 349 passed.

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

*Status 2026-09-25: consumer wired, red run NOT YET OBSERVED.*
`tests/test_exit_parity.py` (7 tests) runs under `.github/workflows/tests.yml`,
which fires on push to `master`, on any PR, and now on `ci-verify/**` — that
branch pattern was added so the break can be pushed and watched without opening
a PR. The observation itself is outstanding: this session has no `gh` CLI and no
GitHub token, so it can push the break but cannot read the run's result, and a
run nobody has looked at is exactly the thing this step exists to rule out.

Until someone reports the red run, test 1a is **"exists and runs, not verified"**
— the middle two of the four facts (exists / runs / actually evaluates / results
read). To discharge:

    git checkout -b ci-verify/exit-rule
    # flip R2's gap branch: fill at the stop instead of the open, in
    # backend/backtest/exit_rules.py — test_every_stop_exit_reproduces and
    # test_gap_and_intraday_split_matches should both go red
    git push -u origin ci-verify/exit-rule
    # watch Actions; confirm FAILURE and that the failing assertions are those two
    git push origin --delete ci-verify/exit-rule

**Test 2 → recurring report.** Live exits keep accruing, so day-agreement and
the fill-bias distribution are a natural nightly/periodic check on the existing
audit surface.

## Re-runs this work owes

- `profit_lock_sweep` **once, after both R5 and the intraday change have
  landed** — not after R5 alone. Record (i) the corrected `tsl_exits` split and
  (ii) whether argmax moves off 0.04/0.01. **No parameter change here**;
  changing live's profit-lock is the modelling session's call.
- ~~The stop-width grid~~ — run 2026-09-25, argmax 5% → 7%, recorded above.
