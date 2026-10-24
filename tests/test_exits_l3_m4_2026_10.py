"""L3+M4 card (2026-10-24): overrides per arm, M4's realised drawdown, the shifted synthetic arm, and the
rule's per-criterion controls."""
import numpy as np

import backend.backtest.exits_atr_2026_10 as xa
import backend.backtest.exits_l3_m4_2026_10 as l3


def test_overrides():
    m = xa.MEDIAN_ATR_IS_PICKS
    st = l3.over("STATIC")
    assert st["take_profit_pct"] == 0.06 and st["stop_loss_pct"] == 0.06 and "atr_exits" not in st
    tl = l3.over("TPLOCK")
    assert tl["atr_exit_mult"][1] is None and np.isclose(tl["atr_exit_mult"][0], 0.06 / m)
    assert np.allclose(tl["atr_lock_mult"], (0.04 / m, 0.01 / m)) and tl["stop_loss_pct"] == 0.06
    assert l3.over("ALLCAP8")["atr_sl_cap"] == 0.08 and "atr_sl_cap" not in l3.over("ALL")


def test_realised_drawdown_and_halt_date():
    tl = [{"pnl": 300.0, "exit_date": "2020-01-02"}, {"pnl": -700.0, "exit_date": "2020-01-03"},
          {"pnl": None, "exit_date": None}, {"pnl": 100.0, "exit_date": "2020-01-01"}]
    r = l3.realised_drawdown(tl)
    # 10,100 -> 10,400 (peak) -> 9,700: 700 / 10,400 = 6.73 % >= 6 %
    assert np.isclose(r["max_drawdown"], 700 / 10_400) and r["m4_halt_date"] == "2020-01-03"
    assert l3.realised_drawdown(tl[:1])["m4_halt_date"] is None


def test_shifted_raises_closed_pnl_by_the_shift():
    res = {"trade_log": [{"pnl": 10.0, "amount": 1000.0}, {"pnl": None, "amount": 500.0}], "equity_curve": []}
    s = l3.shifted(res)["trade_log"]
    assert np.isclose(s[0]["pnl"], 12.0) and s[1]["pnl"] is None


def _m(cd, calmar, dd):
    return {"pnl_per_capital_day": cd, "calmar": calmar, "m4": {"max_drawdown": dd}}


def test_rule_per_criterion_controls():
    ref = {s: _m(0.001, 1.0, 0.05) for s in range(5)}
    assert l3.better({s: _m(0.0012, 1.0, 0.05) for s in range(5)}, ref) == "better"
    assert l3.better(ref, ref) != "better"                                          # the reference itself
    assert l3.better({s: _m(0.0012, 1.0, 0.08) for s in range(5)}, ref) == "worse"   # drawdown 3 points deeper
    assert l3.better({s: _m(0.0008, 1.2, 0.04) for s in range(5)}, ref) == "worse"
    mixed = {s: _m(0.0012 if s < 3 else 0.0008, 1.0, 0.05) for s in range(5)}
    assert l3.better(mixed, ref) == "mixed"
