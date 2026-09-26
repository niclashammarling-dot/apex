from datetime import date, datetime, time, timedelta
from pathlib import Path
from functools import lru_cache
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from loguru import logger

from backend.config import EXIT_CHECK_INTERVAL, GATE_INTERVAL, POLL_INTERVAL_SECTORS
from backend.data.fetcher_yahoo import fetch_all_sectors
from backend.data.lock4_pcr_collector import collect_pcr_snapshot
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


def _build_transition_priors():
    """Transition priors from full monthly leadership history (shared by the singleton and the preview)."""
    from collections import defaultdict
    from backend.db import get_sector_history
    from backend.regime.regime_bayes import build_transition_priors
    monthly: dict = defaultdict(dict)
    for r in get_sector_history(days=0):
        monthly[r["timestamp"][:7]][r["sector"]] = r["avg_score"]
    leadership = [
        {"date": m, "leader": max(scores, key=scores.get)}
        for m, scores in sorted(monthly.items())
        if scores
    ]
    return build_transition_priors(leadership)


def _get_regime_bayes():
    """Return the singleton RegimeBayes instance, creating it on first call."""
    global _regime_bayes
    if _regime_bayes is None:
        from backend.regime.regime_bayes import RegimeBayes
        from backend.ticker_config import get_sectors

        sectors_cfg    = get_sectors()
        sector_etf_map = {s: cfg["etf"] for s, cfg in sectors_cfg.items()}

        transition_priors = _build_transition_priors()
        _regime_bayes = RegimeBayes(sectors_cfg, sector_etf_map, transition_priors)
        logger.info(f"RegimeBayes initialised — {len(transition_priors)} prior rows loaded")
    return _regime_bayes


@lru_cache(maxsize=32)
def _nyse_session_bounds(date_str: str) -> tuple[datetime, datetime] | None:
    """
    NYSE open/close for date_str (YYYY-MM-DD) in America/New_York, or None on a
    non-session. Early closes (13:00 ET — day after Thanksgiving, Christmas Eve)
    come from the calendar, not a fixed clock: until 2026-09-17 this read
    09:30–16:00 on every session, so a half-day kept the gate evaluating a
    closed market for three hours. Cached per date.
    """
    import pandas_market_calendars as mcal
    nyse = mcal.get_calendar("NYSE")
    sched = nyse.schedule(start_date=date_str, end_date=date_str)
    if sched.empty:
        return None
    row = sched.iloc[0]
    return (row["market_open"].tz_convert(NY).to_pydatetime(),
            row["market_close"].tz_convert(NY).to_pydatetime())


def _nyse_sessions_for_date(date_str: str) -> int:
    """Number of NYSE sessions on date_str (0 or 1). Kept for existing callers."""
    return 0 if _nyse_session_bounds(date_str) is None else 1


def is_market_open() -> bool:
    now = datetime.now(NY)
    if now.weekday() >= 5:
        return False
    bounds = _nyse_session_bounds(now.strftime("%Y-%m-%d"))
    if bounds is None:
        return False
    open_, close = bounds
    return open_ <= now <= close


def poll_all_sectors(force: bool = False) -> None:
    if not force and not is_market_open():
        logger.debug("Market closed — skipping sector poll")
        return

    logger.info("Polling all sectors…")
    signals = fetch_all_sectors()
    if not signals:
        logger.error("Sector poll returned 0 signals — Yahoo rate-limit or API outage")
        try:
            from backend.alerts import alert_gate_blocked
            alert_gate_blocked("Sector poll returned 0 signals — Yahoo rate-limit or API outage")
        except Exception:
            pass
        return
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
    regime_closed = wallet.check_regime_exits()
    if regime_closed:
        logger.info(f"Regime exit check: closed {len(regime_closed)} position(s)")


