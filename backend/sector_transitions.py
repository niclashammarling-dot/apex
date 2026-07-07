"""
Sector rotation transition analysis.

Computes historical transition probabilities from sector_snapshots:
  when sector A exits the top-2, what sector most often enters?

Two matrices are maintained:
  - Unconditional  — all history, used as a fallback when regime data is sparse
  - Regime-tagged  — split by market regime (risk_on / risk_off / neutral) at
                     the time of each transition; used when ≥ MIN_REGIME_TRANSITIONS
                     exist for the current regime

Results are cached for 1 hour (recalculated as more data accumulates).
"""
from __future__ import annotations

import time
from collections import defaultdict

_cache: dict = {}
_cache_ts: float = 0
_regime_matrix_cache: dict = {}
_regime_matrix_cache_ts: float = 0
_CACHE_TTL = 3600  # 1 hour

# Minimum number of observed transitions in a regime bucket before we trust
# the conditioned matrix over the unconditional fallback
MIN_REGIME_TRANSITIONS = 5

# Rotation score normalisation — velocity_5d of +0.10 maps to velocity_score = 1.0;
# -0.10 maps to 0.0; flat (0.0) maps to 0.5
_VELOCITY_NORM_CENTER = 0.10
_VELOCITY_NORM_WIDTH  = 0.20

# Blending weights for sector probability = historical prior + live momentum evidence.
# Expand matrix candidate pool to top 5 before blending so momentum can re-rank them.
_HISTORY_WEIGHT        = 0.60
_MOMENTUM_WEIGHT       = 0.40
_MATRIX_CANDIDATE_POOL = 5   # sectors pulled from matrix before momentum re-rank


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


def get_conditioned_transition_matrix(regime: str) -> tuple[dict[str, dict[str, float]], int]:
    """
    Returns (matrix, sample_size) for the given regime ("risk_on" | "risk_off" | "neutral").
    matrix      — {sector: {successor: probability}}, sorted by probability descending
    sample_size — total number of transitions observed in this regime bucket

    Falls back to an empty dict when the regime bucket has no data; caller is
    responsible for checking MIN_REGIME_TRANSITIONS and falling back to the
    unconditional matrix if needed.
    """
    global _regime_matrix_cache, _regime_matrix_cache_ts
    now = time.time()
    if now - _regime_matrix_cache_ts >= _CACHE_TTL or not _regime_matrix_cache:
        _regime_matrix_cache    = _compute_regime_tagged_matrix()
        _regime_matrix_cache_ts = now

    raw_bucket = _regime_matrix_cache.get(regime, {})

    result: dict[str, dict[str, float]] = {}
    total_transitions = 0
    for sector, successors in raw_bucket.items():
        bucket_total = sum(successors.values())
        total_transitions += bucket_total
        if bucket_total > 0:
            result[sector] = {
                s: round(n / bucket_total, 3)
                for s, n in sorted(successors.items(), key=lambda x: x[1], reverse=True)
            }

    return result, total_transitions


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

    current_regime = regime.get("regime", "neutral")

    # Try the regime-conditioned matrix first; fall back to unconditional if sparse
    conditioned_matrix, regime_sample_size = get_conditioned_transition_matrix(current_regime)
    unconditional_matrix                   = get_transition_matrix()

    if regime_sample_size >= MIN_REGIME_TRANSITIONS:
        active_matrix       = conditioned_matrix
        regime_conditioned  = True
    else:
        active_matrix       = unconditional_matrix
        regime_conditioned  = False

    leader_trans    = active_matrix.get(leader, {})
    sector_momentum = compute_sector_rotation_scores()
    sector_breadth  = compute_sector_breadth()   # genuine second data source: not derivable from avg_score
    sector_stats    = regime.get("sectors", {})

    # Blend historical prior + live sector momentum, then re-rank top 3.
    # Pull top-5 from the matrix so a momentum surge can promote a #4/#5 historical
    # candidate above a fading #1/#2.  Default momentum to 0.5 when no ticker data.
    blended: list[dict] = []
    for s, hist_p in list(leader_trans.items())[:_MATRIX_CANDIDATE_POOL]:
        momentum = sector_momentum.get(s, 0.5)
        stats    = sector_stats.get(s, {})
        blended.append({
            "sector":          s,
            "probability":     round(_HISTORY_WEIGHT * hist_p + _MOMENTUM_WEIGHT * momentum, 3),
            "historical_prob": hist_p,
            "sector_score":    stats.get("score"),
            "sector_signal":   stats.get("signal"),
            "breadth":         sector_breadth.get(s),
        })

    # If the matrix has fewer than 3 candidates for this leader (sparse history),
    # fill remaining slots with the highest-momentum sectors not already listed.
    if len(blended) < 3:
        already  = {b["sector"] for b in blended} | {leader}
        momentum_fill = sorted(
            [(s, m) for s, m in sector_momentum.items() if s not in already],
            key=lambda x: x[1], reverse=True,
        )
        for s, momentum in momentum_fill:
            if len(blended) >= 3:
                break
            stats = sector_stats.get(s, {})
            blended.append({
                "sector":          s,
                "probability":     round(_MOMENTUM_WEIGHT * momentum, 3),
                "historical_prob": None,   # no matrix data — momentum-only slot
                "sector_score":    stats.get("score"),
                "sector_signal":   stats.get("signal"),
                "breadth":         sector_breadth.get(s),
            })

    blended.sort(key=lambda x: x["probability"], reverse=True)
    likely_next = blended[:3]

    # confirmed_transition uses the most recent predecessor against the unconditional matrix
    predecessors         = _find_predecessor_chain(leader, n=2)
    recent_predecessor   = predecessors[-1] if predecessors else None
    confirmed_transition = None
    if recent_predecessor and recent_predecessor in unconditional_matrix:
        prob = unconditional_matrix[recent_predecessor].get(leader, 0)
        if prob > 0:
            confirmed_transition = {
                "from":        recent_predecessor,
                "to":          leader,
                "probability": prob,
            }

    watching = _watching_sectors(likely_next, regime)

    return {
        "available":            True,
        "leader":               leader,
        "leader_streak_days":   regime.get("leader_streak", 0),
        "leader_breadth":       sector_breadth.get(leader),   # breadth of current leader — broad vs. one-ticker strength
        "predecessors":         predecessors,         # chronological list, oldest first
        "confirmed_transition": confirmed_transition,
        "likely_next":          likely_next,
        "watching":             watching,
        "regime_conditioned":   regime_conditioned,
        "regime_sample_size":   regime_sample_size,
    }


