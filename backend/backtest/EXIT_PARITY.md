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

## The model is one-sided — targets are still close-only (found 2026-09-25, after the port)

Checking Niclas's "TP on close versus intraday" question before trusting the
sweep turned up a defect in what step (1) shipped.

`brokers/alpaca.py:262-264` places `OrderClass.BRACKET` with **both**
`TakeProfitRequest(limit_price=tp_price)` **and**
`StopLossRequest(stop_price=sl_price)`. Both legs rest at the exchange. So live's
take-profit fills intraday for exactly the same reason its stop does — the moment
price trades through the limit.

Step (1) made stops intraday and left targets on the close. That is a one-sided
fix: the engine now books every intraday stop-out and still misses every intraday
take-profit. Measured on the same window, alongside the invisible stops:

    SL     stop exits   invisible stops | TP touched  TP on close  TP MISSED
    0.04   58           34              | 64          36           28
    0.05   41           27              | 63          36           27
    0.06   33           19              | 60          33           27
    0.07   16            9              | 71          41           30

At SL 5% the two blindnesses are the same size — 27 stops and 27 targets. At SL
7% they are not: 9 invisible stops against 30 missed targets.

`also stopped` is 0 at every width, matching `both_touched = 0` — the missed
targets and the invisible stops fall on disjoint bars, so no tie-break is
involved. R3 addressed the right collision but the deeper issue was never the
tie-break; it was that the TP leg is not modelled intraday at all.

**What this does and does not disturb.** The core measurement stands: the
close-only engine was blind to 27 of 41 stop exits, and that is what step (1)
fixed. What is now qualified is the **argmax conclusion**. The 5% → 7% shift was
measured with intraday stops against close-only targets, and the asymmetry runs
hardest against the widest stop — the 7% cell is the one carrying 30 missed wins
against 9 invisible losses. A symmetric model could move the argmax again, so
"the 5% was an artifact" should be read as "the close-only comparison was not
sound", not as "7% is the answer".

The `profit_lock_sweep` re-run started 2026-09-25 12:27 CEST carries the same
one-sidedness and its output must be read under this caveat.

### What became of the missed TPs under the close model (2026-09-25)

Niclas's correction to the caveat above, and the measurement that settles it.
The missed-TP counts are nearly **flat** across widths (28, 27, 27, 30). What
moves is the denominator — the invisible stops the intraday model adds shrink as
the stop widens (34 → 9). So the symmetric fix adds a similar TP effect to every
cell; it does not pile missed wins onto 7%.

How much it can reorder the grid depends on what those trades actually became
under the close-only model. Three outcomes, very different in kind: **TP later on
the close** (same sign, only timing and slot occupancy), **TIME** (smaller win or
a scratch), **SL** (a win booked as a loss — the sign flip).

    SL     trades  touched | TP later  TIME  SL  TSL  EOB | SL share  pnl of SL-fated
    0.04   73      19      | 15        2     2   0    0   | 10.5%     -115.52
    0.05   75      18      | 16        1     1   0    0   |  5.6%     -143.53
    0.06   71      18      | 16        1     1   0    0   |  5.6%     -142.06
    0.07   71      18      | 16        1     1   0    0   |  5.6%     -136.22

**Units — the two tables count different things.** The 27/27/27/30 row above
counts touch-**BARS**; the table here counts touch-**TRADES**. At SL 5% that is
27 bars across 18 trades, because 8 trades touched the limit on more than one bar
(QCOM 2026-05-22 on three, five others on two). They are the same population
measured differently; nine trades did not disappear.

**The sign-flip channel is one trade per width** (two at 4%). Almost everything
that touched the target intraday went on to book a TP on a later close anyway.
So the *direct* effect is timing rather than sign — but see the slot section
below: the timing effect feeds a slot channel that does bind, so this does **not**
add up to a level shift.

**Slot cost, in trading days.** Calendar days against a trading-day capacity
mixes units. At SL 5% the 16 "TP later" trades were held **41 trading days** past
their first touch — median 2, max 7. Against 3 slots × 188 trading days = 564
slot-days, that is **7.27%** of capacity.

### The slot channel is NOT zero — it binds hard (corrected 2026-09-25)

**Retraction.** An earlier version of this section reported "candidate-days lost
to a full book: 0" and concluded symmetric TP was a pure level shift. That zero
was an instrument artifact, not a measurement. The probe captured the candidate
list keyed on `a[6]` of `_get_candidates_from_cache`, which is `spy_regime`, a
float — `today_str` is the fifth positional arg (`engine_fast.py:699`). So the
dict was keyed by floats, every date lookup missed, and an empty dict rendered as
a clean zero. It was reported before being caught.

