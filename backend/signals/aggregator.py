# ── Regime-conditioned weight vectors ────────────────────────────────────────
#
# Direction from Q2 parameter sweep (2026-05-27): bull markets favor higher
# spread-signal budget (trend + rs); transition/bear markets favor compressed
# signals (momentum + volume). Neutral = current calibrated weights.
#
# Thresholds for bucket assignment are in regime_bayes.market_regime_state().
# Validation gate: recalibrate once sector_posterior_history has ≥ 4 weeks of
# data — compare bucket-conditional PF against held-out 2021–23 transition window.
_WEIGHTS = {
    "bull":    {"momentum": 0.20, "volume": 0.15, "trend": 0.35, "rs": 0.30},
    "neutral": {"momentum": 0.25, "volume": 0.20, "trend": 0.30, "rs": 0.25},
    "bear":    {"momentum": 0.30, "volume": 0.25, "trend": 0.25, "rs": 0.20},
}


def compute(
    momentum_score: float,
    volume_score: float,
    trend_score: float,
    rs_score: float,
    regime_state: str = "neutral",
) -> float:
    """
    Weighted signal score — 4 independent signals, regime-conditioned weights.

    regime_state: "bull" | "neutral" | "bear" — from regime_bayes.market_regime_state().
    Unknown values fall back to neutral weights.

    ev_norm removed: market-state indicator redundant with RegimeBayes gate
    conditioning. Its 0.20 weight added a near-constant term (~0.095 uniform
    across all candidates on any day), contributing zero cross-sectional
    discrimination. Weight redistributed equally to trend and rs.
    Redesign as a ticker-specific EV signal is a future session item.

    Output: 0.0 – 1.0
    """
    w = _WEIGHTS.get(regime_state, _WEIGHTS["neutral"])
    score = (
        w["momentum"] * momentum_score +
        w["volume"]   * volume_score   +
        w["trend"]    * trend_score    +
        w["rs"]       * rs_score
    )
    return round(min(max(score, 0.0), 1.0), 4)
