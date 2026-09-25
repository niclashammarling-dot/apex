"""
Intraday stop rules, shared by engine.py and engine_fast.py.

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

Not modelled anywhere in backend/backtest/: slippage and commission. A live
stop fills at MARKET after trigger, so R2 is optimistic by construction.
Measuring that bias is step (2) of the parity sequence, not this module.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

__all__ = [
    "Bar",
    "ParityStats",
    "build_ohlc_cache",
    "resting_stop",
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

    # (ticker, day, close, stop_price, fill) for exits the close-based SL
    # branch would not have produced at all.
    missed_by_close: list = field(default_factory=list)

    @property
    def total_stop_exits(self) -> int:
        return self.intraday_sl + self.gap_sl + self.intraday_tsl + self.gap_tsl

    def reset(self) -> None:
        self.intraday_sl = self.gap_sl = self.intraday_tsl = self.gap_tsl = 0
        self.both_touched = self.both_touched_tp_on_close = 0
        self.missed_by_close = []

    def summary(self) -> str:
        return (
            f"stop exits {self.total_stop_exits} "
            f"(SL {self.intraday_sl} intraday + {self.gap_sl} gap, "
            f"TSL {self.intraday_tsl} intraday + {self.gap_tsl} gap); "
            f"invisible to close-only SL {len(self.missed_by_close)}; "
            f"both-touched {self.both_touched}, "
            f"of which TP-on-close {self.both_touched_tp_on_close}"
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


def check_intraday_stop(
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
) -> tuple[float, str, str] | None:
    """
    Did this trade's resting stop trigger inside today's bar?

    Returns (fill_price, reason, outcome) or None to hand the trade over to the
    close-based logic untouched. `fill_price` is UNROUNDED — derive pnl from it
    and round only when building the record, matching the reference's order of
    operations.

    `ratchet_from_high` is R4's opposite bound: when True the peak is lifted to
    today's high BEFORE the stop is tested, which is what a live system that
    observed the exact high would have done. The default (False) is the floor
    and the shipped behaviour.
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

    if bar.low > stop_price:
        return None

    # R2 — a gap through the stop cannot fill at the stop.
    gapped = bar.open is not None and bar.open <= stop_price
    fill   = bar.open if gapped else stop_price

    # R3 — the bar also reached the target. The stop wins, but the collision is
    # counted, and separately the subset whose sign this rule flips.
    tp_price = entry * (1.0 + eff_tp) if eff_tp is not None else None
    if stats is not None and tp_price is not None and bar.high is not None and bar.high >= tp_price:
        stats.both_touched += 1
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
            if gapped:
                stats.gap_tsl += 1
            else:
                stats.intraday_tsl += 1
        else:
            if gapped:
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
