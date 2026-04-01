from datetime import datetime, time
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from loguru import logger

from backend.config import POLL_INTERVAL_SECTORS, GATE_INTERVAL, EXIT_CHECK_INTERVAL
from backend.data.fetcher_yahoo import fetch_all_sectors
from backend.db import insert_signal, prune_signals, insert_sector_snapshots, prune_sector_snapshots, insert_ticker_history

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

    # Persist per-ticker scores for threshold calibration (never pruned)
    _record_ticker_history(signals)

    # Auto-manage watchlist based on RECOVERING ticker signals
    _sync_watchlist()


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


def _record_ticker_history(signals: list[dict]) -> None:
    """Write today's per-ticker scores to ticker_history (never pruned)."""
    from datetime import date
    today = date.today().isoformat()
    rows = [
        {
            "ticker":       s["ticker"],
            "sector":       s["sector"],
            "day":          today,
            "signal_score": s["signal_score"],
        }
        for s in signals if s.get("signal_score") is not None
    ]
    insert_ticker_history(rows)


def _sync_watchlist() -> None:
    """
    Auto-add RECOVERING tickers to watchlist; remove auto entries that have faded.
    Manual watchlist entries are never removed automatically.
    """
    from backend.sector_regime import compute_ticker_signals
    from backend.db import upsert_watchlist, prune_watchlist_auto
    from backend.ticker_config import get_sectors

    ticker_sector = {
        ticker: sector
        for sector, cfg in get_sectors().items()
        for ticker in cfg["tickers"]
    }

    signals = compute_ticker_signals()
    recovering = {t for t, v in signals.items() if v["signal"] == "recovering"}

    for ticker in recovering:
        sector = ticker_sector.get(ticker, "Unknown")
        upsert_watchlist(ticker, sector, source="auto")

    removed = prune_watchlist_auto(keep_tickers=recovering)
    if recovering or removed:
        logger.debug(f"Watchlist sync: {len(recovering)} recovering, {removed} removed")


def recalibrate_thresholds() -> None:
    """Re-derive per-sector Lock 1 thresholds from ticker_history. Runs weekly."""
    try:
        from backend.ticker_threshold_calibration import calibrate
        calibrate()
    except Exception as e:
        logger.warning(f"Threshold recalibration failed: {e}")


def send_weekly_report() -> None:
    """Build and email/Slack the weekly performance report. Runs Friday at market close."""
    try:
        from backend.weekly_report import send_weekly_report as _send
        _send()
    except Exception as e:
        logger.error(f"Weekly report failed: {e}")


def run_weekend_sweep() -> None:
    """Run parameter grid search over last 90d. Runs Saturday night."""
    try:
        from backend.backtest.weekend_sweep import run_sweep
        run_sweep()
    except Exception as e:
        logger.error(f"Weekend sweep failed: {e}")


def run_optimizer() -> None:
    """Run autoresearch optimizer over last ~9 months. Runs Saturday night after sweep."""
    try:
        from backend.backtest.optimizer import run_optimizer as _run
        _run()
    except Exception as e:
        logger.error(f"Optimizer failed: {e}")


def run_structural_checks() -> None:
    """Run structural integrity checks. Runs Saturday morning before the sweep."""
    try:
        from backend.maintenance import run_structural_checks as _run
        _run()
    except Exception as e:
        logger.error(f"Structural checks failed: {e}")


def precache_monday_data() -> None:
    """Pre-fetch all sector signals Sunday evening so Monday's first poll is instant."""
    logger.info("Sunday pre-cache: fetching all sectors…")
    poll_all_sectors(force=True)


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
    scheduler.add_job(
        recalibrate_thresholds,
        "cron",
        day_of_week="sun",
        hour=3,
        minute=0,
        id="recalibrate_thresholds",
        replace_existing=True,
    )
    # ── Weekend jobs ──────────────────────────────────────────────────────────
    scheduler.add_job(
        send_weekly_report,
        "cron",
        day_of_week="fri",
        hour=16,
        minute=5,           # 5 min after market close
        id="weekly_report",
        replace_existing=True,
    )
    scheduler.add_job(
        run_structural_checks,
        "cron",
        day_of_week="sat",
        hour=6,
        minute=0,           # Saturday 6am — surface issues before the sweep runs
        id="structural_checks",
        replace_existing=True,
    )
    scheduler.add_job(
        run_weekend_sweep,
        "cron",
        day_of_week="sat",
        hour=20,
        minute=0,           # Saturday 8pm — sweep runs overnight
        id="weekend_sweep",
        replace_existing=True,
    )
    scheduler.add_job(
        run_optimizer,
        "cron",
        day_of_week="sat",
        hour=22,
        minute=0,           # Saturday 10pm — optimizer runs after sweep
        id="optimizer",
        replace_existing=True,
    )
    scheduler.add_job(
        precache_monday_data,
        "cron",
        day_of_week="sun",
        hour=18,
        minute=0,           # Sunday 6pm — fresh signals before Monday open
        id="precache_monday",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(
        f"Scheduler started — sectors every {POLL_INTERVAL_SECTORS}m, "
        f"gate every {GATE_INTERVAL}m, "
        f"exit checks every {EXIT_CHECK_INTERVAL}m (America/New_York)"
    )
