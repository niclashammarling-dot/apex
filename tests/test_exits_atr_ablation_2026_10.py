"""ATR leg ablation (2026-10-24): each arm scales exactly its legs, the median entry gets live's legs,
the tail criterion and the deciles read as the card states."""
import numpy as np
import pandas as pd

import backend.backtest.exits_atr_2026_10 as xa
import backend.backtest.exits_atr_ablation_2026_10 as ab


def test_each_arm_scales_only_its_legs():
    a = 2 * xa.MEDIAN_ATR_IS_PICKS
    live = ab.LIVE
    assert ab.leg_cfg("STATIC", a) == live
    tl = ab.leg_cfg("TPLOCK", a)
    assert np.isclose(tl["tp"], 0.12) and tl["sl"] == live["sl"] and np.allclose(tl["pl"], [0.08, 0.02])
    assert ab.leg_cfg("TP", a)["pl"] == live["pl"] and np.isclose(ab.leg_cfg("TP", a)["tp"], 0.12)
    assert ab.leg_cfg("LOCK", a)["tp"] == live["tp"] and np.allclose(ab.leg_cfg("LOCK", a)["pl"], [0.08, 0.02])
    s = ab.leg_cfg("SL", a)
    assert np.isclose(s["sl"], 0.12) and s["tp"] == live["tp"] and s["pl"] == live["pl"]
    al = ab.leg_cfg("ALL", a)
    assert np.isclose(al["tp"], 0.12) and np.isclose(al["sl"], 0.12)


def test_median_entry_gets_live_legs_in_every_arm():
    for arm in ab.ABLATION:
        c = ab.leg_cfg(arm, xa.MEDIAN_ATR_IS_PICKS)
        assert np.isclose(c["tp"], 0.06) and np.isclose(c["sl"], 0.06) and np.allclose(c["pl"], [0.04, 0.01])


def test_p3_arms_come_from_the_atr_card():
    assert ab.leg_cfg("P3_static", 0.05) == xa.arm_cfg("P3_static", 0.05)
    assert ab.leg_cfg("P3_atr", 0.05) == xa.arm_cfg("P3_atr", 0.05)
    assert ab.peaks_of("P3_atr") == ("polled",) and len(ab.peaks_of("TPLOCK")) == 3


def _R(p1_static, p1_arm):
    def arm(p1):
        return {"tail": {"p1": p1}}
    return {"arms": {"STATIC|polled|picks": arm(p1_static), "TPLOCK|polled|picks": arm(p1_arm)}}


def test_tail_criterion():
    assert ab.tail_ok(_R(-0.09, -0.095), _R(-0.09, -0.099), "TPLOCK")
    assert not ab.tail_ok(_R(-0.09, -0.133), _R(-0.09, -0.09), "TPLOCK")
    st = {"arms": {"STATIC|polled|picks": {"tail": {"p1": -0.09}}}}
    assert ab.tail_ok(st, st, "STATIC")                              # the control: STATIC passes itself


def test_deciles_split_by_entry_atr():
    rng = np.random.default_rng(0)
    n_days, n_t = 300, 20
    days = [f"d{i}" for i in range(n_days)]
    atr = pd.DataFrame(rng.uniform(0.01, 0.05, (n_days, n_t)), index=days)
    close = pd.DataFrame(100.0, index=days, columns=range(n_t))
    P = {"atr": atr, "low": close * 0.99, "close": close}
    E = [(i, j) for i in range(0, n_days - 30, 3) for j in range(n_t)]
    rows = [{"pnl": float(rng.normal(0, 0.03)), "hold": 5, "reason": "TIME", "exit_idx": i + 5} for i, _ in E]
    d = ab.deciles(P, E, rows)
    assert len(d) == 10 and sum(v["n"] for v in d.values()) == len(E)
    assert d["0"]["atr_hi"] <= d["9"]["atr_lo"]
