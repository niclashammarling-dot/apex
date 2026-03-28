"""MACD(12,26,9) + MA50 alignment trend score."""
import pandas as pd


def compute(df: pd.DataFrame) -> dict:
    """
    Trend score from MACD crossover and 50d MA alignment.

    Components (all binary 0/1):
      macd_above_signal  — MACD line > signal line (bullish crossover)       weight 0.30
      hist_growing       — histogram increasing last bar (accel momentum)    weight 0.25
      macd_positive      — MACD line above zero line                         weight 0.25
      ma50_above         — price above 50d MA (medium-term uptrend)          weight 0.20

    trend_score ∈ [0.0, 1.0]
    Requires >= 50 bars — use 90d fetch.
    Falls back to neutral 0.5 if insufficient data.
    """
    close = df["Close"].dropna()

    if len(close) < 35:
        return {
            "trend_score": 0.5,
            "macd":        0.0,
            "macd_signal": 0.0,
            "macd_hist":   0.0,
            "ma50":        None,
        }

    # --- MACD(12, 26, 9) ---
    ema12       = close.ewm(span=12, adjust=False).mean()
    ema26       = close.ewm(span=26, adjust=False).mean()
    macd_line   = ema12 - ema26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    histogram   = macd_line - signal_line

    latest_macd = float(macd_line.iloc[-1])
    latest_sig  = float(signal_line.iloc[-1])
    latest_hist = float(histogram.iloc[-1])
    prev_hist   = float(histogram.iloc[-2]) if len(histogram) > 1 else latest_hist

    macd_above   = 1.0 if latest_macd > latest_sig  else 0.0
    hist_growing = 1.0 if latest_hist > prev_hist    else 0.0
    macd_positive = 1.0 if latest_macd > 0           else 0.0

    # --- MA50 alignment ---
    ma50_val   = None
    ma50_above = 0.5  # neutral when insufficient history
    if len(close) >= 50:
        ma50     = float(close.rolling(50).mean().iloc[-1])
        price    = float(close.iloc[-1])
        ma50_val = round(ma50, 4)
        ma50_above = 1.0 if price > ma50 else 0.0

    trend_score = (
        0.30 * macd_above   +
        0.25 * hist_growing +
        0.25 * macd_positive +
        0.20 * ma50_above
    )

    return {
        "trend_score": round(trend_score, 4),
        "macd":        round(latest_macd, 4),
        "macd_signal": round(latest_sig,  4),
        "macd_hist":   round(latest_hist, 4),
        "ma50":        ma50_val,
    }
