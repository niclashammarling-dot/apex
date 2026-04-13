from backend.config import BASE_WIN_RATE, STOP_LOSS_PCT, TAKE_PROFIT_PCT


def compute(spy_regime: float, rolling_win_rate: float | None = None, atr_pct: float = 0.0) -> dict:
    """
    Expected Value + Kelly position sizing (architecture §4.3).

    P(win) is decoupled from momentum/volume to avoid circular weighting:
      - Base rate:           0.55 (honest prior, slight systematic edge)
      - SPY regime:         +0.03 if SPY > MA50, else -0.03
      - Rolling adjustment:  actual_win_rate delta after WIN_RATE_MIN_TRADES closed trades
      - Clamped to [0.35, 0.75] — prevents Kelly from going absurd

    Dynamic reward-to-risk (B):
      - Uses ATR(14) as % of price to set a realistic stop loss floor.
      - If ATR is wide (volatile stock), the effective stop is wider, lowering B.
      - effective_sl = max(STOP_LOSS_PCT, 1.5 * atr_pct)
      - This prevents Kelly from over-sizing into high-volatility names.

    EV  = p * effective_tp - (1-p) * effective_sl
    f*  = (p*b - (1-p)) / b          ← full Kelly fraction
    pos = f* * 0.25                  ← quarter-Kelly (conservative sizing)
    ev_norm = EV / effective_tp      → [0, 1] for aggregation

    Returns keys: p_win, ev, ev_norm, kelly_size, effective_sl, atr_pct
    """
    p = BASE_WIN_RATE + spy_regime
    if rolling_win_rate is not None:
        p += rolling_win_rate - BASE_WIN_RATE
    p = max(0.35, min(0.75, p))

    # Dynamic stop loss floor: don't size as if the stop is tight when ATR is wide
    effective_sl = max(STOP_LOSS_PCT, 1.5 * atr_pct) if atr_pct > 0 else STOP_LOSS_PCT
    effective_tp = TAKE_PROFIT_PCT
    B            = effective_tp / effective_sl

    ev     = p * effective_tp - (1 - p) * effective_sl
    f_star = (p * B - (1 - p)) / B
    kelly  = max(0.0, f_star * 0.25)
    ev_norm = max(0.0, min(1.0, ev / effective_tp))

    return {
        "p_win":        round(p, 4),
        "ev":           round(ev, 5),
        "ev_norm":      round(ev_norm, 4),
        "kelly_size":   round(kelly, 4),
        "effective_sl": round(effective_sl, 4),
        "atr_pct":      round(atr_pct, 4),
    }
