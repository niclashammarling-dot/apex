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


def check_live_exits() -> list[dict]:
    """
    For each open live trade:
    1. Check Alpaca for filled TP/SL bracket legs.
    2. If held longer than max_hold_days, cancel the bracket and close at market.
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

    closed = []

    for trade in open_trades:
        order_id = trade["alpaca_order_id"]
        try:
            order = broker.get_order_by_id(order_id)
        except Exception as e:
            logger.warning(f"Live exit check [{trade['ticker']}]: could not fetch order {order_id} — {e}")
            continue

        # ── Check TP/SL bracket legs ────────────────────────────────────────
        filled_leg = _find_filled_sell_leg(order)
        if filled_leg:
            exit_price  = filled_leg["filled_avg_price"]
            exit_reason = _leg_reason(filled_leg)
            pnl         = round((exit_price - trade["entry_price"]) * trade["qty"], 2)
            outcome     = "WIN" if pnl > 0 else "LOSS"
            exited_at   = filled_leg["filled_at"] or datetime.now(timezone.utc).isoformat()

        # ── Time-stop ───────────────────────────────────────────────────────
        elif _trading_days_since(trade["timestamp"]) >= max_hold_days:
            logger.info(f"Live time-stop [{trade['ticker']}]: {max_hold_days} trading days elapsed — closing")
            try:
                result = broker.close_position(trade["ticker"])
                exit_price = float(result.get("filled_avg_price") or trade["entry_price"])
            except Exception as e:
                logger.warning(f"Live time-stop [{trade['ticker']}]: close_position failed — {e}")
                continue
            exit_reason = "TIME"
            pnl         = round((exit_price - trade["entry_price"]) * trade["qty"], 2)
            outcome     = "WIN" if pnl > 0 else "LOSS"
            exited_at   = datetime.now(timezone.utc).isoformat()

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
            f"Live exit [{trade['ticker']}]: {exit_reason} "
            f"entry=${trade['entry_price']:.2f} exit=${exit_price:.2f} "
            f"pnl={pnl:+.2f} → {outcome}"
        )
        closed.append({
            "ticker":      trade["ticker"],
            "outcome":     outcome,
            "exit_reason": exit_reason,
            "pnl":         pnl,
        })

    return closed


def check_live_regime_exits() -> list[dict]:
    """
    Exit open live positions whose sector has fallen below the regime allocation
    floor (adjusted_score < ALLOCATION_THRESHOLD), or is no longer in the leaderboard.

    Mirrors wallet.check_regime_exits() for live trades.
    Cancels the Alpaca bracket order and closes at market before recording the exit.
    """
    if not LIVE_ENABLED:
        return []

    from datetime import date
    from backend.regime.regime_bayes import ALLOCATION_THRESHOLD
    from backend.scheduler import _get_regime_bayes

    rb = _get_regime_bayes()
    if rb is None:
        return []

    result = rb.last_result()
    if result is None:
        return []

    try:
        result_date = date.fromisoformat(result.date)
        if (date.today() - result_date).days > 1:
            logger.debug(f"Live regime exit check: result is stale ({result.date}) — skipping")
            return []
    except ValueError:
        return []

    sector_scores: dict[str, float] = {
        e.sector: e.adjusted_score for e in result.leaderboard
    }

    open_trades = get_open_live_trades()
    if not open_trades:
        return []

    flagged = [
        t for t in open_trades
        if sector_scores.get(t["sector"], 0.0) < ALLOCATION_THRESHOLD
    ]
    if not flagged:
        return []

    from backend.brokers import alpaca as broker
    closed = []

    for trade in flagged:
        ticker    = trade["ticker"]
        sector    = trade["sector"]
        adj_score = sector_scores.get(sector, 0.0)

        try:
            result_close = broker.close_position(ticker)
            exit_price   = float(result_close.get("filled_avg_price") or trade["entry_price"])
        except Exception as e:
            logger.warning(f"Live regime exit [{ticker}]: close_position failed — {e}")
            continue

        pnl     = round((exit_price - trade["entry_price"]) * trade["qty"], 2)
        outcome = "WIN" if pnl >= 0 else "LOSS"
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
            f"adj_score={adj_score:.3f} < floor={ALLOCATION_THRESHOLD} | "
            f"entry=${trade['entry_price']:.2f} exit=${exit_price:.2f} pnl={pnl:+.2f} ({pnl_pct:+.1%})"
        )
        closed.append({
            "ticker":      ticker,
            "sector":      sector,
            "outcome":     outcome,
            "exit_reason": "REGIME",
            "adj_score":   adj_score,
            "pnl":         pnl,
            "pnl_pct":     pnl_pct,
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


def _leg_reason(leg: dict) -> str:
    order_type = (leg.get("order_type") or "").lower()
    if "limit" in order_type:
        return "TP"
    if "stop" in order_type:
        return "SL"
    return "MANUAL"
