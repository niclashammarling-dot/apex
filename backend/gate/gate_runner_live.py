"""
Live Gate Runner — the same gate as gate_runner.py (demo = live except trading, ruling 2026-10-28) but:
  - L0's M4 rules run first, once per cycle (portfolio_l0); a halt writes no row
  - L1 also checks the live blocklist, and affordability counts whole shares (bracket orders)
  - On TRADE_QUEUED, places a real Alpaca bracket order
  - Stores gate results in live_gate_history (not the demo signals table), under the code lock_codes derives
    from each row's rule_states; an outcome no rule covers is FILTERED_UNKNOWN and is mailed once per cycle
  - Only runs when LIVE_ENABLED=true

Bracket orders set TP + SL at Alpaca order time, so no separate exit
checker is needed — Alpaca manages the position lifecycle natively.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from loguru import logger

from backend.config import LIVE_DATA_QUALITY_DIVERGENCE, LIVE_ENABLED
from backend.ticker_config import get_sectors
from backend.db import (
    get_live_ticker_gate_fails,
    get_live_trades_exited_since,
    get_lock1_candidates,
    get_open_live_tickers,
    get_open_live_trades,
    get_recently_exited_live_tickers,
    get_recently_failed_live_tickers,
    get_unreconciled_live_trades,
    insert_live_gate_result,
    insert_live_trade,
    set_alert_latch,
)
from backend.gate.chain import build_base_context, evaluate_chain
from backend.gate.cycle import GateCycle, evaluated_reason, record_stale
from backend.gate import lock_codes
from backend.gate.gate_runner import (
    PRE_ROTATION_FLOOR,
    _chain_to_gate_result,
    STOPPED,
    _affordability,
    _alert_unknown,
    _compute_bayesian_multipliers,
    _finalize,
    _overflow_state,
    _persist_multiplier_stats,
    _pre_chain_result,
    _pre_chain_states,
    _run_l5_for_result,
)
from backend.sector_caps import compute_dynamic_caps

_MAX_WORKERS = 5

# The smallest live order (L1 notional_too_small): checked before the chain on the largest possible notional and at
# execution, with one whole share (bracket orders take whole shares).
LIVE_MIN_ORDER = 10.0
# Dedup latches persisted via backend.db.set_alert_latch() (DB-backed, keyed
# "loss_cap:<date>" / "unreconciled:<date>") — not module-level sets. A
# process restart (deploy, uvicorn --reload) must not re-arm an already-sent
# daily alert; 2026-08-07 confirmed an in-memory set does exactly that.


def _ny_today() -> str:
    """
    NY-anchored trading date for daily alert-latch keys — not date.today()
    (server runs 6h ahead of ET; OS-local date can mislabel which trading day
    a same-evening alert belongs to, see 2026-08-07 regime-bayes staleness
    fix). Split out so tests can patch it to cross a day boundary without
    waiting on the clock.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("America/New_York")).date().isoformat()


