"""engine_fast `atr_exits` and `atr_exit_mult` (T8 ATRSYM, 2026-10-21): per-trade TP/SL = atr_pct x mult."""
from backend.backtest import engine_fast
from tests.test_engine_flag_off_parity import END, RUN_KW, START, synthetic_precomputed


def _run(pre, **kw):
    return engine_fast.run(START.isoformat(), END.isoformat(), precomputed=pre, **{**RUN_KW, **kw})


def test_default_mult_is_the_engine_rule_and_another_mult_changes_the_exits():
    pre = synthetic_precomputed()
    base = _run(pre, atr_exits=True)
    assert base["trade_log"]                                                       # the fixture trades
    assert _run(pre, atr_exits=True, atr_exit_mult=(2.0, 1.0))["trade_log"] == base["trade_log"]
    wide = _run(pre, atr_exits=True, atr_exit_mult=(8.0, 8.0))["trade_log"]
    assert wide != base["trade_log"]                                               # the multiplier reaches the exits


def test_mult_is_inert_without_atr_exits():
    pre = synthetic_precomputed()
    assert _run(pre, atr_exit_mult=(8.0, 8.0))["trade_log"] == _run(pre)["trade_log"]
