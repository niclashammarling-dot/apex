from datetime import datetime, time
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from loguru import logger

from backend.config import POLL_INTERVAL_SECTORS, GATE_INTERVAL, EXIT_CHECK_INTERVAL
from backend.data.fetcher_yahoo import fetch_all_sectors
from backend.db import insert_signal, prune_signals

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


def prune_old_signals() -> None:
    deleted = prune_signals(keep_per_ticker=10)
    if deleted:
        logger.info(f"Signal pruning: removed {deleted} stale rows")


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
