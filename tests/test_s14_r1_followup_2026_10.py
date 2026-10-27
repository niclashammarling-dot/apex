"""S14 R1 follow-up (2026-10-27): the null-distribution test, the VIX tables, M4 crossings, the days since a
VIX >= 30 day and the displacement split."""
import numpy as np
import pandas as pd

import backend.backtest.s14_r1_followup_2026_10 as f


def test_null_p_is_the_share_at_or_above():
    ref = np.array([0.0, 0.01, 0.02, 0.03])
    assert f.null_p(0.02, ref) == 0.5 and f.null_p(0.05, ref) == 0.0 and f.null_p(-1.0, ref) == 1.0


def test_null_test_is_calibrated_and_has_power():
    # Calm days of mixed outcomes: the delta = 0 calibration passes near ALPHA, a large delta always passes.
    rng = np.random.default_rng(3)
    calm = [rng.normal(0.0, 0.05, 5) for _ in range(300)]
    mu = float(np.concatenate(calm).mean())
    r = f.null_test(calm, mu, [5, 3, 8], real=0.5, n=400, rng=np.random.default_rng(4))
    assert r["calibration_pass_rate"] <= 0.06
    assert r["p_real"] == 0.0 and r["power"]["0.0117"] >= r["power"]["0.005"]


def test_m4_crossings_count_separate_passages():
    tl = [{"pnl": -700.0, "exit_date": "2020-01-02"},     # 7 % down: first passage
          {"pnl": 500.0, "exit_date": "2020-01-03"},      # 2 % down: below half the halt, re-armed
          {"pnl": -500.0, "exit_date": "2020-01-04"},     # 7 % down again: second passage
          {"pnl": None, "exit_date": None}]
    r = f.m4_crossings(tl, start=10_000.0, halt=0.06)
    assert r["crossings"] == 2 and r["first"] == "2020-01-02" and np.isclose(r["max_drawdown"], 0.07)
    stay = [{"pnl": -700.0, "exit_date": "d1"}, {"pnl": 200.0, "exit_date": "d2"}, {"pnl": -200.0, "exit_date": "d3"}]
    assert f.m4_crossings(stay, start=10_000.0, halt=0.06)["crossings"] == 1   # 5 % never re-arms


def test_since_high_counts_trading_days():
    days = ["d1", "d2", "d3", "d4"]
    assert f.since_high(days, {"d1": 20, "d2": 31, "d3": 20, "d4": 20}) == {"d1": f.NEAR[-1][1], "d2": 0, "d3": 1, "d4": 2}


def test_displacement_splits_one_arm_entries_by_distance():
    on = [{"ticker": "A", "entry_date": "d2", "pnl": 10.0}, {"ticker": "B", "entry_date": "d4", "pnl": 5.0},
          {"ticker": "C", "entry_date": "d1", "pnl": 1.0}]
    off = [{"ticker": "C", "entry_date": "d1", "pnl": 1.0}, {"ticker": "V", "entry_date": "d0", "pnl": 7.0},
           {"ticker": "D", "entry_date": "d3", "pnl": -2.0}]
    vix = {"d0": 35.0}
    since = {"d0": 0, "d1": 1, "d2": 2, "d3": 30, "d4": 100}
    r = f.displacement(on, off, vix, since)
    assert r["shared"] == 1
    assert r["0-25"] == {"on_only": 1, "on_only_pnl": 10.0, "off_only": 0, "off_only_pnl": 0.0}   # V is a VIX entry: left out
    assert r["26-60"]["off_only"] == 1 and r["26-60"]["off_only_pnl"] == -2.0
    assert r["61-1000000"]["on_only"] == 1


def test_vix_tables_years_and_episodes():
    df = pd.DataFrame({"date": ["2020-03-02", "2020-03-02", "2021-05-03", "2025-04-07"],
                       "vix": [45.0, 45.0, 20.0, 33.0], "net": [0.02, -0.01, 0.01, -0.03],
                       "exit_live": ["TP", "SL", "TIME", "SL"]})
    cl = {"2020-03-02": 0, "2025-04-07": 1, "2021-05-03": 2}
    t = f.vix_tables(df, cl, n_eps=2)
    assert t["by_year"]["ge_40"]["2020"]["n"] == 2 and "2025" not in t["by_year"]["ge_40"]
    assert t["by_year"]["ge_30"]["2025"]["mean"] == -0.03
    assert [e["n_ge_40"] for e in t["episodes"]] == [2, 0] and t["episodes_positive"] == 1
    assert t["calm_runs"] == 1 and t["calm_runs_positive"] == 1


def test_fine_bins_count_consistency():
    df = pd.DataFrame({"date": ["2020-01-02", "2020-02-03", "2021-01-04", "2021-01-05"],
                       "vix": [21.0, 21.5, 23.0, 13.0], "net": [0.02, -0.01, -0.03, 0.01]})
    r = f.fine_bins(df, [0, 20, 22, 24])
    assert r["[20, 22)"] == {"n": 2, "days": 2, "win": 0.5, "mean": 0.005, "sum": 0.01,
                                 "years_positive": 1, "years": 1, "months_positive": 0.5}
    assert r["[22, 24)"]["years_positive"] == 0 and r["[0, 20)"]["n"] == 1


def test_tiers_baseline_day_spread_and_below_10():
    df = pd.DataFrame({"date": ["2017-05-01", "2017-05-01", "2018-01-02", "2020-03-02"],
                       "vix": [9.5, 9.5, 9.8, 31.0], "net": [0.02, -0.01, 0.03, -0.02]})
    r = f.tiers(df)
    assert r["all"] == {"n": 4, "win": 0.5, "mean": 0.005}
    assert r["day_vix"]["days"] == 3 and r["day_vix"]["below_10_by_year"] == {"2017": 1, "2018": 1}
    assert r["tiers"]["[0, 10)"]["n"] == 3 and r["tiers"]["[0, 10)"]["days"] == 2 and r["tiers"]["[30, 35)"]["n"] == 1
