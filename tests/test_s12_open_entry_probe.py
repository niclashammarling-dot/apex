"""S12 L0 probe (scripts/s12_open_entry_probe.py): the open-entry wrapper and the card's statistics on
synthetic bars, so a defect is caught before the probe reads any forward return."""
import importlib.util
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from backend.backtest.exit_rules import Bar

_spec = importlib.util.spec_from_file_location("s12", Path(__file__).resolve().parents[1] / "scripts" / "s12_open_entry_probe.py")
s12 = importlib.util.module_from_spec(_spec)
sys.modules["s12"] = s12          # the pool pickles _job by module name
_spec.loader.exec_module(s12)

DAYS = [d.date().isoformat() for d in pd.bdate_range("2024-01-01", periods=6)]
LIVE = {"tp": 0.06, "sl": 0.06, "pl": [0.04, 0.01], "tdays": 25}


def _world(bars: list[tuple[float, float, float, float]]):
    """One ticker; bars[k] = (open, high, low, close) on DAYS[k]."""
    pc = {("AAA", d): c for d, (_, _, _, c) in zip(DAYS, bars)}
    oc = {("AAA", d): Bar(o, h, l, c) for d, (o, h, l, c) in zip(DAYS, bars)}
    P = {"days": DAYS, "tickers": ["AAA"], "sector": {"AAA": "Energy"},
         "close": pd.DataFrame({"AAA": [c for *_, c in bars]}, index=DAYS),
         "open": pd.DataFrame({"AAA": [o for o, *_ in bars]}, index=DAYS)}
    return P, {"price_cache": pc, "ohlc_cache": oc}


FLAT = [(100, 101, 99, 100)] * 6


def test_identity_open_to_close_of_entry_day_and_next():
    bars = [(100, 101, 99, 100), (102, 104, 101, 103), (103, 104, 102, 104)] + FLAT[:3]
    P, pre = _world(bars)
    off = {"tp": 10.0, "sl": 10.0, "pl": None}
    r0 = s12.simulate_open(P, pre, 1, 0, {**off, "tdays": 0}, cost=0.0)
    r1 = s12.simulate_open(P, pre, 1, 0, {**off, "tdays": 1}, cost=0.0)
    assert (r0["exit_idx"], r0["reason"]) == (1, "TIME") and r0["pnl"] == pytest.approx(103 / 102 - 1, abs=6e-5)
    assert (r1["exit_idx"], r1["reason"]) == (2, "TIME") and r1["pnl"] == pytest.approx(104 / 102 - 1, abs=6e-5)


def test_take_profit_on_entry_bar_is_tested_from_bar_d_and_skipped_from_d1():
    # entry at the open of day 1 (100); day 1's high is +7 %, later bars flat at the entry
    bars = [(100, 101, 99, 100), (100, 107, 99.5, 101)] + [(101, 101.5, 100.5, 101)] * 4
    P, pre = _world(bars)
    d = s12.simulate_open(P, pre, 1, 0, LIVE, cost=0.0, first=0)
    d1 = s12.simulate_open(P, pre, 1, 0, LIVE, cost=0.0, first=1)
    assert d["exit_idx"] == 1 and d["reason"] == "TP" and d["pnl"] == pytest.approx(0.06, abs=1e-4)
    assert d1["exit_idx"] != 1 and d1["reason"] != "TP"


def test_cost_raises_the_fill_and_the_bracket_follows_it():
    bars = [(100, 101, 99, 100)] + [(100, 100.5, 99.5, 100)] * 5
    P, pre = _world(bars)
    r = s12.simulate_open(P, pre, 1, 0, {"tp": 10.0, "sl": 10.0, "pl": None, "tdays": 0}, cost=0.006)
    assert r["pnl"] == pytest.approx(100 / 100.6 - 1, abs=6e-5)


def test_prior_close_anchor_moves_the_stop_with_the_gap():
    # prior close 100, open gaps down to 95: a stop on the prior close (94) sits 1.05 % under the fill,
    # a stop on the fill (89.3) does not fire on a low of 93.5
    bars = [(100, 101, 99, 100), (95, 95.5, 93.5, 94.5)] + [(94.5, 95, 94, 94.5)] * 4
    P, pre = _world(bars)
    prev = s12.simulate_open(P, pre, 1, 0, LIVE, cost=0.0, anchor_prev=True)
    fill = s12.simulate_open(P, pre, 1, 0, LIVE, cost=0.0)
    assert prev["exit_idx"] == 1 and prev["reason"] == "SL"
    assert fill["reason"] != "SL" or fill["exit_idx"] != 1


def test_t_sf_matches_known_quantiles():
    assert s12.t_sf(0.0, 10) == pytest.approx(0.5)
    assert s12.t_sf(2.228, 10) == pytest.approx(0.025, abs=2e-4)     # t(0.975, 10)
    assert s12.t_sf(1.660, 100) == pytest.approx(0.05, abs=5e-4)     # t(0.95, 100)
    assert s12.t_sf(-1.660, 100) == pytest.approx(0.95, abs=5e-4)


def test_block_stat_uses_every_day_and_blocks_from_row_zero():
    day = pd.Series([1.0, 3.0, 2.0, 4.0, 6.0, 5.0], index=[0, 1, 25, 26, 50, 51])
    r = s12.block_stat(day, 25)
    assert r["n_blocks"] == 3 and r["n_days"] == 6
    assert r["mean"] == pytest.approx(np.mean([2.0, 3.0, 5.5]))
    assert r["mde"] == pytest.approx(2.8 * np.std([2.0, 3.0, 5.5], ddof=1) / math.sqrt(3))


def test_holm_steps_down_and_stops_at_the_first_non_rejection():
    h = s12.holm({"a": 0.001, "b": 0.03, "c": 0.013, "d": 0.5})
    assert h["a"]["reject"] and h["c"]["reject"] and not h["b"]["reject"] and not h["d"]["reject"]
    assert h["c"]["threshold"] == pytest.approx(0.05 / 3)


def test_run_sims_dedupes_jobs_and_keys_results_by_job():
    # control for the 10:04 crash: a job carried the exit config (a dict), which the de-duplication could not hash
    bars = [(100, 101, 99, 100), (100, 107, 99.5, 101)] + [(101, 101.5, 100.5, 101)] * 4
    P, pre = _world(bars)
    s12._SHARED = {"P": P, "pre": pre, "cfg": LIVE}
    jobs = [("open", 1, 0, 0.0, 0, False), ("open", 1, 0, 0.0, 0, False), ("open", 1, 0, 0.0, 1, False)]
    R = s12.run_sims(jobs, 1, lambda m: None, "t")
    assert len(R) == 2
    assert R[("open", 1, 0, 0.0, 0, False)][:2] == (pytest.approx(0.06, abs=1e-4), "TP")
    assert R[("open", 1, 0, 0.0, 1, False)][1] != "TP"


def test_block_stat_counts_complete_blocks_only_when_told_the_window_length():
    # control for the 10:13 run: a trailing partial block (rows 50-55 of 56) was weighted as a full block
    day = pd.Series([1.0, 3.0, 2.0, 4.0, 60.0], index=[0, 25, 50, 75, 100])
    assert s12.block_stat(day, 25)["n_blocks"] == 5
    r = s12.block_stat(day, 25, n_rows=106)
    assert r["n_blocks"] == 4 and r["n_days"] == 4 and r["mean"] == pytest.approx(2.5)