def compute_ticker_rotation_scores() -> dict[str, float]:
    """
    Per-ticker rotation score (0–1) based purely on ticker-level signals.
    Ticker scores are the bottom-up input that get aggregated into sector
    momentum scores, which then blend with the historical transition matrix
    to produce the final sector probability in the forecast.

    Components (weighted sum):
      velocity_score   (65%) — ticker's own 5d velocity normalised to [0, 1]:
                               velocity_5d = +0.10 → 1.0, flat → 0.5, -0.10 → 0.0
      regime_alignment (35%) — cyclical sector in risk_on OR defensive in risk_off → 1.0;
                               misaligned → 0.1; neutral regime or unknown sector → 0.5

    Returns {ticker: rotation_score} or {} if data unavailable.
    Not cached — callers should compute once per gate cycle.
    """
    from backend.sector_regime import (
        CYCLICAL,
        DEFENSIVE,
        compute_sector_regime,
        compute_ticker_signals,
    )

    regime_data    = compute_sector_regime()
    if not regime_data.get("available"):
        return {}

    ticker_signals = compute_ticker_signals()
    if not ticker_signals:
        return {}

    current_regime = regime_data.get("regime", "neutral")

    result: dict[str, float] = {}
    for ticker, sig in ticker_signals.items():
        sector = sig.get("sector", "")

        # 1. Velocity score — normalise velocity_5d from [-0.10, +0.10] → [0, 1]
        v5d            = sig.get("velocity_5d", 0.0)
        velocity_score = max(0.0, min(1.0,
            (v5d + _VELOCITY_NORM_CENTER) / _VELOCITY_NORM_WIDTH
        ))

        # 2. Regime alignment
        if current_regime == "risk_on":
            regime_alignment = 1.0 if sector in CYCLICAL  else 0.1
        elif current_regime == "risk_off":
            regime_alignment = 1.0 if sector in DEFENSIVE else 0.1
        else:
            regime_alignment = 0.5

        result[ticker] = round(
            0.65 * velocity_score  +
            0.35 * regime_alignment,
            4,
        )

    return result