def _realized_day_contribution(ticker: str, entry_timestamp: str, exit_price: float,
                               qty: float, lifetime_pnl: float, ny_today) -> tuple[float, bool, bool]:
    """
    Today's share of one exit's realized P&L — the single implementation,
    used for exits already booked in live_trades and for fills the exit
    tracker hasn't booked yet (2026-09-25). Returns (amount, carried,
    fell_back).

      - Entered today: entry-to-exit (lifetime_pnl) already is a daily
        figure — used as-is.
      - Carried over from an earlier day: exit_price vs. prior_close, not
        vs. entry — entry-to-exit would double-count the pre-today portion
        of the move.
      - No prior close available: falls back to lifetime_pnl, a
        known-imprecise term (logged, and counted for CHECK 75).
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from backend.brokers.alpaca import get_prior_close

    entry_date = datetime.fromisoformat(entry_timestamp).astimezone(
        ZoneInfo("America/New_York")
    ).date()
    if entry_date == ny_today:
        return lifetime_pnl, False, False
    prior_close = get_prior_close(ticker)
    if prior_close is None:
        logger.warning(
            f"Live gate: no prior close for {ticker} exit — "
            "using lifetime pnl for this leg's realized contribution"
        )
        return lifetime_pnl, True, True
    return (exit_price - prior_close) * qty, True, False


def _compute_apex_day_pnl(positions: list[dict]) -> tuple[float, list[str], dict[str, str]]:
    """
    APEX-side day P&L, independent of the broker's equity/last_equity
    snapshot: realized pnl already booked today (from live_trades, our own
    ledger) plus unrealized pnl for OPEN rows matched to a current broker
    position. Compared against broker.get_account()'s day_pnl to catch a
    2026-08-11-HON-shaped divergence (broker snapshot drops a held position;
    ledger and portfolio-history both stay clean) at the moment it happens
    rather than waiting on the separate untracked-position checker.

    Both terms are genuine daily figures, not lifetime ones (fixed
    2026-08-16, see
    2026-08-12-apex-dual-logging-daily-pnl-conflation-prefix-reference):
    unrealized reads unrealized_intraday_pnl (today's mark vs. prior close),
    not unrealized_pnl (since-entry); realized goes through
    _realized_day_contribution.

    An OPEN row absent from the broker is first checked for a sell fill
    since its entry (find_exit_fill — same two invariants the exit tracker
    uses: fill postdates entry, fill not already consumed by another row).
    A fill covering the whole qty is an exit the tracker hasn't booked yet:
    counted as realized from the fill, not flagged (2026-09-25 QCOM,
    2026-09-16 OXY/EOG/COP — the gate ran seconds before the tracker and
    halted on the broker being *ahead* of the ledger). A partial fill counts
    its filled qty and still flags the remainder; no fill still flags.

    Known, conservative behaviour: find_exit_fill returns one order's fill
    (newest qualifying leg in the orders feed, or one order group in
    activities). A split exit — part through the TP leg, the rest through
    another sell — therefore halts on the remainder. Acceptable because it
    fails closed; the booking-side version of the same gap is filed with
    the split-exit spec (sum every sell fill since entry, VWAP, book only
    when summed qty equals the position).

    Returns (apex_day_pnl, missing, evidence):
      - missing: tickers open in the DB, absent from the broker, and not
        fully explained by a fill — itself evidence, not folded silently
        into the pnl sum as zero.
      - evidence: ticker -> one-line description of what the fill lookup
        found, for every ticker absent from the broker (explained or not).
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from backend.brokers import alpaca as broker
    from backend.live_trades_tracker import find_exit_fill

    ny_today = datetime.now(ZoneInfo("America/New_York")).date()
    ny_midnight = datetime.now(ZoneInfo("America/New_York")).replace(
        hour=0, minute=0, second=0, microsecond=0
    ).astimezone(timezone.utc).isoformat()

    realized = 0.0
    carried = 0   # exits opened on an earlier day — the legs that need a prior close
    fallbacks = 0
    for t in get_live_trades_exited_since(ny_midnight):
        amount, was_carried, fell_back = _realized_day_contribution(
            t["ticker"], t["timestamp"], t["exit_price"], t["qty"], t["pnl"], ny_today)
        realized += amount
        carried += was_carried
        fallbacks += fell_back

    positions_by_ticker = {p["ticker"]: p for p in positions}
    unrealized = 0.0
    missing = []
    evidence: dict[str, str] = {}
    for t in get_open_live_trades():
        pos = positions_by_ticker.get(t["ticker"])
        if pos is not None:
            unrealized += pos["unrealized_intraday_pnl"] or 0.0
            continue

        fill = find_exit_fill(t["ticker"], broker, t["timestamp"], allow_partial=True)
        filled_qty = fill["qty"] or 0.0
        if fill["evidence"] != "found" or filled_qty <= 0:
            missing.append(t["ticker"])
            evidence[t["ticker"]] = f"no sell fill since entry ({fill['evidence']})"
            continue

        filled_qty = min(filled_qty, t["qty"])
        amount, was_carried, fell_back = _realized_day_contribution(
            t["ticker"], t["timestamp"], fill["price"], filled_qty,
            round((fill["price"] - t["entry_price"]) * filled_qty, 2), ny_today)
        realized += amount
        carried += was_carried
        fallbacks += fell_back
        fill_desc = (f"sell fill {filled_qty:g}/{t['qty']:g} sh @ ${fill['price']:.2f} "
                     f"at {fill['exited_at']}")
        if filled_qty < t["qty"]:
            missing.append(t["ticker"])
            evidence[t["ticker"]] = (f"{fill_desc} — {t['qty'] - filled_qty:g} sh unexplained")
        else:
            evidence[t["ticker"]] = f"{fill_desc}, not yet booked — counted as realized"

    # Denominator line for CHECK 75: a zero fallback count is only evidence
    # of a working path when carried > 0. Emitted whenever there is a
    # carried-over leg, whether or not any fell back.
    if carried:
        logger.info(
            f"Live gate: prior-close legs processed={carried} fallbacks={fallbacks}"
        )

    return round(realized + unrealized, 2), missing, evidence


def run(cycle: GateCycle | None = None) -> list[dict]:
    """
    Evaluate the live gate for all LIVE_LOCK1 candidates, one run at a time across threads and processes.
    Does nothing (returns []) when LIVE_ENABLED is false.
    Reads thresholds from live_config.json at call time so Promote takes effect immediately.
    cycle (backend/gate/cycle.py) carries the start every row of this cycle is
    stamped with, and takes the exit reason.

    Finding 2 (2026-10-07 review, built 2026-10-31): two runs that both read open tickers before either
    writes place duplicate brackets. A run that finds the lock held returns with cycle reason "busy" and
    places nothing. flock is per open file, so it holds against a second thread and a second backend alike
    (the :8001 instance that traded on 09-21); the OS releases it if the process dies.
    """
    import fcntl
    cycle = cycle or GateCycle.unrecorded()
    path = live_gate_lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            logger.warning("Live gate: another live gate run holds the lock — this run places nothing")
            cycle.reason = "busy"
            return []
        return _run(cycle)


def live_gate_lock_path():
    """Beside the database, so a test (conftest's DB) never takes production's lock."""
    from backend import db
    return db.DB_PATH.with_name("live_gate.lock")


def _run(cycle: GateCycle) -> list[dict]:
    if not LIVE_ENABLED:
        logger.debug("Live gate: LIVE_ENABLED=false — skipping")
        cycle.reason = "disabled"
        return []

    from backend.brokers import alpaca as broker

    # Dual P&L logging (2026-08-11 HON third snapshot-omission) — broker's
    # equity/last_equity delta alongside APEX's own realized+unrealized.
    # Deliberately sits above the UNRECONCILED early return below: this is
    # read-only observability (two GETs and a log line), not a trading
    # action, and the instrument exists specifically to measure snapshot-vs-
    # ledger divergence — parking it behind the halt meant it went dark for
    # exactly the hours a snapshot-vs-ledger problem was active during this
    # incident, the one window its data would have mattered most. Trading
    # decisions (order placement, cancellation) still respect every gate
    # below in the same order as before; only this measurement moved earlier.
    #
    # acct is fetched once here and reused below for the trading pre-flight
    # (trading_blocked/account_blocked) — not re-fetched.
    acct = None
    try:
        acct = broker.get_account()
    except Exception as e:
        logger.warning(f"Live gate: could not reach Alpaca for dual P&L check — {e}")

    positions_read_ok = apex_pnl_read_ok = False
    apex_day_pnl, missing_from_broker, divergence = None, [], None
    fill_evidence: dict[str, str] = {}
    if acct is not None:
        # A failed positions read is tracked explicitly, not folded into
        # "positions=[]". If get_positions() throws while there happen to be
        # zero OPEN live_trades rows, _compute_apex_day_pnl's missing-ticker
        # mechanism has nothing to compare against and would report a clean
        # divergence — indistinguishable from a genuinely quiet day.
        # positions_read_ok makes that failure a first-class signal instead
        # of a silent zero.
        positions_read_ok = True
        try:
            positions = broker.get_positions()
        except Exception as e:
            logger.warning(f"Live gate: could not fetch positions for dual P&L check — {e}")
            positions = []
            positions_read_ok = False

        apex_pnl_read_ok = True
        try:
            apex_day_pnl, missing_from_broker, fill_evidence = _compute_apex_day_pnl(positions)
        except Exception as e:
            logger.error(f"Live gate: could not compute APEX-side day P&L — {e}")
            apex_day_pnl, missing_from_broker, fill_evidence = None, [], {}
            apex_pnl_read_ok = False

        if apex_pnl_read_ok:
            divergence = round(acct["day_pnl"] - apex_day_pnl, 2)
            logger.info(f"Live gate: day P&L — broker ${acct['day_pnl']:.2f} vs APEX ${apex_day_pnl:.2f} "
                        f"(divergence ${divergence:.2f}" +
                        (f", missing from broker: {missing_from_broker}" if missing_from_broker else "") + ")"
                        + "".join(f"\n  {tk}: {ev}" for tk, ev in fill_evidence.items()))
        else:
            logger.warning("Live gate: skipping divergence check — APEX-side day P&L unavailable")

    # UNRECONCILED gate — the first thing that can block trading. A position
    # that vanished with no fill/order/activity trail (see 2026-08-06 HON
    # incident, CHECK 66) must not release the halt just because a day boundary
    # resets last_equity or the broker quietly restores the position. Only
    # resolve_unreconciled() can clear this — see backend/db.py.
    unreconciled = get_unreconciled_live_trades()
    if unreconciled:
        today = _ny_today()
        tickers = ", ".join(t["ticker"] for t in unreconciled)
        logger.error(f"Live gate: halted — UNRECONCILED position(s) unresolved: {tickers}")
        if set_alert_latch(f"unreconciled:{today}"):
            from backend.alerts import alert_gate_blocked
            alert_gate_blocked(
                f"UNRECONCILED position(s) unresolved: {tickers} — "
                "resolve via backend.db.resolve_unreconciled() before trading resumes"
            )
        cycle.reason = "halt_unreconciled"
        return []

    from backend.db import get_ticker_thresholds
    from backend.live_config import get_live_config
    cfg = get_live_config()
    sector_thresholds = get_ticker_thresholds()

    # Pre-flight: verify account is tradeable before wasting LLM calls.
    # Reuses the acct fetched above for dual P&L logging — a failure there
    # already means this can't succeed either.
    if acct is None:
        logger.error("Live gate: could not reach Alpaca — skipping cycle")
        cycle.reason = "broker_unreachable"
        return []
    if acct["trading_blocked"] or acct["account_blocked"]:
        logger.warning("Live gate: Alpaca account is blocked — skipping cycle")
        cycle.reason = "account_blocked"
        return []

    # Track record, recorded 2026-09-25 so two firings aren't read as one:
    # this halt has fired twice in production (09-16 OXY/EOG/COP, 09-25
    # QCOM), both false — the exit-fill race _compute_apex_day_pnl now
    # covers — and has never caught a real disappearance. HON (08-11), the
    # incident it was built for, predates it. The only evidence it catches
    # what it exists for is the regression test
    # test_no_fill_still_halts (tests/test_live_reconciliation.py).
    #
    # Data-quality gate — evaluated every cycle, independent of the loss-cap
    # threshold. A wrong broker snapshot corrupts position sizing (which reads
    # acct["equity"]/acct["cash"] directly) regardless of whether the
    # resulting "loss" happens to be large enough to trip the cap — a
    # sub-cap divergence is exactly the one that distorts sizing silently
    # instead of announcing itself, so it can't wait on the cap check to be
    # noticed.
    data_quality_triggered = (
        not positions_read_ok
        or not apex_pnl_read_ok
        or bool(missing_from_broker)
        or (divergence is not None and abs(divergence) >= LIVE_DATA_QUALITY_DIVERGENCE)
    )
    if data_quality_triggered:
        today = _ny_today()
        reasons = []
        if not positions_read_ok:
            reasons.append("broker positions unreadable")
        if not apex_pnl_read_ok:
            reasons.append("APEX-side P&L computation failed")
        if missing_from_broker:
            reasons.append(f"missing from broker: {missing_from_broker}")
        if divergence is not None and abs(divergence) >= LIVE_DATA_QUALITY_DIVERGENCE:
            reasons.append(f"divergence ${divergence:.2f} exceeds ${LIVE_DATA_QUALITY_DIVERGENCE:.2f}")
        logger.error(f"Live gate: halted — DATA QUALITY: broker day P&L "
                     f"${acct['day_pnl']:.2f} vs APEX "
                     f"${'unavailable' if apex_day_pnl is None else f'{apex_day_pnl:.2f}'} — "
                     + "; ".join(reasons))
        # One latch per distinct problem per day (2026-09-25), not one per
        # day: under a single day key, 09-25's false QCOM alert would have
        # silenced a genuine HON-shaped disappearance later the same day —
        # still halted, but with no email. Per ticker rather than per set,
        # so a set whose membership shifts doesn't re-alert on every change.
        # Every key is set (list, not any()) so each is recorded once.
        problem_keys = [f"ticker:{tk}" for tk in missing_from_broker]
        if divergence is not None and abs(divergence) >= LIVE_DATA_QUALITY_DIVERGENCE:
            problem_keys.append("divergence")
        if not positions_read_ok:
            problem_keys.append("positions_unreadable")
        if not apex_pnl_read_ok:
            problem_keys.append("apex_pnl_failed")
        newly_latched = [k for k in problem_keys if set_alert_latch(f"data_quality:{today}:{k}")]
        if newly_latched:
            from backend.alerts import alert_data_quality_divergence
            alert_data_quality_divergence(acct["day_pnl"], apex_day_pnl, missing_from_broker,
                                          fill_evidence)
        cycle.reason = "halt_data_quality"
        return []

    # L0 Portfolio: the M4 stop rules (backend/gate/portfolio_l0.py), before L1 starts. Only
    # reached once broker and APEX are confirmed to agree, so a trip here is a genuine loss, not a
    # snapshot artifact. The daily cap is a share of current equity; downstream readers (L5's
    # risk_limits) see the effective dollar cap through cfg.
    from backend.db import live_sample_rows
    from backend.gate import portfolio_l0
    l0 = portfolio_l0.evaluate(acct, live_sample_rows(), cfg["starting_balance"])
    cfg["daily_loss_cap"] = l0["daily_loss_cap"]
    if l0["reason"]:
        today = _ny_today()
        logger.warning(f"Live gate: L0 halt {l0['reason']} — day loss ${l0['day_loss']:.2f} / cap "
                       f"${l0['daily_loss_cap']:.2f}; realised drawdown {l0['drawdown']['drawdown']:.2%}; "
                       f"expectancy {l0['expectancy']}")
        if set_alert_latch(f"{l0['reason']}:{today}"):
            if l0["reason"] == portfolio_l0.HALT_LOSS_CAP:
                from backend.alerts import alert_daily_loss_cap
                alert_daily_loss_cap(l0["day_loss"], l0["daily_loss_cap"])
            else:
                from backend.alerts import alert_gate_blocked
                alert_gate_blocked(f"L0 {l0['reason']}: realised drawdown {l0['drawdown']['drawdown']:.2%} "
                                   f"(peak ${l0['drawdown']['peak']:,.2f}, now ${l0['drawdown']['equity']:,.2f}); "
                                   f"expectancy {l0['expectancy']}")
        cycle.reason = l0["reason"]
        return []
    l0_states: dict[str, str] = {}
    for ident, verdict in portfolio_l0.rule_verdicts(l0).items():
        lock_codes.put(l0_states, ident, verdict)

    stale: list[dict] = []
    candidates = get_lock1_candidates(threshold=cfg["lock1_threshold"],
                                      sector_thresholds=sector_thresholds,
                                      stale_out=stale)
    record_stale(cycle, stale, "Live gate runner")
    n_stale_main = len(stale)
    if not candidates:
        logger.info("Live gate runner: no Lock 1 candidates this cycle")
        cycle.reason = "no_fresh_candidates" if stale else "no_candidates"
        _persist_multiplier_stats({}, None, [], runner="live")
        return []

    # Skip tickers already held, in gate cooloff, in exit cooloff, or on the
    # live-only quarantine list (LIVE_TICKER_BLOCKLIST — broker-integrity
    # holds, not signal-quality exclusions; see backend/config.py).
    from backend.config import LIVE_TICKER_BLOCKLIST
    open_tickers    = get_open_live_tickers()
    failed_tickers  = get_recently_failed_live_tickers(cfg.get("gate_cooloff_hours", 4))
    exited_tickers  = get_recently_exited_live_tickers(
        sl_hours=cfg.get("exit_cooloff_hours", 24),
        tp_hours=cfg.get("tp_cooloff_hours", 168),
    )
    blocklisted_tickers = set(LIVE_TICKER_BLOCKLIST)
    blocked = open_tickers | failed_tickers | exited_tickers | blocklisted_tickers
    pre_checks = {"blocklist": blocklisted_tickers, "position_open": open_tickers,
                  "cooloff": failed_tickers, "exit_cooloff": exited_tickers}
    skipped = [c for c in candidates if c["ticker"] in blocked]
    candidates = [c for c in candidates if c["ticker"] not in blocked]

    ts = cycle.started_at
    unknown: list[dict] = []
    for c in skipped:
        _write_live(c, _pre_chain_result(c, {**l0_states, **_pre_chain_states(c["ticker"], pre_checks)}), ts, unknown)

    if skipped:
        skipped_set = {c["ticker"] for c in skipped}
        # held= counts candidates this cycle already holds; the book size is separate
        # (2026-09-30: "open=6" was read as the position count on 09-28 and 09-29).
        logger.info(f"Live gate runner: skipped {len(skipped)} candidate(s) "
                    f"(held={len(open_tickers & skipped_set)}, "
                    f"cooloff={len(failed_tickers & skipped_set)}, "
                    f"exit_cooloff={len(exited_tickers & skipped_set)}, "
                    f"blocklist={len(blocklisted_tickers & skipped_set)}) "
                    f"— book holds {len(open_tickers)}")

    if not candidates:
        logger.info("Live gate runner: all candidates skipped (open positions / cooloff)")
        cycle.reason = "all_skipped"
        _persist_multiplier_stats({}, None, [], runner="live")
        return []

    from backend.config import EXCLUDED_SECTORS
    candidates = [c for c in candidates if c.get("sector", "") not in EXCLUDED_SECTORS]
    if not candidates:
        logger.info("Live gate runner: all candidates in excluded sectors")
        cycle.reason = "all_excluded"
        _persist_multiplier_stats({}, None, [], runner="live")
        return []

    logger.info(f"Live gate runner: {len(candidates)} candidate(s) — {[c['ticker'] for c in candidates]}")

    _ticker_sector = {t: s for s, data in get_sectors().items() for t in data["tickers"]}
    _positions = broker.get_positions()
    # Exposure ratios: all numerators and denominators must use equity scale.
    # Do not substitute buying_power here — on margin accounts it is 2-4x equity
    # and will inflate projected exposure past every sector cap.
    _starting_balance = cfg["starting_balance"]
    _sector_exposure: dict[str, float] = {}
    for _p in _positions:
        _sec = _ticker_sector.get(_p["ticker"], "")
        if _sec:
            _sector_exposure[_sec] = round(
                _sector_exposure.get(_sec, 0.0) + _p["cost_basis"] / _starting_balance, 3
            )

    wallet_ctx = {
        "balance":          acct["equity"],
        "open_positions":   len(_positions),
        "sector_exposure":  _sector_exposure,
        "starting_balance": _starting_balance,
    }

    # Compute sector regime + rotation scores + Bayesian regime once per gate cycle
    from backend.scheduler import _get_regime_bayes
    from backend.sector_regime import compute_sector_regime, compute_ticker_signals
    from backend.sector_transitions import (
        compute_ticker_rotation_scores,
        get_rotation_forecast,
    )
    sector_regime       = compute_sector_regime()
    rotation_scores     = compute_ticker_rotation_scores()
    ticker_signals      = compute_ticker_signals()
    regime_bayes_result = _get_regime_bayes().last_result()

    sector_exposure_live: dict[str, float] = dict(_sector_exposure)
    dynamic_caps_live    = compute_dynamic_caps(cfg.get("max_sector_exposure", 0.30),
                                                regime_result=regime_bayes_result)
    # Lock 5 judges the cap execution enforces, not the flat base (2026-10-17).
    wallet_ctx["dynamic_caps"] = dynamic_caps_live

    # Pre-rotation promotion — same logic as demo gate
    forecast         = get_rotation_forecast()
    watching_sectors = {w["sector"] for w in forecast.get("watching", [])}
    if watching_sectors:
        pr_sector_thresholds = {
            s: (sector_thresholds or {}).get(s, cfg["lock1_threshold"]) * PRE_ROTATION_FLOOR
            for s in watching_sectors
        }
        pr_candidates = get_lock1_candidates(
            threshold=cfg["lock1_threshold"] * PRE_ROTATION_FLOOR,
            sector_thresholds=pr_sector_thresholds,
            stale_out=stale,
        )
        record_stale(cycle, stale, "Live gate runner", start=n_stale_main)
        existing = {c["ticker"] for c in candidates}
        for c in pr_candidates:
            if (c.get("sector") in watching_sectors
                    and c["ticker"] not in existing
                    and c["ticker"] not in open_tickers
                    and c["ticker"] not in failed_tickers
                    and c["ticker"] not in exited_tickers):
                c = dict(c)
                c["pre_rotation"] = True
                candidates.append(c)
                existing.add(c["ticker"])
        pr_count = sum(1 for c in candidates if c.get("pre_rotation"))
        if pr_count:
            logger.info(f"Live gate: +{pr_count} pre-rotation candidate(s) from {sorted(watching_sectors)}")

    # L1 affordability before the chain (Niclas 2026-10-28: "L1 måste kontrollera först att beloppet räcker …
    # Kan vi spara anrop så är det bra"): the largest notional any later size can reach is the available cash ×
    # max_position_size (Lock 5's size and the Bayesian scaling are capped by it), and a bracket order needs one
    # whole share of it, so a refusal here moves no order that could fill and spends no Grok, options or Claude call.
    max_notional = max(0.0, min(acct["equity"], acct["cash"])) * cfg["max_position_size"]
    pre_states: dict[str, dict] = {}
    affordable = []
    for c in candidates:
        checks = ({k: v for k, v in pre_checks.items() if k != "blocklist"} if c.get("pre_rotation")
                  else pre_checks)
        states = {**l0_states, **_pre_chain_states(c["ticker"], checks)}
        refusal = _affordability(states, max_notional, c.get("price"), min_notional=LIVE_MIN_ORDER, whole_shares=True)
        if refusal:
            logger.info(f"Live gate [{c['ticker']}]: {refusal} before the chain "
                        f"(largest notional ${max_notional:.2f}, price {c.get('price')!r})")
            _write_live(c, _pre_chain_result(c, states, refusal), ts, unknown)
        else:
            pre_states[c["ticker"]] = states
            affordable.append(c)
    candidates = affordable
    if not candidates:
        logger.info("Live gate runner: no candidate affordable this cycle")
        cycle.reason = "all_skipped"
        _persist_multiplier_stats({}, None, [], runner="live")
        return []

    evaluated: list[tuple[dict, dict]] = []

    with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(candidates))) as pool:
        future_to_signal = {
            pool.submit(_evaluate, signal, wallet_ctx, cfg, sector_regime, sector_thresholds,
                        rotation_scores, regime_bayes_result, ticker_signals): signal
            for signal in candidates
        }
        for future in as_completed(future_to_signal):
            signal = future_to_signal[future]
            try:
                result = future.result()
            except Exception as e:
                logger.error(f"Live gate [{signal['ticker']}]: evaluation raised — {e}")
                continue
            result["rule_states"] = {**pre_states[signal["ticker"]], **result.get("rule_states", {})}
            evaluated.append((signal, result))
    cycle.reason = evaluated_reason(len(candidates), len(evaluated))

    results = []
    # Execute trades serially to prevent race conditions on position limits
    open_positions = broker.get_positions()
    open_tickers   = {p["ticker"] for p in open_positions}

    evaluated.sort(key=lambda x: (0 if x[0].get("pre_rotation") else 1, x[0].get("signal_score", 0)), reverse=True)
    max_positions        = cfg["max_positions"]
    overflow_increment   = cfg.get("overflow_quant_increment", 0.05)
    open_count           = len(open_positions)  # snapshot; incremented on each executed trade
    bayesian_multipliers = _compute_bayesian_multipliers(evaluated, regime_bayes_result)

    for signal, result in evaluated:
        ticker   = signal["ticker"]
        order_id = None

        # L5 deferred path — run Claude only when a slot is confirmed available.
        if result["outcome"] == "TRADE_QUEUED_PENDING_L5":
            sector         = signal.get("sector", "")
            base_threshold = (sector_thresholds or {}).get(sector, cfg["lock1_threshold"])
            _put(result, "position_open", ticker in open_tickers)
            if ticker in open_tickers:
                logger.info(f"Live trade skipped [{ticker}]: position already open (pre-L5)")
                result["outcome"] = STOPPED
                result["outcome_reason"] = "position_open"
                result.pop("_l5_lock_results", None)
                result.pop("_l5_context", None)
            elif open_count >= max_positions:
                overflow_level     = open_count - max_positions + 1
                overflow_threshold = round(base_threshold * (1 + overflow_level * overflow_increment), 4)
                _overflow_state(result, signal["signal_score"] < overflow_threshold)
                if signal["signal_score"] < overflow_threshold:
                    logger.warning(
                        f"Live gate [{ticker}]: overflow slot {overflow_level} rejected pre-L5 — "
                        f"score {signal['signal_score']:.4f} below {overflow_threshold:.4f}"
                    )
                    result["outcome"] = STOPPED
                    result.pop("_l5_lock_results", None)
                    result.pop("_l5_context", None)
                else:
                    _run_l5_for_result(signal, result, cfg)
            else:
                _overflow_state(result, False)          # a free slot passes overflow
                _run_l5_for_result(signal, result, cfg)

        # Apply Bayesian sector sizing before notional is computed.
        if result["outcome"] == "TRADE_QUEUED" and result.get("lock3"):
            _scale = bayesian_multipliers.get(ticker, 1.0)
            if _scale != 1.0:
                _raw  = result["lock3"]["position_size_pct"]
                result["lock3"]["position_size_pct"] = min(
                    round(_raw * _scale, 4),
                    cfg.get("max_position_size", 0.10),
                )
                logger.info(
                    f"Live gate [{ticker}]: Bayesian size scale={_scale:.3f} "
                    f"({_raw:.4f} → {result['lock3']['position_size_pct']:.4f})"
                )

        if result["outcome"] == "TRADE_QUEUED":
            _put(result, "position_open", ticker in open_tickers)
            if ticker in open_tickers:
                logger.info(f"Live trade skipped [{ticker}]: position already open")
                result["outcome"] = STOPPED
                result["outcome_reason"] = "position_open"
            elif open_count >= max_positions:
                # Overflow slot — check escalating quant threshold
                sector             = signal.get("sector", "")
                base_threshold     = (sector_thresholds or {}).get(sector, cfg["lock1_threshold"])
                overflow_level     = open_count - max_positions + 1
                overflow_threshold = round(base_threshold * (1 + overflow_level * overflow_increment), 4)
                _overflow_state(result, signal["signal_score"] < overflow_threshold)
                if signal["signal_score"] < overflow_threshold:
                    logger.warning(
                        f"Live gate [{ticker}]: overflow slot {overflow_level} rejected — "
                        f"score {signal['signal_score']:.4f} below {overflow_threshold:.4f} "
                        f"(base {base_threshold:.4f} ×{1 + overflow_level * overflow_increment:.2f})"
                    )
                    result["outcome"] = STOPPED
                else:
                    position_pct    = result["lock3"]["position_size_pct"] if result.get("lock3") else cfg["max_position_size"]
                    available_cash  = max(0.0, min(acct["equity"], acct["cash"]))
                    notional        = available_cash * min(position_pct, cfg["max_position_size"])
                    refusal         = _order_refusal(result, ticker, notional, signal.get("price"))
                    if refusal:
                        result["outcome"] = STOPPED
                        result["outcome_reason"] = refusal
                    else:
                        sector    = signal.get("sector", "")
                        cap_check = _sector_cap_check(sector, notional, sector_exposure_live, dynamic_caps_live,
                                                      cfg.get("max_sector_exposure", 0.30), _starting_balance)
                        result["cap_check"] = cap_check
                        _put(result, "sector_cap", cap_check["over_cap"])
                        sec_cap, projected = cap_check["cap"], cap_check["projected"]
                        if cap_check["over_cap"]:
                            logger.warning(
                                f"Live trade rejected [{ticker}]: {sector} sector cap — "
                                f"projected {projected:.0%} > cap {sec_cap:.0%}"
                            )
                            result["outcome"] = STOPPED
                            result["outcome_reason"] = "sector_cap"
                        else:
                            try:
                                order_id = broker.place_bracket_order(
                                    ticker          = ticker,
                                    notional        = notional,
                                    current_price   = signal["price"],
                                    take_profit_pct = cfg["take_profit_pct"],
                                    stop_loss_pct   = cfg["stop_loss_pct"],
                                )
                                result["outcome"]       = "TRADE_EXECUTED"
                                result["overflow_slot"] = True
                                open_tickers.add(ticker)
                                open_count += 1
                                sector_exposure_live[sector] = projected
                                _record_live_trade(signal, notional, order_id, cfg)
                                _fire_trade_alert(ticker, signal, notional, order_id, cfg)
                                logger.info(
                                    f"Live gate [{ticker}]: overflow slot {overflow_level} executed — "
                                    f"score {signal['signal_score']:.4f} >= {overflow_threshold:.4f}"
                                )
                            except Exception as e:
                                logger.error(f"Live trade failed [{ticker}]: {e}")
                                result["outcome"] = STOPPED
                                result["outcome_reason"] = f"broker_error: {e}"[:300]
                                _put(result, "broker_error", True)
            else:
                _overflow_state(result, False)          # a free slot passes overflow
                position_pct   = result["lock3"]["position_size_pct"] if result.get("lock3") else cfg["max_position_size"]
                available_cash = max(0.0, min(acct["equity"], acct["cash"]))
                notional       = available_cash * min(position_pct, cfg["max_position_size"])
                refusal        = _order_refusal(result, ticker, notional, signal.get("price"))
                if refusal:
                    result["outcome"] = STOPPED
                    result["outcome_reason"] = refusal
                else:
                    sector    = signal.get("sector", "")
                    cap_check = _sector_cap_check(sector, notional, sector_exposure_live, dynamic_caps_live,
                                                  cfg.get("max_sector_exposure", 0.30), _starting_balance)
                    result["cap_check"] = cap_check
                    _put(result, "sector_cap", cap_check["over_cap"])
                    sec_cap, projected = cap_check["cap"], cap_check["projected"]
                    if cap_check["over_cap"]:
                        logger.warning(
                            f"Live trade rejected [{ticker}]: {sector} sector cap — "
                            f"projected {projected:.0%} > cap {sec_cap:.0%}"
                        )
                        result["outcome"] = STOPPED
                        result["outcome_reason"] = "sector_cap"
                    else:
                        try:
                            order_id = broker.place_bracket_order(
                                ticker          = ticker,
                                notional        = notional,
                                current_price   = signal["price"],
                                take_profit_pct = cfg["take_profit_pct"],
                                stop_loss_pct   = cfg["stop_loss_pct"],
                            )
                            result["outcome"] = "TRADE_EXECUTED"
                            open_tickers.add(ticker)
                            open_count += 1
                            sector_exposure_live[sector] = projected
                            _record_live_trade(signal, notional, order_id, cfg)
                            _fire_trade_alert(ticker, signal, notional, order_id, cfg)
                        except Exception as e:
                            logger.error(f"Live trade failed [{ticker}]: {e}")
                            result["outcome"] = STOPPED
                            result["outcome_reason"] = f"broker_error: {e}"[:300]
                            _put(result, "broker_error", True)

        _write_live(signal, result, ts, unknown, order_id)

        _log_summary(ticker, result)
        results.append(result)

    _alert_unknown("live", unknown)
    _persist_multiplier_stats(bayesian_multipliers, regime_bayes_result, evaluated, runner="live")
    return results


