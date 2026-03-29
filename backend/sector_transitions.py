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
from collections import defaultdict
import time

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

    leader_conditioned_count = sum(conditioned_matrix.get(leader, {}).values())
    if leader_conditioned_count >= MIN_REGIME_TRANSITIONS:
        active_matrix       = conditioned_matrix
        regime_conditioned  = True
    else:
        active_matrix       = unconditional_matrix
        regime_conditioned  = False

    leader_trans = active_matrix.get(leader, {})
    likely_next  = [
        {"sector": s, "probability": p}
        for s, p in list(leader_trans.items())[:3]
    ]

    # confirmed_transition always uses unconditional matrix for stability
    predecessor          = _find_predecessor(leader)
    confirmed_transition = None
    if predecessor and predecessor in unconditional_matrix:
        prob = unconditional_matrix[predecessor].get(leader, 0)
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
        "regime_conditioned":   regime_conditioned,   # True = using regime-specific matrix
        "regime_sample_size":   regime_sample_size,   # transitions observed in this regime
    }


def compute_ticker_rotation_scores() -> dict[str, float]:
    """
    Composite rotation score per ticker (0–1).

    Components (weighted sum):
      transition_prob  (50%) — P(ticker's sector is the next rotation target),
                               from the regime-conditioned matrix; 0 if not in likely_next
      velocity_score   (30%) — ticker's own 5d velocity normalised to [0, 1]:
                               velocity_5d = +0.10 → 1.0, flat → 0.5, -0.10 → 0.0
      regime_alignment (20%) — cyclical sector in risk_on OR defensive in risk_off → 1.0;
                               misaligned → 0.1; neutral regime or unknown sector → 0.5

    Returns {ticker: rotation_score} or {} if data unavailable.
    The result is intentionally not cached — callers should compute once per gate cycle
    and pass it down to avoid redundant calls.
    """
    from backend.sector_regime import compute_sector_regime, compute_ticker_signals, CYCLICAL, DEFENSIVE

    regime_data    = compute_sector_regime()
    if not regime_data.get("available"):
        return {}

    ticker_signals = compute_ticker_signals()
    if not ticker_signals:
        return {}

    forecast       = get_rotation_forecast()
    next_probs     = {item["sector"]: item["probability"]
                      for item in forecast.get("likely_next", [])}
    current_regime = regime_data.get("regime", "neutral")

    result: dict[str, float] = {}
    for ticker, sig in ticker_signals.items():
        sector = sig.get("sector", "")

        # 1. Transition probability (0 when sector not forecast as likely next)
        transition_prob = next_probs.get(sector, 0.0)

        # 2. Velocity score — normalise velocity_5d from [-0.10, +0.10] → [0, 1]
        v5d            = sig.get("velocity_5d", 0.0)
        velocity_score = max(0.0, min(1.0,
            (v5d + _VELOCITY_NORM_CENTER) / _VELOCITY_NORM_WIDTH
        ))

        # 3. Regime alignment
        if current_regime == "risk_on":
            regime_alignment = 1.0 if sector in CYCLICAL  else 0.1
        elif current_regime == "risk_off":
            regime_alignment = 1.0 if sector in DEFENSIVE else 0.1
        else:
            regime_alignment = 0.5

        result[ticker] = round(
            0.50 * transition_prob +
            0.30 * velocity_score  +
            0.20 * regime_alignment,
            4,
        )

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
                "velocity":    stats.get("velocity"),    # accelerating/decelerating/flat
                "velocity_5d": stats.get("velocity_5d"), # raw 5d score delta
            })

    return watching
