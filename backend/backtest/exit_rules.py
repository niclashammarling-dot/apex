"""
Intraday bracket rules (stop leg and take-profit leg), shared by engine.py and
engine_fast.py.

Both engines used to decide every exit on the daily CLOSE. Live stops rest at
the exchange as Alpaca bracket legs and trigger intraday, so a day that trades
to -9% and closes at -3% is a realised stop-out live and was invisible to both
engines. This module is the one implementation of the intraday rule; neither
engine carries its own copy, because a duplicated exit rule is the parity bug
class this project already paid for once.

Every rule here is specified and justified in EXIT_PARITY.md. Read that before
changing any of it — the choices are deliberate and several of them are
deliberately conservative rather than obviously correct.

Summary of what is modelled:

  R1  trigger when low <= the stop resting at the START of the bar
  R2  gap-through (open <= stop) fills at the open, otherwise at the stop
  R3  when a bar touches both the stop and the target, the STOP wins, and the
      collision is counted rather than hidden
  R4  the trail is tested against the peak as it stood at bar start; the peak
      updates at bar end (live ratchets off a 5-minute poll, so it usually
      does not see the bar's high). `ratchet_from_high=True` is the opposite
      bound, kept so the spread is measurable.

  R7  stop slippage (2026-10-17): the fill from R2 is moved against the trade by
      STOP_SLIP_INTRADAY_PCT (stop triggered inside the bar) or STOP_SLIP_GAP_PCT
      (gap through the stop at the open), measured on live's stop fills.

  R8  the take-profit leg (2026-10-18, PRODUCTION_GAP row 24b): live's bracket rests
      a LIMIT sell at entry x (1 + tp) next to the stop, so it fills intraday when
      high >= limit, at the limit, and at the OPEN on a gap up through it (a limit
      fills at the limit or better). No slippage: R7 is a market-order effect.
      The open decides when it is beyond either leg. A bar that touches both legs
      from an open between them is ambiguous on daily bars: `tp_first=False` (the
      shipped floor) books the stop, `tp_first=True` the ceiling, as R4.

Not modelled: commission and fees (Alpaca: commission-free; SEC/CAT pass-through
$1.19 on $40,972 sold since 07-07, measured 10-12; Niclas 10-17: ignore).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

# R7 — measured 2026-10-17 on 44 filled sell-stop legs of the Alpaca paper account
# (2026-07-07 .. 10-16): fill vs the engine's model (stop price, or the open on a gap
# through it in the first minutes). Intraday triggers (n 37): median -0.02 %, mean
# +0.05 %; gaps at the open (n 7): median -0.50 %, mean -0.13 %. Medians, adverse
# side only; thin n, review 2026-10-31.
STOP_SLIP_INTRADAY_PCT = 0.0002
STOP_SLIP_GAP_PCT      = 0.005

__all__ = [
    "STOP_SLIP_GAP_PCT",
    "STOP_SLIP_INTRADAY_PCT",
    "Bar",
    "ParityStats",
    "build_ohlc_cache",
    "resting_stop",
    "check_intraday_bracket",
    "check_intraday_stop",
]


@dataclass(frozen=True)
class Bar:
    """One daily bar. `close` is required; the rest may be missing."""
    open:  float | None
    high:  float | None
    low:   float | None
    close: float


@dataclass
class ParityStats:
    """
    Counters for one run. `both_touched` and `both_touched_tp_on_close` are
    R3's exposure measurement and are the reason this is not just an int.
    """
    intraday_sl:  int = 0
    gap_sl:       int = 0
    intraday_tsl: int = 0
    gap_tsl:      int = 0

    # R3 — bars where the stop and the take-profit were BOTH touched, so the
    # daily bar cannot say which came first and the rule had to choose.
    both_touched: int = 0
    # The sign-flipped subset: of those, the ones the close-based engine would
    # have booked as a TP (a WIN) and this rule books as a stop (a LOSS).
    both_touched_tp_on_close: int = 0
    # R8 — the subset of both_touched whose open lay between the legs, so the
    # order really is unknown and `tp_first` decided it. The rest were decided
    # by a gap at the open.
    both_touched_ambiguous: int = 0

    # R8 — take-profit fills from the limit leg.
    intraday_tp: int = 0
    gap_tp:      int = 0
    # TP fills on a bar that CLOSED below the limit: the close-only engine
    # would not have booked a TP that day (EXIT_PARITY.md "one-sided").
    tp_missed_by_close: int = 0

    # (ticker, day, close, stop_price, fill) for exits the close-based SL
    # branch would not have produced at all.
    missed_by_close: list = field(default_factory=list)

    @property
    def total_stop_exits(self) -> int:
        return self.intraday_sl + self.gap_sl + self.intraday_tsl + self.gap_tsl

    def reset(self) -> None:
        self.intraday_sl = self.gap_sl = self.intraday_tsl = self.gap_tsl = 0
        self.both_touched = self.both_touched_tp_on_close = self.both_touched_ambiguous = 0
        self.intraday_tp = self.gap_tp = self.tp_missed_by_close = 0
        self.missed_by_close = []

    def summary(self) -> str:
        return (
            f"stop exits {self.total_stop_exits} "
            f"(SL {self.intraday_sl} intraday + {self.gap_sl} gap, "
            f"TSL {self.intraday_tsl} intraday + {self.gap_tsl} gap); "
            f"invisible to close-only SL {len(self.missed_by_close)}; "
            f"both-touched {self.both_touched}, "
            f"of which TP-on-close {self.both_touched_tp_on_close}, "
            f"ambiguous {self.both_touched_ambiguous}; "
            f"TP {self.intraday_tp} intraday + {self.gap_tp} gap, "
            f"invisible to close-only TP {self.tp_missed_by_close}"
        )


def build_ohlc_cache(
    raw_data: pd.DataFrame,
    tickers: list[str],
) -> dict[tuple[str, str], Bar]:
    """
    {(ticker, 'YYYY-MM-DD'): Bar} from the engine's own downloaded frame.

    Deliberately NOT forward-filled, unlike engine_fast's close `price_cache`.
    A forward-filled bar would carry a previous day's low into a day that never
    traded, minting an intraday stop trigger out of nothing. A ticker with no
    bar on a day simply has no entry, and the caller falls back to the
    close-based logic for it — the same treatment the reference
    (intraday_stop_experiment.py) gives it by building from a dropna'd series.
    """
    cache: dict[tuple[str, str], Bar] = {}
    available = set(raw_data.columns.get_level_values(0))

    for ticker in tickers:
        if ticker not in available:
            continue
        try:
            df = raw_data[ticker]
        except KeyError:
            continue
        try:
            closes = df["Close"].dropna()
        except KeyError:
            continue
        opens = df["Open"]  if "Open"  in df else None
        highs = df["High"]  if "High"  in df else None
        lows  = df["Low"]   if "Low"   in df else None

        for ts, close in closes.items():
            day = ts.date().isoformat()

            def _at(series):
                if series is None:
                    return None
                try:
                    v = series.loc[ts]
                except (KeyError, TypeError):
                    return None
                return None if pd.isna(v) else float(v)

            cache[(ticker, day)] = Bar(
                open=_at(opens), high=_at(highs), low=_at(lows), close=float(close)
            )
    return cache


def resting_stop(
    trade: dict,
    eff_sl: float,
    trailing_stop_pct:       float | None = None,
    profit_lock_trigger_pct: float | None = None,
    profit_lock_trail_pct:   float | None = None,
    *,
    peak: float | None = None,
) -> tuple[float, str]:
    """
    The stop price resting at the exchange at the START of today's bar, and
    the reason it would be booked under.

    Mirrors live: the bracket's STOP leg starts at the fixed stop and
    `_maybe_ratchet_bracket_sl` only ever PATCHes it UPWARD (its gate 4 skips
    when the computed stop would not move the stop up). So the resting stop is
    max(fixed, trailed) — never below the entry stop — and the fixed stop stays
    a hard floor, which is what wallet.py's chain says and what the engines'
    `elif` ladder did not do (see EXIT_PARITY.md R5).
    """
    entry = trade["entry_price"]
    fixed = entry * (1.0 - eff_sl)

    if peak is None:
        peak = trade.get("peak_price") or entry
    peak_gain = (peak - entry) / entry if entry else 0.0

    ratchet_active = bool(
        profit_lock_trigger_pct
        and profit_lock_trail_pct
        and peak_gain >= profit_lock_trigger_pct
    )
    if ratchet_active:
        trailed = peak * (1.0 - profit_lock_trail_pct)
        if trailed > fixed:
            return trailed, "TSL"
        return fixed, "SL"

    # Legacy bare-trailing mode: only reachable when profit-lock is not
    # configured at all. Production has not run this since 2026-06-03, so it
    # has no live counterpart to match; it is given the same never-below-fixed
    # treatment for consistency rather than from evidence.
    if trailing_stop_pct is not None and not (profit_lock_trigger_pct and profit_lock_trail_pct):
        trailed = peak * (1.0 - trailing_stop_pct)
        if trailed > fixed:
            return trailed, "TSL"

    return fixed, "SL"


def check_intraday_bracket(
    trade: dict,
    bar: Bar | None,
    eff_sl: float,
    eff_tp: float,
    trailing_stop_pct:       float | None = None,
    profit_lock_trigger_pct: float | None = None,
    profit_lock_trail_pct:   float | None = None,
    *,
    today_str: str = "",
    stats: ParityStats | None = None,
    ratchet_from_high: bool = False,
    stop_slip: tuple[float, float] = (STOP_SLIP_INTRADAY_PCT, STOP_SLIP_GAP_PCT),
    intraday_tp: bool = True,
    tp_first: bool = False,
) -> tuple[float, str, str] | None:
    """
    Did either leg of this trade's bracket fill inside today's bar?

    Returns (fill_price, reason, outcome) or None to hand the trade over to the
    close-based logic untouched. `fill_price` is UNROUNDED — derive pnl from it
    and round only when building the record, matching the reference's order of
    operations.

    `ratchet_from_high` is R4's opposite bound: when True the peak is lifted to
    today's high BEFORE the stop is tested, which is what a live system that
    observed the exact high would have done. The default (False) is the floor
    and the shipped behaviour.

    `stop_slip` is (intraday, gap) as fractions of the price; (0, 0) is the
    pre-2026-10-17 behaviour.

    `intraday_tp=False` is the pre-2026-10-18 stop-only model (targets on the
    close). `tp_first` is R8's ceiling for a bar that touched both legs from an
    open between them; the default books the stop (the floor).
    """
    if bar is None or bar.low is None or eff_sl is None:
        return None

    entry = trade["entry_price"]
    peak  = trade.get("peak_price") or entry
    if ratchet_from_high and bar.high is not None and bar.high > peak:
        peak = bar.high

    stop_price, reason = resting_stop(
        trade, eff_sl, trailing_stop_pct,
        profit_lock_trigger_pct, profit_lock_trail_pct,
        peak=peak,
    )
    tp_price = entry * (1.0 + eff_tp) if eff_tp is not None else None

    stop_hit = bar.low <= stop_price
    tp_touch = tp_price is not None and bar.high is not None and bar.high >= tp_price
    tp_hit   = intraday_tp and tp_touch
    if not (stop_hit or tp_hit):
        return None

    # The open is the first print, so a gap beyond either leg decides the bar.
    gap_stop = bar.open is not None and bar.open <= stop_price
    gap_tp   = tp_hit and not gap_stop and bar.open is not None and bar.open >= tp_price
    ambiguous = stop_hit and tp_touch and not gap_stop and not (bar.open is not None and bar.open >= tp_price)

    if gap_tp or (tp_hit and not stop_hit) or (tp_hit and ambiguous and tp_first):
        # R8 — the limit fills at the limit, or at the open on a gap up through it.
        fill = bar.open if gap_tp else tp_price
        if stats is not None:
            if gap_tp:
                stats.gap_tp += 1
            else:
                stats.intraday_tp += 1
            if (bar.close - entry) / entry < eff_tp:
                stats.tp_missed_by_close += 1
            if stop_hit:
                stats.both_touched += 1
                stats.both_touched_ambiguous += ambiguous
        return fill, "TP", "WIN"

    # R2 — a gap through the stop cannot fill at the stop.
    fill   = bar.open if gap_stop else stop_price
    # R7 — the live fill lands below the modelled price (market order after trigger).
    fill  *= 1.0 - (stop_slip[1] if gap_stop else stop_slip[0])

    # R3 — the bar also reached the target. The stop wins, but the collision is
    # counted, and separately the subset whose sign this rule flips.
    if stats is not None and tp_touch:
        stats.both_touched += 1
        stats.both_touched_ambiguous += ambiguous
        if (bar.close - entry) / entry >= eff_tp:
            stats.both_touched_tp_on_close += 1

    # Unrounded. The caller derives pnl from this and rounds only for display:
    # rounding here first, then computing pnl from the rounded value, is a
    # representational difference from the reference that survives a passing
    # equivalence check on a window where it happens not to move a decimal.
    pnl_pct = (fill - entry) / entry
    outcome = "WIN" if pnl_pct >= 0 else "LOSS"

    if stats is not None:
        if reason == "TSL":
            if gap_stop:
                stats.gap_tsl += 1
            else:
                stats.intraday_tsl += 1
        else:
            if gap_stop:
                stats.gap_sl += 1
            else:
                stats.intraday_sl += 1

        # Would the close-based SL branch have produced this exit at all?
        if reason == "SL" and (bar.close - entry) / entry > -eff_sl:
            stats.missed_by_close.append(
                (trade["ticker"], today_str,
                 round(bar.close, 2), round(stop_price, 2), round(fill, 2))
            )

    return fill, reason, outcome


def check_intraday_stop(trade: dict, bar: Bar | None, eff_sl: float, eff_tp: float,
                        *args, **kwargs) -> tuple[float, str, str] | None:
    """The stop leg alone (R1-R7): `check_intraday_bracket` with `intraday_tp=False`."""
    return check_intraday_bracket(trade, bar, eff_sl, eff_tp, *args, intraday_tp=False, **kwargs)
