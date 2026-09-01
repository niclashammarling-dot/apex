"""
Live trades tracker — polls Alpaca bracket order legs for fills
and closes live_trades records with real P&L.

Also enforces a time-stop: if a position has been open longer than
max_hold_days (from live_config.json), it cancels the bracket order
and closes the position at market.

Called by the scheduler every EXIT_CHECK_INTERVAL minutes during market hours.
Only runs when LIVE_ENABLED=true.
"""
from datetime import datetime, timezone

import yfinance as yf
from alpaca.trading.enums import OrderStatus
from loguru import logger

from backend.brokers.alpaca import OrderTerminalError
from backend.config import LIVE_ENABLED
from backend.db import (
    close_live_trade,
    get_open_live_trades,
    mark_live_trade_unreconciled,
    set_live_trade_profit_lock_activated,
    update_live_trade_peak_price,
)

# Membership tests against Alpaca's real OrderStatus enum values (imported, not
# hand-typed) — a typo here (e.g. OrderStatus.CANCELLED) raises AttributeError
# at import time instead of silently compiling into a substring check that
# never matches. Pending = order could still receive a fill. Terminal = it
# can't; anything reaching the position-reconciliation branch with a terminal
# or unrecognized status should proceed to reconciliation, not wait forever.
_PENDING_ORDER_STATUSES = {
    OrderStatus.NEW.value,
    OrderStatus.PARTIALLY_FILLED.value,
    OrderStatus.ACCEPTED.value,
    OrderStatus.PENDING_NEW.value,
    OrderStatus.PENDING_CANCEL.value,
    OrderStatus.PENDING_REPLACE.value,
    OrderStatus.PENDING_REVIEW.value,
    OrderStatus.ACCEPTED_FOR_BIDDING.value,
    OrderStatus.HELD.value,
    OrderStatus.STOPPED.value,
    OrderStatus.CALCULATED.value,
}
_TERMINAL_ORDER_STATUSES = {
    OrderStatus.FILLED.value,
    OrderStatus.CANCELED.value,
    OrderStatus.EXPIRED.value,
    OrderStatus.REPLACED.value,
    OrderStatus.DONE_FOR_DAY.value,
    OrderStatus.REJECTED.value,
}


def _trading_days_since(iso_timestamp: str) -> int:
    """Return the number of weekdays (trading days) elapsed since iso_timestamp."""
    from datetime import date

    import pandas as pd
    start = datetime.fromisoformat(iso_timestamp).date()
    today = date.today()
    return len(pd.bdate_range(start, today)) - 1


def _sector_avg_score(sector: str) -> float | None:
    """Most recent avg_score for the sector from sector_snapshots."""
    from backend.db import get_db
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT avg_score FROM sector_snapshots WHERE sector=? ORDER BY timestamp DESC LIMIT 1",
            (sector,),
        ).fetchone()
        return row["avg_score"] if row else None
    finally:
        conn.close()


def _ticker_consecutive_down_days(ticker: str, n: int) -> bool:
    """Return True if ticker has closed down for n consecutive trading days."""
    try:
        hist = yf.Ticker(ticker).history(period=f"{n + 5}d")
        if hist.empty or len(hist) < n + 1:
            return False
        returns = hist["Close"].pct_change().dropna().values
        return len(returns) >= n and all(r < 0 for r in returns[-n:])
    except Exception:
        return False



