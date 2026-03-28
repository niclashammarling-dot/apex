from datetime import datetime, time
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from loguru import logger

from backend.config import POLL_INTERVAL_SECTORS, GATE_INTERVAL, EXIT_CHECK_INTERVAL
from backend.data.fetcher_yahoo import fetch_all_sectors
from backend.db import insert_signal, prune_signals, insert_sector_snapshots, prune_sector_snapshots

NY = ZoneInfo("America/New_York")

scheduler = BackgroundScheduler(timezone="America/New_York")


def is_market_open() -> bool:
    now = datetime.now(NY)
    if now.weekday() >= 5:
        return False
    return time(9, 30) <= now.time() <= time(16, 0)


def poll_all_sectors(force: bool = False) -> None:
    if not force and not is_market_open():
        logger.debug("Market closed — skipping sector poll")
        return

    logger.info("Polling all sectors…")
    signals = fetch_all_sectors()
    for row in signals:
        insert_signal(row)
    logger.info(f"Stored {len(signals)} signals")

    # Aggregate per-sector and snapshot for rotation tracking
    _snapshot_sectors(signals)


def run_gate_candidates() -> None:
    if not is_market_open():
        logger.debug("Market closed — skipping gate evaluation")
        return
    from backend.gate import gate_runner
    gate_runner.run()


def check_exit_conditions() -> None:
    if not is_market_open():
        return
    from backend import wallet
    closed = wallet.check_exits()
    if closed:
        logger.info(f"Exit check: closed {len(closed)} position(s)")


def check_live_exit_conditions() -> None:
    if not is_market_open():
        return
    from backend.live_trades_tracker import check_live_exits
    closed = check_live_exits()
    if closed:
        logger.info(f"Live exit check: closed {len(closed)} position(s)")


def run_live_gate_candidates() -> None:
    if not is_market_open():
        logger.debug("Market closed — skipping live gate evaluation")
        return
    from backend.gate import gate_runner_live
    gate_runner_live.run()


def _snapshot_sectors(signals: list[dict]) -> None:
    """Aggregate signals into per-sector snapshots and persist."""
    from collections import defaultdict
    from datetime import datetime, timezone

    ts = datetime.now(timezone.utc).isoformat()
    buckets: dict[str, list] = defaultdict(list)
    for s in signals:
        buckets[s["sector"]].append(s)

    snapshots = []
    for sector, rows in buckets.items():
        avg_score = round(sum(r["signal_score"] for r in rows) / len(rows), 4)
        top       = max(rows, key=lambda r: r["signal_score"])
        snapshots.append({
            "timestamp":    ts,
            "sector":       sector,
            "avg_score":    avg_score,
            "top_ticker":   top["ticker"],
            "top_score":    top["signal_score"],
            "ticker_count": len(rows),
        })

    insert_sector_snapshots(snapshots)
    logger.debug(f"Sector snapshots written: {len(snapshots)} sectors")


def prune_old_signals() -> None:
    deleted = prune_signals(keep_per_ticker=10)
    if deleted:
        logger.info(f"Signal pruning: removed {deleted} stale rows")
    snap_deleted = prune_sector_snapshots(keep_days=1825)  # keep 5 years
    if snap_deleted:
        logger.info(f"Sector snapshot pruning: removed {snap_deleted} old rows")


def start_scheduler() -> None:
    scheduler.add_job(
        poll_all_sectors,
        "interval",
        minutes=POLL_INTERVAL_SECTORS,
        id="poll_sectors",
        replace_existing=True,
    )
    scheduler.add_job(
        run_gate_candidates,
        "interval",
        minutes=GATE_INTERVAL,
        id="run_gate",
        replace_existing=True,
    )
    scheduler.add_job(
        check_exit_conditions,
        "interval",
        minutes=EXIT_CHECK_INTERVAL,
        id="check_exits",
        replace_existing=True,
    )
    scheduler.add_job(
        run_live_gate_candidates,
        "interval",
        minutes=GATE_INTERVAL,
        id="run_live_gate",
        replace_existing=True,
    )
    scheduler.add_job(
        check_live_exit_conditions,
        "interval",
        minutes=EXIT_CHECK_INTERVAL,
        id="check_live_exits",
        replace_existing=True,
    )
    scheduler.add_job(
        prune_old_signals,
        "cron",
        hour=2,
        minute=0,
        id="prune_signals",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(
        f"Scheduler started — sectors every {POLL_INTERVAL_SECTORS}m, "
        f"gate every {GATE_INTERVAL}m, "
        f"exit checks every {EXIT_CHECK_INTERVAL}m (America/New_York)"
    )
