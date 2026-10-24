"""T5 exposure read: rank p as the share of RND seeds at or above R1."""
import backend.backtest.t5_exposure_2026_10_20 as t5


def _rec(dep, tr, b2, n):
    return {"mean_deployed": dep, "total_return": tr, "B2_spy_em": {"total_return": b2}, "trades": n}


def test_rank_and_measures():
    data = {"arms": {"S": {"ref": _rec(0.4, 0.8, 0.5, 100),
                           "rnd": {"1": _rec(0.3, 0.3, 0.4, 80), "2": _rec(0.5, 0.9, 0.7, 120)}}}}
    r = t5.read(data)["S"]
    assert r["deployed"]["p"] == 0.5 and r["trades"]["p"] == 0.5
    assert r["return_per_deployed"]["ref"] == 2.0 and r["return_per_deployed"]["p"] == 0.0   # 1.0 and 1.8 < 2.0
    assert abs(r["return_minus_b2"]["ref"] - 0.3) < 1e-12 and r["return_minus_b2"]["p"] == 0.0
