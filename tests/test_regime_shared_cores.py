"""
Shared cores the backtest engine calls instead of copying (2026-10-17, engine port):
sector_caps.dynamic_caps_from (row 21) and RegimeBayes.size_multipliers (row 20).

The expected values were produced by the parent code (cf3db17 sector_caps.py and
gate_runner._compute_bayesian_multipliers) on these exact inputs, 2026-10-17; the
refactor must reproduce them.
"""
from unittest.mock import patch

from backend.gate.gate_runner import _compute_bayesian_multipliers
from backend.regime.regime_bayes import RegimeBayes, RegimeResult
from backend.sector_caps import compute_dynamic_caps, dynamic_caps_from

SCORES = {"Technology": 0.62, "Energy": 0.48, "Healthcare": 0.41, "Industrials": 0.30, "RealEstate": 0.12}
STATS  = {"available": True, "sectors": {"Technology": {"streak_days": 3}, "Energy": {"streak_days": 9},
                                         "Healthcare": {"streak_days": 17}, "Industrials": {"streak_days": 30}}}
RR = RegimeResult(date="2026-10-15", leaderboard=[], leader="Technology", qualifiers=[],
                  allocation={"Technology": 0.55, "Energy": 0.30, "Healthcare": 0.15,
                              "Industrials": 0.0, "RealEstate": 0.0})

PARENT_CAPS_WITH    = {"Technology": 0.45, "Energy": 0.407, "Healthcare": 0.3646,
                       "Industrials": 0.1574, "RealEstate": 0.15}
PARENT_CAPS_WITHOUT = {"Technology": 0.3825, "Energy": 0.3731, "Healthcare": 0.3665,
                       "Industrials": 0.2098, "RealEstate": 0.15}


def _live(regime_result):
    with patch("backend.db.get_latest_sector_scores", return_value=SCORES), \
         patch("backend.sector_regime.compute_sector_regime", return_value=STATS):
        return compute_dynamic_caps(0.30, regime_result=regime_result)


def test_live_wrapper_reproduces_the_parent():
    assert _live(RR) == PARENT_CAPS_WITH
    assert _live(None) == PARENT_CAPS_WITHOUT


def test_pure_core_is_what_the_wrapper_returns():
    assert dynamic_caps_from(0.30, SCORES, STATS["sectors"], RR) == PARENT_CAPS_WITH
    assert dynamic_caps_from(0.30, {}, STATS["sectors"], RR) == {}
    # missing streak → the 10-day default (duration weight 1.00), as live
    assert dynamic_caps_from(0.30, SCORES, {}, None)["Industrials"] == round(0.30 * (0.30 / 0.386), 4)


EVALUATED = [({"ticker": t, "sector": s, "signal_score": sc}, {"outcome": o}) for t, s, sc, o in [
    ("AAPL", "Technology", 0.71, "TRADE_QUEUED"), ("MSFT", "Technology", 0.66, "TRADE_QUEUED_PENDING_L5"),
    ("NVDA", "Technology", 0.80, "TRADE_QUEUED"), ("XOM", "Energy", 0.58, "TRADE_QUEUED"),
    ("CVX", "Energy", 0.61, "BLOCKED_L2"), ("PLD", "RealEstate", 0.70, "TRADE_QUEUED")]]
PARENT_MULT = {"AAPL": 0.9818, "MSFT": 0.9125, "NVDA": 1.1062, "XOM": 1.0}


def test_gate_runner_multipliers_reproduce_the_parent():
    assert _compute_bayesian_multipliers(EVALUATED, RR) == PARENT_MULT
    assert _compute_bayesian_multipliers(EVALUATED, None) == {}


def test_size_multipliers_cancel_the_allocation():
    """n·s/Σs: the sector's allocation level drops out (pre-work finding 2)."""
    queued = {"Technology": [("AAPL", 0.71), ("MSFT", 0.66), ("NVDA", 0.80)]}
    lo = RegimeBayes.size_multipliers({"Technology": 0.2}, queued)
    hi = RegimeBayes.size_multipliers({"Technology": 0.9}, queued)
    # Only the 4-decimal rounding of ticker_allocations survives: error ≤ 0.5e-4 / (A/n),
    # 7.5e-4 at A = 0.2, n = 3 (first run: 0.9810 vs 0.9817 for AAPL).
    for t in lo:
        assert abs(lo[t] - hi[t]) <= 1e-3
        assert abs(lo[t] - 3 * dict(queued["Technology"])[t] / 2.17) <= 1e-3
    assert RegimeBayes.size_multipliers({"Technology": 0.0}, queued) == {}


def test_cap_factors_times_base_is_the_live_cap():
    from backend.sector_caps import cap_factors, caps_from_factors
    f = cap_factors(SCORES, STATS["sectors"], RR)
    assert caps_from_factors(0.30, f) == PARENT_CAPS_WITH
    assert caps_from_factors(0.30, cap_factors(SCORES, STATS["sectors"], None)) == PARENT_CAPS_WITHOUT
