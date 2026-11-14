"""
TIME10 shadow exit card runner (vault raw/notes/2026-10/2026-10-24-apex-time10-shadow-exit-card.md): the card's
statistic, null, validity gate, controls and decision rules on synthetic arms. The pipeline (precompute, live
sample, T8's 2022 rows) runs in the read, not here.
"""
import numpy as np

import backend.backtest.time10_shadow_2026_11 as t10

RNG = lambda: np.random.default_rng(7)


def test_capital_day_is_the_ratio_of_sums():
    assert t10.capital_day(np.array([0.06, -0.03]), np.array([5, 10])) == 0.03 / 15
    assert t10.capital_day(np.array([]), np.array([])) is None


def test_identical_arms_have_no_difference_and_no_evidence():
    p, h = np.full(40, 0.01), np.full(40, 8)
    r = t10.swap_permutation(p, h, p, h, 2000, RNG())
    assert r["diff"] == 0 and r["p_two_sided"] == 1.0


def test_a_clearly_better_arm_is_found():
    rng = RNG()
    pl = rng.normal(0.0, 0.01, 200)
    r = t10.swap_permutation(pl + 0.02, np.full(200, 10), pl, np.full(200, 10), 2000, rng)
    assert r["diff"] > 0 and r["p_one_sided"] < 0.01


def test_mde_follows_the_cards_formula():
    rng = RNG()
    pl = rng.normal(0, 0.05, 61)
    pt = pl + rng.normal(0, 0.05, 61)
    assert abs(t10.mde_per_trade(pt, pl) - 2.8 * np.std(pt - pl, ddof=1) / np.sqrt(61)) < 1e-12


def test_tail_reads_the_fifth_percentile_and_the_worst():
    pnl = np.linspace(-0.10, 0.09, 20)
    tl = t10.tail(pnl)
    assert tl["max_loss"] == -0.10 and abs(tl["p95_loss"] - np.percentile(pnl, 5)) < 1e-12


def test_fidelity_gate_needs_sign_agreement_and_a_small_mean_difference():
    real = np.array([0.02, -0.01, 0.03, -0.02, 0.01] * 4)
    assert t10.fidelity(real + 0.001, real)["valid"] is True
    assert t10.fidelity(real + 0.01, real)["valid"] is False           # mean difference 1 pt
    assert t10.fidelity(-real, real)["valid"] is False                 # signs disagree


def test_negative_control_rejects_at_about_five_percent():
    rng = RNG()
    r = t10.negative_control(rng.normal(0.003, 0.05, 61), np.full(61, 8), rng, n=500)
    assert 0.01 <= r["reject_rate_5pct"] <= 0.10


def _tails(t, l):
    return {"TIME10": t10.tail(t), "LIVE": t10.tail(l)}


def test_rules_invalid_veto_support():
    rng = RNG()
    pl = rng.normal(0, 0.01, 400)
    good, bad = pl + 0.01, pl - 0.03
    h = np.full(400, 10)
    ok = {"valid": True}
    assert t10.decide(t10.swap_permutation(good, h, pl, h, 2000, rng), t10.per_trade(good, h, pl, h),
                      0.002, _tails(good, pl), {"valid": False})["outcome"].startswith("invalid")
    assert t10.decide(t10.swap_permutation(bad, h, pl, h, 2000, rng), t10.per_trade(bad, h, pl, h),
                      0.002, _tails(bad, pl), ok)["b_sign_veto"] is True
    r = t10.decide(t10.swap_permutation(good, h, pl, h, 2000, rng), t10.per_trade(good, h, pl, h),
                   0.002, _tails(good, pl), ok)
    assert r["c_support"] and r["d_tail"] and r["outcome"].startswith("support")


def test_support_needs_the_realised_mde_at_most_half_a_point():
    rng = RNG()
    pl = rng.normal(0, 0.01, 400)
    good, h = pl + 0.01, np.full(400, 10)
    r = t10.decide(t10.swap_permutation(good, h, pl, h, 2000, rng), t10.per_trade(good, h, pl, h),
                   0.018, _tails(good, pl), {"valid": True})
    assert r["c_support"] is False and "D4" in r["outcome"]


def test_per_criterion_controls_at_the_cards_n_report_c_as_power():
    # n 61 with a 5 pt per-trade spread: the synthetic winner (+0.3 pt) cannot pass (c); the card expects D4 (§ 8).
    rng = RNG()
    pl = rng.normal(0, 0.05, 61)
    pt = pl + rng.normal(0, 0.05, 61)
    c = t10.criterion_controls(pt, pl, np.full(61, 8), rng, 2000)
    assert c["c_passable_at_this_n"] is False and "D4" in c["c_note"]
    assert c["live"]["c_support"] is False and c["live"]["b_sign_veto"] is False
    assert c["veto_fires_on_a_worse_arm"] is True


def test_per_criterion_controls_pass_c_when_the_power_is_there():
    # Control of the control: with a small spread and a large n, the synthetic winner passes (c).
    rng = RNG()
    pl = rng.normal(0, 0.01, 2000)
    pt = pl + rng.normal(0, 0.005, 2000)
    assert t10.criterion_controls(pt, pl, np.full(2000, 8), rng, 2000)["c_passable_at_this_n"] is True
