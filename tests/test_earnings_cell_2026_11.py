"""Earnings cell L0 producer (2026-11-28): classes, statistics, controls and the item-8 rule as sealed on 2026-11-23
(vault raw/notes/2026-11/2026-11-23-apex-earnings-cell-preregistration-draft.md). Synthetic data only; no fetch."""
import json
import subprocess
from datetime import date
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from backend.backtest import earnings_cell_2026_11 as ec
from backend.backtest.earnings_source import EarningsSet

D = date.fromisoformat


def _days(a="2020-01-01", b="2020-12-31"):
    return [d.date().isoformat() for d in pd.bdate_range(a, b)]


def _rep(day, when="bmo", surprise=5.0, reported=True):
    return {"day": day, "when": when, "surprise": surprise, "reported": reported}


def _quarterly_set(t, days):
    return EarningsSet({t: tuple(D(x) for x in days)})


# ── timing and reports ──────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("ts,want", [("2020-04-29 06:00", "bmo"), ("2020-04-29 09:00", "bmo"), ("2020-04-29 16:00", "amc"),
                                     ("2020-04-29 17:00", "amc"), ("2020-04-29 00:00", "untimed"),
                                     ("2020-04-29 12:00", "untimed"), ("2020-04-29 09:30", "untimed")])
def test_timing_from_the_new_york_timestamp(ts, want):
    assert ec.timing(pd.Timestamp(ts, tz="America/New_York")) == want


def test_timing_converts_a_utc_stamp():
    assert ec.timing(pd.Timestamp("2020-04-29 20:00", tz="UTC")) == "amc"      # 16:00 in New York (EDT)


def test_reports_from_frame_cuts_at_the_run_day_and_keeps_surprise_and_reported():
    idx = pd.DatetimeIndex(["2026-12-10 16:00", "2026-10-28 16:00", "2026-10-12 16:00"], tz="America/New_York")
    df = pd.DataFrame({"EPS Estimate": [1, 1, 1], "Reported EPS": [np.nan, 3.7, np.nan],
                       "Surprise(%)": [np.nan, 10.5, np.nan]}, index=idx)
    got = ec.reports_from_frame(df, pd.Timestamp("2026-11-28", tz="America/New_York"))
    assert [r.day for r in got] == ["2026-10-12", "2026-10-28"]                 # the future row is cut
    assert got[1].surprise == 10.5 and got[1].reported and not got[0].reported   # FDX's stale 10-12 row: D5 count


def test_archive_to_set_marks_failed_and_empty_tickers_as_holes():
    es = ec.earnings_set({"reports": {"AAA": [_rep("2020-01-29")], "BBB": []}, "failed": {"CCC": "boom"}})
    assert es.in_hole("BBB", D("2020-02-01")) and es.in_hole("CCC", D("2020-02-01"))
    assert not es.in_hole("AAA", D("2020-01-29"))


# ── classes ─────────────────────────────────────────────────────────────────────────────────────────────────────

def _classify(reports, days=None, es=None):
    days = days or _days()
    cover = [d.date().isoformat() for d in pd.date_range("2019-07-15", "2021-04-30", freq="90D")]
    es = es or _quarterly_set("AAA", sorted(set(cover) | {r["day"] for r in reports}))
    return ec.classify(days, ["AAA"], {"AAA": reports}, es)["AAA"]


def test_before_open_report_holds_on_the_session_before():
    lab = _classify([_rep("2020-04-29", "bmo", 5.0)])                          # a Wednesday
    assert lab["2020-04-28"] == "HOLD"                                          # close 28 -> close 29 holds the reaction
    assert lab["2020-04-27"] == "PRE2" and lab["2020-04-24"] == "PRE2"          # Monday and the Friday before
    assert all(lab[d] == "POST+" for d in ("2020-04-29", "2020-04-30", "2020-05-01", "2020-05-04", "2020-05-05"))
    assert lab["2020-05-06"] == "CLEAR"


def test_after_close_report_holds_on_the_report_day_and_a_miss_is_post_minus():
    lab = _classify([_rep("2020-04-29", "amc", -3.0)])
    assert lab["2020-04-29"] == "HOLD"
    assert lab["2020-04-28"] == "PRE2" and lab["2020-04-27"] == "PRE2"
    assert lab["2020-04-30"] == "POST-"


