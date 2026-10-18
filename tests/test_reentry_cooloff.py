"""Re-entry cooloff picked as live picks it (2026-10-18, PRODUCTION_GAP row 23):
`db.get_recently_exited_live_tickers` gives only TP/TSL wins the TP window; every other
exit, TIME/EXPIRED included, gets the SL window. Both engines used to set no cooloff
after a TIME exit."""
from datetime import date, timedelta

import pytest

from backend.backtest import engine, engine_fast
from backend.backtest.live_shape import reentry_cooloff_days
from tests.test_engine_flag_off_parity import END, RUN_KW, START, synthetic_precomputed


@pytest.mark.parametrize("reason, outcome, want", [
    ("TP", "WIN", 7), ("TSL", "WIN", 7),
    ("TSL", "LOSS", 1), ("SL", "LOSS", 1), ("SL", "WIN", 1),
    ("TIME", "EXPIRED", 1), ("REGIME", "WIN", 1),
    ("END_OF_BACKTEST", "WIN", 0),
])
def test_window_by_exit(reason, outcome, want):
    assert reentry_cooloff_days(reason, outcome, sl_days=1, tp_days=7) == want


def test_tsl_win_with_no_tp_window_is_not_given_the_sl_window():
    assert reentry_cooloff_days("TSL", "WIN", sl_days=5, tp_days=0) == 0


def _reentries_inside(log: list[dict], reason: str, days: int) -> list:
    out = []
    for t in log:
        if t["exit_reason"] != reason:
            continue
        until = date.fromisoformat(t["exit_date"]) + timedelta(days=days)
        out += [(t["ticker"], t["exit_date"], u["entry_date"]) for u in log
                if u["ticker"] == t["ticker"]
                and date.fromisoformat(t["exit_date"]) < date.fromisoformat(u["entry_date"]) < until]
    return out


def test_no_reentry_inside_the_sl_window_after_a_time_exit():
    """Fails on the parent: a time-stopped ticker was re-entered inside the SL window.
    engine.run downloads its data, so the slow engine is held by the source check below."""
    pre = synthetic_precomputed()
    kw = {**RUN_KW, "sl_cooldown_days": 20, "time_stop_days": 3}
    log = engine_fast.run(START.isoformat(), END.isoformat(), precomputed=pre, **kw)["trade_log"]
    assert any(t["exit_reason"] == "TIME" for t in log)                 # the window has time stops
    assert _reentries_inside(log, "TIME", 20) == []


def test_both_engines_take_the_window_from_the_shared_rule():
    import inspect
    for mod in (engine, engine_fast):
        src = inspect.getsource(mod.run)
        assert "reentry_cooloff_days(" in src and '_er in ("SL", "TSL")' not in src
