"""Identical-arms guard (ruling 2026-10-20), fed the case that prompted it and the battery's one
explained pair."""
from pathlib import Path

import pytest

import backend.backtest.archive_guard as ag
import backend.backtest.battery_2026_10 as b


def test_tonights_case_is_refused_naming_the_parameter():
    # The first 1,000-draw live-vs-engine run: pre_r9 and r9 bit-identical.
    same = {"all": {"eng_mean": 0.004}, "rows": [1, 2]}
    with pytest.raises(ag.IdenticalArms, match=r"peak_from_high"):
        ag.refuse_identical({"pre_r9": ({"peak_from_high": False}, same), "r9": ({"peak_from_high": True}, dict(same))})


def test_different_results_pass_and_same_settings_are_not_a_pair():
    assert ag.refuse_identical({"a": ({"x": 1}, [1]), "b": ({"x": 2}, [2])}) == []
    assert ag.identical_pairs({"a": ({"x": 1}, [1]), "b": ({"x": 1}, [1])}) == []


def test_an_explained_pair_passes_and_is_recorded():
    out = ag.refuse_identical({"a": ({"x": 1}, [1]), "b": ({"x": 2}, [1])}, {frozenset({"a", "b"}): "why"})
    assert out == [{"arms": ["a", "b"], "differ_in": ["x"], "explained": "why"}]


class _Store:
    def __init__(self, recs):
        self.recs = recs

    def has(self, u):
        return u.key() in self.recs

    def get(self, u):
        return self.recs[u.key()]


def test_battery_reader_refuses_an_unexplained_pair_and_accepts_the_hv1_one():
    rec = {"trade_log": [{"ticker": "X"}], "summary": {"n": 1}}
    u1, u2 = b.unit("IS", b.R2, 0), b.unit("IS", b.R2, 0, sl_cooldown_days=3)
    cell = b.Cell("t", "note", [u1, u2], lambda s: {})
    with pytest.raises(ag.IdenticalArms, match="sl_cooldown_days"):
        b.identical_refs(cell, _Store({u1.key(): rec, u2.key(): rec}))
    v1, v2 = b.unit("OOS", b.R2, vix9d_kelly=True), b.unit("OOS", b.R2, vix9d_kelly=True, vix_threshold=None)
    out = b.identical_refs(b.Cell("h", "note", [v1, v2], lambda s: {}), _Store({v1.key(): rec, v2.key(): rec}))
    assert len(out) == 1 and "nothing to stop" in out[0]["explained"]
