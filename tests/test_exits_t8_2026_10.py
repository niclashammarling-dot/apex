"""Exits card T8 (2026-10-22): arm configs, purge, the peak model's reach, the statistics the decision
rule reads, the SL pre-check, and one L2 pass end to end on the engine's synthetic fixture."""
import json
import math
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

import backend.backtest.exits_t8_2026_10 as t8
import backend.backtest.ladder_2026_10 as ld
from backend.backtest.exit_rules import POLLED_PEAK_GAP, STOP_SLIP_GAP_PCT
from tests.test_engine_flag_off_parity import synthetic_precomputed


@pytest.fixture(scope="module")
def fx():
    pre = synthetic_precomputed()
    return pre, ld.panels(pre)


def _rows(pnl, hold, reason="TIME", exit_idx=None):
    return [{"pnl": p, "hold": h, "reason": reason, "exit_idx": (exit_idx or 0)} for p, h in zip(pnl, hold)]


def test_nine_non_live_arms_and_the_card_values():
    assert len(t8.NON_LIVE) == 9 and "LIVE" not in t8.NON_LIVE
    assert t8.ARMS["LIVE"] == {"tp": 0.06, "sl": 0.06, "pl": [0.04, 0.01], "tdays": 25}
    assert t8.ARMS["TRAIL2"]["pl"] == [0.04, 0.02] and t8.ARMS["TRAIL3"]["pl"] == [0.04, 0.03]
    assert t8.ARMS["TP4NS"] == {"tp": 0.04, "sl": t8.NONE, "pl": None, "tdays": 25}
    assert "after reading" in t8.LABELS["TP4NS"]


def test_entry_cfg_atr_arms():
    assert t8.entry_cfg("ATR", 0.02, 3.0) == {"tp": 0.04, "sl": 0.02, "pl": t8.PL, "tdays": 25}
    c = t8.entry_cfg("ATRSYM", 0.02, 3.0)
    assert math.isclose(c["tp"], 0.06) and math.isclose(c["sl"], 0.06)
    assert t8.entry_cfg("ATR", float("nan"), 3.0)["tp"] == 0.06                  # the engine's rule: no ATR, live bracket
    assert t8.entry_cfg("TIME5", 0.02, 3.0) is t8.ARMS["TIME5"]


def test_atrsym_k_sets_the_median_bracket_to_six_percent(fx):
    _, P = fx
    k = t8.atrsym_k(P)
    E = ld.entry_sets(P)
    med = np.median([P["atr"].iat[i, j] for i, j in E["picks"]])
    assert math.isclose(k * med, 0.06)


def test_purge_drops_entries_whose_max_hold_passes_the_last_day():
    E = [(0, 1), (74, 1), (75, 2), (99, 0)]
    keep, n = t8.purge(E, 100)                                                     # last index 99
    assert keep == [(0, 1), (74, 1)] and n == 2


def test_lock_free_arms_are_peak_invariant_and_the_lock_arm_is_not(fx):
    # Why lock-free arms run once (peaks_for): the peak reaches an exit only through the lock.
    pre, P = fx
    E, _ = t8.purge(ld.entry_sets(P)["picks"], len(P["days"]))
    k = t8.atrsym_k(P)
    for arm in ("TIME5", "TIME25", "NOSTOP", "TP4NS"):
        runs = [[t8.simulate(P, pre, i, j, arm, pk, k) for i, j in E] for pk in t8.PEAKS]
        assert runs[0] == runs[1] == runs[2]
    live = [[t8.simulate(P, pre, i, j, "LIVE", pk, k)["pnl"] for i, j in E] for pk in ("close", "high")]
    assert live[0] != live[1]                                                      # positive control: the setting is wired
    assert t8.peaks_for(t8.ARMS["TIME5"]) == ("polled",) and t8.peaks_for(t8.ARMS["LIVE"]) == t8.PEAKS


def test_holm_step_down():
    r = t8.holm({"a": 0.001, "b": 0.02, "c": 0.04, "d": 0.5})
    # thresholds 0.0125, 0.0167, 0.025, 0.05: a passes, b (0.02 > 0.0167) stops the descent
    assert r == {"a": True, "b": False, "c": False, "d": False}
    assert t8.holm({"a": None, "b": 0.01}) == {"a": False, "b": True}


def test_oos_classes():
    hold = {"mean": 0.0003, "p_one_sided": 0.01}
    assert t8.oos_class(0.0005, hold)["class"] == "holds"
    assert t8.oos_class(0.0048, {"mean": 0.0009, "p_one_sided": 0.01})["class"] == "period-dependent"   # r 0.19, T4's TIME5
    assert t8.oos_class(0.0005, {"mean": 0.0003, "p_one_sided": 0.2})["class"] == "period-dependent"
    assert t8.oos_class(0.0005, {"mean": -0.0001, "p_one_sided": 0.9})["class"] == "reverses"
    assert math.isclose(t8.oos_class(0.0048, {"mean": 0.0009, "p_one_sided": 0.01})["retention"], 0.1875)


