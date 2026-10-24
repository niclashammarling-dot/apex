"""S21 reversal L0 card (2026-10-24): residual = total minus the sector ETF's, no future data, R3 is
orthogonal to R1, the rule and the controls read as the card states."""
import numpy as np
import pandas as pd

import backend.backtest.s21_reversal_2026_10 as s21


def _panel(n_days=80, n_tick=12, seed=5):
    rng = np.random.default_rng(seed)
    days = [f"d{i:03d}" for i in range(n_days)]
    tick = [f"T{j}" for j in range(n_tick)]
    close = pd.DataFrame(100 * np.exp(np.cumsum(rng.normal(0, 0.02, (n_days, n_tick)), axis=0)), index=days, columns=tick)
    etf = pd.DataFrame({"E": 50 * np.exp(np.cumsum(rng.normal(0, 0.01, n_days)))}, index=days)
    score = pd.DataFrame(rng.uniform(size=(n_days, n_tick)), index=days, columns=tick)
    return {"days": days, "tickers": tick, "close": close, "etf_close": etf, "score": score,
            "etf_of": {t: "E" for t in tick}, "mask": score.notna(), "atr": close.pct_change().abs()}


def test_residual_is_total_minus_etf_and_uses_no_future():
    P = _panel()
    pr = s21.predictors(P)
    d = 40
    exp = P["close"].iloc[d] / P["close"].iloc[d - 5] - 1 - (P["etf_close"]["E"].iloc[d] / P["etf_close"]["E"].iloc[d - 5] - 1)
    assert np.allclose(pr["R2"].iloc[d].to_numpy(), exp.to_numpy())
    P2 = _panel()
    P2["close"].iloc[d + 1:] *= 2
    P2["etf_close"].iloc[d + 1:] *= 3
    pr2 = s21.predictors(P2)
    for k in ("R1", "R2", "tot20", "res20"):
        assert np.allclose(pr[k].iloc[d].to_numpy(), pr2[k].iloc[d].to_numpy(), equal_nan=True)


def test_r3_is_the_score_without_r1_rank():
    P = _panel()
    pr = s21.predictors(P)
    d = 30
    r1 = pr["R1"].iloc[d].rank()
    r1c = r1 - r1.mean()
    assert abs(float((pr["R3"].iloc[d] * r1c).sum())) < 1e-9


def test_verdict_rule():
    assert s21.verdict({"t": -3.0}, {"t": -1.2}, True, -1, True) == "supported"
    assert s21.verdict({"t": 3.0}, {"t": 1.2}, True, -1, True) == "contradicted"
    assert s21.verdict({"t": 3.0}, {"t": 1.2}, True, 0, True) == "supported"
    assert s21.verdict({"t": -3.0}, {"t": -0.2}, True, -1, True).startswith("unanswered (IS only")
    assert s21.verdict({"t": -3.0}, {"t": -1.2}, True, -1, False).startswith("unanswered (D4")
    assert s21.verdict({"t": -1.0}, {"t": -1.2}, False, -1, True) == "unanswered"


def test_controls_read_the_synthetic_arms():
    def st(t, p):
        return {"t": t, "p": p}
    win = {"h": {h: {"criterion": {"plus": st(4.0, 1e-5), "minus": st(-4.0, 1e-5), "zero": [0.9] * 20}}
                 for h in s21.DECIDING_H}}
    oos = {"h": {h: {"criterion": {"plus": st(1.5, 0.1), "minus": st(-1.5, 0.1), "zero": [0.9] * 20}}
                 for h in s21.DECIDING_H}}
    c = s21.controls(win, oos)["1"]
    assert c == {"minus_supported_negative": True, "plus_supported_positive": True, "zero_reject_rate": 0.0}
