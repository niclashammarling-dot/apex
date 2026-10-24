"""Row 14 (2026-10-18): live's portfolio roof has no hard cap. At or above max_positions,
slot n over the roof needs the raw signal score to clear the sector threshold x
(1 + n x overflow_quant_increment); a rejection moves on to the next candidate.
Both engines used to stop at max_positions."""
import inspect
import math
from datetime import date, timedelta
from unittest.mock import patch

from backend.backtest import engine, engine_fast
from backend.backtest.live_shape import live_overflow_increment, overflow_admits
from tests.test_engine_flag_off_parity import END, RUN_KW, START, synthetic_precomputed


def test_roof_rule():
    assert overflow_admits(0.10, 8, 9, 0.73, 0.05)                    # below the roof: anything
    assert not overflow_admits(0.766, 9, 9, 0.73, 0.05)               # slot 1 needs 0.7665
    assert overflow_admits(0.7665, 9, 9, 0.73, 0.05)                  # rounded to 4 places, as live
    assert not overflow_admits(0.80, 10, 9, 0.73, 0.05)               # slot 2 needs 0.803
    assert not overflow_admits(9.99, 9, 9, 0.73, math.inf)            # inf: the old hard stop


def test_increment_comes_from_live_config():
    with patch("backend.live_config.get_live_config", return_value={"overflow_quant_increment": 0.05}):
        assert live_overflow_increment() == 0.05
    for mod in (engine, engine_fast):
        assert inspect.signature(mod.run).parameters["overflow_quant_increment"].default is None
        assert "overflow_admits(" in inspect.getsource(mod.run)


def _max_concurrent(log):
    days = {}
    for t in log:
        d, end = date.fromisoformat(t["entry_date"]), date.fromisoformat(t["exit_date"])
        while d < end:
            days[d] = days.get(d, 0) + 1
            d += timedelta(days=1)
    return max(days.values(), default=0)


def test_engine_fast_goes_over_the_roof_only_when_the_bar_allows():
    """Fails on the parent: engine_fast broke out of the entry loop at max_positions."""
    pre = synthetic_precomputed()
    run = lambda **x: engine_fast.run(START.isoformat(), END.isoformat(), precomputed=pre,
                                      **{**RUN_KW, "max_positions": 1, **x})["trade_log"]
    assert _max_concurrent(run(overflow_quant_increment=math.inf)) == 1
    assert _max_concurrent(run(overflow_quant_increment=0.0)) > 1      # a flat bar: no roof