def _evaluate(signal: dict, wallet_ctx: dict, cfg: dict,
              sector_regime: dict | None = None,
              sector_thresholds: dict | None = None,
              rotation_scores: dict | None = None,
              regime_bayes_result=None,
              ticker_signals: dict | None = None) -> dict:
    ticker       = signal["ticker"]
    sector       = signal.get("sector", "")
    signal_score = signal["signal_score"]
    on_watchlist = signal.get("pre_rotation", False)

    signal = dict(signal)
    signal["ticker_signal"] = (ticker_signals or {}).get(ticker, {}).get("signal")

    context = build_base_context(
        signal, wallet_ctx, cfg,
        starting_balance=wallet_ctx["starting_balance"],
        gate_history_fn=get_live_ticker_gate_fails,
        sector_regime=sector_regime,
        rotation_scores=rotation_scores,
        regime_bayes_result=regime_bayes_result,
        mode_label="LIVE — real money",
    )
    chain   = evaluate_chain(ticker, sector, signal_score, context, cfg,
                             on_watchlist=on_watchlist, stop_after_lock=4,
                             sector_thresholds=sector_thresholds)
    result  = _chain_to_gate_result(signal, chain)
    # Stash L5 inputs so the serial executor can run L5 only when a slot is available.
    if chain.l5_pending:
        result["_l5_lock_results"] = chain.lock_results
        result["_l5_context"]      = context
    return result



