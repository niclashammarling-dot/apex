"""
Sector regime analysis — derives three actionable signals from sector history:

  1. Trend duration   — how many consecutive trading days each sector has been
                        above/below the sector threshold (0.50)
  2. Rotation signal  — breakout / trending / extended / breakdown / weak
  3. Regime           — risk_on / risk_off / neutral based on cyclical vs
                        defensive sector leadership

Results are used by:
  - dynamic sector caps (duration weighting)
  - Lock 3 context (LLM gets regime + sector signal)
  - Dashboard SectorRegime panel
"""
from __future__ import annotations

from collections import defaultdict

# Sector classification for regime detection
CYCLICAL  = {"Technology", "Financials", "Industrials", "ConsumerDisc",
             "Energy", "Materials", "Communication"}
DEFENSIVE = {"Utilities", "Healthcare", "ConsumerStaples", "RealEstate"}

# Sector-level threshold — lower than individual ticker L1 (0.55) because
# a sector score is an average across tickers, most of which won't all peak together
SECTOR_THRESHOLD = 0.50

# Streak thresholds (trading days) — all derived from 26 years of sector_snapshots data
#
#   CONFIRMED_MIN = 15  → p90 of above-threshold streaks (only 10% of runs reach this)
#   EXTENDED_MIN  = 21  → p95 of above-threshold streaks (top 5%, rotation statistically imminent)
#   BREAKOUT_MIN_PRIOR_WEAK = 17 → p85 of below-threshold streaks (genuine extended weakness)
#   BREAKDOWN_MIN_PRIOR     = 15 → equals CONFIRMED_MIN (must have been trending before breakdown means anything)
#
BREAKOUT_MIN_PRIOR_WEAK  = 17   # p85 of below-threshold streaks
CONFIRMED_MIN            = 15   # p90 of above-threshold streaks
EXTENDED_MIN             = 21   # p95 of above-threshold streaks — rotation imminent
BREAKDOWN_MIN_PRIOR      = 15   # = CONFIRMED_MIN

# Individual ticker threshold — default fallback until calibration runs
# Per-sector thresholds are loaded from ticker_thresholds table (populated by
# ticker_threshold_calibration.py after backfill completes).
TICKER_THRESHOLD_DEFAULT = 0.55

# Velocity — minimum 5d score delta to be considered meaningful movement
# avg_score is 0–1; a 0.015 delta over 5 trading days is ~1.5 pp/day, non-trivial
VELOCITY_THRESHOLD = 0.015

# Regime confidence — spread at which the spread component saturates to 1.0
# Historically cyclical/defensive spreads rarely exceed 0.25 in normal markets
MAX_SPREAD = 0.25

def _get_ticker_thresholds() -> dict[str, float]:
    """Load per-sector thresholds, falling back to default for missing sectors."""
    try:
        from backend.db import get_ticker_thresholds
        return get_ticker_thresholds()
    except Exception:
        return {}


def compute_sector_regime() -> dict:
    """
    Full regime snapshot.  Returns:
      available       — False if no history data yet
      regime          — "risk_on" | "risk_off" | "neutral"
      cyclical_avg    — avg score of cyclical sectors
      defensive_avg   — avg score of defensive sectors
      spread          — cyclical_avg - defensive_avg
      leader          — name of currently highest-scoring sector
      leader_streak   — trading days leader has been above threshold
      breakouts       — sectors that just crossed upward (most actionable)
      breakdowns      — sectors that just crossed downward
      extended        — sectors in a late-stage long uptrend
      sectors         — per-sector detail dict
    """
    try:
        from backend.db import get_sector_history
        raw = get_sector_history(days=365)   # daily aggregates for streak computation
    except Exception:
        return {"available": False}

    if not raw:
        return {"available": False}

    # Normalise to {sector, day, avg_score}
    history = [
        {"sector": r["sector"], "day": r["timestamp"][:10], "avg_score": r["avg_score"]}
        for r in raw
    ]

    sector_stats = _compute_sector_stats(history)
    regime_info  = _compute_regime(sector_stats)

    breakouts  = [s for s, v in sector_stats.items() if v["signal"] == "breakout"]
    breakdowns = [s for s, v in sector_stats.items() if v["signal"] == "breakdown"]
    extended   = [s for s, v in sector_stats.items() if v["signal"] == "extended"]

    leader = max(sector_stats, key=lambda s: sector_stats[s]["score"]) if sector_stats else None

    return {
        "available":         True,
        "regime":            regime_info["regime"],
        "regime_confidence": regime_info["regime_confidence"],
        "cyclical_avg":      regime_info["cyclical_avg"],
        "defensive_avg":     regime_info["defensive_avg"],
        "spread":            regime_info["spread"],
        "leader":            leader,
        "leader_streak":     sector_stats[leader]["streak_days"] if leader else 0,
        "breakouts":         breakouts,
        "breakdowns":        breakdowns,
        "extended":          extended,
        "sectors":           sector_stats,
        "threshold":         SECTOR_THRESHOLD,
    }


