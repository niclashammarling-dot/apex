"""Targeted 7C ablation: every arm pins the pre-R9 peak, and the rank p reads like the battery's."""
import backend.backtest.ablation_r1_2026_10_20 as ab
import backend.backtest.battery_2026_10 as b


def test_every_arm_is_ir1_start0_with_r9_off():
    for revert in ab.ARMS.values():
        ref, *rnd = ab.arm_units(revert)
        kw = dict(ref.kwargs)
        assert ref.window == "IS" and ref.start == 0 and kw["regime_layer"] is False
        assert kw["peak_from_high"] is False and all(kw[k] == v for k, v in revert.items())
        assert len(rnd) == len(b.RND_SEEDS) and all(u.rnd_ref == ref for u in rnd)


class _Store:
    def __init__(self, ref_exp, seed_exps):
        self.ref_exp, self.seed_exps = ref_exp, seed_exps

    def get(self, u):
        if u.rnd_ref is None:
            return {"summary": {"expectancy": {"usd": {"mean": self.ref_exp}}}, "trade_log": [{}] * 7}
        return {"seed": u.seed, "expectancy": {"usd": {"mean": self.seed_exps[u.seed - 1]}}}


def test_read_arm_rank_p_matches_the_battery_formula():
    seeds = [float(s) for s in range(300)]              # 0 .. 299
    r = ab.read_arm(_Store(296.5, seeds), {})
    assert r["p"] == (3 + 1) / 301                        # 297, 298, 299 are >= 296.5
    assert r["rank"] == 298 and r["of"] == 301 and r["trades"] == 7