def check_live_exit_conditions() -> None:
    if not is_market_open():
        return
    from backend.live_trades_tracker import (
        cancel_orphan_brackets,
        check_live_exits,
        check_live_regime_exits,
    )
    # Sweep orphaned bracket legs before processing exits — prevents sell orders
    # attached to dead positions from creating short exposure.
    cancel_orphan_brackets()
    closed = check_live_exits()
    if closed:
        logger.info(f"Live exit check: closed {len(closed)} position(s)")
    regime_closed = check_live_regime_exits()
    if regime_closed:
        logger.info(f"Live regime exit check: closed {len(regime_closed)} position(s)")


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
    from backend.config import EXCLUDED_SECTORS

    ticker_sector = {
        ticker: sector
        for sector, cfg in get_sectors().items()
        for ticker in cfg["tickers"]
    }

    signals = compute_ticker_signals()
    recovering = {
        t for t, v in signals.items()
        if v["signal"] == "recovering" and ticker_sector.get(t) not in EXCLUDED_SECTORS
    }

    for ticker in recovering:
        sector = ticker_sector.get(ticker, "Unknown")
        upsert_watchlist(ticker, sector, source="auto")

    removed = prune_watchlist_auto(keep_tickers=recovering)
    if recovering or removed:
        logger.debug(f"Watchlist sync: {len(recovering)} recovering, {removed} removed")


def publish_audit_state() -> None:
    """
    Run the mechanical audit checks here, where apex.db and the app-written
    data files exist, and push the findings to the orphan branch audit-state
    for CI to merge into the nightly report. See audit/publish_state.py —
    runs in a subprocess so a failing check can't touch this process.
    """
    import subprocess
    import sys
    from pathlib import Path

    repo = Path(__file__).resolve().parent.parent
    try:
        r = subprocess.run([sys.executable, "-m", "audit.publish_state"],
                           cwd=repo, capture_output=True, text=True, timeout=1200)
    except subprocess.TimeoutExpired:
        logger.error("publish_audit_state: timed out after 1200s")
        return
    if r.returncode != 0:
        logger.error(f"publish_audit_state failed (rc={r.returncode}): "
                     f"{(r.stderr or r.stdout).strip()[-800:]}")
    else:
        logger.info(f"publish_audit_state: {r.stdout.strip().splitlines()[-4:]}")


def _eod_cutoff_utc(d: date) -> str:
    """ISO UTC timestamp of 16:15 ET on date d — the moment the EOD run reads its inputs."""
    from datetime import timezone
    return datetime.combine(d, time(16, 15), tzinfo=NY).astimezone(timezone.utc).isoformat()


def _eod_inputs(target: date, live: bool):
    """Assemble (raw_data, sector_snapshots, ipo_shares) for the EOD update, as of `target`.
    Returns None if the OHLCV download fails (nothing sensible can run without it)."""
    import yfinance as yf

    from backend.db import get_latest_sector_scores
    from backend.regime.ipo_sentiment import IpoSentiment
    from backend.ticker_config import get_sectors

    sectors_cfg = get_sectors()

    # Step 1: IPO sentiment
    try:
        ipo        = IpoSentiment(sectors_cfg)
        ipo_result = ipo.compute(reference_date=None if live else target)
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
        if live:
            raw_data = yf.download(
                all_symbols,
                period="60d",
                auto_adjust=True,
                progress=False,
                group_by="ticker",
            )
        else:
            # 60 trading days ≈ 90 calendar days; `end` is exclusive in yfinance.
            raw_data = yf.download(
                all_symbols,
                start=(target - timedelta(days=90)).isoformat(),
                end=(target + timedelta(days=1)).isoformat(),
                auto_adjust=True,
                progress=False,
                group_by="ticker",
            )
            raw_data = raw_data[raw_data.index.date <= target]
    except Exception as e:
        logger.error(f"EOD regime: raw data download failed — aborting: {e}")
        return None

    sector_snapshots = get_latest_sector_scores(
        as_of=None if live else _eod_cutoff_utc(target)
    )
    return raw_data, sector_snapshots, ipo_shares