def _maybe_ratchet_bracket_sl(trade: dict, peak: float, cfg: dict, broker) -> None:
    """
    When peak gain >= profit_lock_trigger_pct, trail the bracket SL up to
    peak * (1 - profit_lock_trail_pct) on every cycle.

    Gates (cheapest first — network call deferred until gate 3 passes):
    1. Config guard: skip if trigger_pct or lock_trail missing from config.
    2. Territory: skip if peak gain < trigger_pct.
    3. Leg fetch: find the open STOP leg on the bracket; warn and skip if absent.
    4. Direction: skip if the computed new_sl would not move the stop upward.
       This is the idempotency gate — no broker call when SL is already current.
    5. Execute: PATCH the leg via replace_stop_leg; set profit_lock_activated on
       first confirmed success (used for audit/display only, not as a gate).
    """
    # Gate 1 — config guard
    trigger_pct = cfg.get("profit_lock_trigger_pct")
    lock_trail  = cfg.get("profit_lock_trail_pct")
    if not trigger_pct or not lock_trail:
        return

    # Gate 2 — territory check (no network call before this passes)
    entry_price = trade["entry_price"]
    if (peak - entry_price) / entry_price < trigger_pct:
        return

    # Gate 4 — fetch bracket legs, find the open STOP leg
    ticker    = trade["ticker"]
    trade_id  = trade["id"]
    parent_id = trade["alpaca_order_id"]
    try:
        order = broker.get_order_by_id(parent_id)
    except Exception as e:
        logger.warning(
            f"Profit-lock ratchet [{ticker}] trade_id={trade_id} parent={parent_id}: "
            f"could not fetch bracket order — {e}"
        )
        return

    # Same defect family as commit 49dfe30: exact-match against a hand-typed
    # tuple containing "cancelled" (never a real Alpaca status) instead of
    # membership against the real vocabulary. Reuses the module-level set so
    # there's exactly one place that enumerates "terminal" going forward.
    sl_leg = next(
        (
            leg for leg in (order.get("legs") or [])
            if "stop" in (leg.get("order_type") or "").lower()
            and leg.get("status") not in _TERMINAL_ORDER_STATUSES
        ),
        None,
    )
    if sl_leg is None:
        logger.warning(
            f"Profit-lock ratchet [{ticker}] trade_id={trade_id} parent={parent_id}: "
            f"no open STOP leg found — position may be exiting or bracket partially filled"
        )
        return

    # Gate 4 (direction) — ratchet only moves stop upward; no broker call if already current
    current_sl = sl_leg.get("stop_price") or 0.0
    new_sl     = round(peak * (1 - lock_trail), 2)
    if new_sl <= current_sl:
        return

    # Gate 5 — execute; set profit_lock_activated on first move (audit/display only)
    leg_id = sl_leg["id"]
    qty    = int(trade["qty"])
    try:
        broker.replace_stop_leg(leg_id, new_sl, qty, ticker)
    except OrderTerminalError as e:
        # Leg filled or cancelled between our fetch and the PATCH — exit check
        # will record the close on this cycle; nothing to ratchet.
        logger.debug(
            f"Profit-lock ratchet [{ticker}] trade_id={trade_id}: "
            f"leg already terminal — exit check will reconcile ({e})"
        )
        return
    except Exception as e:
        logger.error(
            f"Profit-lock ratchet [{ticker}] trade_id={trade_id} parent={parent_id}: "
            f"replace_stop_leg failed — {e}; will retry next cycle"
        )
        return

    set_live_trade_profit_lock_activated(trade_id)
    logger.info(
        f"Profit-lock ratchet [{ticker}] trade_id={trade_id}: "
        f"SL moved ${current_sl:.2f} → ${new_sl:.2f} "
        f"(peak={peak:.2f} trigger={trigger_pct:.1%} trail={lock_trail:.1%})"
    )


