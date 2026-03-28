"""Relative strength vs SPY over a 20-day window."""
import pandas as pd


def compute(df: pd.DataFrame, spy_return_20d: float) -> dict:
    """
    Score how much this ticker outperforms SPY over the last 20 trading days.

    excess_return = ticker_return_20d - spy_return_20d

    Score mapping — linear, centred at 0%:
        +10% excess  → 1.0   (strong outperformer)
          0% excess  → 0.5   (inline with market)
        -10% excess  → 0.0   (strong underperformer)

    rs_score = clip((excess / 0.10 + 1) / 2, 0, 1)

    Falls back to neutral 0.5 if insufficient data.
    """
    close = df["Close"].dropna()

    if len(close) < 20:
        return {
            "rs_score":          0.5,
            "ticker_return_20d": 0.0,
            "excess_return":     0.0,
        }

    ticker_return = float(close.iloc[-1] / close.iloc[-20] - 1)
    excess        = ticker_return - spy_return_20d
    rs_score      = max(0.0, min(1.0, (excess / 0.10 + 1.0) / 2.0))

    return {
        "rs_score":          round(rs_score, 4),
        "ticker_return_20d": round(ticker_return, 4),
        "excess_return":     round(excess, 4),
    }