def _put(result: dict, ident: str, refused: bool) -> None:
    """A live execution-phase check's verdict in the row's rule_states (lock_codes.put)."""
    lock_codes.put(result.setdefault("rule_states", {}), ident,
                   lock_codes.FAIL if refused else lock_codes.PASS)


def _order_refusal(result: dict, ticker: str, notional: float, price: float | None) -> str | None:
    """L1's checks at execution that the order can be bought, before the broker is called (gate_runner's
    _affordability: a price, at least LIVE_MIN_ORDER, one whole share; brokers/alpaca.py floors qty and raises on 0).
    Until 2026-10-29 a below-one-share notional reached the broker and was recorded as a broker error (LLY, 11 rows
    10-12..10-16; Niclas 2026-10-28: "L1 måste kontrollera först att beloppet räcker"). Returns the refusing rule's
    identifier, or None."""
    refusal = _affordability(result.setdefault("rule_states", {}), notional, price,
                             min_notional=LIVE_MIN_ORDER, whole_shares=True)
    if refusal:
        logger.warning(f"Live trade rejected [{ticker}]: {refusal} (notional ${notional:.2f}, price {price!r})")
    return refusal


def _write_live(signal: dict, result: dict, ts: str, unknown: list[dict], order_id: str | None = None) -> None:
    """Finalize a live row and write it to live_gate_history."""
    _finalize(result)
    if result["gate_decision"] == lock_codes.UNKNOWN:
        unknown.append(result)
    insert_live_gate_result({
        "cycle_started_at":       ts,
        "timestamp":              result["timestamp"],
        "ticker":                 signal["ticker"],
        "sector":                 signal.get("sector", ""),
        "signal_score":           signal["signal_score"],
        "lock1_pass":             result["lock1_pass"],
        "lock2_pass":             result["lock2_pass"],
        "lock_leading_pass":      result["lock_leading_pass"],
        "lock_leading_checks":    result.get("lock_leading_checks"),
        "lock3_pass":             result["lock3_pass"],
        "gate_decision":          result["gate_decision"],
        "stop_code":              result["stop_code"],
        "rule_states":            result["rule_states"],
        "lock3_reasoning":        result.get("claude_reasoning"),
        "alpaca_order_id":        order_id,
        "l2_summary":             result.get("l2_summary"),
        "lock3_sentiment_score":  result.get("lock3_sentiment_score"),
        "lock3_conviction":       result.get("lock3_conviction"),
        "macro_reason":           result.get("macro_reason"),
        "ticker_signal":          result.get("ticker_signal"),
        "earnings_near":          result.get("earnings_near"),
        "days_to_earnings":       result.get("days_to_earnings"),
        "overflow_slot":          result.get("overflow_slot", False),
        "outcome_reason":         result.get("outcome_reason"),
        "cap_check":              result.get("cap_check"),
        "signal_ts":              signal.get("timestamp"),
        "bar_date":               signal.get("bar_date"),
    })