What caught it was running the same query over all 188 days at Niclas's
suggestion, which printed **"days with ANY candidate at all: 0/188"** — flatly
impossible when 75 trades were entered. The probe now asserts that count is
non-zero before reporting anything, so an empty capture fails loudly instead of
reporting a zero. A "no candidates were turned away" result and a "the candidate
list never loaded" result look identical in the output unless something holds the
control closed.

Corrected, with the capture bound by name:

    during the 16 touch-to-book windows
      41 window-days; at capacity on 23
      of those 23, 21 HAD an unheld candidate waiting     (previously reported 0)

    across all 188 trading days
      at capacity on 109
      of those, 105 had an unheld candidate               (previously reported 0)
      most frequently turned away: CVX 25, AMD 25, EOG 23,
        MRNA 22, COP 18, OXY 18, JNJ 15, CAT 14

**This reverses the conclusion.** The slot count is binding almost everywhere at
`lock1_threshold = 0.73`: the book is full on 58% of days and on 96% of those
there is something it cannot buy. The 41 freed slot-days symmetric TP would
release are therefore very likely to be *used*, so the compounding channel is
live and **the level-shift expectation is not supported**. Symmetric TP can
reorder the grid, and the before/after has to be read as a compounding
counterfactual like every other cell here.

Two further consequences, both wider than this file:

- **`max_positions` is not a free parameter at this threshold** — it is an active
  constraint on 105 of 188 days. Anything that treats position count as
  non-binding, including the modelling session, needs this.
- **Slot-occupancy artifacts are live in the sweep now running**, and in the
  stop-width grid. Freeing or consuming a slot changes later entries throughout,
  which is the compounding-counterfactual caveat already recorded — this sizes
  it: it is not a small correction.

**The pre-filter over-count cuts the other way now, and 105 was not a usable
bound.** It made the retracted zero stronger; it makes 105 *weaker*, because the
filters it skips — cooldown, sector exposure, VIX, earnings — are exactly the
ones most likely to fire when the book is full. CVX, EOG, COP and OXY are all
Energy and account for 84 of the turn-aways between them, so if any Energy name
was held, `MAX_SECTOR_EXPOSURE` would have rejected them with a free slot
available. Same for cooldown right after an exit.

### `max_positions` binds — measured with the full filter chain (2026-09-25)

Niclas's method, which removes the hand-built probe entirely: run the engine at
`max_positions = 3` and at `4` on the same window and diff the entry lists. An
entry appearing only at 4 passed every filter and was blocked by the slot count
alone. Nothing to mis-bind.

    intraday stops          trades  return   sharpe  maxDD  | only at N  lost vs 3
      max_positions = 3      85     0.1139   1.792   0.0456 |
      max_positions = 4     116     0.1428   1.974   0.0464 |    49         18
      max_positions = 5     142     0.0576   0.980   0.0714 |    83         26

    close-only              trades  return   sharpe  maxDD  | only at N  lost vs 3
      max_positions = 3      75     0.3128   3.263   0.0261 |
      max_positions = 4      96     0.2123   2.485   0.0526 |    47         26
      max_positions = 5     114     0.1552   1.976   0.0403 |    68         29

**One extra slot unlocks 49 entries that cleared every filter** (net +31 after
the 18 that the changed path costs). So the constraint is real and sized, and
"plausibly binding" would now be under-stating it. The non-zero "lost vs 3"
column is the compounding counterfactual again — 4 is not a superset of 3.

### The 3→4 step flips; the parameter does not (narrowed 2026-09-25)

Stated precisely, because the first version over-claimed. Intraday runs
0.1139 → 0.1428 → 0.0576; close-only falls monotonically 0.3128 → 0.2123 →
0.1552. **At 5 both engines agree the extra slot hurts.** So the defensible claim
is: *the blind engine says fewer is always better, while the fixed engine shows
an interior optimum at 4 within a 3–5 grid.* That is still an argmax the
blindness moves, but it is one cell, not the parameter.

**Two caveats travel with it.**

1. *It is the one-sided model again.* These intraday runs have stops intraday and
   targets on the close. Missed TPs hold slots longer, and slot count is exactly
   the axis being compared — so this carries the stop-width caveat, probably more
   strongly than stop width did.
2. *"Binds" and "helps" have different evidential weight.* The 49 entries are a
   structural fact about the book. The +0.029 return and +0.18 Sharpe at 4 are
   one window. Record as **binding (robust)** and **direction at 3→4 (single
   window, one-sided model)**.

