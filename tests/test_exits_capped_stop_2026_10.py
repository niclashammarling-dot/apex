"""Capped ATR stop (2026-10-24): the ceiling binds only the stop, below it the stop is the ATR twin's,
and binding routes the ablation pipeline to this card's arms."""
import importlib

import numpy as np

import backend.backtest.exits_atr_2026_10 as xa
import backend.backtest.exits_atr_ablation_2026_10 as ab
import backend.backtest.exits_capped_stop_2026_10 as cs


def test_ceiling_binds_only_the_stop():
    hi = 4 * xa.MEDIAN_ATR_IS_PICKS                       # scaled stop 24 %
    c = cs.leg_cfg("ALLCAP8", hi)
    assert np.isclose(c["sl"], 0.08) and np.isclose(c["tp"], 0.24) and np.allclose(c["pl"], [0.16, 0.04])
    assert np.isclose(cs.leg_cfg("ALLCAP10", hi)["sl"], 0.10)
    s = cs.leg_cfg("SLCAP8", hi)
    assert np.isclose(s["sl"], 0.08) and s["tp"] == ab.LIVE["tp"] and s["pl"] == ab.LIVE["pl"]


def test_below_the_ceiling_the_stop_is_the_twins():
    lo = xa.MEDIAN_ATR_IS_PICKS                           # scaled stop 6 %
    for arm in cs.CAPPED:
        assert np.isclose(cs.leg_cfg(arm, lo)["sl"], 0.06)
    assert cs.leg_cfg("STATIC", lo) == ab.LIVE


def test_bind_points_the_pipeline_at_the_capped_arms():
    try:
        cs.bind()
        assert ab.ABLATION == list(cs.CAPPED) and ab.all_arms()[0] == "STATIC"
        assert ab.leg_cfg is cs.leg_cfg
    finally:
        importlib.reload(ab)


def test_ceiling_share():
    import pandas as pd
    atr = pd.DataFrame([[0.02, 0.04]])                     # stops 5.0 % and 10.1 %
    sh = cs.ceiling_share({"atr": atr}, [(0, 0), (0, 1)])
    assert sh == {"0.08": 0.5, "0.1": 0.5}