def _sector_cap_check(sector: str, notional: float, exposure: dict[str, float],
                      dynamic_caps: dict[str, float], flat_cap: float,
                      starting_balance: float) -> dict:
    """The execution-side sector cap test, with the evidence persisted per row.

    One cap since 2026-10-17: Lock 5 is shown risk_limits.sector_cap, the same
    dynamic cap execution enforces here (falling back to the flat value when
    compute_dynamic_caps returned nothing for the sector). Until then L5 judged
    the flat max_sector_exposure while execution enforced the dynamic cap. Observe
    only (2026-09-27): `over_flat` records whether the flat cap would have
    rejected this trade, so rows where the two disagree are countable. The
    enforced test is `over_cap`, unchanged.
    """
    cap       = dynamic_caps.get(sector, flat_cap)
    before    = exposure.get(sector, 0.0)
    projected = round(before + notional / starting_balance, 3)
    return {
        "source":    "dynamic" if sector in dynamic_caps else "flat_fallback",
        "cap":       round(cap, 4),
        "flat":      round(flat_cap, 4),
        "before":    before,
        "projected": projected,
        "over_cap":  projected > cap,
        "over_flat": projected > flat_cap,
    }


def _record_live_trade(signal: dict, notional: float, order_id: str, cfg: dict) -> None:
    import sqlite3
    try:
        from datetime import datetime, timezone
        from backend.gate.freshness import signal_class
        price = signal["price"]
        qty   = int(notional / price)   # floor matches place_bracket_order integer fill
        now   = datetime.now(timezone.utc).isoformat()
        insert_live_trade({
            "timestamp":       now,
            "signal_class":    signal_class(signal.get("timestamp"), now),
            "ticker":          signal["ticker"],
            "sector":          signal.get("sector", ""),
            "alpaca_order_id": order_id,
            "entry_price":     price,   # the quote; the tracker replaces it with the fill
            "signal_price":    price,
            "qty":             qty,
            "notional":        round(notional, 2),
            "tp_price":        round(price * (1 + cfg["take_profit_pct"]), 2),
            "sl_price":        round(price * (1 - cfg["stop_loss_pct"]), 2),
        })
    except sqlite3.IntegrityError as e:
        # The held-ticker index refused a second open row (Finding 2): a run outside the lock placed an order.
        logger.critical(f"Duplicate live position refused [{signal['ticker']}]: order {order_id} was placed but "
                        f"the ticker already has a held row; check the broker and close one bracket ({e})")
    except Exception as e:
        logger.warning(f"Failed to record live trade [{signal['ticker']}]: {e}")


