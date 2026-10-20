"""R9 (EXIT_PARITY.md, 2026-10-20): an open trade's peak carries the bar's high, after the
bar's own exit tests. Both engines; the case fails with peak_from_high=False (the parent)."""
from datetime import date

import pandas as pd
import pytest

import backend.backtest.engine as slow
import backend.backtest.engine_fast as fast
from backend.backtest.exit_rules import Bar

DAYS = ["2026-03-02", "2026-03-03", "2026-03-04"]
# Day 1: high +5 % (arms the 4 % lock), close +2 %, low above the fixed stop.
# Day 2: opens above 105 x 0.99, trades down through it, closes at +4 %; no TP, no fixed SL.
BARS = {DAYS[1]: Bar(open=100.5, high=105.0, low=100.0, close=102.0),
        DAYS[2]: Bar(open=104.5, high=104.6, low=103.5, close=104.0)}


def _trade():
    return {"ticker": "PKH", "sector": "Technology", "entry_date": DAYS[0], "entry_price": 100.0,
            "peak_price": 100.0, "shares": 1.0, "amount": 100.0, "signal_score": 0.7}


def _run(engine: str, peak_from_high: bool):
    trade, ohlc = _trade(), {("PKH", d): b for d, b in BARS.items()}
    price = {("PKH", d): b.close for d, b in BARS.items()}
    frame = pd.DataFrame({("PKH", "Close"): [None] + [BARS[d].close for d in DAYS[1:]]},
                         index=pd.to_datetime(DAYS))
    fn = slow._check_exits if engine == "slow" else fast._check_exits_fast
    for d in DAYS[1:]:
        closed = fn([trade], frame if engine == "slow" else price, date.fromisoformat(d), d,
                    0.06, 0.06, 25, None, 0.04, 0.01, ohlc_cache=ohlc, stop_slip=(0.0, 0.0),
                    peak_from_high=peak_from_high)
        if closed:
            return d, closed[0]["record"]
    return None, trade


@pytest.mark.parametrize("engine", ["fast", "slow"])
def test_lock_armed_by_the_high_fills_next_bar_at_the_trail(engine):
    d, rec = _run(engine, True)
    assert d == DAYS[2] and rec["exit_reason"] == "TSL"
    assert rec["exit_price"] == pytest.approx(105.0 * 0.99)          # stop resting at peak x (1 - trail)


@pytest.mark.parametrize("engine", ["fast", "slow"])
def test_close_peak_never_arms_the_lock(engine):
    d, trade = _run(engine, False)        # the parent: peak 102 on day 2's test, then 104 at its close
    assert d is None and trade["peak_price"] == pytest.approx(104.0)


def test_same_bar_high_does_not_fill_the_same_bar():
    # R4 holds: day 1's own low (100.0) is under 105 x 0.99, yet day 1 books nothing.
    trade, ohlc = _trade(), {("PKH", DAYS[1]): BARS[DAYS[1]]}
    closed = fast._check_exits_fast([trade], {("PKH", DAYS[1]): 102.0}, date.fromisoformat(DAYS[1]), DAYS[1],
                                    0.06, 0.06, 25, None, 0.04, 0.01, ohlc_cache=ohlc, stop_slip=(0.0, 0.0))
    assert closed == [] and trade["peak_price"] == 105.0
