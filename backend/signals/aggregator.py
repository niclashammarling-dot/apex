def compute(momentum_score: float, volume_score: float, ev_norm: float) -> float:
    """
    Weighted signal score (architecture §4.4).

    signal_score = 0.40 * momentum_score
                 + 0.30 * volume_score
                 + 0.30 * ev_norm

    Output: 0.0 – 1.0
    Lock 1 threshold: >= 0.65
    """
    score = (
        0.40 * momentum_score +
        0.30 * volume_score   +
        0.30 * ev_norm
    )
    return round(min(max(score, 0.0), 1.0), 4)
