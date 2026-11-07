"""
gate/l3_score.py — L3 SCORE: the quantitative signal

Checks whether the pre-computed signal_score from signals/aggregator.py
meets the sector-specific threshold for this ticker.

Tickers on the watchlist get a 15% lower threshold bar, rewarding
early recovery detection without bypassing signal quality checks.

This lock does no scoring itself — the score is always computed
upstream by signals/aggregator.py before the gate chain runs.
This module is purely a threshold gate on that pre-computed score.

Public API:
    from gate.l3_score import evaluate
    result = evaluate(ticker, sector, signal_score, on_watchlist=False)
"""
from __future__ import annotations

from loguru import logger

from backend.config import DEMO_L3_SCORE_THRESHOLD_MIN, SECTOR_THRESHOLD_FLOORS
from backend.gate.types import LockResult

LOCK_ID            = 3
WATCHLIST_DISCOUNT = 0.15   # 15% lower threshold for watchlist tickers


def evaluate(
    ticker:            str,
    sector:            str,
    signal_score:      float,
    on_watchlist:      bool       = False,
    sector_thresholds: dict | None = None,
    fallback:          float | None = None,
) -> LockResult:
    """
    Evaluate whether the ticker's signal score clears the quant threshold.

    Args:
        ticker:       Stock ticker symbol
        sector:       Apex sector name — used to look up sector threshold
        signal_score: Pre-computed score from signals/aggregator.py (0–1)
        on_watchlist: If True, applies 15% discount to threshold

    Returns:
        LockResult with lock_id=3
    """
    threshold = _sector_threshold(sector, sector_thresholds, fallback)

    if on_watchlist:
        effective_threshold = round(threshold * (1.0 - WATCHLIST_DISCOUNT), 4)
    else:
        effective_threshold = threshold

    passed = signal_score >= effective_threshold

    data = {
        "signal_score":        round(signal_score, 4),
        "threshold":           threshold,
        "effective_threshold": effective_threshold,
        "on_watchlist":        on_watchlist,
        "watchlist_discount":  WATCHLIST_DISCOUNT if on_watchlist else 0.0,
        "sector":              sector,
    }

    if not passed:
        reason = (
            f"Signal score {signal_score:.4f} below threshold "
            f"{effective_threshold:.4f}"
            + (" (watchlist discount applied)" if on_watchlist else "")
        )
        logger.debug(f"L3 SCORE [{ticker}] FAIL — {reason}")
        return LockResult.fail(lock_id=LOCK_ID, reason=reason, data=data)

    reason = (
        f"Signal score {signal_score:.4f} >= threshold "
        f"{effective_threshold:.4f}"
        + (" (watchlist discount applied)" if on_watchlist else "")
    )
    logger.debug(f"L3 SCORE [{ticker}] PASS — {reason}")
    return LockResult.pass_(
        lock_id=LOCK_ID,
        score=signal_score,
        reason=reason,
        data=data,
    )


# ── Private helpers ───────────────────────────────────────────────────────────

def _sector_threshold(sector: str, sector_thresholds: dict | None = None, fallback: float | None = None) -> float:
    """
    Return the sector-specific L3 SCORE threshold. Uses the calibrated
    ticker_thresholds table as the base, then applies SECTOR_THRESHOLD_FLOORS
    as a floor so sweep-validated minimums survive recalibration runs.
    A sector with no calibrated entry, or an empty or unreadable table, falls back to `fallback`: the book's
    l3_score_threshold_min, passed by the chain from the runner's config, as the pre-filter
    (db.get_l3_score_candidates) already does (2026-11-07; until then this read DEMO_L3_SCORE_THRESHOLD_MIN 0.70
    in both books, so in live an uncalibrated sector passed the pre-filter at 0.65 and stopped at L3 at 0.70).
    Without a fallback (callers outside the chain) the constant stays.

    sector_thresholds: pre-fetched dict from the runner (eliminates a DB read
    per ticker per cycle). Falls back to a live DB fetch when None.
    """
    default = DEMO_L3_SCORE_THRESHOLD_MIN if fallback is None else fallback
    if sector_thresholds is not None:
        base = float(sector_thresholds.get(sector, default))
    else:
        try:
            from backend.db import get_ticker_thresholds
            thresholds = get_ticker_thresholds()
            if not thresholds:
                logger.warning(
                    f"L3 SCORE [{sector}]: get_ticker_thresholds() returned empty — "
                    f"falling back to the book's threshold {default}. "
                    "Calibration race or DB read failure."
                )
            base = float(thresholds.get(sector, default))
        except Exception as exc:
            logger.warning(
                f"L3 SCORE [{sector}]: get_ticker_thresholds() raised {exc!r} — "
                f"falling back to the book's threshold {default}."
            )
            base = float(default)
    floor = SECTOR_THRESHOLD_FLOORS.get(sector)
    return max(base, floor) if floor is not None else base
