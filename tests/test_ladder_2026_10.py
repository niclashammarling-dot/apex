"""Test ladder 2026-10-20: the L0 statistics, the vol-matched sets, the L2 trade simulator against the
engine's own exits, and the engine's two sizing options without the score (T5)."""
import math

import numpy as np
import pandas as pd

import backend.backtest.engine_fast as ef
import backend.backtest.ladder_2026_10 as ld
from tests.test_engine_flag_off_parity import END, RUN_KW, START, synthetic_precomputed


def _frame(a):
    return pd.DataFrame(a, index=[f"d{i}" for i in range(a.shape[0])])


def test_row_rank_corr_is_spearman_per_row():
    rng = np.random.default_rng(1)
    x = _frame(rng.normal(size=(4, 30)))
    mask = x.notna()
    assert np.allclose(ld.row_rank_corr(x, x * 3 + 1, mask), 1.0)
    assert np.allclose(ld.row_rank_corr(x, -x, mask), -1.0)
    # rows with fewer than 5 valid cells give NaN, not a correlation of two points
    m2 = mask.copy(); m2.iloc[0, 3:] = False
    assert np.isnan(ld.row_rank_corr(x, x, m2).iloc[0])


def test_nonoverlap_takes_every_hth_observation():
    s = pd.Series(np.arange(10, dtype=float))
    r = ld.nonoverlap(s, 3)                              # 0, 3, 6, 9
    assert r["n"] == 4 and r["mean"] == 4.5


def test_quintile_within_each_day():
    atr = _frame(np.tile(np.arange(1, 11, dtype=float), (2, 1)))
    q = ld.quintile(atr, atr.notna())
    assert list(q.iloc[0]) == [0, 0, 1, 1, 2, 2, 3, 3, 4, 4]


def test_shuffle_keeps_each_days_scores():
    rng = np.random.default_rng(2)
    s = _frame(rng.normal(size=(3, 12)))
    sh = ld.shuffled(s, s.notna(), rng)
    for i in range(3):
        assert sorted(sh.iloc[i]) == sorted(s.iloc[i])


def test_injected_score_hits_its_target_ic():
    # Positive control for T1: the injection is what the card's control reads.
    rng = np.random.default_rng(3)
    f = _frame(rng.normal(size=(600, 80)))
    for tgt in (0.03, 0.10):
        ic = ld.row_rank_corr(ld.injected(f, f.notna(), tgt, rng), f, f.notna()).mean()
        assert abs(ic - tgt) < 0.01


def test_simulate_reproduces_the_engines_exit_on_the_same_trade():
    # L2 parity: a trade simulated alone exits on the same day, reason and pnl as inside run().
    # Live exit semantics (measured slip, intraday TP, R9 peak from the high), as _simulate uses: RUN_KW's golden-test
    # overrides of those three are dropped. Every closed trade is checked, with the profit lock on.
    pre = synthetic_precomputed()
    P = ld.panels(pre)
    kw = {k: v for k, v in RUN_KW.items() if k not in ("stop_slip", "intraday_tp", "peak_from_high")}
    kw.update(take_profit_pct=0.06, stop_loss_pct=0.05, profit_lock_trigger_pct=0.04, profit_lock_trail_pct=0.01,
              time_stop_days=10)
    res = ef.run(START.isoformat(), END.isoformat(), precomputed=pre, **kw)
    cfg = {"tp": 0.06, "sl": 0.05, "pl": (0.04, 0.01), "tdays": 10}
    trades = [x for x in res["trade_log"] if x["exit_reason"] != "END_OF_BACKTEST"]
    assert len(trades) >= 5
    for t in trades:
        i, j = P["days"].index(t["entry_date"]), P["tickers"].index(t["ticker"])
        r = ld._simulate(P, pre, i, j, cfg)
        assert (P["days"][r["exit_idx"]], r["reason"], r["pnl"]) == (t["exit_date"], t["exit_reason"], t["pnl_pct"])


def test_entry_sets_match_quintile_and_day():
    pre = synthetic_precomputed()
    P = ld.panels(pre)
    E = ld.entry_sets(P)
    q = ld.quintile(P["atr"], P["mask"]).to_numpy()
    sc = P["score"].to_numpy()
    assert len(E["picks"]) == len(E["matched"])
    for (i, j), (i2, j2) in zip(E["picks"], E["matched"]):
        assert i == i2 and q[i, j] == q[i2, j2] and sc[i, j] >= ld.PICK > sc[i2, j2]


def test_engine_size_usd_is_fixed_dollars_without_compounding():
    pre = synthetic_precomputed()
    log = ef.run(START.isoformat(), END.isoformat(), precomputed=pre, **{**RUN_KW, "size_usd": 700.0})["trade_log"]
    assert log and all(abs(t["amount"] - 700.0) < 1e-9 for t in log)


def test_engine_size_draw_takes_fractions_from_its_list_independent_of_score():
    pre = synthetic_precomputed()
    fr = [0.05, 0.11]
    log = ef.run(START.isoformat(), END.isoformat(), precomputed=pre,
                 **{**RUN_KW, "size_draw": {"seed": 4, "fractions": fr}})["trade_log"]
    eq = 10_000.0
    assert log
    first = log[0]
    assert any(math.isclose(first["amount"], eq * f, rel_tol=1e-9) for f in fr)
    again = ef.run(START.isoformat(), END.isoformat(), precomputed=pre,
                   **{**RUN_KW, "size_draw": {"seed": 4, "fractions": fr}})["trade_log"]
    assert [t["amount"] for t in again] == [t["amount"] for t in log]          # seeded