def run_eod_regime(as_of: date | None = None, *,
                   now_ny: datetime | None = None) -> str:
    """
    End-of-day Bayesian regime update — scheduled pre-open (08:30 ET) FOR the
    previous session since 2026-09-22 (was 16:15 ET same day); the inputs are
    read as of the session's 16:15 ET close either way.

    Steps:
      1. IPO sentiment — fetch/cache today's IPO sector shares from EDGAR
      2. Download raw OHLCV for all tickers + ETFs (60d lookback)
      3. RegimeBayes.update() — compute posteriors, allocation, persist to DB

    as_of: the trading *session* this update is FOR. The catch-up and replay paths
    pass it explicitly, both from the NYSE calendar. Every input is then read as it
    stood at 16:15 ET on that date — OHLCV truncated to as_of (`_compute_rank_lrs`
    reads `iloc[-1]`, it does not mask by date), sector snapshots as of the 16:15
    cutoff, EDGAR window ending as_of — and the row is stamped with as_of, not with
    the wall-clock date. Before 2026-09-16 the catch-up stamped the restart date and
    the `INSERT OR IGNORE` on the history table then dropped the genuine 16:15 write.

    as_of=None resolves to `_last_eod_due(now)` — the most recent NYSE session whose
    16:15 ET slot has passed — *not* to the wall-clock date. Its only caller is the
    manual `/sectors/regime-bayes/run?persist=true` button (there is no cron on this
    path: since 2026-09-22 the 08:30 job registers `_check_missed_eod_regime`, which
    always passes as_of). Taking the wall-clock date let a press on a non-session day
    stamp a row with that date: the 16:15 guard below passes trivially on a Saturday
    or Sunday, and a full 11-sector row dated Sunday 2026-08-23 sits in the trace,
    which is the only path that produces one. Resolving the session removes the case
    by construction rather than by a time comparison. Found 2026-09-25 from the
    CHECK 65 streak walk, which the off-session row had been silently bridging.

    Behaviour change, deliberate: a press between 00:00 and 16:15 ET on a weekday used
    to be refused as intraday; it now computes the previous session, which is what the
    08:30 cron does. A press after 16:15 ET still computes that day's session.

    A target session that already has history rows is refused, always. There is no
    in-process way to recompute one: RegimeBayes.update decays from self._posteriors,
    the live state AFTER the latest session, so re-running session D applies D's
    evidence on top of D's own posterior (double-counted) — or, for an older D, on
    top of later sessions' state, which it then writes as the live posterior. History
    (INSERT OR IGNORE) would keep the one correct row; sector_posteriors and the
    result cache would take the wrong one, and the next session would decay from it
    into history. The `overwrite=True` escape added 2026-09-25 did exactly that and
    was removed 2026-09-26 without ever having fired (one trace block for every
    session since). Recomputing a persisted session is scripts/replay_eod_regime.py:
    it resets state to D-1 first, on a DB copy by default.

    Returns a status string: "ok", "refused_intraday", "refused_exists", "no_inputs",
    or "failed".
    """
    # Anchor to the NY trading date, not the server's OS-local date — this job
    # can run close to Stockholm's midnight rollover (6h ahead of ET), which would
    # otherwise mislabel today's ET trading day as tomorrow.
    live   = as_of is None
    now_ny = now_ny or datetime.now(NY)
    target = as_of or _last_eod_due(now_ny)
    if now_ny < datetime.combine(target, time(16, 15), tzinfo=NY):
        # Reachable now only via an explicit as_of (replay of a future/today date).
        # The old live path hit it constantly: the button and the old catch-up ran
        # mid-session, stamped today, and INSERT OR IGNORE then dropped the genuine
        # 16:15 write. 7 of the 10 rows in 09-02..09-15 were produced that way.
        # Mid-session reads go through preview_eod_regime(), which persists nothing.
        logger.error(f"EOD regime: {target} 16:15 ET close has not passed — refusing (inputs would be intraday)")
        return "refused_intraday"

    from backend.db import count_sector_posterior_history
    existing = count_sector_posterior_history(target.isoformat())
    if existing:
        logger.error(
            f"EOD regime: {target} already has {existing} persisted posterior row(s) — refusing. "
            f"A re-run would decay from the live state, which already includes {target}, and "
            f"double-count it. To recompute a persisted session use scripts/replay_eod_regime.py "
            f"(resets to the previous session first)."
        )
        return "refused_exists"

    logger.info(f"EOD regime update starting… (as_of={target}{'' if live else ', replay'})")

    inputs = _eod_inputs(target, live)
    if inputs is None:
        return "no_inputs"
    raw_data, sector_snapshots, ipo_shares = inputs

    # Step 3: RegimeBayes update
    try:
        rb     = _get_regime_bayes()
        result = rb.update(target, raw_data, sector_snapshots, ipo_shares)
        logger.info(
            f"Regime update complete — leader={result.leader} "
            f"qualifiers={result.qualifiers}"
        )
        return "ok"
    except Exception as e:
        logger.error(f"RegimeBayes update failed: {e}")
        return "failed"


