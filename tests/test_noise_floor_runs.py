"""NOISE_FLOOR re-measure: summarize() reproduces the 2026-09-14 block from its six recorded runs."""
from scripts.noise_floor_runs import summarize

SCORES_0914 = [0.6479, 0.7045, 0.6896, 0.6497, 0.6830, 0.6183]   # optimizer.py NOISE_FLOOR comment
KEPT_0914 = [3, 8, 9, 8, 4, 5]


def test_summarize_reproduces_the_0914_block():
    s = summarize(SCORES_0914, KEPT_0914)
    assert (s["n_runs"], s["mean"], s["sigma"], s["range"], s["threshold"]) == (6, 0.666, 0.032, 0.086, 0.09)
    assert (s["kept_mean"], s["kept_sigma"]) == (6.2, 2.5)


def test_threshold_takes_the_larger_route():
    # Range below the 2σ difference band: the band decides (0.0911 > 0.086 above).
    assert summarize(SCORES_0914, KEPT_0914)["threshold_routes"]["two_sigma_diff"] > 0.086
    # Two extremes around a tight middle: range 0.10 > 2·σ·√2 ≈ 0.089, so the range decides.
    s = summarize([0.60, 0.65, 0.65, 0.65, 0.65, 0.70], [1, 2, 3, 4, 5, 6])
    assert s["threshold_routes"]["two_sigma_diff"] < s["threshold_routes"]["range"]
    assert s["threshold"] == 0.1


def test_none_cause_follows_the_composite_score_order():
    from scripts.noise_floor_runs import none_cause
    r = {"total_trades": 12, "win_rate": 0.20, "sharpe": 1.0}
    assert none_cause(ValueError("x"), None, 8, 0.30) == "exception:ValueError"
    assert none_cause(None, {**r, "total_trades": 5}, 8, 0.30) == "trades_below_floor"   # trades checked before win rate
    assert none_cause(None, r, 8, 0.30) == "win_rate_below_floor"
    assert none_cause(None, {**r, "win_rate": 0.5, "sharpe": float("nan")}, 8, 0.30) == "sharpe_not_finite"
    assert none_cause(None, {**r, "win_rate": 0.5}, 8, 0.30) == "unclassified"


def test_roster_counts_unique_tickers_and_hashes_them_in_order(monkeypatch):
    import backend.ticker_config as tc
    from scripts.noise_floor_runs import roster
    monkeypatch.setattr(tc, "get_sectors", lambda: {"A": {"tickers": ["MSFT", "AAPL"]}, "B": {"tickers": ["AAPL", "XOM"]}})
    r = roster()
    assert r["n_tickers"] == 3
    monkeypatch.setattr(tc, "get_sectors", lambda: {"B": {"tickers": ["XOM", "AAPL"]}, "A": {"tickers": ["MSFT"]}})
    assert roster()["hash"] == r["hash"]                    # same set, any order: same hash