def test_a_weekend_report_holds_on_the_friday():
    assert _classify([_rep("2020-05-02", "amc")])["2020-05-01"] == "HOLD"      # a Saturday after the close
    assert _classify([_rep("2020-05-02", "bmo")])["2020-05-01"] == "HOLD"


def test_near_is_three_to_fourteen_calendar_days_ahead():
    lab = _classify([_rep("2020-04-29", "amc")])
    assert lab["2020-04-24"] == "NEAR"          # 5 days ahead, but PRE2 is 27-28 here: 24 is NEAR
    assert lab["2020-04-15"] == "NEAR"          # 14 days
    assert lab["2020-04-14"] == "CLEAR"         # 15 days


def test_untimed_report_and_missing_surprise_are_excluded_not_classed():
    lab = _classify([_rep("2020-04-29", "untimed")])
    assert all(lab[d] == "UNTIMED" for d in ("2020-04-24", "2020-04-28", "2020-04-29", "2020-05-06"))
    lab = _classify([_rep("2020-04-29", "amc", None)])
    assert lab["2020-04-29"] == "HOLD" and lab["2020-04-30"] == "POST?"


def test_a_hole_overrides_every_class():
    es = EarningsSet({"AAA": (D("2019-10-01"), D("2020-04-29"))})              # a 211-day gap before the report
    lab = _classify([_rep("2020-04-29", "bmo")], es=es)
    assert lab["2020-04-28"] == "HOLE" and lab["2020-02-03"] == "HOLE"
    assert lab["2020-05-29"] == "HOLE"                                          # after the last returned report


def test_reports_outside_the_window_label_nothing():
    lab = _classify([_rep("2021-03-10", "bmo"), _rep("2019-06-10", "amc")], days=_days("2020-01-02", "2020-12-31"),
                    es=_quarterly_set("AAA", ["2019-06-10", "2019-09-10", "2019-12-10", "2020-03-10", "2020-06-10",
                                              "2020-09-10", "2020-12-10", "2021-03-10"]))
    assert lab["2020-12-30"] != "PRE2" and lab["2020-12-31"] != "HOLD"
    assert lab["2020-01-02"] == "CLEAR"


# ── statistics ──────────────────────────────────────────────────────────────────────────────────────────────────

def test_ols_coefficients_are_class_means_and_lag_zero_clusters_by_day():
    rng = np.random.default_rng(1)
    day = np.repeat(np.arange(200), 10)
    codes = rng.integers(0, 3, len(day))
    y = rng.normal(0, 1, len(day)) + (codes == 1) * 0.5
    beta, se = ec.ols_dk(y, codes, day, 3, 0)
    assert beta[0] == pytest.approx(y[codes == 0].mean())
    assert beta[1] == pytest.approx(y[codes == 1].mean() - y[codes == 0].mean())
    # the intercept-only case: SE = sqrt(sum over days of (sum of residuals)^2) / n
    b0, s0 = ec.ols_dk(y, np.zeros_like(codes), day, 1, 0)
    e = y - y.mean()
    assert s0[0] == pytest.approx(np.sqrt((np.bincount(day, weights=e) ** 2).sum()) / len(y))


def test_day_clustering_widens_the_se_when_a_day_moves_together():
    rng = np.random.default_rng(2)
    day = np.repeat(np.arange(300), 20)
    codes = (rng.random(len(day)) < 0.2).astype(int)
    shock = rng.normal(0, 1, 300)[day]                                          # a common move per day
    y = shock + rng.normal(0, 0.2, len(day))
    _, se_day = ec.ols_dk(y, codes, day, 2, 0)
    _, se_iid = ec.ols_dk(y, codes, np.arange(len(day)), 2, 0)
    assert se_day[0] > 3 * se_iid[0]


def test_two_proportion_test():
    got = ec.two_prop(30, 1000, 10, 1000)
    assert got["diff"] == pytest.approx(0.02) and got["p"] < 0.01
    assert ec.two_prop(10, 1000, 10, 1000)["p"] == pytest.approx(1.0)
    assert ec.two_prop(0, 0, 1, 10)["p"] is None


