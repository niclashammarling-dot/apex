"""R7 stop slippage (2026-10-17): live's stop legs fill at market after trigger, below
the engine's modelled price. Measured on 44 filled Alpaca sell-stop legs; see
exit_rules.py and EXIT_PARITY.md."""
import inspect

import pytest

from backend.backtest import engine, engine_fast
from backend.backtest.exit_rules import (STOP_SLIP_GAP_PCT, STOP_SLIP_INTRADAY_PCT, Bar,
                                         check_intraday_stop)
from tests.test_engine_flag_off_parity import END, RUN_KW, START, synthetic_precomputed

TRADE = {"entry_price": 100.0, "peak_price": 100.0}


def test_measured_values():
    assert (STOP_SLIP_INTRADAY_PCT, STOP_SLIP_GAP_PCT) == (0.0002, 0.005)


def test_intraday_trigger_fills_below_the_stop():
    bar = Bar(open=99.0, high=99.5, low=93.0, close=95.0)
    fill, reason, _ = check_intraday_stop(TRADE, bar, 0.06, 0.06)
    assert reason == "SL" and fill == pytest.approx(94.0 * (1 - STOP_SLIP_INTRADAY_PCT))
    assert check_intraday_stop(TRADE, bar, 0.06, 0.06, stop_slip=(0.0, 0.0))[0] == pytest.approx(94.0)


def test_gap_through_fills_below_the_open():
    bar = Bar(open=92.0, high=92.5, low=91.0, close=92.0)
    fill, _, _ = check_intraday_stop(TRADE, bar, 0.06, 0.06)
    assert fill == pytest.approx(92.0 * (1 - STOP_SLIP_GAP_PCT))


def test_engines_pass_it_through_and_zero_is_the_old_engine():
    for mod in (engine, engine_fast):
        assert inspect.signature(mod.run).parameters["stop_slip"].default is None
    pre = synthetic_precomputed()
    run = lambda **x: engine_fast.run(START.isoformat(), END.isoformat(), precomputed=pre, **{**RUN_KW, **x})
    zero, default = run(stop_slip=(0.0, 0.0)), run(stop_slip=None)
    stops = [t for t in zero["trade_log"] if t["exit_reason"] in ("SL", "TSL")]
    assert stops                                                          # the window has stop exits
    assert default["final_balance"] < zero["final_balance"]               # slip costs money
