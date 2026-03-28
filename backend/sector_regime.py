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

# Streak thresholds (trading days)
BREAKOUT_MIN_PRIOR_WEAK  = 20   # must have been weak ≥20 days before calling it a breakout
CONFIRMED_MIN            = 15   # above threshold ≥15 days → confirmed trend
EXTENDED_MIN             = 45   # above threshold ≥45 days → late stage, start watching
BREAKDOWN_MIN_PRIOR      = 15   # must have been strong ≥15 days before calling breakdown


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
        "available":      True,
        "regime":         regime_info["regime"],
        "cyclical_avg":   regime_info["cyclical_avg"],
        "defensive_avg":  regime_info["defensive_avg"],
        "spread":         regime_info["spread"],
        "leader":         leader,
        "leader_streak":  sector_stats[leader]["streak_days"] if leader else 0,
        "breakouts":      breakouts,
        "breakdowns":     breakdowns,
        "extended":       extended,
        "sectors":        sector_stats,
        "threshold":      SECTOR_THRESHOLD,
    }


# ── Internal helpers ──────────────────────────────────────────────────────────

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

        result[sector] = {
            "score":       round(current_score, 4),
            "above":       above_now,
            "streak_days": streak,
            "signal":      signal,
        }

    return result


def _compute_regime(sector_stats: dict) -> dict:
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

    return {"regime": regime, "cyclical_avg": cyclical_avg,
            "defensive_avg": defensive_avg, "spread": spread}
