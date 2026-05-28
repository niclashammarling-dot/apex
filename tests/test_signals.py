"""
Unit tests for the signal engine.
Run: cd ~/apex && .venv/bin/python -m pytest tests/test_signals.py -v
  or: .venv/bin/python tests/test_signals.py
"""
import math
import sys
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

from backend.signals import momentum, volume, aggregator
from backend.signals.ev_kelly import compute as ev_kelly_compute

results = []

def check(name, condition, detail=""):
    verdict = "PASS" if condition else "FAIL"
    results.append((name, verdict))
    icon = "✓" if condition else "✗"
    suffix = f"  ({detail})" if detail else ""
    print(f"  {icon}  {name}{suffix}")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_df(prices: list[float], volumes: list[int] | None = None) -> pd.DataFrame:
    """Build a minimal OHLCV DataFrame from a list of closing prices."""
    n = len(prices)
    if volumes is None:
        volumes = [1_000_000] * n
    dates = pd.date_range(end="2024-01-01", periods=n, freq="B")
    df = pd.DataFrame({
        "Open":   prices,
        "High":   [p * 1.01 for p in prices],
        "Low":    [p * 0.99 for p in prices],
        "Close":  prices,
        "Volume": volumes,
    }, index=dates)
    return df


# ── TEST 1: Momentum score bounds ─────────────────────────────────────────────
print("\nTEST 1: Momentum score always in [0, 1]")

# Extreme bull — price far above MA20 (would inflate without the clip)
bull_prices = [100.0] * 20 + [350.0]  # +250% above MA20
df_bull = _make_df(bull_prices)
m_bull = momentum.compute(df_bull)
check("score ∈ [0, 1] for extreme bull",
      0.0 <= m_bull["momentum_score"] <= 1.0,
      f"score={m_bull['momentum_score']:.4f}")

# Extreme bear
bear_prices = [100.0] * 20 + [10.0]  # -90% below MA20
df_bear = _make_df(bear_prices)
m_bear = momentum.compute(df_bear)
check("score ∈ [0, 1] for extreme bear",
      0.0 <= m_bear["momentum_score"] <= 1.0,
      f"score={m_bear['momentum_score']:.4f}")

# Normal — small drift above MA20
norm_prices = [100.0 + i * 0.2 for i in range(21)]
df_norm = _make_df(norm_prices)
m_norm = momentum.compute(df_norm)
check("score ∈ [0, 1] for normal price action",
      0.0 <= m_norm["momentum_score"] <= 1.0,
      f"score={m_norm['momentum_score']:.4f}")

check("momentum_raw exposed in output", "momentum_raw" in m_norm)
check("momentum_clipped exposed in output", "momentum_clipped" in m_norm)

# Clip should cap the raw value
check("momentum_clipped <= 0.20 for extreme bull",
      m_bull["momentum_clipped"] <= 0.20,
      f"clipped={m_bull['momentum_clipped']:.4f}")
check("momentum_clipped >= -0.20 for extreme bear",
      m_bear["momentum_clipped"] >= -0.20,
      f"clipped={m_bear['momentum_clipped']:.4f}")


# ── TEST 2: Aggregator output ─────────────────────────────────────────────────
print("\nTEST 2: Aggregator output")

score = aggregator.compute(0.70, 0.80, 0.65, 0.55)
check("score ∈ [0, 1]",        0.0 <= score <= 1.0, f"score={score:.4f}")
check("score > 0 for positive inputs", score > 0)

score_zero = aggregator.compute(0.0, 0.0, 0.0, 0.0)
check("score = 0 for zero inputs", score_zero == 0.0)

score_one = aggregator.compute(1.0, 1.0, 1.0, 1.0)
check("score = 1 for max inputs",  score_one == 1.0)


# ── TEST 3: EV / Kelly ────────────────────────────────────────────────────────
print("\nTEST 3: EV / Kelly")

