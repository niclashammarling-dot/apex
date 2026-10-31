"""Row 11 (2026-10-18): live's ETF negative penalty in both engines, on the return live's
Lock 4 reads. `l5_positioning` takes last / first of `period="8d"` (8 rows: 7 intervals),
compares it in percent rounded to 2 decimals, and the chain gates on score × (1 − p)
against the sector threshold without carrying the penalised score on."""
import inspect

import pandas as pd
import pytest

from backend.backtest import engine, engine_fast
from backend.backtest.live_shape import etf_penalty_fails, etf_return_as_live
from tests.test_engine_flag_off_parity import END, RUN_KW, START, synthetic_precomputed


def _closes(values):
    return pd.Series(values, index=pd.bdate_range("2026-10-01", periods=len(values)), dtype=float)


def test_return_spans_seven_intervals_like_the_8d_download():
    s = _closes([50, 60, 100, 101, 102, 103, 104, 105, 106, 98])
    today = s.index[-1].date()
    assert etf_return_as_live(s, today) == pytest.approx(98 / 100 - 1)      # 8 rows back to 100
    assert etf_return_as_live(s, s.index[3].date()) is None                 # 4 rows: an error in live
    assert etf_return_as_live(s, s.index[4].date()) == pytest.approx(102 / 50 - 1)


def test_gate_compares_the_rounded_percent_and_gates_only_below_the_threshold():
    assert not etf_penalty_fails(0.80, -0.009996, 0.15, -1.0, 0.73)   # -1.00 % is not below -1.0
    assert etf_penalty_fails(0.80, -0.0101, 0.15, -1.0, 0.73)          # 0.68 < 0.73
    assert not etf_penalty_fails(0.90, -0.0101, 0.15, -1.0, 0.73)      # 0.765 clears it
    for off in (None, 0.0):
        assert not etf_penalty_fails(0.80, -0.05, off, -1.0, 0.73)
    assert not etf_penalty_fails(0.80, None, 0.15, -1.0, 0.73)


def test_both_engines_take_the_same_parameters_off_by_default():
    for mod in (engine, engine_fast):
        p = inspect.signature(mod.run).parameters
        assert p["etf_negative_penalty"].default is None and p["etf_negative_floor"].default == -1.0
        src = inspect.getsource(mod.run)
        assert "etf_penalty_fails(" in src and "etf_return_as_live(" in src


def test_engine_fast_blocks_entries_when_on():
    """Fails on the parent: engine_fast had no row 11 (0 hits for etf_negative_penalty)."""
    pre = synthetic_precomputed()
    run = lambda **x: engine_fast.run(START.isoformat(), END.isoformat(), precomputed=pre, **{**RUN_KW, **x})
    off = run()
    on = run(etf_negative_penalty=0.99, etf_negative_floor=100.0)   # every ETF day is "below", nothing clears
    assert off["etf_penalty_blocked"] == [] and off["total_trades"] > 0
    assert on["total_trades"] == 0 and len(on["etf_penalty_blocked"]) > 0
    b = on["etf_penalty_blocked"][0]
    assert b["penalized_score"] == pytest.approx(b["original_score"] * 0.01, abs=1e-4)
