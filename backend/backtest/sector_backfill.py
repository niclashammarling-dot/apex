"""
Sector rotation backfill — populates sector_snapshots with historical data.

Reuses the backtest engine's downloader and per-ticker scorer so the signal
computation is identical to what the live system would have produced.
One snapshot row per sector per trading day is inserted (end-of-day).
Already-present dates are skipped, so the function is idempotent.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from loguru import logger

from backend.config import SPY_TICKER
from backend.ticker_config import get_sectors
from backend.db import insert_sector_snapshots, get_existing_snapshot_dates
from backend.backtest.engine import (
    _download_all,
    _score_all_tickers,
    _spy_data_on,
    _trading_days,
)


def backfill_sector_snapshots(start_date: str, end_date: str) -> int:
    """
    Score every ticker for every trading day in [start_date, end_date]
    and write the per-sector aggregates into sector_snapshots.

    Returns the number of new rows inserted.
    Raises RuntimeError if data download fails.
    """
    start = date.fromisoformat(start_date)
    end   = date.fromisoformat(end_date)

    if end < start:
        raise ValueError("end_date must be >= start_date")

    sectors_cfg   = get_sectors()
    ticker_sector = {t: s for s, cfg in sectors_cfg.items() for t in cfg["tickers"]}
    sector_etf    = {s: cfg["etf"] for s, cfg in sectors_cfg.items()}
    all_tickers   = [SPY_TICKER] + list(sector_etf.values()) + list(ticker_sector.keys())

    # 130-day lookback buffer mirrors backtest engine (needed for MA50 + MACD warmup)
    lookback_start = start - timedelta(days=130)
    raw_data = _download_all(all_tickers, lookback_start, end)
    if raw_data.empty:
        raise RuntimeError("Failed to download historical data for backfill")

    trading_days = _trading_days(start, end, raw_data)
    logger.info(f"Sector backfill: {len(trading_days)} trading days, {len(ticker_sector)} tickers")

    # Skip dates that already have snapshots
    existing = get_existing_snapshot_dates(start_date, end_date)
    todo = [d for d in trading_days if d.isoformat() not in existing]
    logger.info(f"Sector backfill: {len(existing)} days already present, {len(todo)} to compute")

    inserted = 0
    for today in todo:
        spy = _spy_data_on(raw_data, today)

        # threshold=0.0 → score every ticker regardless of signal strength
        candidates = _score_all_tickers(
            raw_data, today, ticker_sector, sector_etf,
            spy["regime"], spy["return_20d"],
            lock1_threshold=0.0,
        )

        if not candidates:
            continue

        buckets: dict[str, list] = defaultdict(list)
        for sig in candidates:
            buckets[sig["sector"]].append(sig)

        snapshots = []
        ts = f"{today.isoformat()}T16:00:00"   # mark as end-of-day
        for sector, sigs in buckets.items():
            avg_score = round(sum(s["signal_score"] for s in sigs) / len(sigs), 4)
            top       = max(sigs, key=lambda s: s["signal_score"])
            snapshots.append({
                "timestamp":    ts,
                "sector":       sector,
                "avg_score":    avg_score,
                "top_ticker":   top["ticker"],
                "top_score":    top["signal_score"],
                "ticker_count": len(sigs),
            })

        insert_sector_snapshots(snapshots)
        inserted += len(snapshots)

    logger.info(f"Sector backfill complete: {inserted} rows inserted")
    return inserted
