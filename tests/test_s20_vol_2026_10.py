"""S20 volatility L0 card (2026-10-24): predictors use no future data, the partial rank removes its
control, Holm and the item-9 rule decide as the card states, and the per-criterion controls read."""
import numpy as np
import pandas as pd

import backend.backtest.s20_vol_2026_10 as s20


def _panel(n_days=120, n_tick=30, seed=3):
    rng = np.random.default_rng(seed)
    days = [f"d{i:03d}" for i in range(n_days)]
    tick = [f"T{j}" for j in range(n_tick)]
    close = pd.DataFrame(100 * np.exp(np.cumsum(rng.normal(0, 0.02, (n_days, n_tick)), axis=0)),
                         index=days, columns=tick)
    etf = pd.DataFrame({"E1": 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n_days)))}, index=days)
    atr = close.pct_change().abs().rolling(14, min_periods=1).mean()
    score = pd.DataFrame(rng.uniform(size=(n_days, n_tick)), index=days, columns=tick)
    return {"days": days, "tickers": tick, "close": close, "etf_close": etf,
            "etf_of": {t: ("E1" if j % 2 == 0 else None) for j, t in enumerate(tick)},
            "atr": atr, "score": score, "mask": score.notna() & close.notna()}


def test_predictors_use_no_future_data():
    P = _panel()
    base = s20.predictors(P)
    d = 80
    P2 = _panel()
    P2["close"].iloc[d + 1:] *= 1.5                      # change only the future
    P2["etf_close"].iloc[d + 1:] *= 0.7
    after = s20.predictors(P2)
    for k in ("V2", "V3", "V4raw", "V5"):
        pd.testing.assert_series_equal(base[k].iloc[d], after[k].iloc[d], check_names=False)


def test_v3_is_nan_without_a_sector_etf_and_windows_need_full_history():
    P = _panel()
    pr = s20.predictors(P)
    assert pr["V3"]["T1"].isna().all()                   # odd tickers have no ETF
    assert pr["V3"]["T0"].iloc[:20].isna().all() and pr["V3"]["T0"].iloc[25:].notna().all()
    assert pr["V4raw"].iloc[:60].isna().all().all()      # rv60 needs 60 returns


def test_partial_rank_is_orthogonal_to_its_control_each_day():
    rng = np.random.default_rng(7)
    z = pd.DataFrame(rng.normal(size=(10, 40)))
    x = 2 * z + pd.DataFrame(rng.normal(size=(10, 40)))
    res = s20.partial_rank(x, z, x.notna())
    zr = z.rank(axis=1)
    zc = zr.sub(zr.mean(axis=1), axis=0)
    assert np.allclose((res * zc).sum(axis=1), 0, atol=1e-9)


def test_holm_steps_down_and_stops():
    r = s20.holm({"a": 0.001, "b": 0.02, "c": 0.04, "d": None})
    # m = 3: 0.001 <= 0.05/3, 0.02 <= 0.05/2, 0.04 <= 0.05/1
    assert r == {"a": True, "b": True, "c": True, "d": False}
    r = s20.holm({"a": 0.001, "b": 0.03, "c": 0.04})
    assert r == {"a": True, "b": False, "c": False}      # 0.03 > 0.025 stops the procedure


def test_verdict_follows_card_item_9():
    pos = {"t": 3.0}
    assert s20.verdict(pos, {"t": 1.2}, +1, True, True) == "supported"
    assert s20.verdict(pos, {"t": 0.5}, +1, True, True).startswith("unanswered (IS only")
    assert s20.verdict(pos, {"t": 1.2}, -1, True, True) == "contradicted"
    assert s20.verdict(pos, {"t": 1.2}, +1, False, True) == "unanswered"
    assert s20.verdict(pos, {"t": 1.2}, +1, True, False).startswith("unanswered (D4")


def test_p_two_sided():
    assert abs(s20.p_two_sided(1.959964) - 0.05) < 1e-4
    assert s20.p_two_sided(None) is None


def test_criterion_controls_read_the_synthetic_arms():
    def st(t, p):
        return {"t": t, "p": p}

    def win(plus, minus, zero):
        return {"h": {str(h): {"criterion": {"plus": plus, "minus": minus, "zero": zero}} for h in s20.HORIZONS}}

    IS = win(st(4.0, 1e-4), st(-4.0, 1e-4), [st(0.1, 0.9)] * 20)
    OOS = win(st(1.5, 0.13), st(-1.5, 0.13), [st(0.1, 0.9)] * 20)
    c = s20.criterion_controls(IS, OOS)["1"]
    assert c == {"plus_supported": True, "minus_contradicted": True, "zero_reject_rate": 0.0}
    IS2 = win(st(2.2, 0.028), st(-4.0, 1e-4), [st(0.1, 0.9)] * 20)   # p above 0.05/8
    assert s20.criterion_controls(IS2, OOS)["1"]["plus_supported"] is False


def test_card_window_runs_on_a_synthetic_panel():
    P = _panel(n_days=140, n_tick=30)
    s20.N_SHUFFLE, s20.N_ZERO_DRAWS = 3, 2
    out = s20.card_window(P)
    assert set(out["h"]) == {"1", "5", "10", "25"}
    assert {"V3", "V4", "V1", "V2", "V4raw", "V5", "V4q", "shuffle", "injected", "criterion"} <= set(out["h"]["1"])
    assert "V4raw~V5" in out["pred_corr"]