def preview_eod_regime():
    """Compute what the EOD update would produce on today's inputs as they stand
    right now, and return it WITHOUT persisting: a throwaway RegimeBayes built the
    same way as the singleton, with its DB / result-cache / trace writers stubbed.
    The singleton's in-memory state is not touched. Returns RegimeResult or None."""
    from backend.regime.regime_bayes import RegimeBayes
    from backend.ticker_config import get_sectors

    target = datetime.now(NY).date()
    inputs = _eod_inputs(target, live=True)
    if inputs is None:
        return None
    raw_data, sector_snapshots, ipo_shares = inputs
    try:
        sectors_cfg    = get_sectors()
        sector_etf_map = {s: cfg["etf"] for s, cfg in sectors_cfg.items()}
        rb = RegimeBayes(sectors_cfg, sector_etf_map, _build_transition_priors())
        rb._save_session_posteriors = lambda *_, **__: None
        rb._save_result            = lambda *_: None
        rb._append_signal_trace    = lambda *_: None
        return rb.update(target, raw_data, sector_snapshots, ipo_shares)
    except Exception as e:
        logger.error(f"EOD regime preview failed: {e}")
        return None


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
    """Run autoresearch optimizer over last ~9 months. Runs after the sweep."""
    try:
        from backend.backtest.optimizer import run_optimizer as _run
        _run()
    except Exception as e:
        logger.error(f"Optimizer failed: {e}")


def run_weekly_research() -> None:
    """Sweep, then optimizer, then threshold recalibration — one sequential job.

    Moved 2026-09-13 from Sat/Sun to Monday 17:00 Stockholm: the host is a
    desktop under WSL2 with no service launcher, and the weekend slots never
    fired (no sweep_results.json / optimizer_results.json has ever been
    written). Chained rather than spaced by clock so ordering is by
    dependency, not by guessed duration.
    """
    logger.info("Weekly research: sweep → optimizer → recalibrate")
    run_weekend_sweep()
    run_optimizer()
    recalibrate_thresholds()
    logger.info("Weekly research: done")


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


EOD_CATCHUP_MAX_DAYS = 10
EOD_REPLAY_LOCK      = Path(__file__).resolve().parent.parent / "data" / ".eod_replay.lock"


def _nyse_sessions_between(after: date | None, through: date) -> list[date]:
    """NYSE sessions strictly after `after` (or the 30 days before `through` if None) up to and including `through`."""
    import pandas_market_calendars as mcal
    start = (after + timedelta(days=1)) if after else (through - timedelta(days=30))
    sched = mcal.get_calendar("NYSE").schedule(start_date=start.isoformat(), end_date=through.isoformat())
    return [ts.date() for ts in sched.index]


