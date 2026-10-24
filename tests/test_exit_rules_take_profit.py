"""R8 take-profit leg (2026-10-18, PRODUCTION_GAP row 24b): live's bracket rests a LIMIT
sell at entry x (1 + tp) beside the stop, so a target fills intraday at the limit, or at
the open on a gap up through it. Until R8 both engines booked targets on the close only
(EXIT_PARITY.md, "The model is one-sided")."""
import inspect
from datetime import date

import pytest

from backend.backtest import engine, engine_fast
from backend.backtest.exit_rules import (Bar, ParityStats, check_intraday_bracket,
                                         check_intraday_stop)
from tests.test_engine_flag_off_parity import END, RUN_KW, START, synthetic_precomputed

TRADE = {"ticker": "X", "entry_price": 100.0, "peak_price": 100.0}
SL, TP = 0.06, 0.06          # stop 94, limit 106


def test_limit_touched_inside_the_bar_fills_at_the_limit_without_slip():
    stats = ParityStats()
    bar = Bar(open=101.0, high=107.0, low=100.0, close=104.0)
    fill, reason, outcome = check_intraday_bracket(TRADE, bar, SL, TP, stats=stats)
    assert (fill, reason, outcome) == (pytest.approx(106.0), "TP", "WIN")
    assert (stats.intraday_tp, stats.gap_tp, stats.tp_missed_by_close) == (1, 0, 1)


def test_gap_up_through_the_limit_fills_at_the_open():
    stats = ParityStats()
    bar = Bar(open=108.0, high=109.0, low=107.5, close=108.5)
    fill, reason, _ = check_intraday_bracket(TRADE, bar, SL, TP, stats=stats)
    assert (fill, reason) == (pytest.approx(108.0), "TP")
    assert (stats.intraday_tp, stats.gap_tp, stats.tp_missed_by_close) == (0, 1, 0)


def test_both_legs_from_an_open_between_them_floor_and_ceiling():
    bar = Bar(open=100.0, high=107.0, low=93.0, close=100.0)
    floor, ceiling = ParityStats(), ParityStats()
    assert check_intraday_bracket(TRADE, bar, SL, TP, stats=floor, stop_slip=(0, 0))[1] == "SL"
    assert check_intraday_bracket(TRADE, bar, SL, TP, stats=ceiling, tp_first=True)[:2] == (pytest.approx(106.0), "TP")
    for s in (floor, ceiling):
        assert (s.both_touched, s.both_touched_ambiguous) == (1, 1)


def test_a_gap_beyond_either_leg_decides_the_bar_whatever_tp_first_says():
    down = Bar(open=93.0, high=107.0, low=92.0, close=100.0)       # opens through the stop
    up   = Bar(open=107.0, high=108.0, low=93.0, close=100.0)      # opens through the limit
    for tp_first in (False, True):
        s = ParityStats()
        assert check_intraday_bracket(TRADE, down, SL, TP, stats=s, tp_first=tp_first)[1] == "SL"
        assert check_intraday_bracket(TRADE, up, SL, TP, stats=s, tp_first=tp_first)[:2] == (pytest.approx(107.0), "TP")
        assert (s.both_touched, s.both_touched_ambiguous) == (2, 0)


def test_stop_only_model_is_the_pre_r8_rule():
    bar = Bar(open=101.0, high=107.0, low=100.0, close=104.0)
    assert check_intraday_bracket(TRADE, bar, SL, TP, intraday_tp=False) is None
    assert check_intraday_stop(TRADE, bar, SL, TP) is None


@pytest.mark.parametrize("eng", ["fast", "slow"])
def test_engine_exit_check_books_the_intraday_target(eng):
    """Fails on the parent: a bar that trades through the limit and closes below it
    was no exit at all in the close-only TP model."""
    import pandas as pd
    day, bar = "2026-03-03", (101.0, 107.0, 100.0, 104.0)
    trade = {**TRADE, "sector": "S", "entry_date": "2026-03-02", "shares": 1.0,
             "amount": 100.0, "signal_score": 0.8}
    ohlc = {("X", day): Bar(*bar)}
    args = (date.fromisoformat(day), day, TP, SL, 30, None, None, None)
    if eng == "fast":
        closed = engine_fast._check_exits_fast([dict(trade)], {("X", day): bar[3]}, *args, ohlc_cache=ohlc)
    else:
        frame = pd.DataFrame([bar], index=pd.DatetimeIndex([pd.Timestamp(day)]),
                             columns=pd.MultiIndex.from_product([["X"], ["Open", "High", "Low", "Close"]]))
        closed = engine._check_exits([dict(trade)], frame, *args, ohlc_cache=ohlc)
    assert len(closed) == 1
    rec = closed[0]["record"]
    assert (rec["exit_reason"], rec["exit_price"], rec["pnl_pct"]) == ("TP", 106.0, 0.06)


def test_engines_default_to_the_live_bracket_and_the_switch_changes_the_run():
    for mod in (engine, engine_fast):
        params = inspect.signature(mod.run).parameters
        assert params["intraday_tp"].default is True and params["tp_first"].default is False
    pre = synthetic_precomputed()
    run = lambda **x: engine_fast.run(START.isoformat(), END.isoformat(), precomputed=pre, **{**RUN_KW, **x})
    off, on = run(intraday_tp=False), run(intraday_tp=True)
    tps = lambda r: [t for t in r["trade_log"] if t["exit_reason"] == "TP"]
    assert tps(off)                                                   # the window has targets
    assert [t["exit_date"] for t in tps(on)] != [t["exit_date"] for t in tps(off)]
