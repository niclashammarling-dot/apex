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

from backend.config import LIVE_ENABLED
from backend.db import close_live_trade, get_open_live_trades


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


def check_live_exits() -> list[dict]:
    """
    For each open live trade:
    1. Check Alpaca for filled TP/SL bracket legs.
    2. If held longer than max_hold_days, cancel the bracket and close at market.
    3. If position no longer exists in Alpaca, find the most recent filled sell
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
        ticker   = trade["ticker"]
        order_id = trade["alpaca_order_id"]

        try:
            order = broker.get_order_by_id(order_id)
        except Exception as e:
            logger.warning(f"Live exit check [{ticker}]: could not fetch order {order_id} — {e}")
            order = None

        # ── Check TP/SL bracket legs ────────────────────────────────────────
        filled_leg = _find_filled_sell_leg(order) if order else None
        if filled_leg:
            exit_price  = filled_leg["filled_avg_price"]
            exit_reason = _leg_reason(filled_leg)
            pnl         = round((exit_price - trade["entry_price"]) * trade["qty"], 2)
            outcome     = "WIN" if pnl > 0 else "LOSS"
            exited_at   = filled_leg["filled_at"] or datetime.now(timezone.utc).isoformat()

        # ── Time-stop ───────────────────────────────────────────────────────
        elif _trading_days_since(trade["timestamp"]) >= max_hold_days:
            logger.info(f"Live time-stop [{ticker}]: {max_hold_days} trading days elapsed — closing")
            try:
                result = broker.close_position(ticker)
                exit_price = float(result.get("filled_avg_price") or trade["entry_price"])
            except Exception as e:
                logger.warning(f"Live time-stop [{ticker}]: close_position failed — {e}")
                continue
            exit_reason = "TIME"
            pnl         = round((exit_price - trade["entry_price"]) * trade["qty"], 2)
            outcome     = "WIN" if pnl > 0 else "LOSS"
            exited_at   = datetime.now(timezone.utc).isoformat()

        # ── Position-reconciliation fallback ─────────────────────────────────
        # Position gone from Alpaca but our DB still says OPEN. Covers:
        #   - Manual closes via Alpaca UI or external tooling
        #   - TP/SL fills on brackets placed with DAY TIF (legs expired; fill
        #     is not visible on the original order but position is gone)
        elif alpaca_positions is not None and ticker not in alpaca_positions:
            exit_price, exit_reason, exited_at = _find_exit_from_orders(ticker, broker)
            if exit_price is None:
                logger.warning(f"Live exit reconciliation [{ticker}]: position gone but no filled order found — skipping")
                continue
            pnl     = round((exit_price - trade["entry_price"]) * trade["qty"], 2)
            outcome = "WIN" if pnl > 0 else "LOSS"
            logger.info(f"Live exit reconciliation [{ticker}]: position closed externally, exit=${exit_price:.2f}")

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

        try:
            result_close = broker.close_position(ticker)
            exit_price   = float(result_close.get("filled_avg_price") or trade["entry_price"])
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
        orders = broker.get_orders(limit=100)
    except Exception as e:
        logger.warning(f"_find_exit_from_orders [{ticker}]: get_orders failed — {e}")
        return None, "MANUAL", datetime.now(timezone.utc).isoformat()

    filled_sells = [
        o for o in orders
        if o["ticker"] == ticker
        and "sell" in (o.get("side") or "").lower()
        and o.get("filled_price")
    ]
    if not filled_sells:
        return None, "MANUAL", datetime.now(timezone.utc).isoformat()

    # Most recent by filled_at, falling back to submitted_at
    best = max(filled_sells, key=lambda o: o.get("filled_at") or o.get("submitted_at") or "")
    exit_price = best["filled_price"]
    exited_at  = best.get("filled_at") or datetime.now(timezone.utc).isoformat()

    order_type = (best.get("type") or "").lower()
    if "limit" in order_type:
        exit_reason = "TP"
    elif "stop" in order_type:
        exit_reason = "SL"
    else:
        exit_reason = "MANUAL"

    return exit_price, exit_reason, exited_at


def _leg_reason(leg: dict) -> str:
    order_type = (leg.get("order_type") or "").lower()
    if "limit" in order_type:
        return "TP"
    if "stop" in order_type:
        return "SL"
    return "MANUAL"
