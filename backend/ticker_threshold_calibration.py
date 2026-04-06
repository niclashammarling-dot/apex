"""
Per-sector ticker threshold calibration.

Two methods, both derived from ticker_history data:

  1. Distribution-based (p80):
     Threshold = 80th percentile of each sector's historical signal scores.
     Ensures equal selectivity across sectors — "top 20%" means the same
     thing in Utilities as it does in Technology.

  2. Outcome-based (predictive):
     Threshold = score above which a ticker most often sustains 15+ days
     above that score within the next 20 trading days.
     Directly measures what score level predicts a confirmed trend.

The final threshold per sector is the higher of the two, so we only
enter when a signal is both statistically rare AND historically predictive.

Results are persisted to ticker_thresholds table and loaded by sector_regime.py
at startup (cached, recalibrated on demand).
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

_CALIBRATION_MARKER = Path(__file__).parent.parent / "data" / "calibration_done.txt"


def _this_week_label() -> str:
    return datetime.now(timezone.utc).strftime("%G-W%V")


def _mark_calibrated() -> None:
    _CALIBRATION_MARKER.write_text(_this_week_label())


def was_calibrated_this_week() -> bool:
    if not _CALIBRATION_MARKER.exists():
        return False
    return _CALIBRATION_MARKER.read_text().strip() == _this_week_label()


def calibrate(min_rows: int = 500) -> dict[str, float]:
    """
    Compute and persist per-sector thresholds.
    Returns {sector: threshold}.
    Skips sectors with fewer than min_rows historical data points.
    """
    from backend.db import save_ticker_thresholds

    dist_thresholds    = _distribution_thresholds(min_rows)
    outcome_thresholds = _outcome_thresholds(min_rows)

    final: dict[str, float] = {}
    all_sectors = set(dist_thresholds) | set(outcome_thresholds)

    logger.info("=== Ticker threshold calibration ===")
    for sector in sorted(all_sectors):
        dist    = dist_thresholds.get(sector)
        outcome = outcome_thresholds.get(sector)

        if dist is None and outcome is None:
            continue

        # Take the higher of the two — only enter when both agree it's strong
        threshold = round(max(t for t in [dist, outcome] if t is not None), 4)
        final[sector] = threshold

        logger.info(
            f"  {sector:20s}  dist_p80={dist or '—':>7}  "
            f"outcome={outcome or '—':>7}  → final={threshold}"
        )

    save_ticker_thresholds(final)
    _mark_calibrated()
    logger.info(f"Calibration complete — {len(final)} sector thresholds saved")
    return final


def _distribution_thresholds(min_rows: int) -> dict[str, float]:
    """p80 of each sector's historical signal score distribution."""
    from backend.db import get_db
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT sector, signal_score FROM ticker_history ORDER BY sector, signal_score"
        ).fetchall()
    finally:
        conn.close()

    by_sector: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        by_sector[r["sector"]].append(r["signal_score"])

    thresholds = {}
    for sector, scores in by_sector.items():
        if len(scores) < min_rows:
            logger.warning(f"  {sector}: only {len(scores)} rows — skipping distribution threshold")
            continue
        scores.sort()
        p80 = scores[int(len(scores) * 0.80)]
        thresholds[sector] = round(p80, 4)

    return thresholds


def _outcome_thresholds(min_rows: int) -> dict[str, float]:
    """
    For each sector, find the score above which a ticker most often goes on
    to sustain a run (score stays above that level for 15+ of next 20 trading days).

    Method:
      - Group ticker_history by (ticker, sector)
      - For each day, check: over the next 20 days, how many days is score >= candidate_threshold?
      - Sweep candidate thresholds from p50 to p95 in steps
      - Pick the threshold where the "sustained run" rate crosses 30%
        (i.e. 30% of entries at or above this score lead to a confirmed 15d run)
    """
    from backend.db import get_db
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT ticker, sector, day, signal_score
            FROM ticker_history
            ORDER BY ticker, day
        """).fetchall()
    finally:
        conn.close()

    # Build per-ticker time series
    by_ticker: dict[tuple, list] = defaultdict(list)
    for r in rows:
        by_ticker[(r["ticker"], r["sector"])].append((r["day"], r["signal_score"]))

    # Collect (score, sustained_20d_above_score) pairs per sector
    sector_pairs: dict[str, list[tuple[float, list[float]]]] = defaultdict(list)

    for (ticker, sector), series in by_ticker.items():
        series_sorted = sorted(series)
        scores = [s for _, s in series_sorted]
        n = len(scores)
        if n < 25:
            continue
        for i in range(n - 20):
            entry_score = scores[i]
            future_20   = scores[i+1 : i+21]
            sector_pairs[sector].append((entry_score, future_20))

    thresholds = {}
    for sector, pairs in sector_pairs.items():
        if len(pairs) < min_rows:
            continue

        all_scores = sorted([p[0] for p in pairs])
        ns = len(all_scores)

        # Sweep p50 → p95 as candidate thresholds
        best_threshold = None
        for pct in range(50, 96, 5):
            candidate = all_scores[int(ns * pct / 100)]
            above_entries  = [(s, fut) for s, fut in pairs if s >= candidate]
            if len(above_entries) < 20:
                continue
            sustained = sum(
                1 for _, fut in above_entries
                if sum(1 for f in fut if f >= candidate) >= 15
            )
            rate = sustained / len(above_entries)
            if rate >= 0.30:
                best_threshold = round(candidate, 4)
            else:
                break  # rate drops as threshold rises — stop here

        if best_threshold is not None:
            thresholds[sector] = best_threshold

    return thresholds


def print_calibration_report() -> None:
    """Print full calibration stats for inspection."""
    from backend.db import get_db
    conn = get_db()

    print("\n=== Per-sector signal score distributions ===\n")
    print(f"  {'Sector':20s}  {'N':>6}  {'p50':>6}  {'p75':>6}  {'p80':>6}  {'p85':>6}  {'p90':>6}  {'mean':>6}")
    print("  " + "-" * 75)

    rows = conn.execute(
        "SELECT sector, signal_score FROM ticker_history ORDER BY sector, signal_score"
    ).fetchall()
    conn.close()

    by_sector: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        by_sector[r["sector"]].append(r["signal_score"])

    for sector in sorted(by_sector):
        s = sorted(by_sector[sector])
        n = len(s)
        if n < 10:
            continue
        def p(pct): return round(s[int(n * pct / 100)], 4)
        mean = round(statistics.mean(s), 4)
        print(f"  {sector:20s}  {n:6d}  {p(50):6.4f}  {p(75):6.4f}  {p(80):6.4f}  {p(85):6.4f}  {p(90):6.4f}  {mean:6.4f}")