def _last_eod_due(now: datetime) -> date:
    """Most recent NYSE session whose 16:15 ET EOD slot has already passed."""
    d = now.date()
    if now.time() < time(16, 15):
        d -= timedelta(days=1)
    while not _nyse_sessions_for_date(d.isoformat()):
        d -= timedelta(days=1)
    return d


def _check_missed_eod_regime() -> None:
    """
    Run the EOD regime update for every session due and not yet written. Called at
    startup and, since 2026-09-22, as the 08:30 ET cron itself — the pre-open run
    FOR the previous session is exactly "the last due session has no row yet".
    Determines the most recent NYSE session for which EOD should have already run,
    then compares against MAX(date) in sector_posterior_history, and runs the
    update FOR that session (as_of=expected).

    Uses the NYSE calendar, not weekday arithmetic: the weekday version fired on
    2026-09-08 for Labor Day (2026-09-07) and wrote a mid-session row for 09-08.
    """
    from backend.db import get_db

    if EOD_REPLAY_LOCK.exists():
        # scripts/replay_eod_regime.py holds this while it rewrites the series. Without
        # it, the uvicorn --reload worker restarted mid-repair on 2026-09-16, saw the
        # deleted range, and ran its own catch-up from a stale in-memory prior in
        # parallel with the replay — two writers racing INSERT OR IGNORE.
        logger.warning(f"EOD regime catch-up: {EOD_REPLAY_LOCK} present — replay in progress, skipping")
        return

    now      = datetime.now(NY)
    expected = _last_eod_due(now)

    # Compare against the history table's own date column, not sector_posteriors.updated_at:
    # updated_at is wall-clock, so a catch-up run on Tuesday morning FOR Monday would
    # read as "Tuesday done" and a second outage before 16:15 would lose Tuesday.
    try:
        conn = get_db()
        try:
            row = conn.execute("SELECT MAX(date) FROM sector_posterior_history").fetchone()
        finally:
            conn.close()
        last_date = date.fromisoformat(row[0]) if row and row[0] else None
    except Exception as e:
        logger.warning(f"EOD regime catch-up: DB check failed — {e}")
        return

    if last_date and last_date >= expected:
        return

    # Fill every NYSE session in (last_date, expected], oldest first — the posterior
    # is sequential, so a two-day outage must be replayed in order, not patched at
    # the newest day. Capped so a stale DB on a fresh install doesn't replay months.
    missed = _nyse_sessions_between(last_date, expected)[-EOD_CATCHUP_MAX_DAYS:]
    logger.warning(
        f"EOD regime missed for {len(missed)} session(s) {missed[0]}..{missed[-1]} "
        f"(last row: {last_date or 'never'}) — running now"
    )
    for d in missed:
        run_eod_regime(as_of=d)


def _check_missed_pcr_collect() -> None:
    """
    Same-evening catch-up for collect_pcr (16:30 ET). Found 2026-09-21: the
    market window's blind `sleep` ended 23 min early (WSL2 monotonic clock runs
    fast after a host sleep), the process died at 16:17 ET, and the 16:30 slot
    never fired; the EOD fallback had exited at 16:10 on "port bound". Open
    interest is a snapshot that OCC republishes overnight, so a run later the
    same evening reads the same data the slot would have — and a run the next
    morning would not. Same-day only, never a past date: a snapshot stamped
    with a date it was not taken on is the provenance defect CHECK 77 exists for.
    """
    from backend.db import get_db
    now = datetime.now(NY)
    today = now.date()
    if now.time() < time(16, 30) or not _nyse_sessions_for_date(today.isoformat()):
        return
    try:
        conn = get_db()
        try:
            n = conn.execute("SELECT COUNT(*) FROM lock4_pcr_history WHERE date = ?",
                             (today.isoformat(),)).fetchone()[0]
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"PCR catch-up: DB check failed — {e}")
        return
    if n:
        return
    logger.warning(f"collect_pcr missed for {today} (started {now:%H:%M} ET, no rows) — running now")
    collect_pcr_snapshot(today=today)


