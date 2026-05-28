def compute(
    momentum_score: float,
    volume_score: float,
    trend_score: float,
    rs_score: float,
) -> float:
    """
    Weighted signal score — 4 independent signals.

    signal_score = 0.25 * momentum_score   (RSI + MA20 deviation)
                 + 0.20 * volume_score      (volume vs 30d avg)
                 + 0.30 * trend_score       (MACD + MA50 alignment)
                 + 0.25 * rs_score          (outperformance vs SPY 20d)

    ev_norm removed: market-state indicator redundant with RegimeBayes gate
    conditioning. Its 0.20 weight added a near-constant term (~0.095 uniform
    across all candidates on any day), contributing zero cross-sectional
    discrimination. Weight redistributed equally to trend and rs.
    Redesign as a ticker-specific EV signal is a future session item.

    Output: 0.0 – 1.0
    """
    score = (
        0.25 * momentum_score +
        0.20 * volume_score   +
        0.30 * trend_score    +
        0.25 * rs_score
    )
    return round(min(max(score, 0.0), 1.0), 4)
