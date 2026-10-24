"""S21b (2026-10-24): episodes are contiguous stressed runs, the episode bootstrap centres on the day mean
and widens with dependence, picks take the k most negative R2 with same-quintile random partners, and
the verdict follows the card."""
import numpy as np

import backend.backtest.s21b_stressed_reversal_2026_10 as s


def test_episodes_are_contiguous_runs():
    st = np.array([0, 1, 1, 0, 0, 1, 0, 1, 1, 1], bool)
    assert s.episodes(st) == [[1, 2], [5], [7, 8, 9]]
    assert s.episodes(np.zeros(4, bool)) == []


def test_episode_ci_centres_on_the_mean_and_covers_it():
    rng = np.random.default_rng(0)
    eps = [[i, i + 1, i + 2] for i in range(0, 300, 3)]
    diff = {i: float(rng.normal(0.01, 0.02)) for e in eps for i in e}
    c = s.episode_ci(diff, eps, rng)
    assert abs(c["mean"] - np.mean(list(diff.values()))) < 1e-12
    assert c["lo"] < c["mean"] < c["hi"] and c["n_episodes"] == 100 and c["n_days"] == 300
    assert s.episode_ci({}, eps, rng)["mean"] is None


def test_picks_for_day_takes_the_most_negative_r2_and_matches_quintiles():
    rng = np.random.default_rng(1)
    n = 30
    r2 = np.array([np.linspace(-0.1, 0.1, n)])
    score = np.array([np.linspace(0, 1, n)])
    mask = np.ones((1, n), bool)
    q = np.array([np.arange(n) % 5])
    p = s.picks_for_day(0, r2, score, mask, q, 50, rng)
    assert p["rev"] == [0, 1, 2, 3, 4]
    assert p["score"] == [29, 28, 27, 26, 25]
    for j, row in zip(p["rev"], p["rnd"]):
        assert len(row) == 50 and all(q[0, c] == q[0, j] and c != j for c in row)
    assert s.picks_for_day(0, r2[:, :5], score[:, :5], mask[:, :5], q[:, :5], 5, rng) == {}


def _win(mean, lo, hi, plus=True, n_days=200):
    ex = {"rev_minus_rnd": {"mean": mean, "lo": lo, "hi": hi}, "controls": {"plus_supported": plus}}
    return {"n_stressed_days": n_days, "exits": {"live": ex, "time10": ex}}


def test_verdict_rule():
    assert s.verdict(_win(0.01, 0.002, 0.02), _win(0.005, -0.01, 0.02))["live"]["verdict"] == "supported"
    assert s.verdict(_win(0.01, 0.002, 0.02), _win(0.005, -0.01, 0.02, n_days=10))["live"]["verdict"] == "supported, OOS unpowered"
    assert s.verdict(_win(0.01, 0.002, 0.02), _win(-0.005, -0.01, 0.0))["live"]["verdict"].startswith("unanswered (OOS")
    assert s.verdict(_win(-0.01, -0.02, -0.002), _win(-0.005, -0.01, 0.0))["live"]["verdict"] == "contradicted"
    assert s.verdict(_win(0.01, -0.002, 0.02), _win(0.005, -0.01, 0.02))["live"]["verdict"] == "unanswered"
    assert s.verdict(_win(0.01, 0.002, 0.02, plus=False), _win(0.005, 0, 0.02))["live"]["verdict"] == "unanswered (control)"
