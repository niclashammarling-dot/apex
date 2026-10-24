"""engine_fast per-trade lock (`atr_lock_mult`), stop ceiling (`atr_sl_cap`) and a fixed leg under
atr_exits (`atr_exit_mult` element None), 2026-10-24 (L3+M4 card): off by default, each reaches the exits,
and a ceiling at or above every scaled stop changes nothing."""
from unittest.mock import patch

from backend.backtest import engine_fast
from tests.test_engine_flag_off_parity import END, RUN_KW, START, synthetic_precomputed


def _run(pre, **kw):
    return engine_fast.run(START.isoformat(), END.isoformat(), precomputed=pre, **{**RUN_KW, **kw})


def test_new_arguments_default_off():
    pre = synthetic_precomputed()
    base = _run(pre, atr_exits=True)["trade_log"]
    assert _run(pre, atr_exits=True, atr_lock_mult=None, atr_sl_cap=None)["trade_log"] == base


def test_a_none_leg_stays_fixed():
    pre = synthetic_precomputed()
    both_none = _run(pre, atr_exits=True, atr_exit_mult=(None, None))["trade_log"]
    assert both_none == _run(pre)["trade_log"]                                     # no scaled leg = the fixed rule


def test_per_trade_lock_reaches_the_exits_and_is_read_per_trade():
    pre = synthetic_precomputed()
    kw = dict(atr_exits=True, profit_lock_trigger_pct=0.04, profit_lock_trail_pct=0.01)
    base = _run(pre, **kw)["trade_log"]
    seen = []
    real = engine_fast._check_exits_fast

    def spy(open_trades, *a, **k):
        seen.extend(t.get("pl_trig") for t in open_trades)
        return real(open_trades, *a, **k)
    with patch.object(engine_fast, "_check_exits_fast", spy):
        tight = _run(pre, atr_lock_mult=(0.1, 0.05), **kw)["trade_log"]
    assert any(v is not None for v in seen)                                        # trades carry their own lock
    assert tight != base


def test_a_ceiling_above_every_stop_is_inert_and_a_low_one_binds():
    pre = synthetic_precomputed()
    kw = dict(atr_exits=True, atr_exit_mult=(2.0, 3.0))
    base = _run(pre, **kw)["trade_log"]
    assert _run(pre, atr_sl_cap=10.0, **kw)["trade_log"] == base
    assert _run(pre, atr_sl_cap=0.001, **kw)["trade_log"] != base
