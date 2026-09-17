"""
Per-ticker put/call-ratio baseline for Lock 4 — partial-pooled P25.

Replaces the pooled scalar PCR_THRESHOLD = 0.85 (decided 2026-09-17, vault:
raw/notes/2026-09/2026-09-17-apex-lock4-pcr-design-rate-and-regime-bucket-decision.md).
A pooled scalar is a ticker fixed effect, not a signal: ticker median PCR spans
0.07–2.97 across the universe, so `pc < 0.85` mostly answers "which ticker is
this" — 21 of 105 tickers can essentially never pass, OXY/EOG-class names
always do. The design rate (25%) is restated per ticker: the check passes when
today's ratio is more call-skewed than usual *for this name*.

Estimator — partial pooling, no n-cliff:

    threshold_t = w_t * P25_t + (1 - w_t) * P25_pooled,   w_t = n_t / (n_t + K)

P25_t is the ticker's own 25th percentile over lock4_pcr_history (dislocation
days excluded), P25_pooled the same over every row, n_t the ticker's row
count. A ticker with no history gets the pooled P25 (w = 0); one with the full
series gets ~93% of its own. An `n >= 20 → own, else 0.85` rule was rejected:
it treats n = 20 as fully trusted and n = 19 as worthless, and the per-ticker
P25 moves median 0.085 / P90 0.335 between halves of a 47%-present series, so
the estimates are the weak part and the estimator should say so.

K = 3 is empirical-Bayes from the series on 2026-09-17: between-ticker variance
of the own P25 is 0.129; the single-observation sampling variance of a P25
estimate, from the half-split differences (var(d) ≈ 2 σ²/n_half), is 0.067 by
median and 0.380 by mean, giving K = σ²/τ² of 0.5 and 2.9 (bootstrap: 0.3).
The mean-based estimate is taken deliberately — the instability is in the tail
(P90 0.335), and shrinkage exists for the tickers in that tail, not the median
one. K = 3 → w = 0.25 / 0.50 / 0.77 / 0.93 at n = 1 / 3 / 10 / 38.

Computed once per process per calendar day from the DB (in memory; no disk
cache, so no code-version key is needed). The table is the collector's
(backend/data/lock4_pcr_collector.py); CHECK 78 reads its completeness.
"""
from __future__ import annotations

from datetime import date

import numpy as np
from loguru import logger

PCR_DESIGN_PCT = 25     # per-ticker design pass rate, as a percentile
PCR_SHRINK_K   = 3.0    # pseudo-observations behind the pooled P25 (see module docstring)

_cache: dict = {"date": None, "pooled": None, "pooled_n": 0, "by_ticker": {}}


def _build() -> dict:
    from backend.db import get_pcr_history
    rows = get_pcr_history(exclude_dislocation=True)
    by_ticker: dict[str, list[float]] = {}
    for r in rows:
        by_ticker.setdefault(r["ticker"], []).append(float(r["pcr"]))
    if not rows:
        return {"date": date.today(), "pooled": None, "pooled_n": 0, "by_ticker": {}}
    pooled = float(np.percentile([r["pcr"] for r in rows], PCR_DESIGN_PCT))
    table  = {}
    for t, xs in by_ticker.items():
        n   = len(xs)
        own = float(np.percentile(xs, PCR_DESIGN_PCT))
        w   = n / (n + PCR_SHRINK_K)
        table[t] = {
            "threshold": round(w * own + (1 - w) * pooled, 4),
            "own_p25":   round(own, 4),
            "n_obs":     n,
            "shrink_w":  round(w, 3),
        }
    thin = sum(1 for v in table.values() if v["n_obs"] < 10)
    logger.info(
        f"PCR baseline rebuilt: {len(table)} tickers, {len(rows)} rows, "
        f"pooled P{PCR_DESIGN_PCT}={pooled:.3f}, {thin} tickers under 10 obs, K={PCR_SHRINK_K}"
    )
    return {"date": date.today(), "pooled": round(pooled, 4), "pooled_n": len(rows), "by_ticker": table}


def get_baseline(refresh: bool = False) -> dict:
    """Return the day's baseline table, rebuilding on the first call of a calendar day."""
    global _cache
    if refresh or _cache["date"] != date.today():
        try:
            _cache = _build()
        except Exception as e:
            # Keep whatever we had; the caller sees pooled=None only if nothing was ever built.
            logger.warning(f"PCR baseline rebuild failed — keeping previous table: {e}")
            if _cache["date"] is None:
                _cache = {"date": date.today(), "pooled": None, "pooled_n": 0, "by_ticker": {}}
    return _cache


def pcr_threshold_for(ticker: str) -> dict | None:
    """
    Partial-pooled P25 threshold for one ticker, or None when no baseline exists
    at all (empty/missing table) — the caller then falls back to the pooled
    scalar and says so in its result, which CHECK 80 counts.
    """
    b = get_baseline()
    if b["pooled"] is None:
        return None
    entry = b["by_ticker"].get(ticker)
    if entry is None:
        return {"threshold": b["pooled"], "own_p25": None, "n_obs": 0, "shrink_w": 0.0,
                "pooled_p25": b["pooled"]}
    return {**entry, "pooled_p25": b["pooled"]}
