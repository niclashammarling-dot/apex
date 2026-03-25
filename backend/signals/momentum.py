import pandas as pd


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Exponential smoothing RSI — avoids pandas-ta version friction."""
    delta    = series.diff()
    gain     = delta.clip(lower=0)
    loss     = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs       = avg_gain / avg_loss.replace(0, float("nan"))
    return 100 - (100 / (1 + rs))


def compute(df: pd.DataFrame) -> dict:
    """
    Momentum score based on price vs 20d MA and RSI(14).

    momentum_score formula (from architecture §4.1):
        momentum      = (price - ma20) / ma20
        rsi_score     = (rsi - 50) / 50          → [-1, +1]
        combined      = 0.6 * momentum + 0.4 * rsi_score
        normalized    → [0, 1] for aggregation

    Returns keys: price, ma20, rsi, momentum_raw, momentum_score
    """
    close = df["Close"]
    price = float(close.iloc[-1])
    ma20  = float(close.rolling(20).mean().iloc[-1])

    rsi_series = _rsi(close, 14)
    rsi        = float(rsi_series.iloc[-1]) if not pd.isna(rsi_series.iloc[-1]) else 50.0

    momentum_raw = (price - ma20) / ma20 if ma20 else 0.0
    rsi_score    = (rsi - 50) / 50                          # [-1, +1]

    # Clip momentum_raw to ±20% before combining.
    # Stocks rarely deviate more than 20% from their 20d MA in normal conditions,
    # and without this clip extreme movers inflate the combined score beyond
    # the normalization range, making the final clamp misleading.
    momentum_clipped = max(-0.20, min(0.20, momentum_raw))

    # combined ∈ [-0.52, 0.52], so combined + 0.5 ∈ [-0.02, 1.02] → maps cleanly to [0,1]
    combined = 0.6 * momentum_clipped + 0.4 * rsi_score

    momentum_score = max(0.0, min(1.0, combined + 0.5))

    return {
        "price":             round(price, 4),
        "ma20":              round(ma20, 4),
        "rsi":               round(rsi, 2),
        "momentum_raw":      round(momentum_raw, 5),
        "momentum_clipped":  round(momentum_clipped, 5),
        "momentum_score":    round(momentum_score, 4),
    }
