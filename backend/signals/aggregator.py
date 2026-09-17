# ── Regime-conditioned weight vectors ────────────────────────────────────────
#
# Direction from Q2 parameter sweep (2026-05-27): bull markets favor higher
# spread-signal budget (trend + rs); transition/bear markets favor compressed
# signals (momentum + volume). Neutral = current calibrated weights.
#
# Thresholds for bucket assignment are in regime_bayes.market_regime_state().
# Validation gate RETIRED 2026-09-17: the former "recalibrate at ≥ 4 weeks of
# sector_posterior_history" named a calibration (bucket-conditional PF vs held-out 2021–23)
# that the live series cannot perform — the gate was unsatisfiable by its own input. Replacement
# is a scheduled backend/backtest/regime_conditioned_cs.py study (filed in the vault,
# apex-moc Open Territory 2026-09-17). Weights unchanged until that study reports.
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
