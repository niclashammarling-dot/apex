"""live_vs_engine_2026_10_20: the per-set reading and the trade's own bracket."""
import numpy as np
import pandas as pd

import backend.backtest.live_vs_engine_2026_10_20 as lv


def test_cfg_reads_the_trades_own_bracket():
    t = {"entry_price": 100.0, "tp_price": 106.0, "sl_price": 94.0}
    assert lv._cfg(t) == {"tp": 0.06, "sl": 0.06, "pl": [0.04, 0.01], "tdays": 25}


def test_read_counts_divergence_and_ranks_against_the_draws():
    D = pd.DataFrame({"live": [0.02, -0.03, 0.01, 0.04], "eng": [-0.01, -0.02, 0.02, 0.05]})
    R = np.array([[-0.01] * 4, [-0.02] * 4, [0.01] * 4, [-0.01] * 4])      # trades x draws
    r = lv._read(D, R)
    assert (r["live_wr"], r["eng_wr"]) == (0.75, 0.5)
    assert (r["live_win_eng_loss"], r["live_loss_eng_win"]) == (1, 0)
    assert r["rnd_wr"] == 0.25 and r["p_live_wr"] == 0.0                    # every draw wins 1 of 4
    assert r["sign_agreement"] == 0.75


def test_peak_measurement_reads_live_peak_against_highs_and_closes():
    days = ["2026-01-05", "2026-01-06", "2026-01-07"]
    P = {"days": days, "tickers": ["X"],
         "high": pd.DataFrame({"X": [101.0, 105.0, 103.0]}, index=days),
         "close": pd.DataFrame({"X": [100.0, 102.0, 101.0]}, index=days)}
    t = {"ticker": "X", "timestamp": "2026-01-05T15:00:00+00:00", "exited_at": "2026-01-07T15:00:00+00:00",
         "entry_price": 100.0, "peak_price": 104.5}
    r = lv.peak_measurement(P, [t])
    assert r["n"] == 1
    assert abs(r["live_peak_minus_max_close_median"] - 0.025) < 1e-12        # 104.5 vs the best close before exit, 102
    assert abs(r["max_high_minus_live_peak_median"] - 0.005) < 1e-12         # 105 vs 104.5
    assert r["share_reaching_4pct_trigger"] == {"live_peak": 1.0, "max_high": 1.0, "max_close": 0.0}


def test_compare_runs_the_peak_model_it_names(monkeypatch):
    # Found 2026-10-20: patching ef._check_exits_fast was overridden by ladder._simulate's explicit
    # **PEAK, so the "pre-R9" pass ran R9. The pass must reach the simulator's own peak model.
    import backend.backtest.ladder_2026_10 as ld
    seen = []
    monkeypatch.setattr(ld, "_simulate", lambda P, pre, i, j, cfg: seen.append(dict(ld.PEAK)) or {"pnl": 0.01, "reason": "TP"})
    days, tick = ["2026-01-05"], ["X", "Y"]
    P = {"days": days, "tickers": tick, "atr": pd.DataFrame({"X": [0.02], "Y": [0.02]}, index=days),
         "mask": pd.DataFrame({"X": [True], "Y": [True]}, index=days)}
    t = {"id": 1, "ticker": "X", "timestamp": "2026-01-05T15:00:00+00:00", "notional": 100.0, "pnl": 1.0,
         "signal_class": "in_session", "entry_price": 100.0, "tp_price": 106.0, "sl_price": 94.0}
    monkeypatch.setattr(lv, "N_RND", 2)
    monkeypatch.setattr(ld, "quintile", lambda a, m: pd.DataFrame({"X": [0], "Y": [0]}, index=days))
    for flag, want in ((False, ld.PEAK_MODELS["close"]), (True, ld.PEAK_MODELS["high"])):
        seen.clear()
        before = dict(ld.PEAK)
        lv.compare(P, {}, [t], flag, np.random.default_rng(0))
        assert seen and all(s == want for s in seen)
        assert ld.PEAK == before                                  # restored after the pass
