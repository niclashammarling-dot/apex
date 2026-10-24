"""ATR-scaled exits card (2026-10-24): the twin's legs, the median identity, the rule a-e, and the
synthetic-twin controls the card requires before any reading."""
import math

import numpy as np
import pandas as pd

import backend.backtest.exits_atr_2026_10 as xa
import backend.backtest.exits_t8_2026_10 as t8


def test_median_entry_gets_the_static_legs():
    med = xa.MEDIAN_ATR_IS_PICKS
    for p, s in xa.STATIC.items():
        tw = xa.twin_cfg(p, med)
        for leg in ("tp", "sl"):
            assert math.isclose(tw[leg], s[leg], rel_tol=1e-12) or (tw[leg] >= xa.NONE and s[leg] >= xa.NONE)
        if s["pl"]:
            assert np.allclose(tw["pl"], s["pl"])
        else:
            assert tw["pl"] is None
        assert tw["tdays"] == s["tdays"]


def test_every_leg_scales_with_atr_including_the_lock():
    tw = xa.twin_cfg("P1", 2 * xa.MEDIAN_ATR_IS_PICKS)
    assert np.isclose(tw["tp"], 0.12) and np.isclose(tw["sl"], 0.12)
    assert np.allclose(tw["pl"], [0.08, 0.02])                       # T8 left the lock fixed
    assert xa.twin_cfg("P2", 2 * xa.MEDIAN_ATR_IS_PICKS)["sl"] >= xa.NONE  # no stop stays no stop


def test_no_usable_atr_falls_back_to_static():
    for bad in (0.0, float("nan"), -1.0, None):
        assert xa.twin_cfg("P1", bad) == xa.STATIC["P1"]


def test_arms_and_peaks():
    assert xa.arms() == ["P1_static", "P1_atr", "P2_static", "P2_atr", "P3_static", "P3_atr"]
    assert xa.peaks_of("P1_atr") == t8.PEAKS and xa.peaks_of("P2_atr") == (t8.DECIDING_PEAK,)


def test_rule_follows_card_item_9():
    pos, neg = {"t": 3.0}, {"t": -3.0, "p_neg_holm": True}
    assert xa.rule(pos, {"t": 1.2}, True, True, 0.001, None)["verdict"] == "supported"
    assert xa.rule(pos, {"t": 1.2}, True, False, 0.001, None)["verdict"] == "lock-model-dependent"
    assert xa.rule(pos, {"t": 1.2}, True, True, 0.001, -2.5)["verdict"] == "entry-dependent"
    assert xa.rule(pos, {"t": 1.2}, True, True, -0.001, None)["verdict"] == "unanswered"     # cost gate
    assert xa.rule(pos, {"t": 0.5}, True, True, 0.001, None)["verdict"] == "unanswered"      # OOS sign check
    assert xa.rule(neg, {"t": -0.5}, True, True, -0.001, None)["verdict"] == "contradicted"
    assert xa.rule(neg, {"t": 1.5}, True, True, -0.001, None)["verdict"] == "unanswered"


def test_synthetic_twin_shifts_capital_day_by_delta():
    rng = np.random.default_rng(0)
    rows = [{"pnl": float(x), "hold": 5, "reason": "TIME", "exit_idx": 5} for x in rng.normal(0, 0.05, 2000)]
    syn = xa.synthetic(rows, 0.001, np.random.default_rng(1))
    d = (t8._pnl(syn).sum() - t8._pnl(rows).sum()) / (5 * len(rows))
    assert abs(d - 0.001) < 0.0005
    assert xa.synthetic(rows, 0.0, np.random.default_rng(1))[0]["hold"] == 5


def _P(n_months=60, per_month=40, seed=2):
    days = [str(d.date()) for d in pd.bdate_range("2019-01-01", periods=n_months * 21)]
    return {"days": days}, [(min(m * 21 + k % 21, len(days) - 1), 0) for m in range(n_months) for k in range(per_month)]


def test_controls_pass_and_fail_as_the_card_requires():
    P, E = _P()
    rng = np.random.default_rng(3)
    rows = [{"pnl": float(x), "hold": int(h), "reason": "TIME", "exit_idx": 0}
            for x, h in zip(rng.normal(0.005, 0.05, len(E)), rng.integers(1, 25, len(E)))]
    R = {("P1_static", t8.DECIDING_PEAK, "picks"): rows}
    c = xa.controls(R, R, P, P, E, E)
    assert c["plus"] == "supported" and c["minus"] == "contradicted"
    assert c["zero_supported_rate"] <= 0.15


def test_paired_trade_t_and_net_diff():
    a = [{"pnl": 0.02, "hold": 2}, {"pnl": 0.00, "hold": 2}]
    r = [{"pnl": 0.01, "hold": 2}, {"pnl": 0.00, "hold": 2}]
    assert np.isclose(xa.net_diff(a, r, 0), 0.01 / 4)
    assert np.isclose(xa.net_diff(a, r, 10), 0.01 / 4)               # same turnover: cost cancels
    pt = xa.paired_trade_t(a, r)
    assert pt["n"] == 2 and np.isclose(pt["mean"], 0.005)