def _synthetic_window(tail_on_hold: bool, seed: int = 3):
    rng = np.random.default_rng(seed)
    days = _days("2019-01-01", "2022-12-31")
    tick = [f"T{i:02d}" for i in range(40)]
    rets = rng.normal(0, 0.015, (len(days), len(tick)))
    reports, sets = {}, {}
    for j, t in enumerate(tick):
        off = j % 60
        rd = [days[k] for k in range(5 + off, len(days), 63)]
        reports[t] = [_rep(d, "amc", float(rng.normal())) for d in rd]
        sets[t] = tuple(D(x) for x in ["2018-11-01"] + rd + ["2023-03-01"])
        if tail_on_hold:
            for d in rd:
                k = days.index(d) + 1                                            # the reaction close
                if k < len(days) and rng.random() < 0.15:
                    rets[k, j] = -0.10
    close = pd.DataFrame(100 * np.exp(np.cumsum(rets, axis=0)), index=days, columns=tick)
    lab = ec.classify(days, tick, reports, EarningsSet(sets))
    return lab, close


def test_card_finds_a_planted_reaction_day_tail_and_none_without_it():
    lab, close = _synthetic_window(True)
    res = ec.card_window(lab, close, 60, log=lambda *_: None)
    t = res["horizons"]["1"]["tail_raw"]
    assert t["diff"] > 0.05 and t["p"] < 1e-6
    lab0, close0 = _synthetic_window(False)
    t0 = ec.card_window(lab0, close0, 60, log=lambda *_: None)["horizons"]["1"]["tail_raw"]
    assert t0["p"] > 0.01
    assert set(res["horizons"]) == {"1", "5", "10", "25"}
    assert res["class_ticker_days"]["HOLD"] > 0 and res["d1"]["ticker_years"] > 0


def test_controls_hold_their_bands_on_null_data():
    lab, close = _synthetic_window(False, seed=5)
    c = ec.card_window(lab, close, 300, log=lambda *_: None)["controls"]
    assert 0.0 <= c["negative_reject_rate"] <= 0.10                             # nominal 5 %
    assert 0.65 <= c["positive_detect_rate"] <= 0.95                            # nominal 80 % at the MDE


def test_positive_control_uses_the_placebos_own_mde_not_the_reaction_days():
    # reaction days carry more variance than the CLEAR days placebos come from: the real HOLD MDE over-detects
    lab, close = _synthetic_window(True, seed=3)
    c = ec.card_window(lab, close, 300, log=lambda *_: None)["controls"]
    assert c["positive_pass"] and c["literal_detect_rate"] > 0.95


# ── the rule ────────────────────────────────────────────────────────────────────────────────────────────────────

def _win(diff, p, neg=0.05, pos=0.8):
    return {"horizons": {"1": {"tail_raw": {"diff": diff, "p": p}}},
            "controls": {"negative_pass": 0.03 <= neg <= 0.07, "positive_pass": 0.7 <= pos <= 0.9}}


def test_rule_reads_item_8():
    assert ec.rule(_win(0.01, 0.01), _win(0.004, 0.3))["outcome"].startswith("HOLD carries tail risk")
    assert ec.rule(_win(-0.01, 0.01), _win(-0.004, 0.3))["outcome"].startswith("less tail")
    assert ec.rule(_win(0.01, 0.01), _win(-0.004, 0.3))["outcome"].startswith("window-dependent")
    assert ec.rule(_win(0.01, 0.20), _win(0.004, 0.3))["outcome"].startswith("no excess tail")
    assert ec.rule(_win(0.01, 0.01, neg=0.12), _win(0.004, 0.3))["outcome"].startswith("card stopped")


def test_main_refuses_a_dirty_backend_and_writes_nothing(tmp_path, monkeypatch):
    def fake_run(args, **kw):
        out = " M backend/backtest/engine.py" if "status" in args else "abc1234"
        return subprocess.CompletedProcess(args, 0, stdout=out, stderr="")
    monkeypatch.setattr(ec.subprocess, "run", fake_run)
    monkeypatch.setattr("sys.argv", ["earnings_cell_2026_11", str(tmp_path / "out")])
    assert ec.main() == 2
    assert not any((tmp_path / "out").iterdir())