def compute_ticker_signals() -> dict[str, dict]:
    """
    Per-ticker streak-based signal classification using the same logic as sectors.
    Uses daily averages from the signals table (last 90 days).
    Returns {ticker: {score, signal, streak_days}} or {} if no data.
    """
    try:
        from backend.db import get_ticker_daily_scores
        rows = get_ticker_daily_scores(days=90)
    except Exception:
        return {}

    if not rows:
        return {}

    by_ticker: dict[str, list] = defaultdict(list)
    for row in rows:
        by_ticker[row["ticker"]].append(row)

    per_sector_thresholds = _get_ticker_thresholds()

    # Build ticker→sector map from entries
    ticker_sector: dict[str, str] = {}
    for ticker, entries in by_ticker.items():
        if entries:
            ticker_sector[ticker] = entries[0].get("sector", "")

    result = {}
    for ticker, entries in by_ticker.items():
        entries_sorted = sorted(entries, key=lambda r: r["day"])
        if not entries_sorted:
            continue

        sector    = ticker_sector.get(ticker, "")
        threshold = per_sector_thresholds.get(sector, TICKER_THRESHOLD_DEFAULT)

        current_score = entries_sorted[-1]["avg_score"]
        above_now     = current_score >= threshold

        streak = 0
        for row in reversed(entries_sorted):
            if (row["avg_score"] >= threshold) == above_now:
                streak += 1
            else:
                break

        prior_rows = entries_sorted[: len(entries_sorted) - streak]
        prior_streak = 0
        if prior_rows:
            prior_above = prior_rows[-1]["avg_score"] >= threshold
            for row in reversed(prior_rows):
                if (row["avg_score"] >= threshold) == prior_above:
                    prior_streak += 1
                else:
                    break

        if above_now:
            if streak <= 5 and prior_streak >= BREAKOUT_MIN_PRIOR_WEAK:
                signal = "breakout"
            elif streak >= EXTENDED_MIN:
                signal = "extended"
            elif streak >= CONFIRMED_MIN:
                signal = "trending"
            else:
                signal = "rising"
        else:
            if streak <= 5 and prior_streak >= BREAKDOWN_MIN_PRIOR:
                signal = "breakdown"
            elif _is_recovering(entries_sorted):
                signal = "recovering"
            else:
                signal = "weak"

        vel = _compute_velocity([r["avg_score"] for r in entries_sorted])

        result[ticker] = {
            "score":        round(current_score, 4),
            "signal":       signal,
            "streak_days":  streak,
            "sector":       sector,
            "velocity":     vel["velocity"],
            "velocity_5d":  vel["velocity_5d"],
            "velocity_20d": vel["velocity_20d"],
            "accel":        vel["accel"],
        }

    return result


# ── Internal helpers ──────────────────────────────────────────────────────────

def _is_recovering(entries_sorted: list) -> bool:
    """
    Returns True if a below-threshold ticker shows a sustained upward slope:
    average of the last 3 days > average of the 3 days before that by ≥0.02.
    Requires at least 6 data points to avoid noise.
    """
    if len(entries_sorted) < 6:
        return False
    recent = sum(r["avg_score"] for r in entries_sorted[-3:]) / 3
    prior  = sum(r["avg_score"] for r in entries_sorted[-6:-3]) / 3
    return recent - prior >= 0.02

def _compute_velocity(scores: list[float]) -> dict:
    """
    Compute momentum metrics from a list of daily avg_scores (oldest → newest).

    velocity_5d  — score delta over the last 5 trading days
    velocity_20d — score delta over the last 20 trading days
    accel        — change in 5d velocity (velocity_5d minus the previous 5d window)
                   positive = momentum building, negative = momentum fading
    velocity     — "accelerating" | "decelerating" | "flat"

    Safe when fewer than 21 rows are available — clamps lookback to available history.
    """
    n   = len(scores)
    cur = scores[-1]

    score_5d_ago  = scores[max(0, n - 6)]   # 5 trading days back
    score_10d_ago = scores[max(0, n - 11)]  # 10 trading days back (prior 5d window start)
    score_20d_ago = scores[max(0, n - 21)]  # 20 trading days back

    velocity_5d       = round(cur - score_5d_ago, 4)
    velocity_20d      = round(cur - score_20d_ago, 4)
    velocity_5d_prior = round(score_5d_ago - score_10d_ago, 4)  # 5d slope 5d ago
    accel             = round(velocity_5d - velocity_5d_prior, 4)

    if velocity_5d >= VELOCITY_THRESHOLD:
        vel_signal = "accelerating"
    elif velocity_5d <= -VELOCITY_THRESHOLD:
        vel_signal = "decelerating"
    else:
        vel_signal = "flat"

    return {
        "velocity_5d":  velocity_5d,
        "velocity_20d": velocity_20d,
        "accel":        accel,
        "velocity":     vel_signal,
    }


