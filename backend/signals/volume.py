import pandas as pd


def compute(df: pd.DataFrame) -> dict:
    """
    Volume anomaly score vs 30d average (architecture §4.2).

    volume_score = min(volume_ratio / 2.0, 1.0)
      ratio 1.0 (normal)  → score 0.50
      ratio 2.0 (2× avg)  → score 1.00
      ratio 0.5 (half avg) → score 0.25

    Returns keys: volume, avg_vol_30d, volume_ratio, volume_score
    """
    vol         = df["Volume"]
    volume      = int(vol.iloc[-1])
    avg_vol_30d = float(vol.rolling(30).mean().iloc[-1])

    volume_ratio = volume / avg_vol_30d if avg_vol_30d > 0 else 1.0
    volume_score = min(volume_ratio / 2.0, 1.0)

    return {
        "volume":       volume,
        "avg_vol_30d":  round(avg_vol_30d, 0),
        "volume_ratio": round(volume_ratio, 3),
        "volume_score": round(volume_score, 4),
    }