def compute_sector_breadth() -> dict[str, float]:
    """
    Fraction of tickers per sector that are above the strength threshold.
    Above-threshold signals: rising, trending, extended, breakout, recovering.

    This is a second data source that sector_regime cannot derive from avg_score
    alone: two sectors can have identical avg_score while one has broad participation
    and the other is carried by a single mega-cap. The base layer sees the same
    number; only breadth tells them apart.

    Returns {sector: breadth} where breadth is 0.0–1.0.
    """
    from backend.sector_regime import compute_ticker_signals
    ticker_signals = compute_ticker_signals()
    if not ticker_signals:
        return {}

    ABOVE = {"rising", "trending", "extended", "breakout", "recovering"}
    by_sector: dict[str, list[bool]] = {}
    for sig in ticker_signals.values():
        sector = sig.get("sector", "")
        if sector:
            by_sector.setdefault(sector, []).append(sig.get("signal", "weak") in ABOVE)

    return {
        sector: round(sum(flags) / len(flags), 4)
        for sector, flags in by_sector.items()
        if flags
    }


def compute_sector_rotation_scores() -> dict[str, float]:
    """
    Aggregate per-ticker rotation scores bottom-up to sector level.
    Uses the mean of ALL tickers in the sector — sectors are judged as a unit,
    consistent with leaderboard allocation. Full-sector averaging also defends
    against single-ticker boosts or short squeezes distorting the sector signal.

    This is the momentum evidence that blends with the historical transition
    matrix inside get_rotation_forecast() to produce the final sector probability.

    Returns {sector: score} where score is 0–1, or {} if data unavailable.
    """
    from backend.sector_regime import compute_ticker_signals

    ticker_scores = compute_ticker_rotation_scores()
    if not ticker_scores:
        return {}

    ticker_signals = compute_ticker_signals()

    by_sector: dict[str, list[float]] = {}
    for ticker, score in ticker_scores.items():
        sector = ticker_signals.get(ticker, {}).get("sector", "")
        if sector:
            by_sector.setdefault(sector, []).append(score)

    result: dict[str, float] = {}
    for sector, scores in by_sector.items():
        result[sector] = round(sum(scores) / len(scores), 4)
    return result


# ── Internal helpers ───────────────────────────────────────────────────────────

def _compute_regime_tagged_matrix() -> dict[str, dict[str, dict[str, int]]]:
    """
    Build three raw transition-count dicts, one per regime, by tagging each monthly
    transition with the regime that was active at the time of the transition.

    Returns {regime_key: {sector: {successor: count}}} where regime_key is one of
    "risk_on", "risk_off", "neutral".
    """
    from backend.sector_regime import CYCLICAL, DEFENSIVE

    try:
        from backend.db import get_sector_history
        raw = get_sector_history(days=0)   # all available history
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

    def _month_regime(scores: dict) -> str:
        cyc_scores  = [v for k, v in scores.items() if k in CYCLICAL]
        def_scores  = [v for k, v in scores.items() if k in DEFENSIVE]
        if not cyc_scores or not def_scores:
            return "neutral"
        spread = sum(cyc_scores) / len(cyc_scores) - sum(def_scores) / len(def_scores)
        if spread > 0.05:
            return "risk_on"
        elif spread < -0.05:
            return "risk_off"
        return "neutral"

    month_regime = {m: _month_regime(monthly[m]) for m in months}

    def _rank(scores: dict) -> dict[str, int]:
        order = sorted(scores, key=lambda s: scores[s], reverse=True)
        return {s: i + 1 for i, s in enumerate(order)}

    ranked = {m: _rank(monthly[m]) for m in months}

    matrices: dict[str, dict] = {
        "risk_on":  defaultdict(lambda: defaultdict(int)),
        "risk_off": defaultdict(lambda: defaultdict(int)),
        "neutral":  defaultdict(lambda: defaultdict(int)),
    }

    prev_top2: set[str] = set()
    for m in months:
        top2        = {s for s, r in ranked[m].items() if r <= 2}
        fallen      = prev_top2 - top2
        risen       = top2 - prev_top2
        regime_key  = month_regime[m]
        for f in fallen:
            for r in risen:
                matrices[regime_key][f][r] += 1
        prev_top2 = top2

    # Convert nested defaultdicts to plain dicts for JSON-safety
    return {k: {s: dict(v) for s, v in m.items()} for k, m in matrices.items()}


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


