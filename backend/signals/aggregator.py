def compute(
    momentum_score: float,
    volume_score: float,
    ev_norm: float,
    trend_score: float,
    rs_score: float,
) -> float:
    """
    Weighted signal score — 5 independent signals.

    signal_score = 0.25 * momentum_score   (RSI + MA20 deviation)
                 + 0.20 * volume_score      (volume vs 30d avg)
                 + 0.20 * ev_norm           (Kelly expected value)
                 + 0.20 * trend_score       (MACD + MA50 alignment)
                 + 0.15 * rs_score          (outperformance vs SPY 20d)

    Output: 0.0 – 1.0
    """
    score = (
        0.25 * momentum_score +
        0.20 * volume_score   +
        0.20 * ev_norm        +
        0.20 * trend_score    +
        0.15 * rs_score
    )
    return round(min(max(score, 0.0), 1.0), 4)