def test_breakeven_cost_equalises_the_net_goal_statistic():
    arm = _rows([0.01] * 10, [5] * 10)                   # 0.2 %/day, 0.2 round trips/day
    ref = _rows([0.01] * 5, [10] * 5)                    # 0.1 %/day, 0.1 round trips/day
    c = t8.breakeven_bp(arm, ref)
    assert math.isclose(c, 100.0)                        # 0.001 / 0.1 per round trip = 1 %
    na = t8.costs(arm)["net_capital_day"]
    assert math.isclose((sum(r["pnl"] for r in arm) - 10 * c / 1e4) / 50, (0.05 - 5 * c / 1e4) / 50)
    assert na["0"] > na["20"]
    assert t8.breakeven_bp(ref, arm) is None             # no advantage: it never breaks even


def test_paired_monthly_reads_the_difference_of_capital_days():
    P = {"days": [f"2020-{m:02d}-01" for m in range(1, 13)]}
    E = [(i, 0) for i in range(12)]
    ref = _rows([0.01] * 12, [10] * 12)
    same = t8.paired_monthly(P, E, ref, ref)
    assert same["mean"] == 0
    up = t8.paired_monthly(P, E, _rows([0.01 + 0.0002 * 10 + 1e-6 * n for n in range(12)], [10] * 12), ref)
    assert math.isclose(up["mean"], 0.0002, abs_tol=1e-5) and up["p_one_sided"] < 0.001


def test_power_mde_matches_the_normal_formula():
    rng = np.random.default_rng(5)
    s = {f"m{i}": float(x) for i, x in enumerate(rng.normal(0, 0.001, 96))}
    p = t8.power(s, np.random.default_rng(6))
    se = p["sd"] / math.sqrt(96)
    assert p["mde_80"] == pytest.approx((1.645 + 0.842) * se, rel=0.15)
    assert 0 <= p["detect_rate_at_0.02pct_day"] <= 1


def test_negative_control_rejects_near_five_percent():
    rng = np.random.default_rng(7)
    n = 4000
    P = {"days": [f"20{10 + i // 400:02d}-{1 + (i // 34) % 12:02d}-01" for i in range(n)]}
    E = [(i, 0) for i in range(n)]
    live = _rows(rng.normal(0.005, 0.05, n), rng.integers(1, 25, n))
    r = t8.negative_control(P, E, live, np.random.default_rng(8))
    assert r["reject_rate_5pct"] < 0.12


def _bar(o, h, l, c):
    return SimpleNamespace(open=o, high=h, low=l, close=c)


def test_sl_precheck_passes_a_gap_at_the_open_and_flags_a_fill_below_the_stop_without_one():
    P = {"tickers": ["X"], "days": ["d0", "d1", "d2"], "close": pd.DataFrame({"X": [100.0, 99, 80]})}
    gap_fill = 80 * (1 - STOP_SLIP_GAP_PCT)
    ok = t8.sl_precheck(P, {"ohlc_cache": {("X", "d2"): _bar(80, 81, 79, 80)}}, [(0, 0)],
                        [{"reason": "SL", "pnl": gap_fill / 100 - 1, "exit_idx": 2, "hold": 2}])
    assert ok["pass"] and ok["gap_filled_at_open"] == 1
    bad = t8.sl_precheck(P, {"ohlc_cache": {("X", "d2"): _bar(97, 98, 79, 80)}}, [(0, 0)],
                         [{"reason": "SL", "pnl": -0.2, "exit_idx": 2, "hold": 2}])
    assert not bad["pass"] and "without a gap" in bad["d3"][0]["why"]
    nobar = t8.sl_precheck(P, {"ohlc_cache": {}}, [(0, 0)], [{"reason": "SL", "pnl": -0.2, "exit_idx": 2, "hold": 2}])
    assert not nobar["pass"]
    small = t8.sl_precheck(P, {"ohlc_cache": {}}, [(0, 0)], [{"reason": "SL", "pnl": -0.06, "exit_idx": 2, "hold": 2}])
    assert small["n_sl_below_-10pct"] == 0 and small["pass"]


def test_post_stop_return_runs_from_the_fill_to_time25s_exit():
    P = {"close": pd.DataFrame({"X": [100.0, 94, 90, 99]})}
    E = [(0, 0)]
    rows = [{"reason": "SL", "pnl": -0.06, "exit_idx": 1, "hold": 1}]
    t25 = [{"reason": "TIME", "pnl": -0.01, "exit_idx": 3, "hold": 3}]
    r = t8.post_stop(P, E, rows, t25)
    assert r["SL"]["n"] == 1 and math.isclose(r["SL"]["mean"], 99 / 94 - 1)        # positive: the stop cut a recovery


def test_capture_over_own_hold_and_over_25_days():
    P = {"high": pd.DataFrame({"X": [100.0, 110, 104, 120]}), "close": pd.DataFrame({"X": [100.0, 105, 103, 115]})}
    r = t8.capture(P, [(0, 0)], [{"pnl": 0.05, "exit_idx": 2, "hold": 2, "reason": "TSL"}], np.array([0.20]))
    assert math.isclose(r["own_hold"]["median"], 0.05 / 0.10) and math.isclose(r["mfe25"]["median"], 0.05 / 0.20)