def _check_missed_audit_publish() -> None:
    """
    Same-evening catch-up for publish_audit_state (16:33 ET), keyed on the
    generated_at date in audit/state/latest.json. Same 2026-09-21 gap as the
    PCR catch-up: no nightly audit ran, so CHECKs 81–83 had no reader that
    night. Same-day only; a late run still reads today's state.
    """
    import json
    now = datetime.now(NY)
    today = now.date()
    if now.time() < time(16, 33) or not _nyse_sessions_for_date(today.isoformat()):
        return
    state = Path(__file__).resolve().parent.parent / "audit" / "state" / "latest.json"
    try:
        gen = json.loads(state.read_text()).get("generated_at") if state.exists() else None
        done = gen and datetime.fromisoformat(gen).astimezone(NY).date() >= today
    except Exception as e:
        logger.warning(f"audit catch-up: could not read {state.name} — {e}")
        return
    if done:
        return
    logger.warning(f"publish_audit_state missed for {today} (last: {gen or 'never'}) — running now")
    publish_audit_state()


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


def _check_missed_sentiment_prefetch() -> None:
    """
    Run sentiment prefetch on startup if it was missed (server down at 9:35 AM ET).
    Only fires on weekdays; uses the same 26-hour staleness threshold as CHECK 17.
    """
    from backend.db import get_db

    now = datetime.now(NY)
    if now.weekday() >= 5:
        return

    try:
        conn = get_db()
        try:
            row = conn.execute("SELECT MAX(fetched_at) FROM sentiment_cache").fetchone()
        finally:
            conn.close()
        last_str = row[0] if row and row[0] else None
    except Exception:
        last_str = None

    if last_str:
        try:
            last_dt = datetime.fromisoformat(last_str)
        except ValueError as e:
            logger.warning(f"Sentiment catch-up: malformed timestamp in DB ({last_str!r}) — {e}")
            last_dt = None
        if last_dt is not None:
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=NY)
            age_hours = (datetime.now(NY) - last_dt).total_seconds() / 3600
            if age_hours <= 26:
                return

    logger.warning("Sentiment pre-fetch missed or stale — running catch-up now")
    try:
        run_sentiment_prefetch()
    except Exception as e:
        logger.warning(f"Sentiment catch-up failed: {e}")


