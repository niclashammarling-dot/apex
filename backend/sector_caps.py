"""
Dynamic sector exposure caps — score-based + duration-weighted.

cap_i = base_cap × score_weight × duration_weight

score_weight   = clamp(score_i / mean_score, 0.5, 1.5)
duration_weight:
  streak < 5d   → 0.85  (unconfirmed, hold back)
  5–19d         → 1.00  (developing)
  20–44d        → 1.15  (confirmed multi-week trend)
  45d+          → 1.00  (extended — score starts reflecting any fade naturally)

Together: a confirmed 3-week Energy uptrend gets meaningfully more cap than a
sector that just spiked yesterday, without any manual override.
"""
from loguru import logger

MIN_MULT = 0.5
MAX_MULT = 1.5


def _duration_weight(streak_days: int) -> float:
    if streak_days < 5:
        return 0.85
    if streak_days < 20:
        return 1.00
    if streak_days < 45:
        return 1.15
    return 1.00   # extended — naturally fades as score drops


def compute_dynamic_caps(base_cap: float) -> dict[str, float]:
    """
    Returns per-sector cap dict {sector: cap_fraction}.
    Falls back to {} (flat cap) if data unavailable.
    """
    try:
        from backend.db import get_latest_sector_scores
        from backend.sector_regime import compute_sector_regime
        scores = get_latest_sector_scores()
        regime = compute_sector_regime()
    except Exception as e:
        logger.warning(f"Dynamic sector caps: could not load data — {e}")
        return {}

    if not scores:
        return {}

    mean_score = sum(scores.values()) / len(scores)
    if mean_score <= 0:
        return {}

    sector_stats = regime.get("sectors", {}) if regime.get("available") else {}

    caps = {}
    for sector, score in scores.items():
        score_w    = max(MIN_MULT, min(MAX_MULT, score / mean_score))
        streak     = sector_stats.get(sector, {}).get("streak_days", 10)
        duration_w = _duration_weight(streak)
        raw_cap    = base_cap * score_w * duration_w
        caps[sector] = round(max(base_cap * MIN_MULT, min(base_cap * MAX_MULT, raw_cap)), 4)
        logger.debug(
            f"Dynamic cap [{sector}]: {caps[sector]:.3f} "
            f"(score={score:.3f} sw={score_w:.2f} streak={streak}d dw={duration_w:.2f})"
        )

    return caps