def test_parse_french_reads_percent_rows_between_notes():
    text = ("This file was created using the 202608 CRSP database.\n\n"
            ",Mkt-RF,SMB,HML,RF\n19260701,    0.09,   -0.25,   -0.27,    0.01\n"
            "19260702,    0.45,   -0.33,   -0.06,    0.01\n\nCopyright 2026 Kenneth R. French\n")
    df = t8.parse_french(text)
    assert list(df.columns) == ["Mkt-RF", "SMB", "HML", "RF"] and len(df) == 2
    assert math.isclose(df.iloc[0]["Mkt-RF"], 0.0009) and str(df.index[1].date()) == "1926-07-02"


def test_carhart_alpha_recovers_a_planted_alpha():
    idx = pd.bdate_range("2020-01-01", periods=600)
    rng = np.random.default_rng(9)
    F = pd.DataFrame(rng.normal(0, 0.01, (600, 4)), index=idx, columns=["Mkt-RF", "SMB", "HML", "Mom"])
    F["RF"] = 0.0
    r = pd.Series(0.0004 + 1.1 * F["Mkt-RF"] + 0.5 * F["Mom"] + rng.normal(0, 0.001, 600), index=idx.strftime("%Y-%m-%d"))
    out = t8.carhart_alpha(r, F)
    assert out["status"] == "run" and math.isclose(out["alpha_annual"], 0.0004 * 252, rel_tol=0.25)
    assert math.isclose(out["loadings"]["mom"], 0.5, abs_tol=0.05)
    assert t8.carhart_alpha(r, None)["status"].startswith("not run")


def test_l3_overrides_pin_the_polled_peak_and_map_each_arm():
    o = t8.l3_over("TIME5", 3.0)
    assert o["peak_haircut"] == POLLED_PEAK_GAP and o["peak_from_high"] is True
    assert (o["take_profit_pct"], o["stop_loss_pct"], o["profit_lock_trigger_pct"], o["time_stop_days"]) == (t8.NONE, t8.NONE, None, 5)
    a = t8.l3_over("ATRSYM", 3.0)
    assert a["atr_exits"] and a["atr_exit_mult"] == (3.0, 3.0) and "take_profit_pct" not in a
    assert t8.l3_over("LIVE", 3.0)["profit_lock_trail_pct"] == 0.01


def test_guard_refuses_an_atr_lock_that_never_arms(fx):
    # The fixture's ATR (1.2-2.7 %) puts most 2 x ATR targets under the 4 % lock trigger, so the ATR
    # arm's three peak runs come out identical: the guard must refuse, not archive, that.
    pre, P = fx
    with pytest.raises(t8.IdenticalArms, match="ATR@close and ATR@high"):
        t8.card_l2("IS", P, pre, t8.atrsym_k(P), None, 1, lambda m: None)


def test_l2_end_to_end_on_the_synthetic_fixture(fx, tmp_path, monkeypatch):
    # One pass of card_l2 through the fork pool: identity holds, the guard passes, every arm is read
    # against LIVE, LIVE57's draws are shared, and decide() can read the result. ATR's target is
    # widened so its lock can arm on this fixture (see the test above).
    monkeypatch.setitem(t8.ARMS, "ATR", {**t8.ARMS["ATR"], "atr": (4.0, 1.0)})
    pre, P = fx
    k = t8.atrsym_k(P)
    days = P["days"]
    live_rows = [{"id": 1, "ticker": P["tickers"][0], "timestamp": days[10] + "T15:00:00"},
                 {"id": 2, "ticker": P["tickers"][1], "timestamp": days[20] + "T15:00:00"},
                 {"id": 3, "ticker": "NOPE", "timestamp": days[20] + "T15:00:00"}]
    out, R = t8.card_l2("IS", P, pre, k, live_rows, 1, lambda m: None)
    assert all(v["pass"] for v in out["identity_time25"].values())
    assert out["entries"]["live57"] == 2 and out["entries"]["live57_skipped"][0]["id"] == 3
    assert set(out["holm_picks"]["polled"]) == set(t8.NON_LIVE)
    for a in t8.NON_LIVE:
        assert f"{a}|polled|picks" in out["vs_live"] and f"{a}|close|picks" in out["vs_live"]
    assert out["live57"]["arms"]["LIVE|polled"]["minus_live_mean"] == 0
    assert np.array(R[("TIME5", "polled", "live57_rnd")]).shape == (2, t8.N_LIVE_RND)
    t8.save_rows(tmp_path / "rows.npz", R)
    z = np.load(tmp_path / "rows.npz")
    assert len(z["LIVE|polled|picks|pnl"]) == out["entries"]["picks"]
    d = t8.decide({"IS": out, "OOS": out}, None)
    assert set(d) == set(t8.NON_LIVE) and all(v["beats_live"] is None for v in d.values())   # (b) needs L3
    json.dumps(out, default=str)