def _compute_sector_stats(history: list[dict]) -> dict[str, dict]:
    """
    For each sector: current score, consecutive-day streak in current state,
    and a rotation signal label.
    """
    by_sector: dict[str, list] = defaultdict(list)
    for row in history:
        by_sector[row["sector"]].append(row)

    result = {}
    for sector, rows in by_sector.items():
        rows_sorted = sorted(rows, key=lambda r: r["day"])
        if not rows_sorted:
            continue

        current_score = rows_sorted[-1]["avg_score"]
        above_now     = current_score >= SECTOR_THRESHOLD

        # Count consecutive days in current state (walking backwards)
        streak = 0
        for row in reversed(rows_sorted):
            if (row["avg_score"] >= SECTOR_THRESHOLD) == above_now:
                streak += 1
            else:
                break

        # Count prior streak (the opposite state before current streak)
        prior_rows = rows_sorted[: len(rows_sorted) - streak]
        prior_streak = 0
        if prior_rows:
            prior_above = prior_rows[-1]["avg_score"] >= SECTOR_THRESHOLD
            for row in reversed(prior_rows):
                if (row["avg_score"] >= SECTOR_THRESHOLD) == prior_above:
                    prior_streak += 1
                else:
                    break

        # Classify signal
        if above_now:
            if streak <= 5 and prior_streak >= BREAKOUT_MIN_PRIOR_WEAK:
                signal = "breakout"    # freshly crossed up after extended weakness
            elif streak >= EXTENDED_MIN:
                signal = "extended"    # long uptrend — late stage, watch for rotation
            elif streak >= CONFIRMED_MIN:
                signal = "trending"    # confirmed multi-week uptrend
            else:
                signal = "rising"      # early, not yet confirmed
        else:
            if streak <= 5 and prior_streak >= BREAKDOWN_MIN_PRIOR:
                signal = "breakdown"   # freshly crossed down after extended strength
            else:
                signal = "weak"

        vel = _compute_velocity([r["avg_score"] for r in rows_sorted])

        result[sector] = {
            "score":        round(current_score, 4),
            "above":        above_now,
            "streak_days":  streak,
            "signal":       signal,
            "velocity":     vel["velocity"],
            "velocity_5d":  vel["velocity_5d"],
            "velocity_20d": vel["velocity_20d"],
            "accel":        vel["accel"],
        }

    return result


def _compute_regime(sector_stats: dict) -> dict:
    """
    Regime classification plus a continuous confidence score (0–1).

    confidence is built from three components:
      spread_score        (50%) — how large is the cyclical/defensive spread?
      participation_score (30%) — what fraction of the leading camp is above threshold?
      duration_score      (20%) — how long has the top sector in that camp been confirmed?

    Neutral regime has confidence = 0.0 by definition (no directional conviction).
    """
    cyclical_scores  = [v["score"] for k, v in sector_stats.items() if k in CYCLICAL]
    defensive_scores = [v["score"] for k, v in sector_stats.items() if k in DEFENSIVE]

    cyclical_avg  = round(sum(cyclical_scores)  / len(cyclical_scores),  4) if cyclical_scores  else 0.0
    defensive_avg = round(sum(defensive_scores) / len(defensive_scores), 4) if defensive_scores else 0.0
    spread        = round(cyclical_avg - defensive_avg, 4)

    if spread > 0.05:
        regime = "risk_on"
    elif spread < -0.05:
        regime = "risk_off"
    else:
        regime = "neutral"

    # ── confidence components ──────────────────────────────────────────────
    if regime == "neutral":
        confidence = 0.0
    else:
        leading_camp = CYCLICAL if regime == "risk_on" else DEFENSIVE

        # 1. Spread magnitude
        spread_score = min(abs(spread) / MAX_SPREAD, 1.0)

        # 2. Sector participation — fraction of leading-camp sectors above threshold
        above_in_camp = sum(1 for k, v in sector_stats.items()
                            if k in leading_camp and v.get("above", False))
        participation_score = above_in_camp / len(leading_camp) if leading_camp else 0.0

        # 3. Duration — max streak among above-threshold sectors in the leading camp,
        #    normalised to CONFIRMED_MIN (full confidence once a sector is confirmed)
        streaks = [v["streak_days"] for k, v in sector_stats.items()
                   if k in leading_camp and v.get("above", False)]
        max_streak     = max(streaks) if streaks else 0
        duration_score = min(max_streak / CONFIRMED_MIN, 1.0)

        confidence = round(
            0.50 * spread_score +
            0.30 * participation_score +
            0.20 * duration_score,
            4,
        )

    return {
        "regime":            regime,
        "regime_confidence": confidence,
        "cyclical_avg":      cyclical_avg,
        "defensive_avg":     defensive_avg,
        "spread":            spread,
    }