### Attribution: the flip is in the displaced trades, and the mechanism is still open

The earlier mechanism sketch — "the blind engine under-prices each position's
risk, so it should favour more exposure" — is **withdrawn**. It predicts the
wrong sign, and an opposite story (a concentrated book of top candidates held
through invisible dips looks excellent) fits equally well. A story that fits
either sign is not a mechanism.

Decomposing the 3→4 change into marginal entries and displacement, which
reconciles exactly:

    intraday    added (49) +438.16 | removed (18)  +14.92 | common (67) -134.09 | net  +289.15
    close-only  added (47) +100.96 | removed (26) +808.94 | common (49) -296.85 | net -1004.83

**The flip is not displacement.** The common-trade path effect is negative in
*both* engines (−134 vs −297) — same sign, so it cannot produce a sign change.
The flip lives in the **removed** term: the trades the 4th slot pushes out of the
book are worth **+808.94 under the blind engine and +14.92 under the fixed one**.
Composition differs accordingly — close-only displaces 15 TPs against 7 SLs;
intraday displaces 7 TPs against 10 SLs.

**But the obvious explanation does not survive checking.** If the displaced
trades were valuable to the blind engine because their intraday stop-outs were
invisible, the +808.94 should sit in trades that breached the stop intraday. It
does not: only **9 of the 26** ever breached, and those 9 booked **−671.83** —
they were already losers on the close path too. The +808.94 comes mostly from
trades that never touched the stop at all.

So the attribution **localises** the flip (to what the 4th slot displaces, not to
what it adds or to path effects on survivors) and **rules out** the invisible-dip
story, without establishing a replacement. Filed as an observation. The mechanism
stays open rather than being narrated.

### The path-luck null, tested (2026-09-25)

"Mechanism open" is not the same as having no hypothesis, and the default worth
testing first is a **null**, not a third story: the flip could be path luck. The
two engines free slots at different moments, so they admit different trades. If
the +1,480.77 carried by the 17 non-breaching displaced trades sits in two or
three names, the flip is one window's draw of which big winners happened to find
a free slot, and it would not survive another window.

It is one sort of 17 rows.

    close-only, 17 non-breaching displaced trades, total +1480.77
      CRM  +309.32 (20.9%)   AMD  +156.26 (10.6%)   ASML +121.98 (8.2%)
      HAL  +118.47 ( 8.0%)   COP  +116.81 ( 7.9%)   MRVL +110.92 (7.5%)
      AMAT +104.70 ( 7.1%)   MRVL +103.60 ( 7.0%)   TMO   +97.10 (6.6%)
      HD    +88.02 ( 5.9%)   CAT   +77.42 ( 5.2%)   CVX   +52.21 (3.5%)
      OXY   +51.97 ( 3.5%)   EOG   +37.85 ( 2.6%)
      CSX    -9.46   META  -14.76   PFE  -41.64
      -> 5 trades needed for 50% of the total; top 3 share 39.7%

**The null is weakened, not confirmed.** It takes five trades to reach half the
value, the largest single name is 20.9%, and **14 of the 17 reached TP** — the
value is spread across a broad set of winners rather than riding on two or three.
So there is something to dig for; this is not purely which lottery tickets landed
in a free slot.

Recorded as "no established mechanism; the path-luck null is weakened — value
spread over 14 positive trades, 5 needed for 50%" rather than as an open
question, which is the more useful thing to hand the modelling session.

Concentration is stated **normalised against an even split**, because a raw
top-3 share is not comparable across different n — with 17 trades an even split
already gives the top 3 17.6%, with 8 trades it gives them 37.5%:

    close-only  n=17  top-3 39.7%  vs even 17.6%  ->  2.25x even
    intraday    n= 8  top-3 49.5%  vs even 37.5%  ->  1.32x even

An earlier version of this section read the intraday side's 49.5% as *more*
concentrated and carried the difference as unexplained. That was the metric, not
the data: normalised, the intraday side is the **less** concentrated of the two,
and at n = 8 its figure says very little either way. There is no unexplained
difference between the sides — there was an uncorrected denominator.

The normalisation also sharpens the close-only result rather than softening it:
2.25x even is genuinely concentrated, and still nowhere near the two-or-three-
ticket pattern the null predicts. "Weakened, not confirmed" remains the label.

The other limit stands: 17 trades from one window is a small sample, and "spread
over 14 winners" is more robust than "two names" without being robust.

