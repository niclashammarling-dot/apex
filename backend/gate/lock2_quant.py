"""
gate/lock2_quant.py — Lock 2: Quantitative Signal

Checks whether the pre-computed signal_score from signals/aggregator.py
meets the sector-specific threshold for this ticker.

Tickers on the watchlist get a 15% lower threshold bar, rewarding
early recovery detection without bypassing signal quality checks.

This lock does no scoring itself — the score is always computed
upstream by signals/aggregator.py before the gate chain runs.
This module is purely a threshold gate on that pre-computed score.

Public API:
    from gate.lock2_quant import evaluate
    result = evaluate(ticker, sector, signal_score, on_watchlist=False)
"""
from __future__ import annotations

from loguru import logger

from backend.config import LOCK1_THRESHOLD, SECTOR_THRESHOLD_FLOORS
from backend.gate.types import LockResult

LOCK_ID            = 2
WATCHLIST_DISCOUNT = 0.15   # 15% lower threshold for watchlist tickers


def evaluate(
    ticker:       str,
    sector:       str,
    signal_score: float,
    on_watchlist: bool = False,
) -> LockResult:
    """
    Evaluate whether the ticker's signal score clears the quant threshold.

    Args:
        ticker:       Stock ticker symbol
        sector:       Apex sector name — used to look up sector threshold
        signal_score: Pre-computed score from signals/aggregator.py (0–1)
        on_watchlist: If True, applies 15% discount to threshold

    Returns:
        LockResult with lock_id=2
    """
    threshold = _sector_threshold(sector)

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
        logger.debug(f"Lock 2 [{ticker}] FAIL — {reason}")
        return LockResult.fail(lock_id=LOCK_ID, reason=reason, data=data)

    reason = (
        f"Signal score {signal_score:.4f} >= threshold "
        f"{effective_threshold:.4f}"
        + (" (watchlist discount applied)" if on_watchlist else "")
    )
    logger.debug(f"Lock 2 [{ticker}] PASS — {reason}")
    return LockResult.pass_(
        lock_id=LOCK_ID,
        score=signal_score,
        reason=reason,
        data=data,
    )


# ── Private helpers ───────────────────────────────────────────────────────────

def _sector_threshold(sector: str) -> float:
    """
    Return the sector-specific Lock 2 threshold. Uses the calibrated
    ticker_thresholds table as the base, then applies SECTOR_THRESHOLD_FLOORS
    as a floor so sweep-validated minimums survive recalibration runs.
    Falls back to LOCK1_THRESHOLD if the sector has no calibrated entry.
    """
    try:
        from backend.db import get_ticker_thresholds
        thresholds = get_ticker_thresholds()
        if not thresholds:
            logger.warning(
                f"Lock 2 [{sector}]: get_ticker_thresholds() returned empty — "
                f"falling back to LOCK1_THRESHOLD {LOCK1_THRESHOLD}. "
                "Calibration race or DB read failure."
            )
        base = float(thresholds.get(sector, LOCK1_THRESHOLD))
    except Exception as exc:
        logger.warning(
            f"Lock 2 [{sector}]: get_ticker_thresholds() raised {exc!r} — "
            f"falling back to LOCK1_THRESHOLD {LOCK1_THRESHOLD}."
        )
        base = float(LOCK1_THRESHOLD)
    floor = SECTOR_THRESHOLD_FLOORS.get(sector)
    return max(base, floor) if floor is not None else base
