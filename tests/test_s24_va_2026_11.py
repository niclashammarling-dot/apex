"""
S24 L0 runner (vault raw/notes/2026-11/2026-11-12-apex-s24-va-l0-card.md, Amendment 1): VA without look-ahead and in
parity with live's check, C2 and its synthetic labels, and Amendment 1's three tests (reference, K-draw mean, the
gate's own positive control).
"""
import math

import numpy as np
import pandas as pd
import pytest

import backend.backtest.ladder_2026_10 as ld
import backend.backtest.s24_va_2026_11 as s24


def _bars(n=30, seed=1):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-01-02", periods=n, freq="B")
    close = pd.Series(100 * np.cumprod(1 + rng.normal(0, 0.01, n)), index=idx)
    vol = pd.Series(rng.integers(1_000_000, 5_000_000, n).astype(float), index=idx)
    return close, vol


def test_va_at_d_uses_no_bar_after_d():
    close, vol = _bars()
    days = [d.strftime("%Y-%m-%d") for d in close.index]
    P = {"days": days, "tickers": ["A"]}
    base = s24.va_panel({"raw_data": {"A": pd.DataFrame({"Close": close, "Volume": vol})}}, P)
    c2, v2 = close.copy(), vol.copy()
    c2.iloc[25:] *= 1.5
    v2.iloc[25:] *= 7
    moved = s24.va_panel({"raw_data": {"A": pd.DataFrame({"Close": c2, "Volume": v2})}}, P)
    assert base["A"].iloc[:25].equals(moved["A"].iloc[:25])                  # days before the change unchanged
    assert base["A"].iloc[:19].isna().all() and base["A"].iloc[19:].notna().all()


def test_va_matches_live_l5_check_on_one_fixture(monkeypatch):
    import backend.gate.l5_positioning as l5
    close, vol = _bars(25, seed=3)
    monkeypatch.setattr(l5.yf, "download", lambda *a, **k: pd.DataFrame({"Close": close, "Volume": vol}))
    live = l5._check_volume_accumulation("A")
    ours = s24.va_series(close, vol).iloc[-1]
    assert abs(live["up_vol"] / live["down_vol"] - ours) < 1e-9


def test_down_volume_zero_is_infinite_as_live():
    idx = pd.date_range("2026-01-02", periods=20, freq="B")
    close = pd.Series(np.arange(20, dtype=float) + 100, index=idx)          # every change up
    assert np.isinf(s24.va_series(close, pd.Series(1e6, index=idx)).iloc[-1])


def test_c2_is_above_minus_below_on_days_with_both_sides():
    days, tick = ["d1", "d2"], ["A", "B", "C", "D"]
    va = pd.DataFrame([[2.0, 1.5, 0.9, 0.5], [2.0, 1.5, 1.4, 1.3]], index=days, columns=tick)
    f = pd.DataFrame([[0.04, 0.02, 0.01, -0.01], [0.03, 0.01, 0.0, 0.0]], index=days, columns=tick)
    cls = pd.DataFrame(True, index=days, columns=tick)
    d, dropped = s24.c2_series(va, f, cls)
    assert abs(d["d1"] - (0.03 - 0.0)) < 1e-12 and math.isnan(d["d2"]) and dropped == 1     # d2 has no side <= 1.2


def test_synthetic_labels_keep_each_days_above_share():
    days, tick = ["d1"], ["A", "B", "C", "D"]
    va = pd.DataFrame([[2.0, 1.5, 0.9, 0.5]], index=days, columns=tick)          # half above 1.2
    pred = pd.DataFrame([[0.1, 0.9, 0.5, 0.2]], index=days, columns=tick)
    lab = s24.labels_from(pred, va, pd.DataFrame(True, index=days, columns=tick))
    assert list(lab.loc["d1"]) == [1.0, 2.0, 2.0, 1.0]                             # the top half by pred


def test_injection_z_is_ladders_construction():
    rng = np.random.default_rng(5)
    f = pd.DataFrame(rng.normal(size=(30, 12)))
    mask = pd.DataFrame(True, index=f.index, columns=f.columns)
    a = 0.1 / math.sqrt(1 - 0.01)
    ours = a * s24.injection_z(f, mask) + np.random.default_rng(9).standard_normal(f.shape)
    theirs = ld.injected(f, mask, 0.1, np.random.default_rng(9))
    assert np.allclose(ours.to_numpy(), theirs.to_numpy())


def test_reference_routine_matches_the_arcsine_law_at_large_n():
    r = 0.10
    got = s24.expected_spearman(2000, r, draws=2000)
    assert abs(got - 6 / math.pi * math.asin(r / 2)) <= 0.002


def test_k_draw_mean_lies_within_three_se_of_the_reference():
    rng = np.random.default_rng(11)
    f = pd.DataFrame(rng.normal(size=(80, 19)))
    mask = pd.DataFrame(True, index=f.index, columns=f.columns)
    cell = s24.injected_cell(f, mask, 1, 0.10, np.random.default_rng(12), {})
    assert abs(cell["mean"] - cell["reference"]) <= 3 * cell["se_k"] and cell["pass"]


def test_the_gate_fails_a_predictor_built_on_the_wrong_day():
    # Amendment 1's own positive control: injected from the next day's return, measured against this day's.
    rng = np.random.default_rng(13)
    f = pd.DataFrame(rng.normal(size=(200, 19)))
    mask = pd.DataFrame(True, index=f.index, columns=f.columns)
    a = 0.10 / math.sqrt(1 - 0.01)
    z_next = s24.injection_z(f.shift(-1), mask)
    means = [ld.nonoverlap(ld.row_rank_corr(a * z_next + rng.standard_normal(f.shape), f, mask), 1)["mean"] for _ in range(20)]
    assert abs(float(np.mean(means)) - s24.expected_spearman(19, 0.10)) > s24.BAND