def _check_missed_live_exits() -> None:
    """
    Run live exit checks immediately on startup if the market is open.
    Interval jobs self-heal after the first fire; this closes the startup gap
    where live positions are unprotected until the first scheduled interval.
    """
    if not is_market_open():
        return
    logger.warning("Startup: market is open — running live exit check immediately")
    check_live_exit_conditions()


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
        # Offset live gate by half the interval so demo and live never fire together.
        # Halves peak concurrent yfinance load on the shared _YF_SEMAPHORE in lock4_leading.
        # Plus half an exit-check interval (2026-09-25): GATE_INTERVAL is a multiple of
        # EXIT_CHECK_INTERVAL, so without it every live gate run fired in the same second
        # as check_live_exits and read the broker before the tracker had booked a fresh
        # exit fill (QCOM 09-25, OXY/EOG/COP 09-16 — the only two data-quality halts on
        # record, both this race). Defence in depth only: the gate's own fill lookup in
        # _compute_apex_day_pnl is the fix.
        #
        # Why the midpoint and not "a few seconds after the tracker": no fixed offset
        # can guarantee the gate reads after a *finished* tracker run. Measured over
        # 401 tracker runs (09-10 → 09-25 logs): p50 1.7s, p95 5s, but real runs of
        # 1-4.5 min exist (09-10 19:26-19:50 back-to-back ~250s; 09-14 several). A
        # seconds-after offset loses to every run longer than it; the midpoint clears
        # everything up to 150s and sits furthest from both neighbouring ticks. The
        # cost — the ledger may be up to 2.5 min behind the broker when the gate
        # reads — is covered by the fill lookup, and the other staleness effects are
        # conservative (a just-exited ticker still OPEN in the DB is skipped as held).
        # Do not tighten this toward the tracker tick to "freshen" the ledger.
        start_date=datetime.now(tz=scheduler.timezone)
                   + timedelta(minutes=GATE_INTERVAL // 2, seconds=EXIT_CHECK_INTERVAL * 30),
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
        _check_missed_eod_regime,   # runs every session due and not yet written, oldest first
        "cron",
        day_of_week="mon-fri",
        hour=8,
        minute=30,          # pre-open (14:30 Stockholm), FOR the previous session. Was 16:15 ET
                            # same day (22:15 Stockholm) — the slot the machine is most often
                            # off for, and the one the 09-18/09-21 outages hit. Every input is
                            # retained and read as of the session's 16:15 close, so the morning
                            # run is the same computation; the startup catch-up (same function)
                            # already covered every missed evening this way. Niclas, 2026-09-22.
        id="eod_regime",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        collect_pcr_snapshot,
        "cron",
        day_of_week="mon-fri",
        hour=16,
        minute=30,          # 30 min after market close — OI data settled post-close
        id="collect_pcr",
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
        publish_audit_state,
        "cron",
        day_of_week="mon-fri",
        hour=16,
        minute=33,          # inside the Task Scheduler EOD window (16:05-16:40 ET), after
                            # collect_pcr 16:30. Was 20:00 ET ("one hour before CI's 01:00 UTC"):
                            # the host was never up at 02:00 Stockholm — fired twice in its
                            # life (09-12, 09-16, both by a late session). 20:33 UTC is still
                            # 4.5 h ahead of the CI cron. Full run measured 72 s (2026-09-16).
        id="publish_audit_state",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    # ── Weekly jobs ──────────────────────────────────────────────────────────
    scheduler.add_job(
        send_weekly_report,
        "cron",
        day_of_week="fri",
        hour=16,
        minute=5,           # 5 min after market close
        id="weekly_report",
        replace_existing=True,
    )
    # structural_checks removed 2026-09-13: never fired in the retained log window
    # (host down Saturday 06:00 ET). The same seven checks run in CI on every
    # push via tests/test_structural.py — a failure domain independent of this host.
    # Weekly research moved 2026-09-13 from Sat 20:00 / Sat 22:00 / Sun 03:00 ET
    # to Monday 17:00 Stockholm — the one slot the host is reliably up.
    # Note: 17:00 Stockholm is 11:00 ET, mid-session; the sweep and optimizer
    # share this process with the live gate loop.
    scheduler.add_job(
        run_weekly_research,
        "cron",
        day_of_week="mon",
        hour=17,
        minute=0,
        timezone="Europe/Stockholm",
        id="weekly_research",
        replace_existing=True,
    )
    # precache_monday (Sun 18:00 ET) removed 2026-09-13: its only purpose was
    # warming Monday's first poll; at any Monday-afternoon slot it fires after
    # that poll. Restore under a Sunday slot if the host ever runs weekends.
    scheduler.start()
    _check_missed_eod_regime()
    _check_missed_calibration()
    _check_missed_weekly_report()
    _check_missed_sentiment_prefetch()
    _check_missed_live_exits()
    _check_missed_pcr_collect()
    _check_missed_audit_publish()
    logger.info(
        f"Scheduler started — sectors every {POLL_INTERVAL_SECTORS}m, "
        f"gate every {GATE_INTERVAL}m, "
        f"exit checks every {EXIT_CHECK_INTERVAL}m (America/New_York)"
    )
