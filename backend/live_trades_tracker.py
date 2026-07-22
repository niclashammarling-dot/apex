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
from loguru import logger

from backend.brokers.alpaca import OrderTerminalError
from backend.config import LIVE_ENABLED
from backend.db import (
    close_live_trade,
    get_open_live_trades,
    set_live_trade_profit_lock_activated,
    update_live_trade_peak_price,
)


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

    sl_leg = next(
        (
            leg for leg in (order.get("legs") or [])
            if "stop" in (leg.get("order_type") or "").lower()
            and leg.get("status") not in ("filled", "cancelled", "replaced", "expired")
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
    if not open_trades:
        return []

    from backend.brokers import alpaca as broker
    from backend.live_config import get_live_config
    cfg           = get_live_config()
    max_hold_days = cfg["max_hold_days"]

    # Snapshot of tickers currently held in Alpaca — used for reconciliation fallback.
    try:
        alpaca_positions = {p["ticker"] for p in broker.get_positions()}
    except Exception as e:
        logger.warning(f"Live exit check: could not fetch Alpaca positions — skipping reconciliation: {e}")
        alpaca_positions = None

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
                    logger.warning(f"Live time-stop [{ticker}]: position not found on broker — reconciling")
                    exit_price, exit_reason, exited_at = _find_exit_from_orders(ticker, broker)
                    if exit_price is None:
                        logger.warning(f"Live time-stop [{ticker}]: position gone but no filled order — skipping")
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
            _terminal_statuses = {"cancelled", "expired", "replaced", "done_for_day"}
            if (order is not None
                    and (order.get("filled_qty") or 0) == 0
                    and not any(s in (order.get("status") or "").lower() for s in _terminal_statuses)):
                logger.debug(f"Live exit check [{ticker}]: buy order pending (status={order.get('status')}) — waiting for fill")
                continue
            # Position is confirmed gone — cancel any remaining bracket legs immediately.
            # Orphaned sell orders that fire against a zero position create short exposure.
            try:
                cancelled = broker.cancel_open_orders(ticker)
                if cancelled:
                    logger.info(f"Live exit reconciliation [{ticker}]: cancelled {cancelled} orphaned bracket order(s)")
            except Exception as _ce:
                logger.warning(f"Live exit reconciliation [{ticker}]: could not cancel orders — {_ce}")
            exit_price, exit_reason, exited_at = _find_exit_from_orders(ticker, broker)
            if exit_price is None:
                # Order history exhausted (>100 orders) or bracket leg not visible.
                # Fall back to current market price so the trade doesn't zombie.
                exit_price = _current_price(ticker)
                if exit_price is None:
                    logger.warning(f"Live exit reconciliation [{ticker}]: position gone, no order found, price unavailable — skipping")
                    continue
                exit_reason = "RECONCILIATION"
                exited_at   = datetime.now(timezone.utc).isoformat()
                logger.warning(f"Live exit reconciliation [{ticker}]: no fill found — closing at current price ${exit_price:.2f}")
            else:
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
    open position. Prevents bracket legs from creating short exposure after
    a position is closed externally (margin call, manual close, etc.).
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
            try:
                n = broker.cancel_open_orders(ticker)
                if n:
                    logger.warning(f"cancel_orphan_brackets [{ticker}]: cancelled {n} orphaned sell order(s) (no open position)")
                    cancelled += n
            except Exception as e:
                logger.warning(f"cancel_orphan_brackets [{ticker}]: cancel failed — {e}")

    return cancelled


def _find_filled_sell_leg(order: dict) -> dict | None:
    """Return the first filled sell leg from a bracket order, or None."""
    for leg in order.get("legs") or []:
        if "sell" in (leg.get("side") or "") and "filled" in (leg.get("status") or ""):
            if leg.get("filled_avg_price"):
                return leg
    return None


def _find_exit_from_orders(ticker: str, broker) -> tuple[float | None, str, str]:
    """
    Search recent Alpaca orders for the most recent filled sell order for ticker.
    Returns (exit_price, exit_reason, exited_at). exit_price is None if not found.
    """
    try:
        orders = broker.get_orders(limit=100, nested=True)
    except Exception as e:
        logger.warning(f"_find_exit_from_orders [{ticker}]: get_orders failed — {e}")
        return None, "MANUAL", datetime.now(timezone.utc).isoformat()

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
    if not filled_sells:
        return None, "MANUAL", datetime.now(timezone.utc).isoformat()

    # Most recent by filled_at, falling back to submitted_at
    best = max(filled_sells, key=lambda o: o.get("filled_at") or o.get("submitted_at") or "")
    exit_price = best.get("filled_price") or best.get("filled_avg_price")
    exited_at  = best.get("filled_at") or datetime.now(timezone.utc).isoformat()

    order_type = (best.get("type") or best.get("order_type") or "").lower()
    if "limit" in order_type:
        exit_reason = "TP"
    elif "stop" in order_type:
        exit_reason = "SL"
    else:
        exit_reason = "MANUAL"

    return exit_price, exit_reason, exited_at


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