**Still deferred until after symmetric TP.** The one-sided confound touches
exactly this axis — missed TPs hold slots longer, and slot count is what is being
compared. The concentration check only sizes how much there is to dig for; it
does not make digging now any more sound.

This was not on the four-step list. It belongs to step (3), the live-anchored
comparison line, since it changes which parameter set "beats what is running".

### Rules for symmetric TP, when it is built

Mirror of the stop side, specified now while the reasoning is live:

- **Gap-up through the limit fills at the OPEN.** A limit sell fills at the limit
  or better, so the favourable gap is the mirror of R2's adverse one.
- **Both-touched bars get the floor/ceiling flag**, exactly like R4's ratchet:
  stop-first is the conservative floor, TP-first the ceiling. Daily bars admit no
  taste-free answer, but the spread is measurable, and **this is where R3's
  counter finally has something to count** — it has read 0 on every run so far
  precisely because the TP side was never modelled intraday.

### Sequencing: symmetric TP goes directly after (1), ahead of (2)

Niclas, 2026-09-25. The reason step (1) came first applies unchanged: the
asymmetry is a function of the axis being compared, so it does **not** cancel in
a comparison. And step (2) is a cost term calibrated against fills — building it
on a one-sided exit model would bake the asymmetry into the calibration.

**The in-flight `profit_lock_sweep` keeps its value as the ONE-SIDED BASELINE.**
Started 2026-09-25 12:27 CEST under intraday stops with close-only targets.
Labelled as such, it is the "before" for symmetric TP, so the ~2.7 hours of
compute buys a before/after rather than being discarded.

**Why the engine is not edited while it runs.** The rule is *do not edit what the
measurement depends on while the measurement is running* — not "the modules are
already imported", which is not a guarantee. `multiprocessing` spawns fresh
interpreters on Windows, and each worker re-imports the engine from disk, so an
edit mid-sweep can land in later runs silently while the earlier ones used the
old code, producing a grid that is internally inconsistent and looks fine.
`backend/backtest/` has no `multiprocessing`, `ProcessPool`, `concurrent.futures`
or `joblib` (checked 2026-09-25), so this sweep is single-process and the
in-memory reasoning happens to hold — but the rule is the protection, and the
import state is a coincidence that a future parallel sweep would remove.

**Not in today's scope; it changes what step (1) means.** The four-step sequence
specified intraday stops, and intraday stops is what shipped. Symmetric bracket
modelling — the TP leg intraday, at the limit price, with a gap-through at the
open on the upside — is a distinct piece of work and needs its own before/after
and its own tie-break rule, since a bar that touches both legs then genuinely
does need R3 (and R3 would stop being a dead counter).

## `profit_lock_sweep` re-run — the one-sided baseline (2026-09-25)

Completed 14:34 CEST. **20 runs, not 26** — the `trail < trigger` constraint
removes 6 of the 25 grid cells, plus the baseline. Intraday stops ON, targets on
the close, so this is the one-sided baseline and the "before" for symmetric TP.

    Baseline (no profit-lock)   n= 534  WR=46.8%  PF=1.323  TSL= 33  bal=$12,635

    trig=4% trail=0.5%          n= 630  WR=55.2%  PF=1.443  TSL=196  bal=$15,754
    trig=4% trail=1.0%  <- LIVE n= 608  WR=55.3%  PF=1.428  TSL=163  bal=$15,993
    trig=4% trail=1.5%  <- BEST n= 594  WR=55.6%  PF=1.537  TSL=141  bal=$16,596
    trig=4% trail=2.0%          n= 580  WR=55.3%  PF=1.447  TSL=124  bal=$15,328
    trig=4% trail=2.5%          n= 595  WR=55.0%  PF=1.453  TSL=107  bal=$14,794
    trig=3% trail=2.0%          n= 648  WR=57.4%  PF=1.328  TSL=191  bal=$14,655
    trig=5% trail=1.5%          n= 555  WR=51.0%  PF=1.384  TSL= 83  bal=$13,808
    trig=1% trail=0.5%          n=1029  WR=65.9%  PF=1.154  TSL=612  bal=$13,150

**Reading the `◀ best` markers:** they are a *running* best — the sweep marks any
cell beating the maximum so far, so four cells carry one. Only `4% / 1.5%` is the
argmax.

