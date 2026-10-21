"""S14 L0: classes follow Hu et al.'s recursion on the VIX close of d, the path low is the basket's
worst close against the entry close, risk is reported per class, and the verdict maps each
pre-registered outcome."""
import numpy as np
import pandas as pd
import pytest

import backend.backtest.s14_vix_l0_2026_10_21 as s


def _vix(vals):
    idx = pd.date_range("2020-01-01", periods=len(vals), freq="D")
    return pd.Series(vals, index=idx), list(idx)


def test_jump_is_against_the_average_up_to_the_day_before():
    v, days = _vix([20.0, 20.0, 23.5, 31.0, 31.0, 40.0])
    c = s.vix_classes(v, days[1:], warmup=1)
    # d2: 23.5 - 20 >= 3 -> jump. Vbar(d2) = 0.3*20 + 0.7*23.5 = 22.45; d3: 31 - 22.45 -> jump and >= 30.
    # Vbar(d3) = 0.3*22.45 + 0.7*31 = 28.435; d4: 31 - 28.435 < 3, >= 30 -> level only.
    # Vbar(d4) = 0.3*28.435 + 0.7*31 = 30.23; d5: 40 - 30.23 -> both.
    assert list(c) == ["NEITHER", "JUMP_ONLY", "BOTH", "LEVEL_ONLY", "BOTH"]


def test_level_is_inclusive_at_thirty():
    v, days = _vix([30.0, 30.0, 30.0])
    assert list(s.vix_classes(v, days[1:], warmup=1)) == ["LEVEL_ONLY", "LEVEL_ONLY"]


def test_path_low_is_the_worst_close_against_the_entry_close():
    closes = pd.DataFrame({"A": [100.0, 90.0, 110.0, 120.0]}, index=["d0", "d1", "d2", "d3"])
    low = s.path_low(closes, 2)
    assert list(low.index) == ["d0", "d1"]
    assert low["d0"] == pytest.approx(-10.0) and low["d1"] == pytest.approx(22.2222, abs=1e-3)


def test_risk_reports_dispersion_tail_and_drawdown_share_per_class():
    y = pd.Series([1.0, -8.0, 2.0, 0.0])
    low = pd.Series([0.5, -9.0, 1.0, -1.0])
    c = pd.Series(["NEITHER", "BOTH", "BOTH", "NEITHER"])
    r = s.risk(y, low, c)
    assert r["BOTH"]["dd_share"] == 0.5 and r["NEITHER"]["dd_share"] == 0.0
    assert r["JUMP_ONLY"] is None and r["BOTH"]["sd"] == pytest.approx(np.std([-8.0, 2.0], ddof=1))


def _res(effect, p, hi, oos=0.1):
    cell = lambda: {"effect": effect, "p": p, "ci95": [effect - 0.3, hi]}
    pool = lambda e: {"pool": {c: (cell() if e is None else {"effect": e}) for c in s.CLASSES}}
    return {"IS": {f"h{h}": pool(None) for h in s.HORIZONS}, "OOS": {f"h{h}": pool(oos) for h in s.HORIZONS}}


def test_verdict_rule():
    v = s.verdict(_res(0.8, 0.001, 1.1))
    assert v["h1 JUMP_ONLY"]["verdict"] == "replicates the one-day premium"
    assert v["h5 BOTH"]["verdict"] == "premium survives an APEX-length hold"
    assert s.verdict(_res(0.8, 0.001, 1.1, oos=-0.1))["h1 JUMP_ONLY"]["verdict"] == "one-day premium, OOS sign differs"
    neg = s.verdict(_res(-0.8, 0.001, -0.5))
    assert neg["h5 LEVEL_ONLY"]["verdict"] == "the gate avoids a loss"
    assert neg["h5 JUMP_ONLY"]["verdict"].startswith("significant negative")
    assert s.verdict(_res(0.1, 0.5, 0.9))["h1 BOTH"]["verdict"] == "unanswered at this power"
    assert s.verdict(_res(-0.1, 0.5, 0.2))["h10 BOTH"]["verdict"] == "no premium detected"
