from datetime import datetime, time
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from loguru import logger

from backend.config import EXIT_CHECK_INTERVAL, GATE_INTERVAL, POLL_INTERVAL_SECTORS
from backend.data.fetcher_yahoo import fetch_all_sectors
from backend.db import (
    insert_sector_snapshots,
    insert_signal,
    insert_ticker_history,
    prune_sector_snapshots,
    prune_signals,
)

NY = ZoneInfo("America/New_York")

scheduler = BackgroundScheduler(timezone="America/New_York")

# Persistent RegimeBayes singleton — keeps in-memory posteriors between daily runs
# (DB also persists them, so restarts are safe)
_regime_bayes = None


def _get_regime_bayes():
    """Return the singleton RegimeBayes instance, creating it on first call."""
    global _regime_bayes
    if _regime_bayes is None:
        from backend.db import get_sector_history
        from backend.regime.regime_bayes import RegimeBayes, build_transition_priors
        from backend.ticker_config import get_sectors

        sectors_cfg    = get_sectors()
        sector_etf_map = {s: cfg["etf"] for s, cfg in sectors_cfg.items()}

        # Build transition priors from full leadership history
        raw_history  = get_sector_history(days=0)
        from collections import defaultdict
        monthly: dict = defaultdict(dict)
        for r in raw_history:
            monthly[r["timestamp"][:7]][r["sector"]] = r["avg_score"]
        leadership = [
            {"date": m, "leader": max(scores, key=scores.get)}
            for m, scores in sorted(monthly.items())
            if scores
        ]
        transition_priors = build_transition_priors(leadership)

        _regime_bayes = RegimeBayes(sectors_cfg, sector_etf_map, transition_priors)
        logger.info(f"RegimeBayes initialised — {len(transition_priors)} prior rows loaded")
    return _regime_bayes


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
    from backend.db import prune_watchlist_auto, upsert_watchlist
    from backend.sector_regime import compute_ticker_signals
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


def run_eod_regime() -> None:
    """
    End-of-day Bayesian regime update.
    Runs at 4:15 PM after market close on trading days.

    Steps:
      1. IPO sentiment — fetch/cache today's IPO sector shares from EDGAR
      2. Download raw OHLCV for all tickers + ETFs (60d lookback)
      3. RegimeBayes.update() — compute posteriors, allocation, persist to DB
    """
    from datetime import date

    import yfinance as yf

    from backend.db import get_latest_sector_scores
    from backend.regime.ipo_sentiment import IpoSentiment
    from backend.ticker_config import get_sectors

    logger.info("EOD regime update starting…")

    sectors_cfg = get_sectors()

    # Step 1: IPO sentiment
    try:
        ipo        = IpoSentiment(sectors_cfg)
        ipo_result = ipo.compute()
        ipo_shares = ipo_result.ipo_shares
        logger.info(
            f"IPO sentiment: total={ipo_result.total_ipos} "
            f"risk_off={ipo_result.risk_off}"
        )
    except Exception as e:
        logger.warning(f"IPO sentiment failed — using uniform shares: {e}")
        n          = len(sectors_cfg)
        ipo_shares = {s: round(1.0 / n, 4) for s in sectors_cfg}

    # Step 2: raw OHLCV for all tickers + ETFs
    try:
        all_symbols = list({
            t
            for cfg in sectors_cfg.values()
            for t in cfg["tickers"] + [cfg["etf"]]
        })
        raw_data = yf.download(
            all_symbols,
            period="60d",
            auto_adjust=True,
            progress=False,
            group_by="ticker",
        )
    except Exception as e:
        logger.error(f"EOD regime: raw data download failed — aborting: {e}")
        return

    # Step 3: RegimeBayes update
    try:
        sector_snapshots = get_latest_sector_scores()
        rb     = _get_regime_bayes()
        result = rb.update(date.today(), raw_data, sector_snapshots, ipo_shares)
        logger.info(
            f"Regime update complete — leader={result.leader} "
            f"qualifiers={result.qualifiers}"
        )
    except Exception as e:
        logger.error(f"RegimeBayes update failed: {e}")


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


