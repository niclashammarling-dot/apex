"""Daily loss cap and sector cap as run() parameters in both engines (2026-10-17).
Daily loss: 5 % of the starting balance by default (live $500 / $10,000, demo $100 /
$2,000); before, both engines used config's $100 on a $10,000 simulation (1 %).
Sector cap: default unchanged (config 0.25); the profit read passes live's 0.30."""
import inspect

from backend.backtest import engine, engine_fast
from backend.backtest.live_shape import DAILY_LOSS_CAP_PCT
from tests.test_engine_flag_off_parity import END, RUN_KW, START, synthetic_precomputed


def test_both_engines_take_both_parameters_with_the_same_defaults():
    for mod in (engine, engine_fast):
        p = inspect.signature(mod.run).parameters
        assert p["daily_loss_cap"].default is None and p["max_sector_exposure"].default is None
        src = inspect.getsource(mod.run)
        assert "DAILY_LOSS_CAP_PCT * initial_balance" in src
        assert "if projected > sector_max" in src or "sector_cap = day_caps.get(sector, sector_max)" in src
    assert DAILY_LOSS_CAP_PCT == 0.05


def test_both_parameters_reach_the_decision():
    pre = synthetic_precomputed()
    kw = {k: v for k, v in RUN_KW.items() if k != "daily_loss_cap"}
    run = lambda **x: engine_fast.run(START.isoformat(), END.isoformat(), precomputed=pre, **{**kw, **x})
    assert run()["trade_log"] == run(daily_loss_cap=500.0)["trade_log"]       # default = 5 % of 10,000
    assert run(daily_loss_cap=0.0)["total_trades"] == 0 or \
           run(daily_loss_cap=0.0)["trade_log"] != run()["trade_log"]
    assert run(max_sector_exposure=0.05)["trade_log"] != run(max_sector_exposure=0.6)["trade_log"]
