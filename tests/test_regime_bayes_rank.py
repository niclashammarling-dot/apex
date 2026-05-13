"""Tests for _compute_rank_lrs and lr_rank integration in _apply_signals."""
import math
import pandas as pd
import pytest

from backend.regime.regime_bayes import _apply_signals, _compute_rank_lrs


def _make_raw(etf_closes: dict[str, list[float]]) -> pd.DataFrame:
    """Build a group_by='ticker' style yfinance DataFrame."""
    arrays = []
    for etf, prices in etf_closes.items():
        for col in ["Close", "Open", "High", "Low", "Volume"]:
            arrays.append((etf, col))
    idx = pd.MultiIndex.from_tuples(arrays)
    n = max(len(v) for v in etf_closes.values())
    data = {}
    for etf, prices in etf_closes.items():
        data[(etf, "Close")] = prices + [None] * (n - len(prices))
        for col in ["Open", "High", "Low", "Volume"]:
            data[(etf, col)] = [0.0] * n
    return pd.DataFrame(data, columns=idx)


class TestComputeRankLrs:
    def test_rank1_gets_base_lr(self):
        # XLK strongest, XLE weakest — 10d return computed from 11 prices
        prices_strong = [100.0] * 10 + [115.0]  # +15%
        prices_weak   = [100.0] * 10 + [85.0]   # -15%
        raw = _make_raw({"XLK": prices_strong, "XLE": prices_weak})
        etf_map = {"Technology": "XLK", "Energy": "XLE"}
        lrs = _compute_rank_lrs(raw, etf_map, n_days=10)
        assert lrs["Technology"] == pytest.approx(1.5, abs=0.001)
        assert lrs["Energy"]     == pytest.approx(1.5 ** -1.0, abs=0.001)

    def test_middle_rank_near_neutral(self):
        # 3 sectors: rank 2 of 3 maps to exponent = (3+1-4)/(3-1) = 0
        prices = {
            "A": [100.0] * 10 + [110.0],  # rank 1
            "B": [100.0] * 10 + [105.0],  # rank 2
            "C": [100.0] * 10 + [100.0],  # rank 3
        }
        raw = _make_raw(prices)
        etf_map = {"s1": "A", "s2": "B", "s3": "C"}
        lrs = _compute_rank_lrs(raw, etf_map, n_days=10)
        assert lrs["s2"] == pytest.approx(1.0, abs=0.001)

    def test_ties_get_average_rank(self):
        # Two sectors with identical return → both get same LR from average rank
        prices_same = [100.0] * 10 + [110.0]
        prices_low  = [100.0] * 10 + [90.0]
        raw = _make_raw({"A": prices_same, "B": prices_same, "C": prices_low})
        etf_map = {"s1": "A", "s2": "B", "s3": "C"}
        lrs = _compute_rank_lrs(raw, etf_map, n_days=10)
        # A and B tie for rank 1 and 2 → average rank = 1.5
        # exponent = (3 + 1 - 2*1.5) / (3-1) = 1/2 = 0.5 → LR = 1.5^0.5 ≈ 1.2247
        expected = pytest.approx(1.5 ** 0.5, abs=0.001)
        assert lrs["s1"] == expected
        assert lrs["s2"] == expected
        assert lrs["s1"] == lrs["s2"]

    def test_missing_etf_excluded_not_erroring(self):
        prices = {"XLK": [100.0] * 10 + [110.0]}
        raw = _make_raw(prices)
        etf_map = {"Technology": "XLK", "Energy": "XLE"}  # XLE not in raw
        lrs = _compute_rank_lrs(raw, etf_map, n_days=10)
        assert "Technology" in lrs
        assert "Energy" not in lrs  # excluded, not LR=1.0

    def test_insufficient_data_excluded(self):
        raw = _make_raw({"XLK": [100.0, 110.0]})  # only 2 prices, need 11
        lrs = _compute_rank_lrs(raw, {"Technology": "XLK"}, n_days=10)
        assert lrs == {}

    def test_symmetry(self):
        # LR for rank k should equal 1 / LR for rank (n+1-k)
        prices = {
            f"E{i}": [100.0] * 10 + [100.0 + i]
            for i in range(8)
        }
        raw = _make_raw(prices)
        etf_map = {f"s{i}": f"E{i}" for i in range(8)}
        lrs = _compute_rank_lrs(raw, etf_map, n_days=10)
        vals = sorted(lrs.values(), reverse=True)
        n = len(vals)
        for i in range(n // 2):
            assert vals[i] * vals[n - 1 - i] == pytest.approx(1.0, abs=0.01)


class TestApplySignalsWithRank:
    def test_lr_rank_default_is_neutral(self):
        result_no_rank = _apply_signals(0.3, 1.0, 1.0, 1.0, 1.0)
        result_neutral = _apply_signals(0.3, 1.0, 1.0, 1.0, 1.0, lr_rank=1.0)
        assert result_no_rank["posterior"] == result_neutral["posterior"]

    def test_after_rank_and_posterior_match(self):
        result = _apply_signals(0.3, 1.2, 1.1, 1.0, 1.0, lr_rank=1.5)
        assert result["after_rank"] == result["posterior"]

    def test_lr_rank_in_trace(self):
        result = _apply_signals(0.3, 1.0, 1.0, 1.0, 1.0, lr_rank=1.5)
        assert "lr_rank" in result
        assert "after_rank" in result
        assert result["lr_rank"] == 1.5

    def test_bullish_rank_raises_posterior(self):
        base = _apply_signals(0.4, 1.0, 1.0, 1.0, 1.0, lr_rank=1.0)
        bull = _apply_signals(0.4, 1.0, 1.0, 1.0, 1.0, lr_rank=1.5)
        assert bull["posterior"] > base["posterior"]

    def test_bearish_rank_lowers_posterior(self):
        base = _apply_signals(0.4, 1.0, 1.0, 1.0, 1.0, lr_rank=1.0)
        bear = _apply_signals(0.4, 1.0, 1.0, 1.0, 1.0, lr_rank=0.667)
        assert bear["posterior"] < base["posterior"]