def _find_predecessor_chain(current_leader: str, n: int = 2) -> list[str]:
    """
    Returns up to n predecessor sectors in chronological order (oldest first),
    e.g. ["Energy", "Financials"] means Energy led, then Financials, then
    current_leader.

    Tracks rank-1 (leader) changes rather than top-2 membership.  This avoids
    a common false positive: a sector that briefly hits rank-1 for a single month
    while the real predecessor is still fading out of the top-2.

    A sector must have held rank-1 for at least MIN_PREDECESSOR_MONTHS consecutive
    months to qualify as a predecessor.  Shorter blips are skipped entirely so the
    chain reflects meaningful leadership periods, not noise.
    """
    # Minimum consecutive months at rank-1 required to count as a true predecessor.
    # 1 = any sector that held rank-1 for at least 1 month qualifies.
    # Keeping this at 1 ensures we show the most recent actual leaders rather than
    # reaching back to stale history when recent tenures are short (e.g. volatile markets).
    MIN_PREDECESSOR_MONTHS = 1

    try:
        from backend.db import get_sector_history
        raw = get_sector_history(days=0)   # full history, not capped at 180d
        if not raw:
            return []

        monthly: dict[str, dict[str, float]] = defaultdict(dict)
        for r in raw:
            month = r["timestamp"][:7]
            monthly[month][r["sector"]] = r["avg_score"]

        months = sorted(monthly.keys())
        if len(months) < 2:
            return []

        def _rank(scores):
            order = sorted(scores, key=lambda s: scores[s], reverse=True)
            return {s: i + 1 for i, s in enumerate(order)}

        ranked  = {m: _rank(monthly[m]) for m in months}
        # Rank-1 leader for each month — the single sector with the highest score
        leaders = [min(ranked[m], key=ranked[m].get) for m in months]

        # Walk backwards from newest to find where current_leader became rank-1
        i = len(months) - 1
        while i >= 0 and leaders[i] == current_leader:
            i -= 1
        # i is the last month current_leader was NOT rank-1 (-1 = always rank-1)

        if i < 0:
            return []   # leader has held rank-1 for the entire history window

        chain:      list[str] = []
        seen:       set[str]  = {current_leader}
        search_end: int       = i   # search months[0..search_end] for predecessors

        for _ in range(n):
            pred = None
            j    = search_end

            while j >= 0:
                candidate = leaders[j]

                if candidate in seen:
                    j -= 1
                    continue

                # Count consecutive months this candidate held rank-1 ending at month j
                k = j
                while k >= 0 and leaders[k] == candidate:
                    k -= 1
                # candidate held rank-1 from months[k+1] to months[j]  (j-k months)
                tenure = j - k

                if tenure >= MIN_PREDECESSOR_MONTHS:
                    pred       = candidate
                    search_end = k   # look before this predecessor's tenure next iteration
                    break

                # This candidate had too short a tenure — skip it entirely and look further back
                j = k

            if pred is None:
                break

            chain.insert(0, pred)   # prepend → list stays chronological (oldest first)
            seen.add(pred)

        return chain

    except Exception:
        return []


def _watching_sectors(likely_next: list[dict], regime: dict) -> list[dict]:
    """
    Cross-reference likely_next with sectors currently showing early signals.
    These are the highest-conviction setups: historically likely + already moving.
    """
    sector_stats  = regime.get("sectors", {})
    early_signals = {"recovering", "rising", "breakout"}

    watching = []
    for item in likely_next:
        sector = item["sector"]
        stats  = sector_stats.get(sector, {})
        signal = stats.get("signal", "weak")
        if signal in early_signals:
            watching.append({
                "sector":        sector,
                "probability":   item["probability"],
                "historical_prob": item.get("historical_prob"),
                "signal":        signal,
                "score":         stats.get("score"),
                "streak_days":   stats.get("streak_days"),
                "velocity":      stats.get("velocity"),
                "velocity_5d":   stats.get("velocity_5d"),
            })

    return watching