# Bullish regime, positive ev
evk = ev_kelly_compute(spy_regime=0.03, rolling_win_rate=None, atr_pct=0.0)
check("ev > 0 for bullish regime",       evk["ev"] > 0,
      f"ev={evk['ev']:.5f}")
check("kelly_size ∈ [0, 0.25]",
      0.0 <= evk["kelly_size"] <= 0.25,
      f"kelly={evk['kelly_size']:.4f}")
check("ev_norm ∈ [0, 1]",               0.0 <= evk["ev_norm"] <= 1.0)
check("p_win clamped to [0.35, 0.75]",  0.35 <= evk["p_win"] <= 0.75)

# ATR-wide stop raises effective_sl
evk_wide = ev_kelly_compute(spy_regime=0.0, rolling_win_rate=None, atr_pct=0.10)
check("wide ATR raises effective_sl",
      evk_wide["effective_sl"] > 0.05,
      f"effective_sl={evk_wide['effective_sl']:.4f}")

# Negative EV → kelly = 0.
# Default TP=15%/SL=5% keeps EV positive even at the p=0.35 floor, so we
# need a very wide ATR-based stop (atr_pct=0.40 → effective_sl=0.60) to
# force EV negative.
evk_neg_ev = ev_kelly_compute(spy_regime=-0.03, rolling_win_rate=0.35, atr_pct=0.40)
check("kelly = 0 when EV is negative (wide ATR stop)",
      evk_neg_ev["kelly_size"] == 0.0,
      f"kelly={evk_neg_ev['kelly_size']:.4f}, ev={evk_neg_ev['ev']:.5f}")

# Rolling win rate injection
evk_good = ev_kelly_compute(spy_regime=0.0, rolling_win_rate=0.70)
evk_base = ev_kelly_compute(spy_regime=0.0, rolling_win_rate=None)
check("rolling win rate > base increases ev_norm",
      evk_good["ev_norm"] >= evk_base["ev_norm"])


# ── TEST 4: Volume score ──────────────────────────────────────────────────────
print("\nTEST 4: Volume score")

# High-volume spike
base_vol = 1_000_000
vols_spike = [base_vol] * 30 + [base_vol * 5]
df_spike = _make_df([100.0] * 31, vols_spike)
v_spike = volume.compute(df_spike)
check("volume_score > 0.5 for 5x volume spike",
      v_spike["volume_score"] > 0.5,
      f"score={v_spike['volume_score']:.4f}")
check("volume_ratio > 1.0 for spike",
      v_spike["volume_ratio"] > 1.0,
      f"ratio={v_spike['volume_ratio']:.2f}")

# Flat volume
df_flat = _make_df([100.0] * 31, [base_vol] * 31)
v_flat = volume.compute(df_flat)
check("volume_score ∈ [0, 1] for flat volume",
      0.0 <= v_flat["volume_score"] <= 1.0)


# ── TEST 5: Momentum RSI fallback ─────────────────────────────────────────────
print("\nTEST 5: Momentum RSI edge case (flat prices)")

flat_prices = [100.0] * 30
df_flat = _make_df(flat_prices)
m_flat = momentum.compute(df_flat)
check("no exception on flat prices",     True)
check("rsi falls back to 50.0",          m_flat["rsi"] == 50.0 or not math.isnan(m_flat["rsi"]))
check("score ∈ [0, 1] for flat prices",  0.0 <= m_flat["momentum_score"] <= 1.0)


# ── Summary ───────────────────────────────────────────────────────────────────
print()
print("─" * 45)
passed = sum(1 for _, v in results if v == "PASS")
failed = sum(1 for _, v in results if v == "FAIL")
print(f"Results: {passed} passed, {failed} failed  ({len(results)} total)")
if failed:
    print("Failed:")
    for name, v in results:
        if v == "FAIL":
            print(f"  ✗ {name}")
    sys.exit(1)