**`TSL=33` on the no-profit-lock baseline is correct, not a mislabel.**
`profit_lock_sweep.py:90` passes `trailing_stop_pct = TRAILING_STOP_PCT = 0.09`
to the baseline, so those are legacy bare-trailing exits at 9% — which is what
"Baseline (TSL only, no profit-lock)" says. The grid cells pass the same 0.09
*plus* profit-lock, where the legacy branch is gated off by
`not (profit_lock_trigger_pct and profit_lock_trail_pct)`, so it is inert. The
whole TSL column is sound. (Checked because a trailing-exit count under "no
profit-lock" would otherwise have made every TSL figure suspect.)

### The argmax: a plateau, not a one-step move

The first reading — "argmax moves one grid step, 1.0% → 1.5%" — over-states it.
At trigger 4% the trail neighbours are essentially level: 0.5% → 1.443,
1.0% → 1.428, 1.5% → **1.537**, 2.0% → 1.447, 2.5% → 1.453. **A single cell
standing above flat neighbours is what noise looks like on a grid.** The robust
statement is: *trail 0.5–2.5% at trigger 4% is a plateau around PF ≈ 1.44, and
1.5% is its highest point on this run.*

And the one-sided caveat applies to this axis too: trail width governs how long a
trade stays alive to reach its target, and targets are still decided on the
close. Symmetric TP can move this argmax as it can the others.

### The contamination question stays open, with a corrected spec

I proposed running two cells under the old close-path ladder to see whether
`829bae5`'s `tsl_exits` were contaminated. **That check could not have returned a
positive** (Niclas). R5 only reorders the *close-path* ladder; this sweep runs
intraday stops with 100% High/Low coverage, so every stop is decided by
`resting_stop`, whose label is just whichever term binds, and the close-path
SL/TSL branches are never reached. The two arms would have produced identical
counts whether or not the original sweep was contaminated — the same shape as the
ugrep search that could not match and the float-keyed capture dict.

It also means the counts above are **not** "correct because R5 landed". They are
correct because the intraday path never consults the branch order.

Correct spec, recorded so it costs ~15 minutes if anyone ever needs it:

    close-only engine (intraday_stops=False), old ladder vs new ladder,
    on the TIGHT cells (trigger 1%, trail 0.5%), where the fixed stop binds
    after a ~5.5% one-day drop rather than ~8% — plausible over five years.

**Deliberately not run** (Niclas): it answers whether the old sweep was valid on
its own terms, and the live parameter is being re-decided on a better engine
regardless. No outcome changes a decision.

**No parameter change from this run.** Changing live's profit-lock is the
modelling session's call.

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

*Status 2026-09-25: VERIFIED by an observed red run.*

`tests/test_exit_parity.py` (7 tests) runs under `.github/workflows/tests.yml`,
which fires on push to `master`, on any PR, and on `ci-verify/**` — that branch
pattern was added so a break can be pushed and watched without opening a PR, and
it stays for the next verification.

The evidence, read by Niclas at **10:28 UTC on 2026-09-25**:

- `master` green.
- `ci-verify/exit-rule`, carrying R2 inverted so a gap-through fills at the stop
  instead of the open: **red**, with exactly the two parametrisations of
  `test_every_stop_exit_reproduces` failing (`[fast]` and `[slow]`) and nothing
  else. It failed on the assertion, not on setup — so the fixture reached the
  runner and the replay actually executed. That distinction is the whole point:
  a job that errors before it evaluates looks red for the wrong reason.

**What the failure pattern additionally shows.** Exactly **11 of 41** cases
failed, all `SL`, all on the *correct* exit date, and every observed price above
the expected one (HII 386.38 vs 350.35, AMAT 607.52 vs 581.31). That is precisely
the gap-through bucket — 11 gaps, 30 intraday — so the fixture exercises both
buckets and the 30 intraday cases passed untouched. The price gaps also size how
much a naive fill-at-stop model flatters a gap loss.

**Limit, recorded rather than chased.** This red run proves the wiring and the
*gap* bucket. Nothing deliberately broke the intraday bucket, so the only
evidence for it is the 1b certification itself. A future verification wanting to
cover it should break R1's trigger test rather than R2's fill.

The branch was deleted after the run was read.

**Test 2 → recurring report.** Live exits keep accruing, so day-agreement and
the fill-bias distribution are a natural nightly/periodic check on the existing
audit surface.

## Re-runs this work owes

- `profit_lock_sweep` **once, after both R5 and the intraday change have
  landed** — not after R5 alone. Record (i) the corrected `tsl_exits` split and
  (ii) whether argmax moves off 0.04/0.01. **No parameter change here**;
  changing live's profit-lock is the modelling session's call.
- ~~The stop-width grid~~ — run 2026-09-25, argmax 5% → 7%, recorded above.
