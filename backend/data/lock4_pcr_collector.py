"""
data/lock4_pcr_collector.py — Daily P/C ratio collection for Lock 4 baseline calibration.

Fetches near-term put/call OI ratios for all active APEX tickers after market close
and stores them to lock4_pcr_history. After 4-8 weeks of data, the per-ticker P25
percentile replaces the interim fixed PCR_THRESHOLD (0.85) in lock4_leading.py.

Design rules:
  - Only tickers in the current universe (get_sectors()) are collected — stays clean
    if the universe changes.
  - Idempotent: duplicate (ticker, date) rows are silently skipped.
  - Dislocation auto-flag: VIX >= VIX_DISLOCATION_THRESHOLD marks the day; can also
    be set manually via db.set_pcr_dislocation(date, True).
  - Uses the same 2-expiry logic as _check_put_call_ratio in lock4_leading.py so the
    baseline is directly comparable to live gate readings.

Entry point:
    from backend.data.lock4_pcr_collector import collect_pcr_snapshot
    collect_pcr_snapshot()
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import yfinance as yf
from loguru import logger

from backend.db import insert_pcr_observation
from backend.ticker_config import get_sectors

VIX_DISLOCATION_THRESHOLD = 30.0


def _fetch_vix() -> float | None:
    try:
        data = yf.download("^VIX", period="1d", interval="1m", progress=False, auto_adjust=True)
        return float(data["Close"].values.flatten()[-1]) if not data.empty else None
    except Exception as e:
        logger.warning(f"PCR collector: VIX fetch failed — {e}")
        return None


def _fetch_pcr(ticker: str) -> dict | None:
    """
    Fetch near-term (2 expiries) put/call OI ratio for a ticker.
    Returns None on any data failure — caller skips the ticker.
    """
    try:
        t        = yf.Ticker(ticker)
        expiries = t.options
        if not expiries:
            logger.debug(f"PCR collector [{ticker}]: no options data")
            return None

        call_oi = put_oi = 0
        used_expiries: list[str] = []
        for exp in expiries[:2]:
            chain    = t.option_chain(exp)
            call_oi += int(chain.calls["openInterest"].fillna(0).sum())
            put_oi  += int(chain.puts["openInterest"].fillna(0).sum())
            used_expiries.append(exp)

        if call_oi + put_oi == 0:
            logger.debug(f"PCR collector [{ticker}]: zero open interest")
            return None

        pcr = put_oi / call_oi if call_oi > 0 else 99.0
        return {
            "pcr":      round(pcr, 4),
            "call_oi":  call_oi,
            "put_oi":   put_oi,
            "expiry_1": used_expiries[0] if len(used_expiries) > 0 else None,
            "expiry_2": used_expiries[1] if len(used_expiries) > 1 else None,
        }
    except Exception as e:
        logger.warning(f"PCR collector [{ticker}]: fetch failed — {e}")
        return None


def collect_pcr_snapshot(today: date | None = None) -> dict:
    """
    Fetch and store P/C ratios for all active APEX tickers.

    Args:
        today: Date to record observations for. Defaults to today (UTC date).

    Returns:
        Summary dict: {collected, skipped, errors, is_dislocation}
    """
    if today is None:
        today = datetime.now(timezone.utc).date()

    date_str = today.isoformat()
    sectors  = get_sectors()

    tickers = sorted({
        t
        for cfg in sectors.values()
        for t in cfg["tickers"]
    })

    vix            = _fetch_vix()
    is_dislocation = vix is not None and vix >= VIX_DISLOCATION_THRESHOLD

    if is_dislocation:
        logger.warning(
            f"PCR collector: VIX={vix:.1f} >= {VIX_DISLOCATION_THRESHOLD} — "
            f"marking {date_str} as dislocation day"
        )

    collected = skipped = errors = 0

    for ticker in tickers:
        result = _fetch_pcr(ticker)
        if result is None:
            errors += 1
            continue
        insert_pcr_observation(
            ticker         = ticker,
            date           = date_str,
            pcr            = result["pcr"],
            expiry_1       = result["expiry_1"],
            expiry_2       = result["expiry_2"],
            call_oi        = result["call_oi"],
            put_oi         = result["put_oi"],
            is_dislocation = is_dislocation,
        )
        collected += 1

    logger.info(
        f"PCR snapshot {date_str}: collected={collected} skipped={skipped} "
        f"errors={errors} dislocation={is_dislocation}"
    )
    return {
        "collected":       collected,
        "skipped":         skipped,
        "errors":          errors,
        "is_dislocation":  is_dislocation,
        "vix":             vix,
        "date":            date_str,
    }
