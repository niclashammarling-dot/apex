"""
Sector rotation transition analysis.

Computes historical transition probabilities from sector_snapshots:
  when sector A exits the top-2, what sector most often enters?

Results are cached for 1 hour (recalculated as more data accumulates).
"""
from __future__ import annotations
from collections import defaultdict
import time

_cache: dict = {}
_cache_ts: float = 0
_CACHE_TTL = 3600  # 1 hour


# ── Public API ─────────────────────────────────────────────────────────────────

def get_transition_matrix() -> dict[str, dict[str, float]]:
    """
    Returns {sector: {successor: probability}} — cached for 1h.
    e.g. {"Energy": {"ConsumerStaples": 0.40, "Communication": 0.20, ...}}
    """
    global _cache, _cache_ts
    now = time.time()
    if now - _cache_ts < _CACHE_TTL and _cache:
        return _cache

    raw = _compute_raw_matrix()
    result = {}
    for sector, successors in raw.items():
        total = sum(successors.values())
        if total > 0:
            result[sector] = {
                s: round(n / total, 3)
                for s, n in sorted(successors.items(), key=lambda x: x[1], reverse=True)
            }

    _cache    = result
    _cache_ts = now
    return result


def get_rotation_forecast() -> dict:
    """
    Full rotation forecast snapshot:
      available            — False if insufficient data
      leader               — current leading sector
      leader_streak_days   — how long leader has held
      predecessor          — sector that led just before current leader
      confirmed_transition — {from, to, probability} if predecessor→leader matches matrix
      likely_next          — top-3 [{sector, probability}] successors when leader fades
      watching             — likely_next sectors already showing RECOVERING/RISING
    """
    from backend.sector_regime import compute_sector_regime

    regime = compute_sector_regime()
    if not regime.get("available"):
        return {"available": False}

    leader = regime.get("leader")
    if not leader:
        return {"available": False}

    matrix          = get_transition_matrix()
    leader_trans    = matrix.get(leader, {})
    likely_next     = [
        {"sector": s, "probability": p}
        for s, p in list(leader_trans.items())[:3]
    ]

    predecessor          = _find_predecessor(leader)
    confirmed_transition = None
    if predecessor and predecessor in matrix:
        prob = matrix[predecessor].get(leader, 0)
        if prob > 0:
            confirmed_transition = {
                "from":        predecessor,
                "to":          leader,
                "probability": prob,
            }

    watching = _watching_sectors(likely_next, regime)

    return {
        "available":            True,
        "leader":               leader,
        "leader_streak_days":   regime.get("leader_streak", 0),
        "predecessor":          predecessor,
        "confirmed_transition": confirmed_transition,
        "likely_next":          likely_next,
        "watching":             watching,
    }


# ── Internal helpers ───────────────────────────────────────────────────────────

def _compute_raw_matrix() -> dict[str, dict[str, int]]:
    """Raw transition counts from all available sector_snapshots history."""
    try:
        from backend.db import get_sector_history
        raw = get_sector_history(days=0)
    except Exception:
        return {}

    if not raw:
        return {}

    monthly: dict[str, dict[str, float]] = defaultdict(dict)
    for r in raw:
        month = r["timestamp"][:7]
        monthly[month][r["sector"]] = r["avg_score"]

    months = sorted(monthly.keys())
    if len(months) < 3:
        return {}

    def _rank(scores):
        order = sorted(scores, key=lambda s: scores[s], reverse=True)
        return {s: i + 1 for i, s in enumerate(order)}

    ranked    = {m: _rank(monthly[m]) for m in months}
    matrix: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    prev_top2: set[str] = set()

    for m in months:
        top2   = {s for s, r in ranked[m].items() if r <= 2}
        fallen = prev_top2 - top2
        risen  = top2 - prev_top2
        for f in fallen:
            for r in risen:
                matrix[f][r] += 1
        prev_top2 = top2

    return dict(matrix)


def _find_predecessor(current_leader: str) -> str | None:
    """
    Walk backwards through monthly rankings to find the sector that held top-2
    just before the current leader entered.
    """
    try:
        from backend.db import get_sector_history
        raw = get_sector_history(days=180)   # 6 months is plenty
        if not raw:
            return None

        monthly: dict[str, dict[str, float]] = defaultdict(dict)
        for r in raw:
            month = r["timestamp"][:7]
            monthly[month][r["sector"]] = r["avg_score"]

        months = sorted(monthly.keys())
        if len(months) < 2:
            return None

        def _rank(scores):
            order = sorted(scores, key=lambda s: scores[s], reverse=True)
            return {s: i + 1 for i, s in enumerate(order)}

        ranked = {m: _rank(monthly[m]) for m in months}

        # Find the earliest month in the lookback where leader entered top-2
        entry_idx = None
        for i, m in enumerate(months):
            if ranked[m].get(current_leader, 99) <= 2:
                entry_idx = i
                break

        if entry_idx is None or entry_idx == 0:
            return None

        prev_m    = months[entry_idx - 1]
        prev_top2 = {s for s, r in ranked[prev_m].items() if r <= 2}
        prev_top2.discard(current_leader)

        if not prev_top2:
            return None

        prev_scores = monthly[prev_m]
        return max(prev_top2, key=lambda s: prev_scores.get(s, 0))

    except Exception:
        return None


def _watching_sectors(likely_next: list[dict], regime: dict) -> list[dict]:
    """
    Cross-reference likely_next with sectors currently showing early signals.
    These are the highest-conviction setups: historically likely + already moving.
    """
    sector_stats  = regime.get("sectors", {})
    next_set      = {item["sector"] for item in likely_next}
    early_signals = {"recovering", "rising", "breakout"}

    watching = []
    for item in likely_next:
        sector = item["sector"]
        stats  = sector_stats.get(sector, {})
        signal = stats.get("signal", "weak")
        if signal in early_signals:
            watching.append({
                "sector":      sector,
                "probability": item["probability"],
                "signal":      signal,
                "score":       stats.get("score"),
                "streak_days": stats.get("streak_days"),
            })

    return watching