def run_sentiment_prefetch() -> None:
    """Pre-fetch Reddit and RSS sentiment for all watchlist tickers at market open."""
    from backend.sentiment.sentiment_prefetch import run as prefetch_run
    summary = prefetch_run()
    logger.info(
        f"Sentiment pre-fetch: {summary['success']}/{summary['tickers']} tickers cached"
    )


def prune_old_signals() -> None:
    deleted = prune_signals(keep_per_ticker=10)
    if deleted:
        logger.info(f"Signal pruning: removed {deleted} stale rows")
    snap_deleted = prune_sector_snapshots(keep_days=1825)  # keep 5 years
    if snap_deleted:
        logger.info(f"Sector snapshot pruning: removed {snap_deleted} old rows")


def _check_missed_eod_regime() -> None:
    """
    Run the EOD regime update on startup if it was missed (server down at 16:15 ET).
    Determines the most recent trading day for which EOD should have already run,
    then compares against the last updated_at in sector_posteriors.
    """
    from datetime import timedelta

    from backend.db import get_db

    now     = datetime.now(NY)
    today   = now.date()
    weekday = now.weekday()  # 0=Mon … 4=Fri, 5=Sat, 6=Sun

    if weekday == 5:                        # Saturday — EOD should have run Friday
        expected = today - timedelta(days=1)
    elif weekday == 6:                      # Sunday — EOD should have run Friday
        expected = today - timedelta(days=2)
    elif now.time() >= time(16, 15):        # Weekday past 16:15 — today
        expected = today
    elif weekday == 0:                      # Monday before 16:15 — last Friday
        expected = today - timedelta(days=3)
    else:                                   # Tue–Fri before 16:15 — yesterday
        expected = today - timedelta(days=1)

    try:
        with get_db() as conn:
            row = conn.execute("SELECT MAX(updated_at) FROM sector_posteriors").fetchone()
        last_updated_str = row[0] if row and row[0] else None
    except Exception as e:
        logger.warning(f"EOD regime catch-up: DB check failed — {e}")
        return

    last_date = datetime.fromisoformat(last_updated_str).date() if last_updated_str else None
    if last_date and last_date >= expected:
        return

    logger.warning(
        f"EOD regime missed for {expected} "
        f"(last update: {last_date or 'never'}) — running now"
    )
    run_eod_regime()


def _check_missed_calibration() -> None:
    """
    Run threshold calibration on startup if the server was down at the
    scheduled Sunday 3 AM window and it hasn't run yet this week.
    """
    from backend.ticker_threshold_calibration import calibrate, was_calibrated_this_week
    if not was_calibrated_this_week():
        logger.warning("Threshold calibration was missed this week — running now")
        try:
            calibrate()
        except Exception as e:
            logger.warning(f"Catch-up calibration failed: {e}")


def _check_missed_weekly_report() -> None:
    """
    Fire the weekly report immediately on startup if the server was down
    when it was scheduled (Friday 16:05 ET) and it hasn't been sent yet
    this week.
    """
    from backend.weekly_report import send_weekly_report, was_sent_this_week
    now = datetime.now(NY)
    # Only relevant if it's Friday after 16:05 or the weekend (Sat/Sun)
    day = now.weekday()  # 0=Mon … 4=Fri, 5=Sat, 6=Sun
    past_friday_close = (
        (day == 4 and now.time() >= time(16, 5)) or
        day in (5, 6)
    )
    if past_friday_close and not was_sent_this_week():
        logger.warning("Weekly report was missed (server was down at scheduled time) — sending now")
        send_weekly_report()


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
        run_eod_regime,
        "cron",
        day_of_week="mon-fri",
        hour=16,
        minute=15,          # 15 min after market close — snapshots settled
        id="eod_regime",
        replace_existing=True,
    )
    scheduler.add_job(
        run_sentiment_prefetch,
        "cron",
        day_of_week="mon-fri",
        hour=9,
        minute=35,          # 9:35 AM ET — 5 min after market open, first prints settling
        id="sentiment_prefetch",
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
    _check_missed_eod_regime()
    _check_missed_calibration()
    _check_missed_weekly_report()
    logger.info(
        f"Scheduler started — sectors every {POLL_INTERVAL_SECTORS}m, "
        f"gate every {GATE_INTERVAL}m, "
        f"exit checks every {EXIT_CHECK_INTERVAL}m (America/New_York)"
    )
