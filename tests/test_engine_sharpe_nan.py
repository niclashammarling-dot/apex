"""engine.py Sharpe (2026-10-24): a NaN in the equity curve gives NaN, not a silent 0.0 (the 2026-10-11
re-test's 17 runs all reported 0.000); a normal curve gives a finite value."""
import math

from backend.backtest import engine


def _metrics(balances):
    ec = [{"date": f"2024-01-{i + 1:02d}", "balance": b} for i, b in enumerate(balances)]
    return engine._compute_metrics("2024-01-01", "2024-01-31", 10_000.0, balances[-1], [], ec)


def test_nan_in_the_curve_is_visible():
    assert math.isnan(_metrics([10_000.0, 10_100.0, float("nan"), 10_050.0])["sharpe"])


def test_a_normal_curve_has_a_finite_sharpe():
    s = _metrics([10_000.0, 10_100.0, 10_050.0, 10_200.0, 10_150.0])["sharpe"]
    assert math.isfinite(s) and s != 0.0


def test_a_flat_curve_stays_zero():
    assert _metrics([10_000.0] * 5)["sharpe"] == 0.0