def _fire_trade_alert(ticker: str, signal: dict, notional: float, order_id: str, cfg: dict) -> None:
    try:
        from backend.alerts import alert_trade_executed
        price = signal["price"]
        alert_trade_executed(
            ticker    = ticker,
            sector    = signal.get("sector", ""),
            notional  = notional,
            price     = price,
            tp        = round(price * (1 + cfg["take_profit_pct"]), 2),
            sl        = round(price * (1 - cfg["stop_loss_pct"]), 2),
            order_id  = order_id,
        )
    except Exception as e:
        logger.warning(f"Alert dispatch failed [{ticker}]: {e}")


def _mark(passed: bool | None) -> str:
    return "?" if passed is None else "✓" if passed else "✗"


def _log_summary(ticker: str, result: dict) -> None:
    l1        = result["lock1"]
    l2        = result.get("lock2")
    l_leading = result.get("lock_leading")
    l3        = result.get("lock3")
    outcome   = result["outcome"]

    parts = [f"L1={'✓' if l1['passed'] else '✗'}({l1['score']:.3f})"]
    if l2:
        parts.append(f"L2={'✓' if l2['passed'] else '✗'}({l2.get('score', '?')})")
    if l_leading:
        parts.append(f"LD={'✓' if l_leading['passed'] else '✗'}(price {_mark(l_leading.get('price_group_pass'))} "
                     f"options {_mark(l_leading.get('options_group_pass'))})")
    if l3:
        parts.append(f"L3={'✓' if l3['passed'] else '✗'}({l3.get('decision')} {l3.get('confidence', 0):.2f})")

    logger.info(f"Live gate [{ticker}]: {' → '.join(parts)} → {outcome}")
