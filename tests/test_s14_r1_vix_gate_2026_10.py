"""S14 R1 card (2026-10-27): episode clusters, the cluster bootstrap, the centred null draw, the arms'
overrides, the period rows, and the card's rule with per-criterion controls."""
import numpy as np
import pandas as pd

import backend.backtest.exits_l3_m4_2026_10 as l3
import backend.backtest.s14_r1_vix_gate_2026_10 as s


def test_day_clusters_join_one_gap_day_and_number_calm_runs():
    days = [f"2020-01-{d:02d}" for d in range(1, 11)]
    level = [False, True, False, True, True, False, False, True, False, False]
    eps, cl = s.day_clusters(days, level)
    assert eps == [("2020-01-02", "2020-01-05", 3), ("2020-01-08", "2020-01-08", 1)]
    assert [cl[d] for d in days] == [2, 0, 0, 0, 0, 3, 3, 1, 4, 4]


def test_boot_ci_resamples_whole_clusters():
    rng = np.random.default_rng(0)
    x = np.array([1.0, 1.0, 1.0, 0.0])
    g = np.array([0, 0, 0, 1])
    c = s.boot_ci(x, g, rng)
    assert c["mean"] == 0.75 and c["n_episodes"] == 2 and c["n_rows"] == 4
    # Whole clusters only: a draw is {0,0}=1.0, {0,1}=0.75 or {1,1}=0.0, never a row-level mix.
    assert c["lo"] in (0.0, 0.75) and c["hi"] in (0.75, 1.0)
    assert s.boot_ci(np.empty(0), np.empty(0), rng)["mean"] is None


def test_null_draw_is_centred_shifted_and_keeps_episode_ids():
    rng = np.random.default_rng(1)
    calm = [np.array([0.02, 0.04]) for _ in range(10)]          # mean 0.03
    x, g = s.null_draw(calm, 0.03, [3, 1], 0.005, rng)
    assert len(x) == 8 and list(np.unique(g)) == [0, 1] and (g == 0).sum() == 6
    assert np.isclose(x.mean(), 0.005)


def test_overrides_differ_only_in_the_gate():
    on, off, unit = s.over("ON"), s.over("OFF"), s.over("R1UNIT")
    assert on["vix_threshold"] == 30.0 and off["vix_threshold"] is None
    assert {k: v for k, v in on.items() if k != "vix_threshold"} == l3.over("STATIC")
    assert {k: v for k, v in off.items() if k != "vix_threshold"} == l3.over("STATIC")
    assert set(unit) == {"peak_from_high", "peak_haircut"}


def test_period_rows_bucket_by_entry_date_and_count_vix_entries():
    tl = [{"entry_date": "2020-03-02", "pnl": 10.0, "amount": 1000.0, "days_held": 5},
          {"entry_date": "2022-05-02", "pnl": -5.0, "amount": 500.0, "days_held": 2},
          {"entry_date": "2019-05-02", "pnl": 3.0, "amount": 300.0, "days_held": 1},
          {"entry_date": "2020-07-01", "pnl": None, "amount": 100.0, "days_held": 0}]
    r = s.period_rows(tl, {"2020-03-02": 45.0, "2022-05-02": 29.9, "2020-07-01": 31.0})
    assert r["2020"]["trades"] == 1 and np.isclose(r["2020"]["pnl_per_capital_day"], 10 / 5000)
    assert r["2022"]["pnl"] == -5.0 and r["rest"]["trades"] == 1
    assert r["entries_on_vix_ge_30"] == 2                       # open trades count as entries


def test_in_period_rest_is_the_complement():
    d = pd.Series(["2020-01-31", "2020-02-01", "2020-06-30", "2021-06-01", "2022-12-31"])
    assert list(s.in_period(d, "2020")) == [False, True, True, False, False]
    assert list(s.in_period(d, "rest")) == [True, False, False, True, False]


def _m(cd, dd):
    return {"pnl_per_capital_day": cd, "m4": {"max_drawdown": dd}}


def test_l3_rule_per_criterion():
    on = {k: _m(0.001, 0.05) for k in range(5)}
    assert s.l3_rule(on, {k: _m(0.0012, 0.055) for k in range(5)})["costs"]
    assert not s.l3_rule(on, on)["costs"]                                         # gate off equal to on
    r = s.l3_rule(on, {k: _m(0.0012, 0.08) for k in range(5)})                   # 3 points deeper
    assert not r["costs"] and r["protects"]
    mixed = s.l3_rule(on, {k: _m(0.0012 if k < 3 else 0.0008, 0.05) for k in range(5)})
    assert not mixed["costs"] and not mixed["protects"]


def _L2(lo, hi, p2020=0.01, plus=True, zero_ok=True):
    ci = lambda m: {"mean": m}
    return {"controls": {"plus_supported": plus, "zero_ok": zero_ok}, "vix_ge_30": {"lo": lo, "hi": hi},
            "periods": {"2020": {"vix_ge_30": ci(p2020)}, "2022": {"vix_ge_30": ci(0.01)}}}


def _L3(off_cd, off_dd, ctl=True):
    return {"controls": {"a": ctl}, "IS": {"ON": {k: _m(0.001, 0.05) for k in range(5)},
                                           "OFF": {k: _m(off_cd, off_dd) for k in range(5)}}}


def test_decide_follows_the_card():
    assert s.decide(_L2(0.002, 0.02), _L3(0.0012, 0.055))["verdict"] == "costs money"
    assert s.decide(_L2(0.002, 0.02, p2020=-0.001), _L3(0.0012, 0.055))["verdict"] == "trade-off"   # 2020 alone negative
    assert s.decide(_L2(0.002, 0.02), _L3(0.0012, 0.08))["verdict"] == "protects"
    assert s.decide(_L2(-0.02, -0.002), _L3(0.0012, 0.055))["verdict"] == "protects"
    assert s.decide(_L2(-0.001, 0.02), _L3(0.0012, 0.055))["verdict"] == "trade-off"
    assert s.decide(_L2(0.002, 0.02, plus=False), _L3(0.0012, 0.055))["verdict"] == "unanswered (L2 control)"
    assert s.decide(_L2(0.002, 0.02, zero_ok=False), _L3(0.0012, 0.055))["verdict"] == "unanswered (L2 control)"
    assert s.decide(_L2(0.002, 0.02), _L3(0.0012, 0.055, ctl=False))["verdict"] == "unanswered (L3 control)"
    assert s.decide(_L2(-0.001, 0.02), None)["verdict"] == "L2 only: no verdict without L3"


def test_null_pass_rates_count_one_and_two_sided_passes():
    rng = np.random.default_rng(2)
    calm = [np.array([0.01, -0.01]) for _ in range(40)]          # centred: every draw's mean is exactly 0
    r = s.null_pass_rates(calm, 0.0, [2, 1, 3], 10, rng)
    assert r == {"draws": 10, "pass_rate": 0.0, "two_sided_rate": 0.0}
    up = [np.array([0.05, 0.05]) for _ in range(40)]              # not centred: every draw passes
    assert s.null_pass_rates(up, 0.0, [2, 1, 3], 5, rng)["pass_rate"] == 1.0


def test_calm_null_excludes_episode_days_and_vix_rows():
    df = pd.DataFrame({"date": ["d1", "d1", "d2", "d3", "d4"], "vix": [20, 20, 35, 20, 20],
                       "net": [0.01, 0.03, 0.5, 0.02, 0.04]})
    calm, mu, ep_days, eps, cl = s.calm_null(df)
    assert ep_days == [1] and [len(c) for c in calm] == [2, 1, 1] and np.isclose(mu, 0.025)
