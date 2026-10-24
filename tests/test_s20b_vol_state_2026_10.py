"""S20b vol-state L0 card (2026-10-24): the state effect, the block permutation, the per-criterion
controls (a stressed-only IC is found, a level IC is not), the rule."""
import numpy as np
import pandas as pd

import backend.backtest.s20b_vol_state_2026_10 as sb


def _classes(n=400, stressed_every=5):
    lab = ["JUMP_ONLY" if i % stressed_every == 0 else "NEITHER" for i in range(n)]
    return pd.Series(lab, index=[f"d{i:04d}" for i in range(n)])


def test_effect_measures_the_difference_not_the_level():
    rng = np.random.default_rng(1)
    c = _classes()
    base = pd.Series(rng.normal(0.03, 0.05, len(c)), index=c.index)
    e = sb.effect(base, c, 1)
    assert abs(e["effect"]) < 3 * e["se"]                            # same level everywhere: no effect
    shifted = base + 0.05 * sb.stressed_of(c).astype(float)
    e2 = sb.effect(shifted, c, 1)
    assert abs(e2["effect"] - 0.05) < 3 * e2["se"] and e2["t"] > 2
    assert e2["n_stressed"] == 80 and "JUMP_ONLY" in e2["classes"]


def test_missing_class_rows_are_dropped_and_tiny_classes_give_no_effect():
    c = _classes(50, stressed_every=1000)                            # one stressed day only
    c.iloc[0] = "JUMP_ONLY"
    ic = pd.Series(np.linspace(0, 1, 50), index=c.index)
    assert sb.effect(ic, c, 1)["effect"] is None
    c2 = _classes()
    c2.iloc[3] = "MISSING"
    assert sb.effect(pd.Series(np.zeros(len(c2)) + np.arange(len(c2)) * 1e-6, index=c2.index), c2, 1)["n_neither"] == 319


def test_block_permute_keeps_blocks_and_counts():
    c = _classes(105)
    p = sb.block_permute(c, np.random.default_rng(0))
    assert sorted(p.tolist()) == sorted(c.tolist())
    blocks = {tuple(c.to_numpy()[i:i + sb.BLOCK]) for i in range(0, 105, sb.BLOCK)}
    assert all(tuple(p.to_numpy()[i:i + sb.BLOCK]) in blocks for i in range(0, 105, sb.BLOCK))


def test_conditional_injection_is_found_and_level_injection_is_not():
    rng = np.random.default_rng(4)
    n, m = 600, 60
    days = [f"d{i:04d}" for i in range(n)]
    f = pd.DataFrame(rng.normal(size=(n, m)), index=days)
    mask = f.notna()
    c = pd.Series(["LEVEL_ONLY" if (i // 30) % 4 == 0 else "NEITHER" for i in range(n)], index=days)
    st = sb.stressed_of(c)
    sb.TARGET = 0.2                                                  # strong, so a small panel can see it
    cond = sb.conditional_injected(f, mask, st, rng)
    e = sb.effect(sb.ld.row_rank_corr(cond, f, mask), c, 1)
    assert e["t"] > 3
    lvl = sb.ld.injected(f, mask, 0.2, rng)
    e2 = sb.effect(sb.ld.row_rank_corr(lvl, f, mask), c, 1)
    assert abs(e2["t"]) < 3
    sb.TARGET = 0.03


def test_verdict_rule():
    pos, neg = {"t": 3.0}, {"t": -3.0}
    assert sb.verdict(pos, {"t": 1.5}, True, +1, 40) == "supported"
    assert sb.verdict(pos, {"t": 1.5}, True, +1, 10) == "IS-supported, OOS unpowered"
    assert sb.verdict(pos, {"t": 0.2}, True, +1, 40).startswith("unanswered (IS only")
    assert sb.verdict(neg, {"t": -1.5}, True, +1, 40) == "contradicted"
    assert sb.verdict(neg, {"t": -1.5}, True, 0, 40) == "supported"         # score: either sign
    assert sb.verdict(pos, {"t": 1.5}, False, +1, 40) == "unanswered"