def _log_vol_slope(trade: dict) -> None:
    """
    For positions held 3+ trading days, log trailing vol_score slope and hold_days.
    Prospective data collection only — no gate logic. Co-located here so the signal
    and any future action point are in the same function.
    Log both slope and hold_days so duration-correlation can be tested retrospectively.
    """
    hold_days = _trading_days_since(trade["timestamp"])
    if hold_days < 3:
        return
    ticker = trade["ticker"]
    from backend.db import get_db
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT DATE(timestamp), AVG(volume_score)
                FROM signals
                WHERE ticker = ? AND timestamp >= ?
                GROUP BY DATE(timestamp)
                ORDER BY DATE(timestamp)
            """, (ticker, trade["timestamp"]))
            path = [(d, v) for d, v in cur.fetchall() if v is not None]
    except Exception as e:
        logger.debug(f"Vol slope [{ticker}]: db fetch failed — {e}")
        return
    if len(path) < 3:
        return
    vols = [v for _, v in path]
    slope = (vols[-1] - vols[0]) / (len(vols) - 1)
    logger.info(
        f"Vol slope [{ticker}] trade_id={trade['id']}: "
        f"hold_days={hold_days} slope={slope:+.4f}/day "
        f"vol_path={[round(v, 3) for v in vols]}"
    )


def _check_untracked_positions(alpaca_positions: set[str], open_trades: list[dict]) -> None:
    """
    Mirror of the reconciliation fallback: a ticker held at the broker with no
    matching internal OPEN row. Covers a wiped position quietly reappearing
    (the live theory for the 2026-08-06 HON incident), a manual dashboard
    trade, or partial-fill drift. One alert per ticker while the mismatch
    persists — re-alerts if it clears and recurs.

    Dedup is a DB-backed latch (backend.db.set_alert_latch), not a module-level
    set: 2026-08-07 confirmed a plain in-memory set gets cleared by any process
    restart (a deploy, uvicorn --reload), which re-sent an already-latched
    alert on every restart during a live incident rather than staying silent.
    """
    from backend.alerts import alert_position_untracked
    from backend.db import clear_alert_latches_except, set_alert_latch
    open_tickers = {t["ticker"] for t in open_trades}
    untracked = alpaca_positions - open_tickers
    for ticker in untracked:
        if set_alert_latch(f"untracked:{ticker}"):
            logger.error(f"Live exit check [{ticker}]: held at broker with no matching OPEN row")
            alert_position_untracked(ticker)
    # Clear the latch for tickers no longer mismatched, so a future recurrence re-alerts.
    clear_alert_latches_except("untracked:", {f"untracked:{t}" for t in untracked})


def check_live_exits() -> list[dict]:
    """
    For each open live trade:
    1. Check Alpaca for filled TP/SL bracket legs.
    2. Updates peak_price in DB each run; profit-lock ratchet moves bracket SL upward.
    3. If held longer than max_hold_days, cancel the bracket and close at market.
    4. If position no longer exists in Alpaca, find the most recent filled sell
       order for that ticker and record the exit (covers manual closes and TP/SL
       fills on positions whose bracket legs are no longer visible).
    Returns list of closed trade dicts.
    """
    if not LIVE_ENABLED:
        return []

    open_trades = get_open_live_trades()

    from backend.brokers import alpaca as broker
    from backend.live_config import get_live_config
    cfg           = get_live_config()
    max_hold_days = cfg["max_hold_days"]

    # Snapshot of tickers currently held in Alpaca — used for the reconciliation
    # fallback below AND the untracked-position check (the mirror direction:
    # broker holds a ticker with no matching internal OPEN row — a position
    # restored after a wipe, a manual dashboard trade, partial-fill drift).
    # Computed even when open_trades is empty, so a restored/untracked position
    # is still caught on a cycle where nothing else needs polling.
    try:
        alpaca_positions = {p["ticker"] for p in broker.get_positions()}
    except Exception as e:
        logger.warning(f"Live exit check: could not fetch Alpaca positions — skipping reconciliation: {e}")
        alpaca_positions = None

    if alpaca_positions is not None:
        try:
            _check_untracked_positions(alpaca_positions, open_trades)
        except Exception as e:
            logger.warning(f"Live exit check: untracked-position check failed — {e}")

    if not open_trades:
        return []

    closed = []

    for trade in open_trades:
        ticker      = trade["ticker"]
        order_id    = trade["alpaca_order_id"]
        entry_price = trade["entry_price"]

        try:
            order = broker.get_order_by_id(order_id)
        except Exception as e:
            logger.warning(f"Live exit check [{ticker}]: could not fetch order {order_id} — {e}")
            order = None

        # ── Peak price tracking ─────────────────────────────────────────────
        current = _current_price(ticker)
        if current is not None:
            peak = trade.get("peak_price") or entry_price
            if current > peak:
                peak = current
                update_live_trade_peak_price(trade["id"], peak)
        else:
            peak = trade.get("peak_price") or entry_price

        # ── Profit-lock ratchet ─────────────────────────────────────────────
        _maybe_ratchet_bracket_sl(trade, peak, cfg, broker)

        # ── Vol slope observation (prospective data collection) ──────────────
        _log_vol_slope(trade)

        # ── Check TP/SL bracket legs ────────────────────────────────────────
        filled_leg = _find_filled_sell_leg(order) if order else None
        if filled_leg:
            exit_price  = filled_leg["filled_avg_price"]
            pnl         = round((exit_price - entry_price) * trade["qty"], 2)
            exit_reason = _leg_reason(filled_leg, entry_price)
            outcome     = "WIN" if pnl > 0 else "LOSS"
            exited_at   = filled_leg["filled_at"] or datetime.now(timezone.utc).isoformat()

        # ── Time-stop ───────────────────────────────────────────────────────
        elif _trading_days_since(trade["timestamp"]) >= max_hold_days:
            logger.info(f"Live time-stop [{ticker}]: {max_hold_days} trading days elapsed — closing")
            try:
                result = broker.close_position(ticker)
                exit_price = float(result.get("filled_avg_price") or current or entry_price)
            except Exception as e:
                if "position not found" in str(e).lower() or "40410000" in str(e):
                    # alpaca_positions is a snapshot taken at the top of this
                    # cycle, before this trade's close_position() call — if it
                    # showed the ticker present, this is two broker reads
                    # disagreeing on the same symbol within one cycle, not
                    # ordinary order-history lag. Named explicitly (2026-08-23)
                    # because it's stronger evidence of a genuine fault than
                    # either read alone, and had no distinct signal before.
                    contradiction = alpaca_positions is not None and ticker in alpaca_positions
                    if contradiction:
                        logger.error(f"Live time-stop [{ticker}]: CONTRADICTION — this cycle's "
                                      f"positions snapshot shows {ticker} held, but close_position() "
                                      f"just reported it not found")
                    else:
                        logger.warning(f"Live time-stop [{ticker}]: position not found on broker — reconciling")
                    exit_price, exit_reason, exited_at, evidence = \
                        _find_exit_from_orders(ticker, broker, trade["timestamp"])

                    # A contradiction is never resolved by a third feed
                    # silently (2026-08-23) — even a corroborating fill from
                    # activities would book an exit while the position may
                    # still sit in the broker's own current-positions view,
                    # running the HON shape in the opposite direction (APEX
                    # goes flat in the DB while the broker still reports the
                    # row held). Two broker feeds directly disagreeing about
                    # whether a position exists is a human question, not
                    # something _find_exit_from_orders' evidence — however
                    # strong — gets to settle on its own.
                    if contradiction:
                        note = (
                            f"CONTRADICTION: this cycle's positions snapshot showed {ticker} held, "
                            f"but close_position() reported it not found at "
                            f"{datetime.now(timezone.utc).isoformat()}. "
                            f"_find_exit_from_orders evidence={evidence}"
                            + (f", candidate exit=${exit_price:.2f} at {exited_at} "
                               f"(NOT booked — contradiction overrides corroboration)"
                               if exit_price is not None else ", no candidate fill either")
                            + f". entry ${trade['entry_price']:.2f} x {trade['qty']:g} on order {order_id}. "
                            "Two broker reads disagree about whether this position exists — resolve manually."
                        )
                        logger.error(f"Live time-stop [{ticker}]: UNRECONCILED (contradiction) — {note}")
                        mark_live_trade_unreconciled(trade["id"], note)
                        try:
                            from backend.alerts import alert_position_unreconciled
                            alert_position_unreconciled(ticker, trade["entry_price"], trade["qty"], note)
                        except Exception as _ae:
                            logger.warning(f"Live time-stop [{ticker}]: alert failed — {_ae}")
                        continue

                    if evidence == "exit_in_progress":
                        # Positive evidence the exit is actually happening,
                        # not evidence of nothing — freezing the whole
                        # account over an order doing exactly what it should
                        # is the freeze-isn't-free cost paid for no reason.
                        # Bounded retry: leave the trade OPEN, no alert, the
                        # completing fill row should show up next cycle.
                        logger.info(f"Live time-stop [{ticker}]: sell order still filling per "
                                    f"account-activities — retrying next cycle, not freezing")
                        continue

                    if exit_price is None:
                        # 2026-08-23: this used to warn and continue, retrying
                        # silently next cycle — the weaker response of the two
                        # call sites despite having the stronger evidence (APEX
                        # itself just attempted a destructive close against a
                        # position of uncertain state). Site 2's freeze/alert
                        # is symmetric with this outcome.
                        note = (
                            f"time-stop close_position() reported {ticker} not found at "
                            f"{datetime.now(timezone.utc).isoformat()}; {_evidence_clause(evidence)} "
                            f"(entry ${trade['entry_price']:.2f} x {trade['qty']:g} on order {order_id})"
                        )
                        logger.error(f"Live time-stop [{ticker}]: UNRECONCILED — {note}")
                        mark_live_trade_unreconciled(trade["id"], note)
                        try:
                            from backend.alerts import alert_position_unreconciled
                            alert_position_unreconciled(ticker, trade["entry_price"], trade["qty"], note)
                        except Exception as _ae:
                            logger.warning(f"Live time-stop [{ticker}]: alert failed — {_ae}")
                        continue
                    pnl     = round((exit_price - entry_price) * trade["qty"], 2)
                    outcome = "WIN" if pnl > 0 else "LOSS"
                    logger.info(f"Live exit reconciliation [{ticker}]: time-stop found position gone externally, exit=${exit_price:.2f}")
                    close_live_trade(
                        trade_id    = trade["id"],
                        exit_price  = exit_price,
                        pnl         = pnl,
                        outcome     = outcome,
                        exit_reason = exit_reason,
                        exited_at   = exited_at,
                    )
                    logger.info(
                        f"Live exit [{ticker}]: {exit_reason} "
                        f"entry=${trade['entry_price']:.2f} exit=${exit_price:.2f} "
                        f"pnl={pnl:+.2f} → {outcome}"
                    )
                    closed.append({"ticker": ticker, "outcome": outcome, "exit_reason": exit_reason, "pnl": pnl})
                    continue
                logger.warning(f"Live time-stop [{ticker}]: close_position failed — {e}")
                continue
            exit_reason = "TIME"
            pnl         = round((exit_price - entry_price) * trade["qty"], 2)
            outcome     = "WIN" if pnl > 0 else "LOSS"
            exited_at   = datetime.now(timezone.utc).isoformat()

        # ── Position-reconciliation fallback ─────────────────────────────────
        # Position gone from Alpaca but our DB still says OPEN. Covers:
        #   - Manual closes via Alpaca UI or external tooling
        #   - TP/SL fills on brackets placed with DAY TIF (legs expired; fill
        #     is not visible on the original order but position is gone)
        elif alpaca_positions is not None and ticker not in alpaca_positions:
            # Parent order buy leg not yet filled (e.g. placed on a market holiday,
            # pending next session). Position will appear once the order fills.
            # Membership test against Alpaca's real order-status vocabulary — not
            # a substring check on a hand-typed word. A substring check on
            # "cancelled" (never a real Alpaca status; they use "canceled")
            # against a get_order_by_id() status is what let a canceled parent
            # order be silently misread as "still pending" forever in the
            # 2026-08-06 HON incident's diagnosis. An unrecognized status is
            # logged, not silently sorted into either bucket.
            status = (order.get("status") if order else None) or ""
            if (order is not None
                    and (order.get("filled_qty") or 0) == 0
                    and status in _PENDING_ORDER_STATUSES):
                logger.debug(f"Live exit check [{ticker}]: buy order pending (status={status}) — waiting for fill")
                continue
            if order is not None and status and status not in _TERMINAL_ORDER_STATUSES \
                    and status not in _PENDING_ORDER_STATUSES:
                logger.warning(f"Live exit check [{ticker}]: unrecognized order status {status!r} "
                                f"— treating as terminal (proceeding to reconciliation) rather than "
                                f"silently waiting on it")
            # Corroborate before cancelling anything. get_positions() reporting
            # a ticker absent is a single unverified read — the same class of
            # input that produced the fabricated -$4.04 exit (2026-08-04) and,
            # uncorroborated, would later cancel a live protective order on a
            # genuinely-held position twice (2026-08-04, 2026-08-11 — see
            # cancel_orphan_brackets and the HON incident cluster). A filled
            # sell order in get_orders() is independent, positive evidence the
            # position actually closed; only then is cancelling the remaining
            # bracket leg safe (it prevents a double-exit, not an orphan-risk
            # cancellation on a position we still hold). No fill found means
            # this is a suspected bad read, not a confirmed close — leave the
            # resting order in place (harmless: it can't fill against nothing)
            # and fall through to the freeze/alert below instead of acting.
            exit_price, exit_reason, exited_at, evidence = _find_exit_from_orders(ticker, broker, trade["timestamp"])
            if exit_price is not None:
                try:
                    cancelled = broker.cancel_open_orders(ticker)
                    if cancelled:
                        logger.info(f"Live exit reconciliation [{ticker}]: cancelled {cancelled} bracket order(s) "
                                    f"remaining after confirmed exit")
                except Exception as _ce:
                    logger.warning(f"Live exit reconciliation [{ticker}]: could not cancel orders — {_ce}")
            if exit_price is None and evidence == "exit_in_progress":
                # Positive evidence the exit is actually happening, not
                # evidence of nothing — see the time-stop path's identical
                # branch above. Bounded retry, no freeze, no alert.
                logger.info(f"Live exit reconciliation [{ticker}]: sell order still filling per "
                            f"account-activities — retrying next cycle, not freezing")
                continue
            if exit_price is None:
                # Position gone from the broker but no fill, order, or account
                # activity anywhere explains it (order history exhausted, bracket
                # leg not visible, or the position value simply vanished — see
                # 2026-08-06 HON incident: position gone, zero transaction trail
                # on either side of the ledger, real equity loss, not a mis-book).
                # Do NOT fabricate an exit at current market price — that silently
                # mislabels an unexplained loss as a small approximate one. Freeze
                # the trade as UNRECONCILED and alert; a human must resolve it via
                # broker support/dashboard before this trade record is touched again.
                note = (
                    f"position gone from broker at {datetime.now(timezone.utc).isoformat()}; "
                    f"{_evidence_clause(evidence)} (entry ${trade['entry_price']:.2f} "
                    f"x {trade['qty']:g} on order {order_id})"
                )
                logger.error(f"Live exit reconciliation [{ticker}]: UNRECONCILED — {note}")
                mark_live_trade_unreconciled(trade["id"], note)
                try:
                    from backend.alerts import alert_position_unreconciled
                    alert_position_unreconciled(ticker, trade["entry_price"], trade["qty"], note)
                except Exception as _ae:
                    logger.warning(f"Live exit reconciliation [{ticker}]: alert failed — {_ae}")
                continue
            logger.info(f"Live exit reconciliation [{ticker}]: position closed externally, exit=${exit_price:.2f}")
            pnl     = round((exit_price - trade["entry_price"]) * trade["qty"], 2)
            outcome = "WIN" if pnl > 0 else "LOSS"

        else:
            continue

        close_live_trade(
            trade_id    = trade["id"],
            exit_price  = exit_price,
            pnl         = pnl,
            outcome     = outcome,
            exit_reason = exit_reason,
            exited_at   = exited_at,
        )

        logger.info(
            f"Live exit [{ticker}]: {exit_reason} "
            f"entry=${trade['entry_price']:.2f} exit=${exit_price:.2f} "
            f"pnl={pnl:+.2f} → {outcome}"
        )
        closed.append({
            "ticker":      ticker,
            "outcome":     outcome,
            "exit_reason": exit_reason,
            "pnl":         pnl,
        })

    return closed


def check_live_regime_exits() -> list[dict]:
    """
    Exit open live positions whose sector has dropped out of the leaderboard
    AND the ticker has closed down for TICKER_RECOVERY_DAYS consecutive trading days.

    Mirrors wallet.check_regime_exits() for live trades.
    Cancels the Alpaca bracket order and closes at market before recording the exit.
    """
    if not LIVE_ENABLED:
        return []

    from datetime import date

    import pandas as pd
    from backend.regime.regime_bayes import TICKER_RECOVERY_DAYS
    from backend.scheduler import _get_regime_bayes

    rb = _get_regime_bayes()
    if rb is None:
        return []

    result = rb.last_result()
    if result is None:
        return []

    try:
        result_date = date.fromisoformat(result.date)
        if len(pd.bdate_range(result_date, date.today())) - 1 > 1:
            logger.debug(f"Live regime exit check: result is stale ({result.date}) — skipping")
            return []
    except ValueError:
        return []

    leaderboard_sectors: set[str] = {e.sector for e in result.leaderboard}

    open_trades = get_open_live_trades()
    if not open_trades:
        return []

    flagged = [
        t for t in open_trades
        if t["sector"] not in leaderboard_sectors
        and _ticker_consecutive_down_days(t["ticker"], TICKER_RECOVERY_DAYS)
    ]
    if not flagged:
        return []

    from backend.brokers import alpaca as broker
    closed = []

    for trade in flagged:
        ticker = trade["ticker"]
        sector = trade["sector"]

        price_now = _current_price(ticker)
        try:
            result_close = broker.close_position(ticker)
            exit_price   = float(result_close.get("filled_avg_price") or price_now or trade["entry_price"])
        except Exception as e:
            logger.warning(f"Live regime exit [{ticker}]: close_position failed — {e}")
            continue

        pnl     = round((exit_price - trade["entry_price"]) * trade["qty"], 2)
        outcome = "WIN" if pnl > 0 else "LOSS"
        pnl_pct = (exit_price - trade["entry_price"]) / trade["entry_price"]

        close_live_trade(
            trade_id    = trade["id"],
            exit_price  = exit_price,
            pnl         = pnl,
            outcome     = outcome,
            exit_reason = "REGIME",
            exited_at   = datetime.now(timezone.utc).isoformat(),
        )

        logger.info(
            f"Live regime exit [{ticker}] sector={sector} "
            f"off leaderboard + {TICKER_RECOVERY_DAYS}d down | "
            f"entry=${trade['entry_price']:.2f} exit=${exit_price:.2f} pnl={pnl:+.2f} ({pnl_pct:+.1%})"
        )
        closed.append({
            "ticker":           ticker,
            "sector":           sector,
            "outcome":          outcome,
            "exit_reason":      "REGIME",
            "entry_price":      trade["entry_price"],
            "exit_price":       exit_price,
            "notional":         trade["notional"],
            "held_days":        _trading_days_since(trade["timestamp"]),
            "sector_avg_score": _sector_avg_score(sector),
            "pnl":              pnl,
            "pnl_pct":          pnl_pct,
        })

    if closed:
        from backend.alerts import alert_regime_exits
        alert_regime_exits(closed, mode="LIVE")

    return closed


def cancel_orphan_brackets() -> int:
    """
    Scan all open sell orders in Alpaca and cancel any whose ticker has no
    open position AND has a filled sell order corroborating the position
    genuinely closed. Prevents bracket legs from creating short exposure
    after a position is closed externally (margin call, manual close, etc.).

    Position-absence-from-get_positions() alone is not treated as sufficient
    evidence — it's a single unverified read, the same input class that
    (uncorroborated) cancelled a live protective order on a still-held HON
    position twice (2026-08-04, 2026-08-11 — see the HON incident cluster).
    A resting sell order left in place on a genuinely-closed position is
    harmless (nothing to fill against); cancelling the stop on a position
    still actually held is real exposure. The asymmetry means the corrobo-
    ration check only needs to gate the cancel, not the leave-alone.

    Returns number of orders cancelled.
    """
    if not LIVE_ENABLED:
        return 0
    from backend.brokers import alpaca as broker
    try:
        positions  = {p["ticker"] for p in broker.get_positions()}
        all_orders = broker.get_orders(limit=200, nested=True)
    except Exception as e:
        logger.warning(f"cancel_orphan_brackets: could not fetch broker state — {e}")
        return 0

    # Flatten to include bracket legs
    flat: list[dict] = []
    for o in all_orders:
        flat.append(o)
        for leg in (o.get("legs") or []):
            flat.append({**leg, "ticker": o["ticker"]})

    filled_sell_tickers = {
        o["ticker"] for o in flat
        if "sell" in (o.get("side") or "").lower()
        and (o.get("filled_price") or o.get("filled_avg_price"))
    }

    cancelled = 0
    seen: set[str] = set()
    for o in flat:
        ticker = o.get("ticker")
        status = (o.get("status") or "").lower()
        side   = (o.get("side") or "").lower()
        if (ticker
                and ticker not in positions
                and "sell" in side
                and status in {"new", "held", "accepted", "pending_new", "partially_filled"}
                and ticker not in seen):
            seen.add(ticker)
            if ticker not in filled_sell_tickers:
                logger.warning(f"cancel_orphan_brackets [{ticker}]: absent from positions but no filled "
                                f"sell corroborates a genuine close — suspected bad broker read, leaving "
                                f"resting order in place (not cancelling)")
                continue
            try:
                n = broker.cancel_open_orders(ticker)
                if n:
                    logger.warning(f"cancel_orphan_brackets [{ticker}]: cancelled {n} orphaned sell order(s) "
                                    f"(no open position, confirmed closed via filled sell)")
                    cancelled += n
            except Exception as e:
                logger.warning(f"cancel_orphan_brackets [{ticker}]: cancel failed — {e}")

    return cancelled


def _evidence_clause(evidence: str) -> str:
    """
    Human-readable clause for an UNRECONCILED note's evidence field —
    2026-08-23. "Confirmed absent" and "couldn't check" both freeze the
    trade but are not the same fact, and whoever triages this cold (the
    time-stop path has never fired in production as of this writing —
    max_hold_days=25 against a 33-trading-day-old account — so its first
    real firing may be weeks out, long after this session is forgotten)
    needs the distinction spelled out in the note itself, not re-derived
    from the code.
    """
    return {
        "both_feeds_empty":       "CONFIRMED ABSENT — both the orders feed and the account-activities "
                                   "feed were read successfully and neither shows a corroborating fill",
        "orders_unavailable":     "UNVERIFIED — the orders feed itself could not be read; "
                                   "account-activities was never reached to check either",
        "activities_unavailable": "PARTIALLY VERIFIED — the orders feed was empty but account-activities "
                                   "could not be read to corroborate that; treat as unconfirmed, not absent",
        "exit_in_progress":       "EXIT IN PROGRESS, not absent — a sell order for this ticker is actively "
                                   "filling per account-activities (leaves_qty > 0); this should not have "
                                   "reached a freeze path at all — see the calling code",
    }.get(evidence, evidence)


def _find_filled_sell_leg(order: dict) -> dict | None:
    """Return the first filled sell leg from a bracket order, or None."""
    for leg in order.get("legs") or []:
        if "sell" in (leg.get("side") or "") and "filled" in (leg.get("status") or ""):
            if leg.get("filled_avg_price"):
                return leg
    return None


def _find_exit_from_orders(ticker: str, broker, entry_timestamp: str) -> tuple[float | None, str, str, str]:
    """
    Search recent Alpaca orders, then /account/activities, for the most
    recent filled sell for ticker. Returns
    (exit_price, exit_reason, exited_at, evidence).

    `evidence` names which state produced the result, because a None here
    is not one thing (2026-08-23 — the "empty/failed read indistinguishable
    from a genuine zero" class, re-identified inside this function's own
    degradation path once its None started triggering a freeze):
      - "found"                — a fill cleared both invariants; exit_price
        is set.
      - "both_feeds_empty"     — orders AND activities were both read
        successfully and neither had a usable fill. Two independent
        confirmations of absence — the strongest form of "no fill."
      - "orders_unavailable"   — get_orders() itself failed; nothing
        checked at all.
      - "activities_unavailable" — orders was empty but the activities
        corroboration call failed; only one feed was actually checked.
      - "exit_in_progress"     — activities shows a sell order for this
        ticker actively filling (leaves_qty > 0 on its latest row) but not
        yet complete. Positive evidence the position is genuinely closing,
        not evidence of nothing — callers must NOT freeze on this the way
        they freeze on the other None states; bounded retry next cycle
        instead (2026-08-23: freezing the whole account over an order
        doing exactly what it should is the freeze-isn't-free cost paid
        for no reason).
    Callers must not treat "orders_unavailable"/"activities_unavailable"
    the same as "both_feeds_empty" in an alert or note — "couldn't verify"
    and "confirmed absent" call for different confidence language even
    though both freeze the trade.

    Two invariants added 2026-08-18 after the LLY incident (row 25, entered
    2026-06-26, silently assigned row 8's real exit — same ticker, a fill
    from a *different* trade closed 2026-05-28, a month before row 25's own
    entry — because this function matched on ticker alone with no check that
    the fill postdated the trade it was being attached to, or that it hadn't
    already been consumed by an earlier-closed trade for the same ticker),
    applied identically to both feeds:

    1. The fill must be timestamped after this trade's own entry. A fill that
       predates entry cannot possibly be this trade's exit, no matter how
       plausible the price looks.
    2. The fill must not already be recorded as another live_trades row's
       exit (same exit_price + exited_at pair) — Alpaca's paper-account order
       history can be thin enough to return a stale fill already consumed by
       a prior trade for the same ticker.
    """
    try:
        orders = broker.get_orders(limit=100, nested=True)
    except Exception as e:
        logger.warning(f"_find_exit_from_orders [{ticker}]: get_orders failed — {e}")
        return None, "MANUAL", datetime.now(timezone.utc).isoformat(), "orders_unavailable"

    # Flatten: include top-level orders + nested bracket legs so TP/SL fills are visible
    flat: list[dict] = []
    for o in orders:
        flat.append(o)
        for leg in (o.get("legs") or []):
            flat.append({**leg, "ticker": o["ticker"]})

    filled_sells = [
        o for o in flat
        if o.get("ticker") == ticker
        and "sell" in (o.get("side") or "").lower()
        and (o.get("filled_price") or o.get("filled_avg_price"))
    ]

    # Invariant 1: fill must postdate this trade's own entry.
    filled_sells = [
        o for o in filled_sells
        if (o.get("filled_at") or o.get("submitted_at") or "") > entry_timestamp
    ]

    # Invariant 2: fill must not already be consumed by another trade's
    # recorded exit. Sorted newest-first so the first candidate that clears
    # both invariants is used; a stale/consumed fill doesn't disqualify a
    # genuinely newer one for the same ticker.
    filled_sells.sort(key=lambda o: o.get("filled_at") or o.get("submitted_at") or "", reverse=True)

    for candidate in filled_sells:
        exit_price = candidate.get("filled_price") or candidate.get("filled_avg_price")
        exited_at  = candidate.get("filled_at") or datetime.now(timezone.utc).isoformat()

        from backend.db import get_db
        conn = get_db()
        try:
            already_consumed = conn.execute(
                "SELECT 1 FROM live_trades WHERE exit_price = ? AND exited_at = ? LIMIT 1",
                (exit_price, exited_at),
            ).fetchone()
        finally:
            conn.close()
        if already_consumed:
            logger.warning(
                f"_find_exit_from_orders [{ticker}]: candidate fill "
                f"(price={exit_price}, at={exited_at}) already recorded as "
                f"another trade's exit — skipping, not reusing"
            )
            continue

        order_type = (candidate.get("type") or candidate.get("order_type") or "").lower()
        if "limit" in order_type:
            exit_reason = "TP"
        elif "stop" in order_type:
            exit_reason = "SL"
        else:
            exit_reason = "MANUAL"

        return exit_price, exit_reason, exited_at, "found"

    # Orders feed found nothing usable. Before declaring "no fill" —
    # corroborate against /account/activities, an independent read of the
    # same account (2026-08-23, both call sites of this function). The
    # HON arc's founding lesson (2026-08-06) is that orders is exactly the
    # feed that can go empty while a position genuinely closed cleanly — a
    # canceled OCO leg carries null fill fields in the orders feed, while
    # activities stayed correct to the penny throughout that whole incident.
    # Both feeds agreeing on empty is materially stronger evidence than
    # orders alone; a fill visible in activities but not orders means the
    # orders feed was the one that was empty, not the account.
    #
    # No ticker filter exists on this Alpaca endpoint (confirmed against the
    # SDK's own request model — see get_activities()'s docstring), so this
    # reads account-wide and filters by ticker here, same as the orders
    # flatten step above. `after=entry_timestamp` both scopes the read and
    # enforces invariant 1 at the source.
    try:
        activities = broker.get_activities("FILL", after=entry_timestamp)
    except Exception as e:
        logger.warning(f"_find_exit_from_orders [{ticker}]: activities corroboration unavailable — {e}")
        return None, "MANUAL", datetime.now(timezone.utc).isoformat(), "activities_unavailable"

    sell_fills = [
        a for a in activities
        if a.get("ticker") == ticker
        and a.get("side") == "sell" and a.get("price")
        and (a.get("filled_at") or "") > entry_timestamp
    ]

    # Group by order — a single sell order can produce several partial-fill
    # rows before it's done (2026-08-23; TradeActivity carries leaves_qty/
    # cum_qty precisely because of this). Taking the first matching row's
    # price would book one slice's price instead of the volume-weighted
    # average across the whole exit; a row with leaves_qty > 0 means the
    # order hasn't finished filling yet, not that it produced no evidence.
    by_order: dict[str, list[dict]] = {}
    for a in sell_fills:
        by_order.setdefault(a.get("order_id") or a.get("filled_at"), []).append(a)

    # Newest order group first, by its most recent row's timestamp.
    groups = sorted(
        by_order.values(),
        key=lambda rows: max(r.get("filled_at") or "" for r in rows),
        reverse=True,
    )

    saw_in_progress = False
    for rows in groups:
        latest = max(rows, key=lambda r: r.get("filled_at") or "")
        if (latest.get("leaves_qty") or 0) > 0:
            # Positive evidence the position is genuinely closing, not lost
            # — a materially different state from "nothing explains this."
            # Not booked (the price isn't final yet) and not the trigger for
            # the both-feeds-empty freeze either; see "exit_in_progress"
            # below.
            saw_in_progress = True
            logger.info(f"_find_exit_from_orders [{ticker}]: sell order "
                        f"{latest.get('order_id')} still filling "
                        f"(leaves_qty={latest['leaves_qty']}) — not a completed exit yet")
            continue

        total_qty = sum(r.get("qty") or 0 for r in rows)
        if total_qty <= 0:
            continue
        exit_price = round(sum((r.get("qty") or 0) * (r.get("price") or 0) for r in rows) / total_qty, 4)
        exited_at  = latest.get("filled_at")

        from backend.db import get_db
        conn = get_db()
        try:
            already_consumed = conn.execute(
                "SELECT 1 FROM live_trades WHERE exit_price = ? AND exited_at = ? LIMIT 1",
                (exit_price, exited_at),
            ).fetchone()
        finally:
            conn.close()
        if already_consumed:
            continue

        logger.info(f"_find_exit_from_orders [{ticker}]: orders feed empty, activities feed "
                    f"found a corroborating fill (${exit_price}, {len(rows)} slice(s)) — using it")
        return exit_price, "MANUAL", exited_at, "found"

    if saw_in_progress:
        # A sell order for this ticker is actively filling — this is not an
        # absence of evidence, it's evidence the exit hasn't finished. Freezing
        # the whole account (CHECK 66) over an order that's in the middle of
        # doing exactly what it should is the "the freeze isn't free" cost
        # named at review, paid for no reason: the next cycle will very likely
        # see the completing row. Callers must not freeze on this — bounded
        # retry, not UNRECONCILED.
        return None, "MANUAL", datetime.now(timezone.utc).isoformat(), "exit_in_progress"

    # No candidate cleared both invariants in either feed, and both feeds
    # were actually read successfully — two independent confirmations of
    # absence, not one. Freeze and alert, don't fabricate.
    logger.warning(f"_find_exit_from_orders [{ticker}]: orders and activities both show "
                    f"no corroborating fill since entry")
    return None, "MANUAL", datetime.now(timezone.utc).isoformat(), "both_feeds_empty"


def _current_price(ticker: str) -> float | None:
    """Fetch latest close price for a single ticker via yfinance."""
    try:
        hist = yf.Ticker(ticker).history(period="2d")
        if hist.empty:
            return None
        return float(hist["Close"].iloc[-1])
    except Exception as e:
        logger.warning(f"_current_price [{ticker}]: {e}")
        return None


def _leg_reason(leg: dict, entry_price: float | None = None) -> str:
    order_type = (leg.get("order_type") or "").lower()
    if "limit" in order_type:
        return "TP"
    if "stop" in order_type:
        fill = leg.get("filled_avg_price")
        if entry_price and fill and float(fill) > entry_price:
            return "TSL"
        return "SL"
    return "MANUAL"
